#!/usr/bin/env bash
# Start / stop / inspect JalDrishti services on one host. Each service runs in its own
# tmux session inside a restart loop (the lab containers have no systemd, cron or sudo).
#
#   pondctl.sh start worker|gateway     pondctl.sh stop worker|gateway
#   pondctl.sh restart worker|gateway   pondctl.sh status
#
# Per-host settings live in ~/pond.env (never committed), e.g. for sys2:
#   WORKER_NAME=sys2  PORT=3298  BIND=0.0.0.0  NICE=0
#   PG_DSN=postgresql://user:pass@127.0.0.1:5432/db
# and on sys1 additionally GATEWAY_WORKERS=sys1=http://127.0.0.1:4297,sys2=http://172.17.0.99:3298,...
set -euo pipefail

REPO=${REPO:-$HOME/village_pond_planner}
VENV=${VENV:-$HOME/pondenv}
ENV_FILE=${ENV_FILE:-$HOME/pond.env}
BIN=${BIN:-$HOME/pond-bin}
LOGS=${LOGS:-$HOME/pond-logs}
mkdir -p "$LOGS"

load_env() {
  set -a
  # shellcheck disable=SC1090
  [ -f "$ENV_FILE" ] && . "$ENV_FILE"
  set +a
  APP_VERSION=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo dev)
  export APP_VERSION
}

worker_loop() {
  load_env
  cd "$REPO/backend"
  # 1 CPU per container: stop numpy/BLAS from starting one thread per host core (120 here)
  export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
  # 512 MiB per container: one malloc arena per request thread fragments memory badly
  export MALLOC_ARENA_MAX=2
  export POND_DATA=${POND_DATA:-$HOME/pond-data}
  while true; do
    nice -n "${NICE:-0}" "$VENV/bin/uvicorn" main:app --host "${BIND:-0.0.0.0}" --port "${PORT:?PORT missing in $ENV_FILE}" \
      --workers 1 --no-access-log --timeout-keep-alive 75 --limit-concurrency 64 --backlog 512 \
      >>"$LOGS/worker.log" 2>&1 || true
    echo "$(date -Is) worker exited; restarting" >>"$LOGS/worker.log"
    sleep 1
  done
}

gateway_loop() {
  load_env
  while true; do
    # public 10.1.75.53:3297 arrives on port 3000 inside sys1; 3297 is kept for internal use
    GOMEMLIMIT=${GOMEMLIMIT:-150MiB} GOGC=${GOGC:-50} "$BIN/gateway" -addr "${GATEWAY_ADDR:-0.0.0.0:3000,0.0.0.0:3297}" \
      -static "$REPO/frontend/dist" -workers "${GATEWAY_WORKERS:?GATEWAY_WORKERS missing in $ENV_FILE}" \
      -version "$APP_VERSION" >>"$LOGS/gateway.log" 2>&1 || true
    echo "$(date -Is) gateway exited; restarting" >>"$LOGS/gateway.log"
    sleep 0.2
  done
}

session() { echo "pond-$1"; }

start() {
  local svc=$1
  if tmux has-session -t "$(session "$svc")" 2>/dev/null; then
    echo "$svc already running"
    return
  fi
  tmux new-session -d -s "$(session "$svc")" "bash '$REPO/deploy/pondctl.sh' _loop $svc"
  echo "$svc started"
}

stop() {
  local svc=$1
  tmux kill-session -t "$(session "$svc")" 2>/dev/null || true
  # the loop's child keeps running after its tmux session dies; stop it too
  if [ "$svc" = worker ]; then pkill -u "$USER" -f "uvicorn main:app" || true; fi
  if [ "$svc" = gateway ]; then pkill -u "$USER" -x gateway || true; fi
  echo "$svc stopped"
}

status() {
  load_env
  for svc in worker gateway; do
    if tmux has-session -t "$(session "$svc")" 2>/dev/null; then echo "$svc: running"; else echo "$svc: stopped"; fi
  done
  if [ -n "${PORT:-}" ]; then
    curl -s -m 3 "http://127.0.0.1:${PORT}/api/health" || echo "worker health: no answer"
    echo
  fi
}

case "${1:-}" in
  start) start "${2:?service}" ;;
  stop) stop "${2:?service}" ;;
  restart) stop "${2:?service}"; sleep 1; start "$2" ;;
  status) status ;;
  _loop) if [ "$2" = worker ]; then worker_loop; else gateway_loop; fi ;;
  *) echo "usage: $0 start|stop|restart worker|gateway | status" >&2; exit 2 ;;
esac

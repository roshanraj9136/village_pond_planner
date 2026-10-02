#!/usr/bin/env bash
# Start / stop / inspect JalDrishti services on one host. Each service runs in its own
# tmux session inside a restart loop (the lab containers have no systemd, cron or sudo).
#
#   pondctl.sh start|stop|restart db|worker|gateway
#   pondctl.sh ensure         start every service listed in SERVICES that is not running
#   pondctl.sh install-hook   run `ensure` on every SSH login (see below)
#   pondctl.sh status
#
# Per-host settings live in ~/pond.env (never committed). Container IPs change whenever the
# lab restarts the containers, so nothing refers to them: each system's worker listens on
# port 3000, which the lab host publishes as 10.1.75.53:3298 / 3299 / 3300 for sys2 / 3 / 4.
#   sys1: WORKER_NAME=sys1 PORT=4297 BIND=127.0.0.1 NICE=10 SERVICES="worker gateway" SITES_WORKER=sys2
#         GATEWAY_WORKERS=sys1=http://127.0.0.1:4297,sys2=http://10.1.75.53:3298,sys3=...:3299,sys4=...:3300
#   sys2: WORKER_NAME=sys2 PORT=3000 SERVICES="db worker" PG_PORT=5433 PG_PASSWORD=...
#         PG_DSN=postgresql://pond:...@127.0.0.1:5433/postgres
#   sys3, sys4: WORKER_NAME=sys3 PORT=3000 SERVICES=worker
set -euo pipefail

REPO=${REPO:-$HOME/village_pond_planner}
VENV=${VENV:-$HOME/pondenv}
ENV_FILE=${ENV_FILE:-$HOME/pond.env}
BIN=${BIN:-$HOME/pond-bin}
LOGS=${LOGS:-$HOME/pond-logs}
PG_DATA=${PG_DATA:-$HOME/pond-pg}
mkdir -p "$LOGS"

load_env() {
  set -a
  # shellcheck disable=SC1090
  [ -f "$ENV_FILE" ] && . "$ENV_FILE"
  set +a
  APP_VERSION=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo dev)
  export APP_VERSION
}

pg_bin() { ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1; }

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
      -sites-worker "${SITES_WORKER:-}" -version "$APP_VERSION" >>"$LOGS/gateway.log" 2>&1 || true
    echo "$(date -Is) gateway exited; restarting" >>"$LOGS/gateway.log"
    sleep 0.2
  done
}

# PostgreSQL owned by this user (the system cluster needs sudo to start, which we do not
# have), listening on 127.0.0.1 only: just the worker on the same system talks to it.
db_init() {
  [ -f "$PG_DATA/PG_VERSION" ] && return
  local bin pwfile
  bin=$(pg_bin)
  pwfile=$(mktemp)
  printf '%s\n' "${PG_PASSWORD:?PG_PASSWORD missing in $ENV_FILE}" >"$pwfile"
  "$bin/initdb" -D "$PG_DATA" -U pond --pwfile="$pwfile" --auth=scram-sha-256 -E UTF8 --no-locale >>"$LOGS/db.log" 2>&1
  rm -f "$pwfile"
  cat >>"$PG_DATA/postgresql.conf" <<EOF

# JalDrishti (pondctl.sh)
listen_addresses = '127.0.0.1'
port = ${PG_PORT:-5433}
unix_socket_directories = '$PG_DATA'
max_connections = 30
shared_buffers = 32MB
EOF
}

db_loop() {
  load_env
  db_init
  local bin
  bin=$(pg_bin)
  while true; do
    # After a container restart the old postmaster.pid survives and its PID may now belong
    # to another process, which makes postgres refuse to start. No postgres of ours runs
    # at this point (this loop is the only one that starts it), so the file is stale.
    if ! pgrep -u "$USER" -f "$bin/postgres -D $PG_DATA" >/dev/null; then
      rm -f "$PG_DATA/postmaster.pid" "$PG_DATA"/.s.PGSQL.*.lock
    fi
    "$bin/postgres" -D "$PG_DATA" >>"$LOGS/db.log" 2>&1 || true
    echo "$(date -Is) db exited; restarting" >>"$LOGS/db.log"
    sleep 2
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
  case $svc in
    worker) pkill -u "$USER" -f "uvicorn main:app" || true ;;
    gateway) pkill -u "$USER" -x gateway || true ;;
    db) [ -f "$PG_DATA/PG_VERSION" ] && "$(pg_bin)/pg_ctl" -D "$PG_DATA" stop -m fast >/dev/null 2>&1 || true ;;
  esac
  echo "$svc stopped"
}

ensure() {
  load_env
  # the database first, so the worker finds it on its first request
  for svc in ${SERVICES:-worker}; do start "$svc"; done
}

# The containers have no init system, so nothing of ours starts after the lab restarts them.
# sshd runs ~/.ssh/rc on every login: any deploy, status check or login brings everything back.
install_hook() {
  mkdir -p "$HOME/.ssh"
  cat >"$HOME/.ssh/rc" <<EOF
# JalDrishti: start any stopped service (pondctl.sh install-hook). Silent and in the
# background so it never delays a login or mixes into scp/rsync output.
(bash '$REPO/deploy/pondctl.sh' ensure </dev/null >>'$LOGS/ensure.log' 2>&1 &)
EOF
  echo "login hook installed"
}

status() {
  load_env
  for svc in ${SERVICES:-worker}; do
    if tmux has-session -t "$(session "$svc")" 2>/dev/null; then echo "$svc: running"; else echo "$svc: stopped"; fi
  done
  if [ -n "${PORT:-}" ]; then
    curl -s -m 3 "http://127.0.0.1:${PORT}/api/health" || echo "worker health: no answer"
    echo
  fi
  if [[ " ${SERVICES:-} " == *" db "* ]]; then
    "$(pg_bin)/pg_isready" -h 127.0.0.1 -p "${PG_PORT:-5433}" || true
  fi
}

case "${1:-}" in
  start) start "${2:?service}" ;;
  stop) stop "${2:?service}" ;;
  restart) stop "${2:?service}"; sleep 1; start "$2" ;;
  ensure) ensure ;;
  install-hook) install_hook ;;
  status) status ;;
  _loop)
    case $2 in
      worker) worker_loop ;;
      gateway) gateway_loop ;;
      db) db_loop ;;
      *) echo "unknown service $2" >&2; exit 2 ;;
    esac ;;
  *) echo "usage: $0 start|stop|restart db|worker|gateway | ensure | install-hook | status" >&2; exit 2 ;;
esac

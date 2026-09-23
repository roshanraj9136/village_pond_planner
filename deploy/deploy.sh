#!/usr/bin/env bash
# Roll out JalDrishti to the four lab systems from a developer machine (Git Bash / Linux / macOS).
#
#   deploy/deploy.sh              servers `git pull` from GitHub (the normal path)
#   SYNC=tar deploy/deploy.sh     copy this working tree instead (test uncommitted changes)
#
# Order: build frontend -> sync code on every host -> rebuild the gateway on sys1 ->
# restart workers one at a time, each only after the previous one reports healthy (the
# site keeps serving throughout) -> restart the gateway -> smoke-test the public URL.
set -euo pipefail

# JD_ prefix: plain names like PUBLIC or HOST are already set on some systems (Windows sets PUBLIC)
JD_HOST=${JD_HOST:-10.1.75.53}
JD_KEY=${JD_KEY:-$HOME/.ssh/id_ed25519}
SYNC=${SYNC:-git}
JD_URL=${JD_URL:-http://$JD_HOST:3297}
GATEWAY_SSH=2297
WORKER_SSH=(2297 2298 2299 2300)
ROOT=$(cd "$(dirname "$0")/.." && pwd)

remote() { local port=$1; shift; ssh -i "$JD_KEY" -p "$port" -o BatchMode=yes -o ConnectTimeout=10 "student@$JD_HOST" "$@"; }
say() { printf '\n== %s\n' "$*"; }

say "building frontend"
(cd "$ROOT/frontend" && npm install --no-audit --no-fund --silent && npm run build --silent)
find "$ROOT/frontend/dist" -type f \( -name '*.js' -o -name '*.css' -o -name '*.svg' -o -name '*.html' \) -exec gzip -k -9 -f {} \;

for port in "${WORKER_SSH[@]}"; do
  say "syncing code on :$port ($SYNC)"
  if [ "$SYNC" = git ]; then
    remote "$port" 'git -C ~/village_pond_planner pull --ff-only --quiet && git -C ~/village_pond_planner log -1 --format="%h %s"'
  else
    tar -C "$ROOT" --exclude='__pycache__' --exclude='.pytest_cache' --exclude='venv*' --exclude='*.db' \
      -czf - backend deploy gateway sample_data | remote "$port" 'tar -xzf - -C ~/village_pond_planner'
  fi
  remote "$port" '~/pondenv/bin/pip install -q --no-cache-dir --retries 10 -r ~/village_pond_planner/backend/requirements.txt'
done

say "uploading frontend and building gateway on sys1"
tar -C "$ROOT/frontend" -czf - dist | remote "$GATEWAY_SSH" \
  'rm -rf ~/village_pond_planner/frontend/dist.new && mkdir -p ~/village_pond_planner/frontend/dist.new &&
   tar -xzf - -C ~/village_pond_planner/frontend/dist.new --strip-components=1 &&
   rm -rf ~/village_pond_planner/frontend/dist.old && { [ -d ~/village_pond_planner/frontend/dist ] && mv ~/village_pond_planner/frontend/dist ~/village_pond_planner/frontend/dist.old || true; } &&
   mv ~/village_pond_planner/frontend/dist.new ~/village_pond_planner/frontend/dist && rm -rf ~/village_pond_planner/frontend/dist.old'
remote "$GATEWAY_SSH" 'cd ~/village_pond_planner/gateway && export GOPATH=~/tools/gopath GOCACHE=~/tools/gocache GOTOOLCHAIN=local PATH=~/tools/go/bin:$PATH &&
  go vet ./... && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o ~/pond-bin/gateway.new . && mv ~/pond-bin/gateway.new ~/pond-bin/gateway'

for port in "${WORKER_SSH[@]}"; do
  say "restarting worker on :$port"
  remote "$port" 'bash ~/village_pond_planner/deploy/pondctl.sh restart worker >/dev/null
    . ~/pond.env
    for i in $(seq 1 60); do
      if curl -sf -m 2 "http://127.0.0.1:$PORT/api/health" >/dev/null; then echo "$WORKER_NAME healthy after ${i}s"; exit 0; fi
      sleep 1
    done
    echo "$WORKER_NAME did not become healthy" >&2; tail -20 ~/pond-logs/worker.log >&2; exit 1'
done

say "restarting gateway"
remote "$GATEWAY_SSH" 'bash ~/village_pond_planner/deploy/pondctl.sh restart gateway >/dev/null; sleep 3; tail -1 ~/pond-logs/gateway.log'

say "smoke test $JD_URL"
for path in / /status /docs /api/health /gateway/status /api/sample/contour_map; do
  printf '%s %s\n' "$(curl -s -o /dev/null -m 15 -w '%{http_code}' "$JD_URL$path")" "$path"
done
curl -s -m 60 -o /dev/null -w '%{http_code} POST /analyzeContour (sample)\n' \
  -F "contour_map=@$ROOT/sample_data/contours_1m.kml" "$JD_URL/analyzeContour"

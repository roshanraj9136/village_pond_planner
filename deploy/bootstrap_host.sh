#!/usr/bin/env bash
# One-time setup of a JalDrishti host: Python environment + satellite DEM cache for the
# Durg / Bhilai / Raipur region (20-23 N, 80-83 E). Safe to re-run; finished steps are skipped.
set -euo pipefail
REPO=${REPO:-$HOME/village_pond_planner}
VENV=${VENV:-$HOME/pondenv}
DATA=${POND_DATA:-$HOME/pond-data}

if [ ! -x "$VENV/bin/pip" ]; then
  if ! python3 -m venv --clear "$VENV" 2>/dev/null; then
    # no python3-venv/ensurepip on this host and no sudo: make the venv without pip, then bootstrap it
    python3 -m venv --clear --without-pip "$VENV"
    curl -fsS --retry 5 -o /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py
    "$VENV/bin/python" /tmp/get-pip.py -q --no-cache-dir
    mv /tmp/get-pip.py "$VENV/get-pip.py"
  fi
fi
for attempt in 1 2 3 4 5; do
  "$VENV/bin/pip" install -q --no-cache-dir --retries 10 --timeout 60 -r "$REPO/backend/requirements.txt" && break
  echo "pip attempt $attempt failed; retrying" >&2
  sleep 5
done
mkdir -p "$DATA/dem" "$DATA/rain"
cd "$REPO/backend"
"$VENV/bin/python" dem.py region 20 22 80 82 --root "$DATA/dem"
echo BOOTSTRAP_DONE

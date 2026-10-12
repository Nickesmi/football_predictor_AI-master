#!/usr/bin/env bash
# Roll back a Python 3.11 cutover: restore the .venv kept by cutover_python311.sh.
#   scripts/orchestration/rollback_python311.sh <stamp>
# Refuses while the API or workers are running. The 3.11 environment is kept
# as .venv.py311.failed.<now> for diagnosis. Database is not touched; a DB
# restore (only if needed) is a separate manual step (docs/CUTOVER_PYTHON311.md).
set -euo pipefail
ROOT="${FP_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
API_PORT="${API_PORT:-8001}"
STAMP="${1:?usage: rollback_python311.sh <stamp>}"
cd "$ROOT"
running_here() {  # any process matching $1 whose working directory is this deployment
  local pid cwd
  for pid in $(pgrep -f "$1" || true); do
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
    [ "$cwd" = "$ROOT" ] && return 0
  done
  return 1
}
[ -d ".venv.py313.$STAMP" ] || { echo "ABORT: .venv.py313.$STAMP not found" >&2; exit 1; }
if (echo >/dev/tcp/127.0.0.1/"$API_PORT") >/dev/null 2>&1 || running_here "uvicorn api.main:app" \
   || running_here "scripts/(collection|odds|settlement|live)_worker.py"; then
  echo "ABORT: stop the API and workers first; this script never stops services." >&2; exit 1
fi
NOW="$(date +%Y%m%d-%H%M%S)"
[ -d .venv ] && mv .venv ".venv.py311.failed.$NOW"
mv ".venv.py313.$STAMP" .venv
echo ".venv restored: $(.venv/bin/python --version). The 3.11 env was kept as .venv.py311.failed.$NOW"

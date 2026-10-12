#!/usr/bin/env bash
# Python 3.11 cutover for the local deployment (docs/CUTOVER_PYTHON311.md).
#
#   scripts/orchestration/cutover_python311.sh            # dry run: checks + plan, changes nothing
#   scripts/orchestration/cutover_python311.sh --execute  # performs the cutover
#
# Rebuilds .venv on Python 3.11 from requirements-lock.txt. Launchers, crontab
# and systemd units already run .venv/bin/python, so nothing else changes.
# The previous environment is kept as .venv.py313.<stamp> for rollback
# (scripts/orchestration/rollback_python311.sh <stamp>).
# Never stops or restarts the running application: it refuses to run while it is up.
set -euo pipefail

ROOT="${FP_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
BACKUP_ROOT="${FP_BACKUP_DIR:-$HOME/Desktop/football_predictor_backups}"
API_PORT="${API_PORT:-8001}"
SMOKE_PORT="${FP_SMOKE_PORT:-8099}"
STAMP="$(date +%Y%m%d-%H%M%S)"
EXECUTE=0
[ "${1:-}" = "--execute" ] && EXECUTE=1
cd "$ROOT"

running_here() {  # any process matching $1 whose working directory is this deployment
  local pid cwd
  for pid in $(pgrep -f "$1" || true); do
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
    [ "$cwd" = "$ROOT" ] && return 0
  done
  return 1
}
step() { printf '\n== %s\n' "$*"; }
fail() { echo "ABORT: $*" >&2; exit 1; }
run()  { if [ "$EXECUTE" = 1 ]; then "$@"; else echo "   (dry run) $*"; fi; }

step "1. Preconditions"
command -v python3.11 >/dev/null || fail "python3.11 not found (brew install python@3.11)"
echo "   python3.11: $(python3.11 --version)"
[ -f requirements-lock.txt ] || fail "requirements-lock.txt missing"
[ -d .venv ] && echo "   current .venv: $(.venv/bin/python --version 2>&1)" || echo "   no .venv yet"
if (echo >/dev/tcp/127.0.0.1/"$API_PORT") >/dev/null 2>&1 || running_here "uvicorn api.main:app"; then
  fail "the API is running (port $API_PORT / uvicorn api.main). Stop it yourself first; this script never stops services."
fi
running_here "scripts/(collection|odds|settlement|live)_worker.py" && fail "football workers are running; stop them first."
if [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  echo "   WARNING: tracked files have uncommitted changes (a frozen baseline will report code_revision +dirty)."
fi

step "2. Back up the database"
DEST="$BACKUP_ROOT/cutover_$STAMP"
DB="${FOOTBALL_PREDICTOR_DB_PATH:-$ROOT/data/engine.db}"
echo "   $DB -> $DEST/engine.db"
if [ "$EXECUTE" = 1 ]; then
  mkdir -p -m 700 "$DEST"
  sqlite3 "$DB" ".backup '$DEST/engine.db'"
  [ "$(sqlite3 "$DEST/engine.db" 'PRAGMA integrity_check;')" = "ok" ] || fail "backup integrity check failed"
  shasum -a 256 "$DEST/engine.db" > "$DEST/engine.db.sha256"
  chmod 600 "$DEST"/*
fi

step "3. Keep the current environment for rollback"
[ -d .venv ] && run mv .venv ".venv.py313.$STAMP"

step "4. Build .venv on Python 3.11 from the lock"
run python3.11 -m venv .venv
run .venv/bin/python -m pip install --quiet --upgrade pip
run .venv/bin/pip install --quiet -r requirements-lock.txt
run .venv/bin/pip check

step "5. Verify (isolated tests, integrity audit)"
run .venv/bin/python -m pytest tests -q -p no:cacheprovider
run .venv/bin/python scripts/test_integrity_audit.py

step "6. Smoke test on port $SMOKE_PORT against a COPY of the backup"
if [ "$EXECUTE" = 1 ]; then
  cp "$DEST/engine.db" "$DEST/smoke_copy.db"
  FOOTBALL_PREDICTOR_DB_PATH="$DEST/smoke_copy.db" APP_ENV=development HOME="$(mktemp -d)" \
    .venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port "$SMOKE_PORT" >"$DEST/smoke.log" 2>&1 &
  SMOKE_PID=$!
  trap 'kill $SMOKE_PID 2>/dev/null || true' EXIT
  for _ in $(seq 1 30); do curl -sf "http://127.0.0.1:$SMOKE_PORT/api/health" >/dev/null && break; sleep 1; done
  curl -sf "http://127.0.0.1:$SMOKE_PORT/api/health" >"$DEST/smoke_health.json" || fail "smoke: /api/health failed (see $DEST/smoke.log)"
  curl -sf "http://127.0.0.1:$SMOKE_PORT/api/baseline/status" >"$DEST/smoke_baseline.json" || fail "smoke: /api/baseline/status failed"
  echo "   health:   $(cat "$DEST/smoke_health.json")"
  echo "   baseline: $(cat "$DEST/smoke_baseline.json")"
  kill $SMOKE_PID; trap - EXIT
else
  echo "   (dry run) start uvicorn on $SMOKE_PORT with a DB copy, GET /api/health and /api/baseline/status, stop it"
fi

step "Done"
if [ "$EXECUTE" = 1 ]; then
  echo "   .venv is now $(.venv/bin/python --version). Previous env: .venv.py313.$STAMP. Backup: $DEST"
  echo "   Start the app with ./run_web.command (and workers as before)."
  echo "   Rollback: scripts/orchestration/rollback_python311.sh $STAMP"
else
  echo "   Dry run only. Re-run with --execute after stopping the API and workers."
fi

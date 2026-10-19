#!/bin/bash
# M3.5 Mac shadow cycle wrapper, run by launchd (deploy/shadow/macos/com.footballpredictor.shadow.plist).
# One Medium cycle under the pinned manifest. Exits 0 without doing anything when the emergency stop
# file exists or the Mac has no network route (no request is attempted, so no quota is spent; the gap
# is recorded as missed slots by the next cycle).
set -euo pipefail
ROOT="${FOOTBALL_SHADOW_ROOT:?FOOTBALL_SHADOW_ROOT is not set}"
RUN="$ROOT/run"
echo "{\"wrapper_started_at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" >&2
if [ -e "$RUN/SHADOW_STOP" ]; then echo '{"stopped": "SHADOW_STOP present"}'; exit 0; fi
if ! /sbin/route -n get default >/dev/null 2>&1; then echo '{"skipped": "no network route"}'; exit 0; fi
set -a
. "$ROOT/etc/shadow.env"
. "$ROOT/etc/api_key.env"
set +a
export API_FOOTBALL_CONSUMER=shadow_cycle
cd "$RUN"
exec "$ROOT/venv/bin/python" "$ROOT/code/scripts/shadow_cycle.py" \
  --db "$RUN/engine_shadow.db" --ledger "$RUN/api_football_ledger.db" \
  --manifest "$RUN/shadow_manifest.v3.json" \
  --profile medium --max-requests-per-cycle 20 --budget 70

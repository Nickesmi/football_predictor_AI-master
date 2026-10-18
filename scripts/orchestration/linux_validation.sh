#!/usr/bin/env bash
# Linux validation for the shadow pathway (M3.4) in an isolated python:3.11-slim container.
# Read-only mount of the repository; results written to $OUT on the host. Sends no provider requests
# (no API keys inside; the network guard of tests/conftest.py blocks external calls).
#
#   scripts/orchestration/linux_validation.sh [<restored production DB copy>]
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${OUT:-$ROOT/../football_predictor_backups/linux_validation_$(date +%Y%m%d_%H%M)}"
COPY="${1:-}"
mkdir -p "$OUT"
docker info >/dev/null 2>&1 || { echo "BLOCKED: Docker daemon not available (docker info failed)"; exit 3; }
MOUNT_COPY=()
if [ -n "$COPY" ]; then cp "$COPY" "$OUT/migration_copy.db"; MOUNT_COPY=(-v "$OUT/migration_copy.db:/work/migration_copy.db"); fi
docker run --rm --platform linux/amd64 \
  -v "$ROOT":/src:ro -v "$OUT":/out "${MOUNT_COPY[@]}" \
  -e API_FOOTBALL_KEY= -e APIFOOTBALL_API_KEY= -e RAPIDAPI_KEY= -e ODDS_API_KEY= \
  python:3.11-slim bash -c '
    set -e
    cp -r /src /work/repo 2>/dev/null || { mkdir -p /work && cp -r /src /work/repo; }
    cd /work/repo && rm -rf .venv venv .venv311 data/*.db .cache
    pip install -q -r requirements-lock.txt > /out/pip.log 2>&1
    python --version | tee /out/python_version.txt
    python -m pytest tests -q -p no:cacheprovider > /out/full_suite.log 2>&1; echo "full_suite exit $?" >> /out/summary.txt
    python -m pytest tests/golden -q -p no:cacheprovider > /out/golden.log 2>&1; echo "golden exit $?" >> /out/summary.txt
    python -m pytest tests/test_shadow_validation.py tests/test_shadow_operations.py tests/test_shadow_candidate_pathway.py \
        tests/test_team_history_rh1.py tests/test_team_identity.py tests/test_team_registry.py \
        tests/test_odds_history_k53.py tests/test_odds_m3.py tests/test_odds_layer.py tests/test_demo_quota.py \
        tests/test_workspace_isolation.py -q -p no:cacheprovider > /out/shadow_identity_odds.log 2>&1
    echo "shadow_identity_odds exit $?" >> /out/summary.txt
    if [ -f /work/migration_copy.db ]; then
      M0_EXISTING_DB_COPY=/work/migration_copy.db python -m pytest tests/test_schema_migrations.py -q -p no:cacheprovider \
        > /out/migrations.log 2>&1; echo "migrations exit $?" >> /out/summary.txt
    fi
    python scripts/shadow_dry_run.py --profile medium --fixtures 53 --budget 70 --other-consumers 20 \
        --out /out/dry_run_medium.json > /out/dry_run.log 2>&1; echo "dry_run exit $?" >> /out/summary.txt
    python scripts/test_integrity_audit.py > /out/integrity_audit.log 2>&1; echo "integrity_audit exit $?" >> /out/summary.txt
  '
cat "$OUT/summary.txt"
echo "results: $OUT"

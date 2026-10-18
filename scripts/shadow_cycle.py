#!/usr/bin/env python3
"""
Shadow validation cycle (M3 Phase F) — observations only, nothing counted.

    python scripts/shadow_cycle.py --db <shadow db> --budget 60
    python scripts/shadow_cycle.py --db data/engine.db --budget 60 --allow-production   # only after cutover approval

Runs one cycle of src/shadow/cycle.py: fixture discovery (every 12 h),
league-date odds collection, decision snapshots (legacy, v1, BILQE) and
settlement. --budget is the approved API-Football daily request budget for
the whole day (shared with every other caller logged in provider_quota_log);
with 0 nothing is requested. Schedule every 30 minutes
(scripts/orchestration/shadow/; not installed by this script).
"""

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = (PROJECT_ROOT / "data" / "engine.db").resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--budget", required=True, type=int, help="approved API-Football requests per UTC day")
    parser.add_argument("--allow-production", action="store_true")
    parser.add_argument("--force-discovery", action="store_true")
    parser.add_argument("--ledger", type=Path, help="shared API-Football ledger for all consumers (API_FOOTBALL_LEDGER)")
    parser.add_argument("--max-age", type=int, default=240, help="ODDS_MAX_AGE_MINUTES for the shadow (provisional)")
    parser.add_argument("--max-requests-per-cycle", type=int, default=None)
    parser.add_argument("--profile", choices=("low", "medium", "full"), default="medium",
                        help="competition set and closing capture (src/shadow/cycle.PROFILES)")
    args = parser.parse_args()

    db = args.db.resolve()
    if db == LIVE_DB and (PROJECT_ROOT / "data" / "SERVICE_HOLD.json").exists():
        print("refusing: data/engine.db is under a service hold; shadow cycles never release it", file=sys.stderr)
        return 2
    if db == LIVE_DB and not args.allow_production:
        print("refusing: data/engine.db needs --allow-production (after the cutover is approved)", file=sys.stderr)
        return 2
    if not db.exists():
        print(f"refusing: {db} does not exist; create the shadow DB from a verified backup first", file=sys.stderr)
        return 2
    if os.getenv("BASELINE_FREEZE"):
        print("note: BASELINE_FREEZE is set; shadow observations are still never counted", file=sys.stderr)
    os.environ["FOOTBALL_PREDICTOR_DB_PATH"] = str(db)
    os.environ["ODDS_REQUEST_BUDGET_API_FOOTBALL"] = str(max(0, args.budget))
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.ops.workspace_guard import isolate  # noqa: E402

    if db != LIVE_DB:
        isolate(db.parent / "workspace")   # own working directory and cache next to the isolated DB
    if args.ledger:
        os.environ["API_FOOTBALL_LEDGER"] = str(args.ledger.resolve())
    os.environ.setdefault("API_FOOTBALL_CONSUMER", "shadow_cycle")
    from src.ops import api_guard  # noqa: E402

    if args.budget > 0 or api_guard.required():
        # Deployment mode: every API-Football request of this process goes through the shared ledger.
        try:
            api_guard.install("shadow_cycle")
        except api_guard.LedgerUnavailable as exc:
            print(f"refusing (fail closed): {exc}", file=sys.stderr)
            return 2

    # One cycle process at a time per shadow DB (two schedulers, a manual run, a wake-up burst).
    import fcntl

    lock_file = open(db.parent / "shadow_cycle.lock", "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({"skipped": "another shadow cycle process holds the lock"}))
        return 0
    os.environ["ODDS_MAX_AGE_MINUTES"] = str(args.max_age)
    sys.path.insert(0, str(PROJECT_ROOT))
    from src import config  # noqa: E402
    from src.db.database import get_db  # noqa: E402
    from src.shadow.cycle import run_cycle  # noqa: E402

    from dataclasses import replace  # noqa: E402

    from src.shadow.cycle import PROFILES  # noqa: E402

    cfg = PROFILES[args.profile]
    if args.max_requests_per_cycle is not None:
        cfg = replace(cfg, max_requests_per_cycle=max(0, args.max_requests_per_cycle))
    report = run_cycle(get_db(), config.APIFOOTBALL_API_KEY, config.APIFOOTBALL_HOST, cfg,
                       force_discovery=args.force_discovery)
    report["profile"] = args.profile
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

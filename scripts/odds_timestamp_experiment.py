#!/usr/bin/env python3
"""
Minimal odds-timestamp experiment (M3.2) — see src/odds/timestamp_experiment.py.

    python scripts/odds_timestamp_experiment.py plan --db <isolated db with upcoming fixtures>   # offline
    python scripts/odds_timestamp_experiment.py fetch --db <isolated db> --fixture <id> \
        --approved-requests <N> --out-dir <dir>          # ONE request per call; needs owner approval
    python scripts/odds_timestamp_experiment.py analyse --out-dir <dir>                         # offline

`fetch` refuses without --approved-requests, refuses once the out-dir already
holds that many fetches, refuses the live DB, and stores the raw response.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = (PROJECT_ROOT / "data" / "engine.db").resolve()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan")
    pl.add_argument("--db", required=True, type=Path)
    pl.add_argument("--fixtures", type=int, default=4)
    fe = sub.add_parser("fetch")
    fe.add_argument("--db", required=True, type=Path)
    fe.add_argument("--fixture", required=True)
    fe.add_argument("--approved-requests", type=int, required=True)
    fe.add_argument("--out-dir", required=True, type=Path)
    an = sub.add_parser("analyse")
    an.add_argument("--out-dir", required=True, type=Path)
    a = p.parse_args()
    sys.path.insert(0, str(PROJECT_ROOT))

    if a.cmd == "analyse":
        from src.odds.timestamp_experiment import analyse

        fetches = [json.loads(f.read_text()) for f in sorted(a.out_dir.glob("fetch_*.json"))]
        print(json.dumps(analyse(fetches), indent=1, default=str))
        return 0

    db = a.db.resolve()
    if db == LIVE_DB or not db.exists():
        print("refusing: use an existing isolated DB", file=sys.stderr)
        return 2
    import sqlite3

    conn = sqlite3.connect(f"file:{db}?immutable=1", uri=True) if a.cmd == "plan" else sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    if a.cmd == "plan":
        from src.odds.timestamp_experiment import plan

        now = datetime.now(timezone.utc).isoformat()
        rows = [dict(r) for r in conn.execute(
            "SELECT s.fixture_id AS id, s.league_id, s.kickoff_utc AS kickoff FROM shadow_fixtures s "
            "WHERE s.kickoff_utc > ? ORDER BY s.kickoff_utc", (now,))]
        print(json.dumps(plan(rows, a.fixtures, needs_discovery=not rows), indent=1))
        return 0

    a.out_dir.mkdir(parents=True, exist_ok=True)
    done = len(list(a.out_dir.glob("fetch_*.json")))
    if a.approved_requests <= 0 or done >= a.approved_requests:
        print(f"refusing: {done} of {a.approved_requests} approved requests already used", file=sys.stderr)
        return 2
    os.environ["FOOTBALL_PREDICTOR_DB_PATH"] = str(db)
    os.environ["ODDS_REQUEST_BUDGET_API_FOOTBALL"] = str(a.approved_requests)
    from src import config  # noqa: E402
    from src.odds.providers import api_football  # noqa: E402

    kickoff = conn.execute("SELECT kickoff_utc FROM shadow_fixtures WHERE fixture_id = ?", (a.fixture,)).fetchone()
    fetched_at = datetime.now(timezone.utc)
    payload = api_football.fetch_fixture(conn, a.fixture, config.APIFOOTBALL_API_KEY, config.APIFOOTBALL_HOST,
                                         now=fetched_at)
    (a.out_dir / f"fetch_{done + 1:03d}.json").write_text(json.dumps({
        "fixture_id": a.fixture, "fetched_at": fetched_at.isoformat(), "kickoff": kickoff[0] if kickoff else None,
        "payload": payload}, indent=1))
    print(f"fetch {done + 1}/{a.approved_requests} stored")
    return 0


if __name__ == "__main__":
    sys.exit(main())

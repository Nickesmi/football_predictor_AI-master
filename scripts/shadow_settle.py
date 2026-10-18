#!/usr/bin/env python3
"""
Settle shadow observations for one date with at most ONE API-Football request (M3.2).

    python scripts/shadow_settle.py --db <isolated shadow db> --date 2026-10-16 --out settle.json

GET /fixtures?date=<date> (1 request, budget guard + quota log), then the
offline settlement of src/shadow/cycle.py. Only fixtures with final status
FT/AET/PEN and a regulation-time score are recorded; postponed, abandoned,
cancelled or still-running fixtures stay unsettled. The live DB is refused.
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
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--date", required=True)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--ledger", type=Path, help="shared API-Football ledger (API_FOOTBALL_LEDGER)")
    a = p.parse_args()
    db = a.db.resolve()
    if db == LIVE_DB or not db.exists():
        print("refusing: --db must be an existing isolated shadow DB", file=sys.stderr)
        return 2
    datetime.strptime(a.date, "%Y-%m-%d")
    os.environ["FOOTBALL_PREDICTOR_DB_PATH"] = str(db)
    os.environ["ODDS_REQUEST_BUDGET_API_FOOTBALL"] = "1"
    os.environ["API_FOOTBALL_CONSUMER"] = "shadow_settle"
    if a.ledger:
        os.environ["API_FOOTBALL_LEDGER"] = str(a.ledger.resolve())
    from src.ops import api_guard  # noqa: E402

    if api_guard.required() or a.ledger:
        try:
            api_guard.install("shadow_settle")
        except api_guard.LedgerUnavailable as exc:
            print(f"refusing (fail closed): {exc}", file=sys.stderr)
            return 2
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.ops.workspace_guard import isolate  # noqa: E402

    isolate(db.parent / "workspace")   # own working directory and cache next to the isolated DB
    sys.path.insert(0, str(PROJECT_ROOT))
    import sqlite3

    from src import config  # noqa: E402
    from src.shadow import cycle  # noqa: E402

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cycle._ensure(conn)
    now = datetime.now(timezone.utc)
    report = {"date": a.date, "started_at": now.isoformat(), "discovery_requests": 0, "discovery_skipped": 0,
              "fixtures_inserted": 0, "outcomes_recorded": 0, "selections_settled": 0, "errors": [],
              "requests_sent": 0, "request_cap": 1}
    statuses = {}
    original = cycle._ingest_fixture_list

    def capture(conn_, payload, now_, report_):
        for item in (payload or {}).get("response") or []:
            fx = item.get("fixture") or {}
            statuses[str(fx.get("id"))] = {"status": (fx.get("status") or {}).get("short"),
                                           "fulltime": (item.get("score") or {}).get("fulltime")}
        original(conn_, payload, now_, report_)

    cycle._ingest_fixture_list = capture
    cycle.discover(conn, [a.date], config.APIFOOTBALL_API_KEY, config.APIFOOTBALL_HOST, None, now,
                   lambda s: None, report)
    cycle.settle(conn, now, report)
    observed = [r[0] for r in conn.execute("SELECT fixture_id FROM shadow_observations")]
    report["observed_fixtures"] = {fid: statuses.get(fid, {"status": "NOT_IN_RESPONSE"}) for fid in observed}
    report["settled_observations"] = [r[0] for r in conn.execute(
        "SELECT o.fixture_id FROM shadow_observations o JOIN shadow_outcomes r USING (fixture_id)")]
    report["unsettled_observations"] = sorted(set(observed) - set(report["settled_observations"]))
    report["quota_log"] = [dict(r) for r in conn.execute(
        "SELECT endpoint, cost, status, http_status, requests_remaining, at FROM provider_quota_log "
        "WHERE at >= ? ORDER BY id", (now.replace(microsecond=0).isoformat(),))]
    a.out.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: report[k] for k in ("requests_sent", "outcomes_recorded", "selections_settled",
                                             "settled_observations", "unsettled_observations", "errors",
                                             "quota_log")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

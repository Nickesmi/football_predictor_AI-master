#!/usr/bin/env python3
"""
API-Football odds validation probe (M3 Phase C) — isolated database only.

    python scripts/validate_odds_provider.py --db <isolated copy> --date 2026-10-17 \
        --max-fixtures 12 --budget 13 --out report.json

Sends at most --budget requests (1 for /fixtures?date=, then 1 per fixture
for /odds?fixture=), all through the budget guard and provider_quota_log.
Not-started fixtures of the tracked competitions are stored in the isolated
DB as provider 'api_football' rows (absolute UTC kickoff), their odds are
collected with the production collector, and the report gives coverage,
rejections and quote ages per bucket (0-60, 61-120, 121-240, >240 minutes).

The live database is refused. Nothing here sets a production budget.
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
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--date", required=True, help="fixture date (UTC) to probe, YYYY-MM-DD")
    parser.add_argument("--max-fixtures", type=int, default=10)
    parser.add_argument("--budget", type=int, required=True, help="hard cap on requests for this run")
    parser.add_argument("--max-age", type=int, default=240, help="provisional window for the report (minutes)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    db = args.db.resolve()
    if db == LIVE_DB or not db.exists():
        print("refusing: --db must be an existing isolated copy, never data/engine.db", file=sys.stderr)
        return 2
    if args.budget > 30:
        print("refusing: keep the probe within 30 requests of the 100/day free allowance", file=sys.stderr)
        return 2

    os.environ["FOOTBALL_PREDICTOR_DB_PATH"] = str(db)
    os.environ["ODDS_REQUEST_BUDGET_API_FOOTBALL"] = str(args.budget)
    sys.path.insert(0, str(PROJECT_ROOT))
    from src import config  # noqa: E402
    from src.db.database import get_db  # noqa: E402
    from src.odds import collector, freshness, store  # noqa: E402
    from src.odds.model import iso_utc  # noqa: E402
    from src.odds.providers import api_football  # noqa: E402
    from src.odds.providers.the_odds_api import API_FOOTBALL_SPORT_KEYS  # noqa: E402

    conn = get_db()
    now = datetime.now(timezone.utc)
    used_before = store.requests_used_today(conn, api_football.PROVIDER, now)
    payload = api_football.fetch_fixtures_by_date(conn, args.date, config.APIFOOTBALL_API_KEY,
                                                  config.APIFOOTBALL_HOST, now=now)
    response = payload.get("response") or []
    tracked = [r for r in response
               if (r.get("league") or {}).get("id") in API_FOOTBALL_SPORT_KEYS
               and ((r.get("fixture") or {}).get("status") or {}).get("short") in ("NS", "TBD")]
    tracked.sort(key=lambda r: r["fixture"]["date"])
    chosen = tracked[: args.max_fixtures]
    for r in chosen:
        f, t, lg = r["fixture"], r["teams"], r["league"]
        conn.execute(
            """INSERT OR IGNORE INTO matches (id, date, kickoff, home_team, away_team, league_name, league_id,
                                              status, provider)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'NS', 'api_football')""",
            (str(f["id"]), iso_utc(f["date"])[:10], iso_utc(f["date"]), t["home"]["name"], t["away"]["name"],
             lg["name"], lg["id"]),
        )
    conn.commit()
    fixtures = [dict(conn.execute("SELECT * FROM matches WHERE id = ?", (str(r["fixture"]["id"]),)).fetchone())
                for r in chosen]
    result = collector.collect_api_football(conn, fixtures, config.APIFOOTBALL_API_KEY,
                                            config.APIFOOTBALL_HOST, now=now)
    decided = datetime.now(timezone.utc)
    coverage = freshness.coverage_report(conn, [f["id"] for f in fixtures], decided, args.max_age)
    per_market = [dict(r) for r in conn.execute(
        f"""SELECT market, line, COUNT(*) AS quotes, COUNT(DISTINCT fixture_id) AS fixtures,
                   COUNT(DISTINCT bookmaker) AS bookmakers
            FROM odds_quotes WHERE fixture_id IN ({','.join('?' * len(fixtures))})
            GROUP BY market, line ORDER BY fixtures DESC, quotes DESC LIMIT 25""",
        [f["id"] for f in fixtures]).fetchall()] if fixtures else []
    quota_rows = [dict(r) for r in conn.execute(
        "SELECT endpoint, cost, status, http_status, requests_remaining, at FROM provider_quota_log "
        "WHERE provider = ? AND at >= ? ORDER BY id", (api_football.PROVIDER, iso_utc(now))).fetchall()]
    report = {
        "probe_started_at": iso_utc(now),
        "decision_time": iso_utc(decided),
        "date": args.date,
        "fixtures_returned": len(response),
        "tracked_not_started": len(tracked),
        "fixtures_probed": [{"id": f["id"], "league": f["league_name"], "home": f["home_team"],
                             "away": f["away_team"], "kickoff": f["kickoff"]} for f in fixtures],
        "collection": result,
        "coverage": coverage,
        "model_markets": [m for m in per_market if (m["market"], m["line"]) in
                          (("1X2", ""), ("TOTALS", "2.5"), ("BTTS", ""))],
        "all_markets": per_market,
        "requests_used_before": used_before,
        "requests_this_run": sum(r["cost"] for r in quota_rows if r["status"] != "refused"),
        "quota_log": quota_rows,
    }
    args.out.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: report[k] for k in ("fixtures_returned", "tracked_not_started", "requests_this_run")}
                     | {"coverage_pct": coverage["coverage_pct"], "by_bucket": coverage["by_bucket"],
                        "by_status": coverage["by_status"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

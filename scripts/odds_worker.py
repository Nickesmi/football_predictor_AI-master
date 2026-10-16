#!/usr/bin/env python3
"""
Odds Worker — collect real bookmaker odds for upcoming tracked fixtures (M2.5).

    python scripts/odds_worker.py --dry-run          # plan and cost only, sends nothing
    python scripts/odds_worker.py                    # collect within the approved budget
    python scripts/odds_worker.py --provider api_football
    python scripts/odds_worker.py --provider api_football --map-fixtures   # + 1 request per date first

Nothing is sent unless the operator has approved a daily request budget
(ODDS_REQUEST_BUDGET or ODDS_REQUEST_BUDGET_<PROVIDER>; default 0). The Odds
API costs (markets x regions) per competition request; API-Football costs one
request per fixture and shares its daily limit with the fixture feeds.
--map-fixtures first maps API-Football ids onto stored (e.g. SofaScore)
fixtures from GET /fixtures?date= (team names + kickoff), one request per date.
Every request, refusal and failure is logged in provider_quota_log. Events
that cannot be mapped to exactly one stored fixture are reported, not guessed.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402
from src.db.database import get_db  # noqa: E402
from src.odds import collector, quota, store  # noqa: E402
from src.odds.providers import api_football, the_odds_api  # noqa: E402


def upcoming_fixtures(conn, now, days):
    dates = [(now + timedelta(days=d)).strftime("%Y-%m-%d") for d in range(-1, days + 1)]
    rows = conn.execute(
        f"SELECT * FROM matches WHERE status IN ('NS', 'TBD') AND date IN ({','.join('?' * len(dates))})",
        dates,
    ).fetchall()
    return [dict(r) for r in rows]


def plan(conn, provider, fixtures, now):
    if provider == the_odds_api.PROVIDER:
        sports = sorted({s for s in map(the_odds_api.sport_for, fixtures) if s})
        requests, cost = len(sports), len(sports) * the_odds_api.request_cost()
        detail = {"competitions": sports, "markets": the_odds_api.requested_markets(),
                  "regions": the_odds_api.requested_regions()}
    else:
        targets = collector.api_football_targets(conn, fixtures)
        requests, cost = len(targets), len(targets) * api_football.REQUEST_COST
        mapped = {fid for t in targets.values() for fid in t["fixture_ids"]}
        detail = {"api_football_events": len(targets), "fixtures_with_identity": len(mapped),
                  "fixtures_without_identity": len(fixtures) - len(mapped)}
    return {
        "provider": provider, "upcoming_fixtures": len(fixtures), "requests": requests,
        "estimated_cost": cost, "approved_daily_budget": quota.daily_budget(provider),
        "used_today": store.requests_used_today(conn, provider, now), **detail,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=[the_odds_api.PROVIDER, api_football.PROVIDER],
                        default=the_odds_api.PROVIDER)
    parser.add_argument("--days", type=int, default=1, help="collect for fixtures up to this many days ahead")
    parser.add_argument("--dry-run", action="store_true", help="report the plan and cost; send nothing")
    parser.add_argument("--map-fixtures", action="store_true",
                        help="api_football: map provider ids onto stored fixtures first (1 request per date)")
    args = parser.parse_args()

    conn = get_db()
    now = datetime.now(timezone.utc)
    fixtures = upcoming_fixtures(conn, now, args.days)
    report = {}
    if args.map_fixtures and args.provider == api_football.PROVIDER:
        dates = sorted({f["date"] for f in fixtures})
        report["mapping_plan"] = {"dates": dates, "estimated_cost": len(dates) * api_football.REQUEST_COST}
        if not args.dry_run:
            report["mapping"] = collector.map_api_football_fixtures(
                conn, dates, config.APIFOOTBALL_API_KEY, config.APIFOOTBALL_HOST, now=now)
    report["plan"] = plan(conn, args.provider, fixtures, now)
    if not args.dry_run:
        if args.provider == the_odds_api.PROVIDER:
            report["result"] = collector.collect_the_odds_api(conn, fixtures, os.getenv("ODDS_API_KEY", ""), now=now)
        else:
            report["result"] = collector.collect_api_football(
                conn, fixtures, config.APIFOOTBALL_API_KEY, config.APIFOOTBALL_HOST, now=now)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Offline end-to-end dry run of a shadow profile (M3.3) — no network, isolated temp DB.

    python scripts/shadow_dry_run.py --profile medium --fixtures 53 --budget 70 --out dryrun.json

Simulates one busy match day: a fake API-Football serves `--fixtures` fixtures
spread over the 14 tracked competitions with clustered kickoffs, odds for each,
final results after kickoff, and an HTTP 500 on every `--fail-every`-th odds
request. Cycles run every 30 minutes for 30 hours; the host "sleeps" between
--sleep-from and --sleep-to (no cycles). Reports requests by type, refusals,
observations, deferrals, missed slots and the largest cycle.
"""

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEAGUES = [39, 40, 140, 135, 78, 61, 88, 94, 144, 203, 179, 2, 3, 848]
KICKOFF_SLOTS = ["11:30", "14:00", "14:00", "14:00", "16:30", "16:30", "19:00", "19:00", "19:45"]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", default="medium", choices=("low", "medium", "full"))
    p.add_argument("--fixtures", type=int, default=53)
    p.add_argument("--budget", type=int, default=70)
    p.add_argument("--fail-every", type=int, default=15)
    p.add_argument("--sleep-from", default="15:00")
    p.add_argument("--sleep-to", default="17:00")
    p.add_argument("--out", type=Path)
    a = p.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="shadow-dry-run-"))
    os.environ["FOOTBALL_PREDICTOR_DB_PATH"] = str(tmp / "dry.db")
    os.environ["HOME"] = str(tmp)
    os.environ["ODDS_REQUEST_BUDGET_API_FOOTBALL"] = str(a.budget)
    os.environ["ODDS_MAX_AGE_MINUTES"] = "240"
    for k in ("API_FOOTBALL_KEY", "APIFOOTBALL_API_KEY", "RAPIDAPI_KEY"):
        os.environ[k] = ""
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.db.database import get_db
    from src.odds.model import iso_utc
    from src.shadow import cycle as cycle_mod
    from src.shadow.report import build_report

    conn = get_db()
    day = datetime(2030, 1, 5, tzinfo=timezone.utc)   # a Saturday, far from any real data
    fixtures = []
    for i in range(a.fixtures):
        hh, mm = KICKOFF_SLOTS[i % len(KICKOFF_SLOTS)].split(":")
        ko = day.replace(hour=int(hh), minute=int(mm))
        lid = LEAGUES[i % len(LEAGUES)]
        fixtures.append({"id": 900000 + i, "league": lid, "kickoff": ko, "home": f"Home {i}", "away": f"Away {i}"})
        for side in ("home", "away"):   # one finished prior fixture each, so identities resolve with history
            conn.execute("INSERT INTO matches (id, date, kickoff, home_team, away_team, league_name, league_id, status, "
                         "provider, home_goals, away_goals) VALUES (?, '2029-12-20', '15:00', ?, 'Prior', 'L', ?, "
                         "'FT', 'api_football', 1, 1)", (f"prior-{i}-{side}", f"{side.title()} {i}", lid))
    conn.commit()

    calls = Counter()
    state = {"odds_requests": 0, "now": day}

    def transport(url, headers, timeout):
        q = parse_qs(urlparse(url).query)
        path = urlparse(url).path
        if path == "/fixtures":
            calls["fixtures"] += 1
            date = q["date"][0]
            items = []
            for f in fixtures:
                if iso_utc(f["kickoff"])[:10] != date:
                    continue
                done = state["now"] >= f["kickoff"] + timedelta(hours=2)
                items.append({"fixture": {"id": f["id"], "date": iso_utc(f["kickoff"]),
                                          "status": {"short": "FT" if done else "NS"}},
                              "league": {"id": f["league"], "name": "L", "season": 2029},
                              "teams": {"home": {"name": f["home"], "id": f["id"] * 10},
                                        "away": {"name": f["away"], "id": f["id"] * 10 + 1}},
                              "score": {"fulltime": {"home": 1 if done else None, "away": 0 if done else None}}})
            body = {"errors": [], "response": items}
        else:
            calls["odds"] += 1
            state["odds_requests"] += 1
            if a.fail_every and state["odds_requests"] % a.fail_every == 0:
                calls["odds_failed"] += 1
                return 500, {}, b"error"
            fid = int(q["fixture"][0])
            f = next(x for x in fixtures if x["id"] == fid)
            upd = state["now"] - timedelta(minutes=150)
            body = {"errors": [], "response": [{"fixture": {"id": fid, "date": iso_utc(f["kickoff"])},
                                                "update": iso_utc(upd), "bookmakers": [{"name": f"b{k}", "bets": [
                {"name": "Match Winner", "values": [{"value": "Home", "odd": "2.10"}, {"value": "Draw", "odd": "3.40"},
                                                    {"value": "Away", "odd": "3.60"}]},
                {"name": "Goals Over/Under", "values": [{"value": "Over 2.5", "odd": "1.95"},
                                                        {"value": "Under 2.5", "odd": "1.90"}]},
                {"name": "Both Teams Score", "values": [{"value": "Yes", "odd": "1.80"}, {"value": "No", "odd": "2.00"}]}]}
                for k in range(4)]}]}
        return 200, {"x-ratelimit-requests-remaining": "90"}, json.dumps(body).encode()

    cycle_mod._bilqe = lambda match: {"skipped": "dry run"}
    cfg = cycle_mod.PROFILES[a.profile]
    sleep_from = day.replace(hour=int(a.sleep_from[:2]), minute=int(a.sleep_from[3:]))
    sleep_to = day.replace(hour=int(a.sleep_to[:2]), minute=int(a.sleep_to[3:]))
    t = day.replace(hour=6)
    reports = []
    while t <= day + timedelta(hours=30):
        if not (sleep_from <= t < sleep_to):
            state["now"] = t
            reports.append(cycle_mod.run_cycle(conn, "key", "host", cfg, now=t, transport=transport,
                                               sleep=lambda s: None))
        t += timedelta(minutes=30)
    rep = build_report(conn)
    per_cycle = [r.get("requests_sent", 0) for r in reports]
    summary = {
        "profile": a.profile, "fixtures_offered": a.fixtures, "daily_budget": a.budget,
        "cycles_run": len(reports), "requests_by_type": dict(calls),
        "requests_total": calls["fixtures"] + calls["odds"],
        "max_requests_in_one_cycle": max(per_cycle), "per_cycle_cap": cfg.max_requests_per_cycle,
        "refused_by_budget": conn.execute("SELECT COUNT(*) FROM provider_quota_log WHERE status = 'refused'"
                                          ).fetchone()[0],
        "observations": rep["observations"], "observation_status": rep["odds_coverage"]["by_status"],
        "deferred_total": sum(r.get("observations_deferred", 0) for r in reports),
        "retry_limit_hits": sum(len(r.get("retry_limit_reached", [])) for r in reports),
        "missed_slots_recorded": sum(r.get("missed_slots", 0) or 0 for r in reports),
        "fixtures_settled": rep["settlement"]["observed_and_settled"],
        "requests_per_utc_day": {r[0]: {"sent": r[1], "refused": r[2]} for r in conn.execute(
            "SELECT substr(at, 1, 10), SUM(status != 'refused'), SUM(status = 'refused') FROM provider_quota_log "
            "GROUP BY 1 ORDER BY 1")},
        "not_observed": [str(f["id"]) + " " + iso_utc(f["kickoff"])[11:16] for f in fixtures
                         if conn.execute("SELECT 1 FROM shadow_observations WHERE fixture_id = ?",
                                         (str(f["id"]),)).fetchone() is None],
        "integrity": rep["integrity"], "picks": conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0],
    }
    print(json.dumps(summary, indent=1))
    if a.out:
        a.out.write_text(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

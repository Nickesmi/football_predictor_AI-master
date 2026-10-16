#!/usr/bin/env python3
"""
Team identity coverage and before/after report (M3.1, K43) — read-only on a copy.

    python scripts/team_identity_report.py --db <isolated copy> --out report.json \
        [--fixtures-db <db with upcoming fixtures>] [--since 2026-07-01]

1. Coverage: every distinct team that appears in stored fixtures of the tracked
   competitions since --since is resolved with src/identity/teams.py; counts per
   status and competition, plus the full list of non-resolved teams.
2. Legacy path: for the same teams, which source team_stats_db.get_team_stats
   would have used (team_state under the profile key, hard-coded table exact,
   SUBSTRING match, hash-generated statistics).
3. Before/after on fixtures (NS fixtures of tracked competitions in
   --fixtures-db): legacy inputs vs identity-checked inputs, data quality and
   1X2 / O/U 2.5 / BTTS probabilities. The probability mathematics is the same.
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = (PROJECT_ROOT / "data" / "engine.db").resolve()


def legacy_source(conn, team_stats_db, name, venue, league_key):
    nl = name.lower().strip()
    row = conn.execute("SELECT matches_played FROM team_state WHERE team_name_lower = ? AND league = ? "
                       "AND venue = 'overall'", (nl, league_key)).fetchone()
    if row and row[0] >= 1:
        return "team_state_under_profile_key"
    for scope, db in (("league_table", None), ("all_leagues_table", None)):
        if scope == "league_table":
            if league_key not in team_stats_db.LEAGUE_STATS:
                continue
            home, away = team_stats_db.LEAGUE_STATS[league_key]
            db = home if venue == "home" else away
        else:
            db = team_stats_db.ALL_HOME if venue == "home" else team_stats_db.ALL_AWAY
        if nl in db or team_stats_db._TEAM_NAME_ALIASES.get(nl) in db:
            return f"{scope}_exact"
        if team_stats_db._lookup_in_db(db, nl) is not None:
            key = next(k for k in sorted(db, key=len, reverse=True) if k in nl or nl in k)
            return f"{scope}_SUBSTRING:{key}"
    return "HASH_SYNTHETIC"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--fixtures-db", type=Path, action="append", default=[])
    p.add_argument("--since", default="2026-07-01")
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args()
    if a.db.resolve() == LIVE_DB or any(f.resolve() == LIVE_DB for f in a.fixtures_db):
        print("refusing: use isolated copies, never data/engine.db", file=sys.stderr)
        return 2
    os.environ["FOOTBALL_PREDICTOR_DB_PATH"] = str(a.db.resolve())
    sys.path.insert(0, str(PROJECT_ROOT))
    import sqlite3

    from src.engine.probability_engine import (LEAGUE_KEY_MAP, estimate_probabilities_from_inputs,
                                               resolve_probability_inputs)
    from src.identity.competitions import COMPETITIONS, competition_for_fixture
    from src.identity.inputs import UnresolvedTeamIdentity, resolve_identity, resolve_probability_inputs_v2
    from src.identity.teams import TEAM_IDENTITY_VERSION, resolve_team
    from src.ml import team_stats_db

    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row

    # 1-2. Coverage over teams seen in tracked-competition fixtures
    coverage = defaultdict(Counter)
    legacy = defaultdict(Counter)
    unresolved = []
    seen = set()
    rows = conn.execute("SELECT provider, league_id, league_name, home_team, away_team FROM matches "
                        "WHERE date >= ?", (a.since,)).fetchall()
    for r in rows:
        comp = competition_for_fixture(r["provider"], r["league_id"])
        if comp is None:
            continue
        for venue, name in (("home", r["home_team"]), ("away", r["away_team"])):
            if (comp.key, name) in seen:
                continue
            seen.add((comp.key, name))
            res = resolve_team(conn, comp, name, r["provider"])
            coverage[comp.key][res.status] += 1
            legacy[comp.key][legacy_source(conn, team_stats_db, name, venue,
                                           LEAGUE_KEY_MAP.get(r["league_name"], "default")).split(":")[0]] += 1
            if not res.resolved:
                unresolved.append({"competition": comp.key, "name": name, "status": res.status,
                                   "reason": res.reason, "evidence": res.evidence})
    totals = Counter()
    for c in coverage.values():
        totals.update(c)
    legacy_totals = Counter()
    for c in legacy.values():
        legacy_totals.update(c)

    # 3. Before/after on upcoming fixtures
    fixtures = []
    for fdb in a.fixtures_db:
        fc = sqlite3.connect(fdb)
        fc.row_factory = sqlite3.Row
        for m in fc.execute("SELECT * FROM matches WHERE status IN ('NS', 'TBD') AND provider = 'api_football'"):
            m = dict(m)
            if competition_for_fixture(m["provider"], m["league_id"]) and m["id"] not in {f["id"] for f in fixtures}:
                fixtures.append(m)
    comparisons = []
    for m in fixtures:
        old = estimate_probabilities_from_inputs(resolve_probability_inputs(m))
        entry = {"fixture_id": m["id"], "league": m["league_name"], "home": m["home_team"], "away": m["away_team"],
                 "legacy_sources": {v: legacy_source(conn, team_stats_db, m[f"{v}_team"], v,
                                                     LEAGUE_KEY_MAP.get(m["league_name"], "default"))
                                    for v in ("home", "away")},
                 "dq_before": old.get("data_quality"),
                 "before": {k: old.get(k) for k in ("1X2", "O/U 2.5", "BTTS")}}
        try:
            new = estimate_probabilities_from_inputs(resolve_probability_inputs_v2(conn, m))
            entry.update({"identity": "RESOLVED", "dq_after": new.get("data_quality"),
                          "after": {k: new.get(k) for k in ("1X2", "O/U 2.5", "BTTS")},
                          "max_abs_change_pp": round(100 * max(
                              abs(new[k][s] - old[k][s]) for k in ("1X2", "O/U 2.5", "BTTS") for s in new[k]), 1)})
        except UnresolvedTeamIdentity as exc:
            entry.update({"identity": "UNRESOLVED", "dq_after": None, "after": None,
                          "unresolved": {s: {"name": r.provider_name, "status": r.status, "reason": r.reason}
                                         for s, r in exc.resolutions.items() if not r.resolved}})
        comparisons.append(entry)

    resolved_cmp = [c for c in comparisons if c["identity"] == "RESOLVED"]
    report = {
        "identity_version": TEAM_IDENTITY_VERSION,
        "since": a.since,
        "competitions": [c.key for c in COMPETITIONS],
        "coverage_totals": dict(totals),
        "coverage_by_competition": {k: dict(v) for k, v in sorted(coverage.items())},
        "legacy_source_totals": dict(legacy_totals),
        "legacy_source_by_competition": {k: dict(v) for k, v in sorted(legacy.items())},
        "unresolved_teams": unresolved,
        "fixtures_compared": len(comparisons),
        "fixtures_resolved": len(resolved_cmp),
        "dq_before_mean": round(sum(c["dq_before"] for c in comparisons) / len(comparisons), 1) if comparisons else None,
        "dq_after_mean_resolved": (round(sum(c["dq_after"] for c in resolved_cmp) / len(resolved_cmp), 1)
                                   if resolved_cmp else None),
        "dq_before_mean_resolved": (round(sum(c["dq_before"] for c in resolved_cmp) / len(resolved_cmp), 1)
                                    if resolved_cmp else None),
        "fixtures_unchanged": sum(1 for c in resolved_cmp if c["max_abs_change_pp"] == 0.0),
        "comparisons": comparisons,
    }
    a.out.write_text(json.dumps(report, indent=1, default=str, ensure_ascii=False))
    print(json.dumps({k: report[k] for k in ("coverage_totals", "legacy_source_totals", "fixtures_compared",
                                             "fixtures_resolved", "fixtures_unchanged", "dq_before_mean",
                                             "dq_before_mean_resolved", "dq_after_mean_resolved")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

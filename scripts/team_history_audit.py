#!/usr/bin/env python3
"""
Team-history reconstruction audit (M3.3 Package A) — isolated copies only.

    python scripts/team_history_audit.py --db <isolated db with team_history_rh1> --out audit.json

For every team key in team_history_rh1_keys: the legacy statistics the
production lookup returns today (team_stats_db.get_team_stats under the
profile key: source, matches, scored/conceded) next to the reconstructed
point-in-time state (fixtures only from the competition's league ids). Named
cases (Arsenal, Inter, Chelsea U21, Rangers, Gent) are listed separately, and
the 31 registry entries are audited against the reconstruction.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = (PROJECT_ROOT / "data" / "engine.db").resolve()
NAMED = [("england-premier-league", "Arsenal"), ("italy-serie-a", "Inter"),
         ("england-premier-league", "Chelsea U21"), ("scotland-premiership", "Rangers"),
         ("belgium-pro-league", "Gent")]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args()
    if a.db.resolve() == LIVE_DB:
        print("refusing: isolated copies only", file=sys.stderr)
        return 2
    os.environ["FOOTBALL_PREDICTOR_DB_PATH"] = str(a.db.resolve())
    sys.path.insert(0, str(PROJECT_ROOT))
    import importlib.util
    import sqlite3

    from src.db.database import get_db
    from src.engine.probability_engine import LEAGUE_KEY_MAP
    from src.history.reconstruct import state_at
    from src.identity import registry as registry_mod
    from src.identity.competitions import COMPETITIONS
    from src.identity.teams import normalise_team, resolve_team
    from src.ml import team_stats_db

    spec = importlib.util.spec_from_file_location("tir", PROJECT_ROOT / "scripts" / "team_identity_report.py")
    tir = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tir)

    conn = get_db()
    comps = {c.key: c for c in COMPETITIONS}
    now = datetime.now(timezone.utc)
    league_name = {}
    for c in COMPETITIONS:
        row = conn.execute("SELECT league_name FROM matches WHERE league_id IN (?, ?) GROUP BY 1 ORDER BY COUNT(*) DESC "
                           "LIMIT 1", (c.api_football_id, c.sofascore_id)).fetchone()
        league_name[c.key] = row[0] if row else c.history_labels[0]

    def legacy(comp_key, name):
        ln = league_name[comp_key]
        key = LEAGUE_KEY_MAP.get(ln, "default")
        st = team_stats_db.get_team_stats(name, "home", key)
        return {"source": tir.legacy_source(conn, team_stats_db, name, "home", key), "profile_key": key,
                "matches_played": st.matches_played, "home_scored": st.scored, "home_conceded": st.conceded}

    def rebuilt(comp_key, name):
        s = state_at(conn, comps[comp_key], name, now)
        return {"status": s.status, "canonical_team_id": s.canonical_team_id, "matches_played": s.matches_played,
                "rolling_scored": s.rolling_scored, "rolling_conceded": s.rolling_conceded,
                "as_of_fixture_id": s.as_of_fixture_id, "available_from": s.available_from,
                "raw_names": list(s.raw_names)}

    teams = []
    for comp_key, team_key, names in conn.execute(
            "SELECT competition_key, team_key, raw_names FROM team_history_rh1_keys ORDER BY 1, 2"):
        name = json.loads(names)[0]
        teams.append({"competition": comp_key, "team": name, "legacy": legacy(comp_key, name),
                      "reconstructed": rebuilt(comp_key, name)})

    named = []
    for comp_key, name in NAMED:
        entry = {"competition": comp_key, "team": name, "legacy": legacy(comp_key, name),
                 "reconstructed": rebuilt(comp_key, name),
                 "team_state_identity_v1": resolve_team(conn, comps[comp_key], name).status}
        src_fx = conn.execute(
            f"SELECT league_id, COUNT(*) FROM matches WHERE (home_team = ? OR away_team = ?) GROUP BY 1",
            (name, name)).fetchall()
        entry["stored_fixtures_by_league_id"] = {str(r[0]): r[1] for r in src_fx}
        named.append(entry)

    reg = registry_mod.load(allow_proposed=True)
    audit = []
    for (comp_key, norm), e in sorted(reg.entries.items(), key=lambda kv: kv[1]["id"]):
        rb = rebuilt(comp_key, e["provider_team_name"])
        tsv = resolve_team(conn, comps[comp_key], e["provider_team_name"], registry=reg)
        legacy_records = [r[0] for r in conn.execute(
            f"SELECT team_name || ' @ ' || league FROM team_state WHERE venue = 'overall' AND league IN "
            f"({','.join('?' * len(comps[comp_key].history_labels))})", comps[comp_key].history_labels)
            if normalise_team(r[0].split(' @ ')[0]) == norm]
        audit.append({"id": e["id"], "decision": e["decision"], "provider_team_name": e["provider_team_name"],
                      "reconstructed_status": rb["status"], "reconstructed_matches": rb["matches_played"],
                      "reconstructed_raw_names": rb["raw_names"], "team_state_v1_with_entry": tsv.status,
                      "team_state_records": legacy_records})

    from collections import Counter
    summary = {
        "teams": len(teams),
        "legacy_sources": dict(Counter(t["legacy"]["source"].split(":")[0] for t in teams)),
        "reconstructed_status": dict(Counter(t["reconstructed"]["status"] for t in teams)),
        "matches_equal": sum(1 for t in teams if t["legacy"]["matches_played"] == t["reconstructed"]["matches_played"]),
        "legacy_claims_more_matches": sum(1 for t in teams
                                          if t["legacy"]["matches_played"] > t["reconstructed"]["matches_played"]),
        "legacy_claims_fewer_matches": sum(1 for t in teams
                                           if t["legacy"]["matches_played"] < t["reconstructed"]["matches_played"]),
    }
    out = {"generated_at": now.isoformat(), "summary": summary, "named_cases": named, "registry_audit": audit,
           "teams": teams}
    a.out.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    print(json.dumps({"summary": summary, "named": [{k: v for k, v in n.items()} for n in named]},
                     indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

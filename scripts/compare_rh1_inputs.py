#!/usr/bin/env python3
"""
Legacy vs identity-v1 (team_state) vs identity-v1-rh1 (rebuilt history), market by market (M3.3 Package B).

    python scripts/compare_rh1_inputs.py --db <isolated copy> --fixtures-db <db> [...] --out report.json

The history is rebuilt in --db (an isolated copy). Each fixture is evaluated at
decision time = kickoff - 120 min. Reports, separately for 1X2, O/U 2.5 and BTTS,
how many fixtures change and by how much; data quality; and forecast eligibility
(identity resolved, data quality >= 60).
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = (PROJECT_ROOT / "data" / "engine.db").resolve()
MARKETS = {"1X2": ("home", "draw", "away"), "O/U 2.5": ("over", "under"), "BTTS": ("yes", "no")}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--fixtures-db", type=Path, action="append", default=[])
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args()
    if LIVE_DB in [a.db.resolve()] + [f.resolve() for f in a.fixtures_db]:
        print("refusing: isolated copies only", file=sys.stderr)
        return 2
    os.environ["FOOTBALL_PREDICTOR_DB_PATH"] = str(a.db.resolve())
    sys.path.insert(0, str(PROJECT_ROOT))
    import sqlite3

    from src.db.database import get_db
    from src.engine.probability_engine import estimate_probabilities_from_inputs, resolve_probability_inputs
    from src.history.reconstruct import reconstruct
    from src.identity.competitions import competition_for_fixture
    from src.identity.inputs import (UnresolvedTeamIdentity, resolve_probability_inputs_rh1,
                                     resolve_probability_inputs_v2)
    from src.odds.model import iso_utc

    conn = get_db()
    history = reconstruct(conn)
    fixtures, seen = [], set()
    for fdb in a.fixtures_db:
        fc = sqlite3.connect(f"file:{fdb.resolve()}?immutable=1", uri=True)
        fc.row_factory = sqlite3.Row
        cols = {r[1] for r in fc.execute("PRAGMA table_info(shadow_fixtures)")}
        kick = {}
        if cols:
            kick = {r[0]: r[1] for r in fc.execute("SELECT fixture_id, kickoff_utc FROM shadow_fixtures")}
        for m in fc.execute("SELECT * FROM matches WHERE status IN ('NS', 'TBD') AND provider = 'api_football'"):
            m = dict(m)
            if m["id"] in seen or not competition_for_fixture(m["provider"], m["league_id"]):
                continue
            ko = kick.get(m["id"]) or m["kickoff"]
            try:
                m["_decided_at"] = datetime.fromisoformat(iso_utc(ko)) - timedelta(minutes=120)
            except Exception:
                continue
            seen.add(m["id"])
            fixtures.append(m)

    def probs(fn):
        try:
            out = estimate_probabilities_from_inputs(fn())
            return {k: out[k] for k in ("1X2", "O/U 2.5", "BTTS", "data_quality")}
        except UnresolvedTeamIdentity as exc:
            return {"NO_FORECAST": {s: r.status for s, r in exc.resolutions.items() if not r.resolved}}

    rows = []
    for m in fixtures:
        rows.append({"fixture_id": m["id"], "league": m["league_name"], "match": f"{m['home_team']} v {m['away_team']}",
                     "decided_at": iso_utc(m["_decided_at"]),
                     "legacy": probs(lambda: resolve_probability_inputs(m)),
                     "identity_v1": probs(lambda: resolve_probability_inputs_v2(conn, m)),
                     "identity_v1_rh1": probs(lambda: resolve_probability_inputs_rh1(conn, m, m["_decided_at"]))})

    def compare(variant):
        ok = [r for r in rows if "NO_FORECAST" not in r[variant]]
        out = {"forecasts": len(ok), "no_forecast": len(rows) - len(ok)}
        for market, sels in MARKETS.items():
            d = [abs(r[variant][market][s] - r["legacy"][market][s]) * 100 for r in ok for s in sels]
            changed = sum(1 for r in ok if any(abs(r[variant][market][s] - r["legacy"][market][s]) > 1e-9 for s in sels))
            out[market] = {"fixtures_changed": changed,
                           "mean_abs_change_pp": round(sum(d) / len(d), 2) if d else None,
                           "max_abs_change_pp": round(max(d), 1) if d else None}
        dq_b = [r["legacy"]["data_quality"] for r in ok]
        dq_a = [r[variant]["data_quality"] for r in ok]
        out["data_quality"] = {"mean_legacy": round(sum(dq_b) / len(dq_b), 1) if ok else None,
                               "mean_variant": round(sum(dq_a) / len(dq_a), 1) if ok else None}
        out["eligible_dq60"] = sum(1 for x in dq_a if x >= 60)
        out["legacy_dq60_all_fixtures"] = sum(1 for r in rows if r["legacy"]["data_quality"] >= 60)
        from collections import Counter
        out["no_forecast_reasons"] = dict(Counter(st for r in rows if "NO_FORECAST" in r[variant]
                                                  for st in r[variant]["NO_FORECAST"].values()))
        return out

    report = {"history": history, "fixtures": len(rows), "decision_time": "kickoff - 120 min",
              "identity_v1_vs_legacy": compare("identity_v1"), "identity_v1_rh1_vs_legacy": compare("identity_v1_rh1"),
              "rows": rows}
    a.out.write_text(json.dumps(report, indent=1, default=str, ensure_ascii=False))
    print(json.dumps({k: report[k] for k in ("fixtures", "identity_v1_vs_legacy", "identity_v1_rh1_vs_legacy")},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

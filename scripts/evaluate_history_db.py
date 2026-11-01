#!/usr/bin/env python3
"""
Evaluate one demo database copy on a fixed fixture set (history rebuild review, 2026-11-01). Sends nothing.

    FOOTBALL_PREDICTOR_DB_PATH=<copy> python scripts/evaluate_history_db.py --fixtures <dir> --out <json>

The database named by FOOTBALL_PREDICTOR_DB_PATH must be a throwaway copy: match analysis records its
predictions there. For every fixture in <dir>/fixtures-*.json (API-Football) and <dir>/fd-fixtures-*.json
(football-data.org): the identity-checked analysis status (available / thin / stale / unavailable), the
resolved records with their match counts and last match dates, a leakage check (a resolved record whose
history contains a match dated on or after the fixture's kick-off date), and the 1X2 probabilities and
expected goals of the match analysis (unchanged models, weights and calibration).
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def fixtures(d: Path) -> list[tuple[str, dict]]:
    out = []
    for p in sorted(glob.glob(str(d / "fixtures-*.json"))):
        out += [("api_football", f) for f in json.loads(Path(p).read_text()) if f.get("source") == "api_football"]
    for p in sorted(glob.glob(str(d / "fd-fixtures-*.json"))):
        out += [("football_data", f) for f in json.loads(Path(p).read_text())["fixtures"]]
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--fixtures", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--no-probabilities", action="store_true")
    a = p.parse_args()
    logging.disable(logging.INFO)
    db_path = os.environ["FOOTBALL_PREDICTOR_DB_PATH"]
    from api.services.match_analysis_service import LEAGUE_NAME_MAP, get_match_analysis
    from src.db.error_intelligence import prediction_from_analysis
    from src.identity.analysis_inputs import resolve_for_analysis

    ro = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = []
    for provider, f in fixtures(a.fixtures):
        lid = f.get("league_id_apifootball") or f["league"]["id"]
        kickoff = (f.get("kickoff_utc") or f["date"])[:10]
        q = dict(provider=provider, league_id=str(lid), league_name=f["league"]["name"],
                 country=f["league"].get("country", ""), league_code=f["league"].get("code"),
                 home=f["home_team"]["name"], away=f["away_team"]["name"], home_id=str(f["home_team"]["id"]),
                 away_id=str(f["away_team"]["id"]))
        res = resolve_for_analysis(ro, **q, profile_key=LEAGUE_NAME_MAP.get(q["league_name"], q["league_name"]),
                                   match_date=kickoff)
        row = {"id": str(f["id"]), "provider": provider, "kickoff": kickoff, "league": q["league_name"],
               "country": q["country"], "home": q["home"], "away": q["away"]}
        if res is None:
            row["status"] = "legacy"
        else:
            thin = any("has only" in w for w in res["warnings"])
            stale = any("history ends" in w for w in res["warnings"])
            row["status"] = "unavailable" if not res["available"] else (
                "thin+stale" if thin and stale else "thin" if thin else "stale" if stale else "sufficient")
            row["competition"] = res["identity"]["competition"]
            leak = []
            for side in ("home", "away"):
                r = res["identity"][side]
                row[f"{side}_record"] = r.get("team_name_lower")
                row[f"{side}_label"] = r.get("history_league")
                row[f"{side}_status"] = r.get("status")
                row[f"{side}_matches"] = r.get("matches_played")
                if r.get("team_name_lower"):
                    last = ro.execute("SELECT last_match_date FROM team_state WHERE team_name_lower=? AND league=? "
                                      "AND venue='overall'", (r["team_name_lower"], r["history_league"])).fetchone()
                    row[f"{side}_last"] = last[0] if last else None
                    n = ro.execute("SELECT COUNT(*) FROM match_history WHERE league = ? AND match_date >= ? AND "
                                   "(lower(home_team) = ? OR lower(away_team) = ?)",
                                   (r["history_league"], kickoff, r["team_name_lower"], r["team_name_lower"])).fetchone()[0]
                    if n:
                        leak.append(f"{side}:{n}")
            row["leakage"] = leak
        if not a.no_probabilities and row["status"] not in ("unavailable",):
            try:
                an = get_match_analysis(f"eval-{provider}-{f['id']}", home=q["home"], away=q["away"], league=q["league_name"],
                                        provider=provider if row["status"] != "legacy" else None, league_id=q["league_id"],
                                        league_code=q["league_code"], country=q["country"], home_id=q["home_id"],
                                        away_id=q["away_id"], kickoff_utc=f.get("kickoff_utc"))
                pr = prediction_from_analysis(an)
                row["prediction_available"] = an.get("prediction_available")
                if pr:
                    row.update(p_home=pr["home_win_pct"], p_draw=pr["draw_pct"], p_away=pr["away_win_pct"],
                               xg_home=pr["pred_home_goals"], xg_away=pr["pred_away_goals"])
                row["data_quality"] = an.get("data_quality_score")
            except Exception as e:  # recorded, not hidden
                row["analysis_error"] = str(e)[:200]
        rows.append(row)
    a.out.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    print(f"{len(rows)} fixtures -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

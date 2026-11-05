#!/usr/bin/env python3
"""
Prediction coverage of one database copy and one code version on cached fixture days (2026-11-03). Sends nothing.

    FOOTBALL_PREDICTOR_DB_PATH=<throwaway copy> python scripts/evaluate_prediction_coverage.py \
        --code-root <worktree> --fixtures <cache dir> --days 2026-11-03 [2026-11-02 ...] --out <json>

The database must be a throwaway copy (analysis code may write to it). --code-root selects the code version, so
the same script measures the code as deployed (before) and the candidate (after).

Per fixture: the analysis as the page serves it. The page-load precompute fills the analysis cache before any
analysis request, so the served result is the precompute result: in code without build_match_analysis that is
the name-only _compute_match_analysis; with it, build_match_analysis with the fixture's provider ids. A
prediction is available when the frontend would show it (frontend/src/demo/predictionInputs.js: no
"has NO real data" note and prediction_available is not false). Also the identity resolution of each side
(source, record, matches, last match date, reason) and the 1X2 probabilities. Models, weights and calibration
are those of the code root, unchanged.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--code-root", required=True, type=Path)
    p.add_argument("--fixtures", required=True, type=Path)
    p.add_argument("--days", nargs="+", required=True)
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args()
    sys.path.insert(0, str(a.code_root.resolve()))
    logging.disable(logging.WARNING)
    db_path = os.environ["FOOTBALL_PREDICTOR_DB_PATH"]
    from api.services import match_analysis_service as svc
    from src.db.error_intelligence import prediction_from_analysis
    from src.identity.analysis_inputs import resolve_for_analysis

    new_code = hasattr(svc, "build_match_analysis")
    if new_code:
        from api.services.fixtures_service import fixture_identity_params
    ro = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = []
    for day in a.days:
        for f in json.loads((a.fixtures / f"fixtures-{day}.json").read_text()):
            if f.get("source") != "api_football":
                continue
            lid = f.get("league_id_apifootball") or f["league"]["id"]
            kickoff = (f.get("kickoff_utc") or f["date"])[:10]
            row = {"day": day, "id": str(f["id"]), "league_id": int(lid), "league": f["league"]["name"],
                   "country": f["league"].get("country", ""), "home": f["home_team"]["name"],
                   "away": f["away_team"]["name"], "home_id": str(f["home_team"]["id"]),
                   "away_id": str(f["away_team"]["id"]), "status": f.get("status"), "kickoff": kickoff}
            res = resolve_for_analysis(ro, provider="api_football", league_id=str(lid), league_name=row["league"],
                                       country=row["country"], home=row["home"], away=row["away"],
                                       home_id=row["home_id"], away_id=row["away_id"],
                                       profile_key=svc.LEAGUE_NAME_MAP.get(row["league"], row["league"]),
                                       match_date=kickoff)
            if res is not None:
                row["identity_available"] = res["available"]
                row["blocking"], row["warnings"] = res["blocking"], res["warnings"]
                for side in ("home", "away"):
                    own = res["identity"][side]
                    hist = res["identity"].get(f"{side}_history") or {
                        "source": "competition", "domestic": None,
                        "record": own if own.get("status") == "RESOLVED" else None}
                    rec = hist.get("record") or {}
                    row[f"{side}_own_status"] = own.get("status")
                    row[f"{side}_own_matches"] = own.get("matches_played")
                    row[f"{side}_source"] = hist["source"] if rec else None
                    row[f"{side}_record"] = rec.get("history_league") and f"{rec['team_name_lower']} @ {rec['history_league']}"
                    row[f"{side}_matches"] = rec.get("matches_played")
                    if rec.get("team_name_lower"):
                        last = ro.execute("SELECT last_match_date FROM team_state WHERE team_name_lower=? AND league=? "
                                          "AND venue='overall'", (rec["team_name_lower"], rec["history_league"])).fetchone()
                        row[f"{side}_last"] = last[0] if last else None
            fid = f"cov-{day}-{f['id']}"
            try:
                if new_code:
                    an = svc.build_match_analysis(fid, **fixture_identity_params(f))
                else:
                    an = svc._compute_match_analysis(row["home"], row["away"], row["league"], shuffle_tiers=False)
                notes = an.get("missing_inputs") or []
                row["served_notes"] = notes
                row["served_available"] = (not any("has NO real data" in n for n in notes)
                                           and an.get("prediction_available") is not False)
                pr = prediction_from_analysis(an) if row["served_available"] else None
                if pr:
                    row.update(p_home=pr["home_win_pct"], p_draw=pr["draw_pct"], p_away=pr["away_win_pct"],
                               xg_home=pr["pred_home_goals"], xg_away=pr["pred_away_goals"])
                row["data_quality"] = an.get("data_quality_score")
            except Exception as e:  # recorded, never hidden
                row["served_available"] = False
                row["analysis_error"] = f"{type(e).__name__}: {str(e)[:200]}"
            rows.append(row)
    a.out.write_text(json.dumps(rows, indent=1, ensure_ascii=False, default=str))
    print(f"{len(rows)} fixtures -> {a.out} (code {'new' if new_code else 'deployed'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

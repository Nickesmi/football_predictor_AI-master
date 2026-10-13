"""BILQE rejection funnel on real matches, using an ISOLATED copy of the database.

Usage:
    python scripts/diagnose_bilqe.py --db COPY.db --out OUT.json

Re-runs match analysis and BILQE for every match recorded in qualification_log
under three odds scenarios:
  none        no odds (current reality: no real bookmaker odds are collected)
  zero_edge   EXPLICITLY SYNTHETIC prices at implied = model probability
  edge_6pp    EXPLICITLY SYNTHETIC prices at implied = model probability - 6 pp,
              quoted by two bookmakers including a sharp one
Synthetic prices exist only in memory to isolate the odds-dependent layers;
they are never stored. The database copy may be modified by analysis side
effects; never point --db at data/engine.db.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARGS = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ARGS.add_argument("--db", required=True)
ARGS.add_argument("--out", required=True)
ARGS = ARGS.parse_args()
if Path(ARGS.db).resolve() == (ROOT / "data" / "engine.db").resolve():
    sys.exit("Refusing to run on the production database.")
os.environ.update(HOME=tempfile.mkdtemp(), FOOTBALL_PREDICTOR_DB_PATH=str(Path(ARGS.db).resolve()),
                  APIFOOTBALL_API_KEY="", API_FOOTBALL_KEY="", RAPIDAPI_KEY="", ODDS_API_KEY="")
os.environ.pop("BASELINE_FREEZE", None)
sys.path.insert(0, str(ROOT))

from api.services.match_analysis_service import _compute_match_analysis  # noqa: E402
from src.db.database import get_db  # noqa: E402
from src.db.error_intelligence import get_league_adjustment  # noqa: E402
from src.engine.qualification_engine import qualify_picks  # noqa: E402

ORDER = ["data_quality", "volatility", "calibration", "model_consensus", "market_edge", "liquidity"]


def synthetic_odds(analysis, edge_pp):
    quotes = []
    for pick in analysis.get("top_picks", []):
        implied = max(1.0, pick["probability"] - edge_pp)
        price = round(100.0 / implied, 3)
        quotes += [{"market": pick["market"], "odds": price, "bookmaker": b} for b in ("pinnacle", "bet365")]
    return quotes


def run(conn, matches, scenario):
    funnel = Counter()
    first_reason, failed_any = Counter(), Counter()
    tiers, market_types = Counter(), Counter()
    for home, away, league in matches:
        analysis = _compute_match_analysis(home, away, league, shuffle_tiers=False, fixture_id="")
        odds = None if scenario == "none" else synthetic_odds(analysis, 0.0 if scenario == "zero_edge" else 6.0)
        result = qualify_picks(analysis, home, away, league, conn=conn, bookmaker_odds=odds)
        candidates = len(analysis.get("top_picks", []))
        funnel["candidates"] += candidates
        market_types.update(p.get("market_type", "?") for p in analysis.get("top_picks", []))
        gates = result.layer_gate_results
        if not gates.get("data_quality", {}).get("passed", False):
            first_reason["match-level: " + gates["data_quality"]["rejection"].split(" <")[0]] += candidates
            continue
        funnel["after_data_quality"] += candidates
        if not gates.get("volatility", {}).get("passed", True):
            first_reason["match-level: volatility"] += candidates
            continue
        funnel["after_volatility"] += candidates
        for pick in result.qualified_picks + result.rejected_picks:
            outcomes = pick.layer_outcomes
            alive = True
            for layer in ORDER[2:]:
                if alive and outcomes[layer]["passed"]:
                    funnel[f"after_{layer}"] += 1
                elif alive:
                    alive = False
            if alive:
                funnel["after_hard_gates"] += 1
            for layer, o in outcomes.items():
                if not o["passed"]:
                    failed_any[layer] += 1
            first_reason[pick.rejection_reason.split(":")[0].split(" <")[0].split(" >")[0] or "qualified"] += 1
            tiers[pick.tier] += 1
    return {"funnel": dict(funnel), "first_rejection": dict(first_reason.most_common()),
            "failed_any_layer": dict(failed_any), "tiers": dict(tiers),
            "candidate_market_types": dict(market_types.most_common())}


def main():
    conn = get_db()
    matches = [tuple(r) for r in conn.execute(
        "SELECT DISTINCT home_team, away_team, league_name FROM qualification_log ORDER BY 3, 1, 2"
    ).fetchall()]
    leagues = sorted({m[2] for m in matches})
    report = {
        "matches": len(matches),
        "leagues": {lg: {k: get_league_adjustment(conn, lg).get(k) for k in ("active", "reliability", "sample_count", "status")}
                    for lg in leagues},
        "scenarios": {s: run(conn, matches, s) for s in ("none", "zero_edge", "edge_6pp")},
    }
    Path(ARGS.out).write_text(json.dumps(report, indent=2))
    print(json.dumps({s: v["funnel"] | {"tiers": v["tiers"]} for s, v in report["scenarios"].items()}, indent=1))


if __name__ == "__main__":
    main()

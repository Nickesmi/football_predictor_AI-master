"""
Shadow mode (Phase 3 §12).

Runs a challenger model alongside the champion for a real match WITHOUT
letting it affect the production prediction returned to any caller. Both
predictions are logged; once the match's real result is known,
evaluate_shadow_predictions() scores both against it. This is the
evidence a future promotion decision (champion_challenger.py) should be
built on for any model that hasn't already gone through the full
walk-forward backtest — a walk-forward backtest is himself already a form
of "shadow test on history"; live shadow mode is the same discipline
applied prospectively to matches that haven't happened yet.

Shadow mode NEVER changes what the pipeline returns to a real caller —
predict_with_shadow() calls the real prediction_pipeline.predict() for the
champion untouched, and separately computes (but does not return as "the"
prediction) the challenger's number.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from src.prediction_service import live_models, model_agreement, snapshot_db
from src.prediction_service.feature_engine import generate_features, FeatureGenerationError
from src.prediction_service.prediction_pipeline import predict as production_predict, PredictionResult, PredictionRefused

_CHAMPION_TO_LIVE_KEY = {
    "frequency": "frequency", "home_baseline": "home_baseline", "elo": "elo",
    "poisson_real_data": "poisson_real_data", "dixon_coles": "dixon_coles",
    "xgboost_real_data_calibrated": "xgboost_real_data",
}


@dataclass
class ShadowComparison:
    shadow_id: str
    champion_model: str
    champion_probability: float
    challenger_model: str
    challenger_probability: float
    production_result: PredictionResult   # what the caller actually gets


def _deterministic_shadow_id(match_id: str, market: str, challenger: str, prediction_timestamp) -> str:
    key = f"shadow|{match_id}|{market}|{challenger}|{pd.Timestamp(prediction_timestamp).isoformat()}"
    return "shadow_" + hashlib.sha1(key.encode()).hexdigest()[:16]


def predict_with_shadow(
    home_team: str,
    away_team: str,
    league: str,
    market: str,
    prediction_timestamp,
    kickoff_timestamp,
    historical_matches: pd.DataFrame,
    challenger_model: str,
    match_id: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> ShadowComparison:
    """Runs the REAL production pipeline for the champion (this is what's
    returned/used), plus computes the challenger's number purely for
    logging and later comparison. Raises PredictionRefused if the
    production (champion) path refuses — a refused production prediction
    means there's nothing meaningful to shadow-compare against either."""
    conn = conn or snapshot_db.get_connection()

    production_result = production_predict(
        home_team, away_team, league, market, prediction_timestamp, kickoff_timestamp,
        historical_matches, match_id=match_id, conn=conn,
    )

    live_key = _CHAMPION_TO_LIVE_KEY.get(challenger_model, challenger_model)
    snapshot = generate_features(home_team, away_team, prediction_timestamp, league, historical_matches, match_id=match_id)
    all_preds = live_models.predict_all_available_models(snapshot.raw_row, historical_matches)
    challenger_probs = all_preds.get(live_key)
    challenger_p = model_agreement.extract_market_probability(challenger_probs, market) if challenger_probs else None

    if challenger_p is None:
        challenger_p = float("nan")  # challenger doesn't cover this market — still logged, clearly marked

    shadow_id = _deterministic_shadow_id(production_result.match_id, market, challenger_model, prediction_timestamp)
    snapshot_db.save_shadow_prediction(conn, {
        "shadow_id": shadow_id, "match_id": production_result.match_id,
        "home_team": home_team, "away_team": away_team, "market": market,
        "prediction_timestamp": production_result.prediction_timestamp,
        "champion_model": production_result.champion_model,
        "champion_probability": production_result.calibrated_probability,
        "challenger_model": challenger_model, "challenger_probability": challenger_p,
        "actual_outcome": None, "champion_brier_contribution": None, "challenger_brier_contribution": None,
        "evaluated_at": None, "created_at": datetime.now(timezone.utc).isoformat(),
    })

    return ShadowComparison(
        shadow_id=shadow_id, champion_model=production_result.champion_model,
        champion_probability=production_result.calibrated_probability,
        challenger_model=challenger_model, challenger_probability=challenger_p,
        production_result=production_result,
    )


def evaluate_shadow_predictions(conn: sqlite3.Connection, results: dict[str, int]) -> dict:
    """results: {match_id: actual_outcome (0/1)} for markets now settled.
    Scores every still-unevaluated shadow prediction whose match_id has a
    known result, WITHOUT retraining or changing anything about the
    production champion — this only produces evidence for a future,
    separate champion_challenger.evaluate_promotion() call."""
    eps = 1e-7
    scored = 0
    for row in snapshot_db.get_unevaluated_shadow_predictions(conn):
        outcome = results.get(row["match_id"])
        if outcome is None:
            continue
        champ_p = max(eps, min(1 - eps, row["champion_probability"]))
        chal_p = row["challenger_probability"]
        champ_brier = (champ_p - outcome) ** 2
        chal_brier = (max(eps, min(1 - eps, chal_p)) - outcome) ** 2 if chal_p == chal_p else None  # NaN check

        conn.execute(
            """UPDATE shadow_predictions
               SET actual_outcome = ?, champion_brier_contribution = ?,
                   challenger_brier_contribution = ?, evaluated_at = ?
               WHERE shadow_id = ?""",
            (outcome, champ_brier, chal_brier, datetime.now(timezone.utc).isoformat(), row["shadow_id"]),
        )
        scored += 1
    conn.commit()
    return {"scored": scored}


def shadow_mode_summary(conn: sqlite3.Connection, challenger_model: Optional[str] = None) -> dict:
    """Aggregate champion-vs-challenger performance over all EVALUATED
    shadow predictions so far — the evidence base for a promotion
    decision. Returns None fields if too few samples exist yet; never
    claims a challenger is better on a handful of matches."""
    where = "WHERE actual_outcome IS NOT NULL"
    params = []
    if challenger_model:
        where += " AND challenger_model = ?"
        params.append(challenger_model)

    rows = conn.execute(
        f"SELECT champion_brier_contribution, challenger_brier_contribution FROM shadow_predictions {where}",
        params,
    ).fetchall()
    rows = [r for r in rows if r["challenger_brier_contribution"] is not None]

    n = len(rows)
    if n == 0:
        return {"n_evaluated": 0, "champion_avg_brier": None, "challenger_avg_brier": None,
                "challenger_better": None, "note": "no evaluated shadow predictions yet"}

    champ_avg = sum(r["champion_brier_contribution"] for r in rows) / n
    chal_avg = sum(r["challenger_brier_contribution"] for r in rows) / n
    return {
        "n_evaluated": n,
        "champion_avg_brier": round(champ_avg, 4),
        "challenger_avg_brier": round(chal_avg, 4),
        "challenger_better": chal_avg < champ_avg,
        "note": "n<30 — directional only, not a promotion-grade sample" if n < 30 else "sufficient for a promotion evaluation",
    }

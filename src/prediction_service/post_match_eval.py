"""
Post-match evaluation (Phase 3 §17).

After a match's real result is known, score every prediction that was
made about it against reality. This ONLY records what happened — it does
NOT retrain, recalibrate, or touch the champion registry. Continuous
automatic retraining after every match is explicitly out of scope (Phase
3 §17: "Do NOT automatically retrain after every match") — updating a
model is a separate, deliberate act that goes back through
champion_challenger.evaluate_promotion(), never an automatic side effect
of scoring.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone

from src.prediction_service import snapshot_db

EPS = 1e-7


def evaluate_prediction(conn: sqlite3.Connection, prediction_id: str, actual_outcome: int) -> dict:
    """actual_outcome: 1 if the market's selection happened, 0 otherwise."""
    if actual_outcome not in (0, 1):
        raise ValueError(f"actual_outcome must be 0 or 1, got {actual_outcome!r}")

    pred = snapshot_db.get_prediction(conn, prediction_id)
    if pred is None:
        raise ValueError(f"no prediction found for prediction_id={prediction_id!r} — cannot evaluate what wasn't recorded")

    p = max(EPS, min(1 - EPS, pred["calibrated_probability"]))
    brier = (p - actual_outcome) ** 2
    log_loss = -(actual_outcome * math.log(p) + (1 - actual_outcome) * math.log(1 - p))
    predicted_yes = pred["calibrated_probability"] >= 0.5
    correct = int(predicted_yes == bool(actual_outcome))

    record = {
        "prediction_id": prediction_id, "actual_outcome": actual_outcome, "correct": correct,
        "brier_contribution": round(brier, 5), "log_loss_contribution": round(log_loss, 5),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }
    snapshot_db.save_post_match_evaluation(conn, record)
    return record


def evaluate_predictions_for_match(conn: sqlite3.Connection, match_id: str, outcomes_by_market: dict[str, int]) -> list[dict]:
    """outcomes_by_market: {market: actual_outcome} for one now-finished match.
    Evaluates every recorded prediction for that match+market combination."""
    rows = conn.execute("SELECT prediction_id, market FROM predictions WHERE match_id = ?", (match_id,)).fetchall()
    results = []
    for row in rows:
        outcome = outcomes_by_market.get(row["market"])
        if outcome is None:
            continue
        results.append(evaluate_prediction(conn, row["prediction_id"], outcome))
    return results

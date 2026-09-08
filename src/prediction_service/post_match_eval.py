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
from typing import Optional

import pandas as pd

from src.prediction_service import snapshot_db, observability

EPS = 1e-7


def evaluate_prediction(
    conn: sqlite3.Connection, prediction_id: str, actual_outcome: int, evaluated_at: Optional[str] = None,
) -> dict:
    """actual_outcome: 1 if the market's selection happened, 0 otherwise.
    Always scores against the prediction's LATEST version — evaluating an
    older, superseded version isn't meaningful (Phase 4 §12/§27)."""
    if actual_outcome not in (0, 1):
        raise ValueError(f"actual_outcome must be 0 or 1, got {actual_outcome!r}")

    pred = snapshot_db.get_prediction(conn, prediction_id)
    if pred is None:
        raise ValueError(f"no prediction found for prediction_id={prediction_id!r} — cannot evaluate what wasn't recorded")

    evaluated_at = evaluated_at or datetime.now(timezone.utc).isoformat()
    # §27: a result cannot be known before the prediction that's being
    # scored against it was even made — this would mean the "evaluation"
    # is actually leaking information backwards in time.
    if pd.Timestamp(evaluated_at) < pd.Timestamp(pred["prediction_timestamp"]):
        raise ValueError(
            f"evaluated_at ({evaluated_at}) is BEFORE this prediction's own prediction_timestamp "
            f"({pred['prediction_timestamp']}) — a result cannot be known before the prediction was made."
        )

    p = max(EPS, min(1 - EPS, pred["calibrated_probability"]))
    brier = (p - actual_outcome) ** 2
    log_loss = -(actual_outcome * math.log(p) + (1 - actual_outcome) * math.log(1 - p))
    predicted_yes = pred["calibrated_probability"] >= 0.5
    correct = int(predicted_yes == bool(actual_outcome))

    record = {
        "prediction_id": prediction_id, "version_evaluated": pred["version"],
        "actual_outcome": actual_outcome, "correct": correct,
        "brier_contribution": round(brier, 5), "log_loss_contribution": round(log_loss, 5),
        "evaluated_at": evaluated_at,
    }
    snapshot_db.save_post_match_evaluation(conn, record)
    observability.log_settlement(prediction_id, pred["market"], bool(correct), record["brier_contribution"])
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

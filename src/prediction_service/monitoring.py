"""
Monitoring dashboard data (Phase 3 §18).

Aggregates the predictions.db tables into the metrics a monitoring
dashboard would show, over a caller-supplied date window (the caller
picks daily/weekly/monthly/seasonal boundaries — this module doesn't
assume a particular cadence exists yet, since a fresh deployment has no
history to show).
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from src.prediction_service import observability


def compute_period_metrics(conn: sqlite3.Connection, start: str, end: str) -> dict:
    """start/end: ISO date/datetime strings, inclusive start, exclusive end,
    compared against created_at. Covers both successful predictions and
    refusals in the same window, so coverage/no-prediction-rate are honest
    (not just computed over survivors)."""
    predictions = conn.execute(
        """SELECT p.*, e.actual_outcome, e.correct, e.brier_contribution, e.log_loss_contribution
           FROM predictions p LEFT JOIN post_match_evaluations e ON p.prediction_id = e.prediction_id
           WHERE p.created_at >= ? AND p.created_at < ?""",
        (start, end),
    ).fetchall()
    refusals = conn.execute(
        "SELECT * FROM no_prediction_log WHERE created_at >= ? AND created_at < ?",
        (start, end),
    ).fetchall()

    n_predictions = len(predictions)
    n_refusals = len(refusals)
    n_total_requests = n_predictions + n_refusals

    evaluated = [p for p in predictions if p["actual_outcome"] is not None]
    n_evaluated = len(evaluated)

    metrics = {
        "period": {"start": start, "end": end},
        "n_predictions_made": n_predictions,
        "n_refusals": n_refusals,
        "n_total_requests": n_total_requests,
        "prediction_coverage": round(n_predictions / n_total_requests, 4) if n_total_requests else None,
        "no_prediction_rate": round(n_refusals / n_total_requests, 4) if n_total_requests else None,
        "n_evaluated_against_reality": n_evaluated,
    }

    if n_evaluated > 0:
        metrics["accuracy_pct"] = round(100.0 * sum(p["correct"] for p in evaluated) / n_evaluated, 2)
        metrics["brier_score"] = round(sum(p["brier_contribution"] for p in evaluated) / n_evaluated, 4)
        metrics["log_loss"] = round(sum(p["log_loss_contribution"] for p in evaluated) / n_evaluated, 4)
        avg_predicted = sum(p["calibrated_probability"] for p in evaluated) / n_evaluated
        avg_actual = sum(p["actual_outcome"] for p in evaluated) / n_evaluated
        metrics["calibration_gap"] = round(avg_predicted - avg_actual, 4)
    else:
        metrics.update({"accuracy_pct": None, "brier_score": None, "log_loss": None, "calibration_gap": None})

    if n_predictions > 0:
        agreement_levels = [p["model_agreement_level"] for p in predictions]
        metrics["model_disagreement_rate"] = round(
            sum(1 for lvl in agreement_levels if lvl == "low") / n_predictions, 4
        )
        metrics["avg_confidence_score"] = round(sum(p["confidence_score"] for p in predictions) / n_predictions, 2)
    else:
        metrics["model_disagreement_rate"] = None
        metrics["avg_confidence_score"] = None

    if n_refusals > 0:
        by_stage: dict[str, int] = {}
        for r in refusals:
            by_stage[r["stage_failed"]] = by_stage.get(r["stage_failed"], 0) + 1
        metrics["data_quality_failures_by_stage"] = by_stage
    else:
        metrics["data_quality_failures_by_stage"] = {}

    # By-market and by-league breakdowns (evaluated predictions only).
    by_market: dict[str, dict] = {}
    by_league: dict[str, dict] = {}
    for p in evaluated:
        for grouping, key in ((by_market, p["market"]), (by_league, p["league"])):
            bucket = grouping.setdefault(key, {"n": 0, "brier_sum": 0.0, "correct": 0})
            bucket["n"] += 1
            bucket["brier_sum"] += p["brier_contribution"]
            bucket["correct"] += p["correct"]
    metrics["by_market"] = {
        k: {"n": v["n"], "brier": round(v["brier_sum"] / v["n"], 4), "accuracy_pct": round(100.0 * v["correct"] / v["n"], 2)}
        for k, v in by_market.items()
    }
    metrics["by_league"] = {
        k: {"n": v["n"], "brier": round(v["brier_sum"] / v["n"], 4), "accuracy_pct": round(100.0 * v["correct"] / v["n"], 2)}
        for k, v in by_league.items()
    }

    return metrics


def detect_drift(conn: sqlite3.Connection, recent_start: str, recent_end: str,
                  baseline_start: str, baseline_end: str, brier_drift_threshold: float = 0.03) -> dict:
    """Compares a recent window's Brier score against an earlier baseline
    window. A meaningfully worse recent Brier is flagged as possible
    drift — this is a simple, transparent trigger for a human to
    investigate, not an automatic retraining signal (§17 forbids that)."""
    recent = compute_period_metrics(conn, recent_start, recent_end)
    baseline = compute_period_metrics(conn, baseline_start, baseline_end)

    if recent["brier_score"] is None or baseline["brier_score"] is None:
        return {"drift_detected": False, "reason": "insufficient evaluated predictions in one or both windows",
                "recent": recent, "baseline": baseline}

    delta = recent["brier_score"] - baseline["brier_score"]
    drift_detected = delta > brier_drift_threshold
    if drift_detected:
        observability.log_drift_detected("all_markets_pooled", round(delta, 4), brier_drift_threshold)
    return {
        "drift_detected": drift_detected,
        "brier_delta": round(delta, 4),
        "threshold": brier_drift_threshold,
        "recent_brier": recent["brier_score"], "baseline_brier": baseline["brier_score"],
        "recent_n": recent["n_evaluated_against_reality"], "baseline_n": baseline["n_evaluated_against_reality"],
    }

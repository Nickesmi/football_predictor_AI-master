"""
Regression tests for post_match_eval.py and monitoring.py (Phase 3 §17/§18).
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.prediction_service import snapshot_db
from src.prediction_service.prediction_pipeline import predict
from src.prediction_service.post_match_eval import evaluate_prediction, evaluate_predictions_for_match
from src.prediction_service.monitoring import compute_period_metrics, detect_drift


@pytest.fixture()
def real_matches():
    return pd.read_csv("data/real_historical/matches.csv")


@pytest.fixture()
def db_conn(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_db, "_DB_PATH", tmp_path / "test_predictions.db")
    conn = snapshot_db.get_connection()
    yield conn
    conn.close()


def _make_prediction(real_matches, db_conn, home="Arsenal FC", away="Chelsea FC", market="home_win"):
    return predict(
        home, away, "English Premier League", market,
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, conn=db_conn,
    )


# ── post_match_eval ──────────────────────────────────────────────────

def test_evaluate_prediction_computes_correct_brier_and_correctness(real_matches, db_conn):
    result = _make_prediction(real_matches, db_conn)
    record = evaluate_prediction(db_conn, result.prediction_id, actual_outcome=1)
    expected_brier = (result.calibrated_probability - 1) ** 2
    assert record["brier_contribution"] == pytest.approx(expected_brier, abs=1e-4)
    assert record["correct"] == int(result.calibrated_probability >= 0.5)


def test_evaluate_unknown_prediction_id_raises():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(snapshot_db._SCHEMA)
    with pytest.raises(ValueError, match="no prediction found"):
        evaluate_prediction(conn, "does_not_exist", actual_outcome=1)


def test_invalid_outcome_value_rejected(real_matches, db_conn):
    result = _make_prediction(real_matches, db_conn)
    with pytest.raises(ValueError, match="must be 0 or 1"):
        evaluate_prediction(db_conn, result.prediction_id, actual_outcome=2)


def test_evaluate_predictions_for_match_scores_all_markets_for_that_match(real_matches, db_conn):
    r1 = _make_prediction(real_matches, db_conn, market="home_win")
    r2 = predict(
        "Arsenal FC", "Chelsea FC", "English Premier League", "over_2_5",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, match_id=r1.match_id, conn=db_conn,
    )
    results = evaluate_predictions_for_match(db_conn, r1.match_id, {"home_win": 1, "over_2_5": 0})
    assert len(results) == 2


# ── monitoring ───────────────────────────────────────────────────────

def test_period_metrics_report_zero_coverage_with_no_activity(db_conn):
    metrics = compute_period_metrics(db_conn, "2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z")
    assert metrics["n_total_requests"] == 0
    assert metrics["prediction_coverage"] is None


def test_period_metrics_reflect_real_predictions_and_refusals(real_matches, db_conn):
    from src.prediction_service.prediction_pipeline import PredictionRefused

    r1 = _make_prediction(real_matches, db_conn)
    evaluate_prediction(db_conn, r1.prediction_id, actual_outcome=1)

    try:
        predict(
            "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
            prediction_timestamp="2025-05-25T16:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",  # invalid
            historical_matches=real_matches, conn=db_conn,
        )
    except PredictionRefused:
        pass

    all_rows = db_conn.execute("SELECT created_at FROM predictions").fetchall()
    start = min(r["created_at"] for r in all_rows)[:10] + "T00:00:00Z"
    end_dt = pd.Timestamp(start) + pd.Timedelta(days=1)

    metrics = compute_period_metrics(db_conn, start, end_dt.isoformat())
    assert metrics["n_predictions_made"] == 1
    assert metrics["n_refusals"] == 1
    assert metrics["n_total_requests"] == 2
    assert metrics["prediction_coverage"] == pytest.approx(0.5)
    assert metrics["no_prediction_rate"] == pytest.approx(0.5)
    assert metrics["n_evaluated_against_reality"] == 1
    assert metrics["accuracy_pct"] is not None
    assert "timestamp_validation" in metrics["data_quality_failures_by_stage"]


def test_by_market_and_by_league_breakdowns_are_populated(real_matches, db_conn):
    r1 = _make_prediction(real_matches, db_conn, market="home_win")
    evaluate_prediction(db_conn, r1.prediction_id, actual_outcome=1)

    all_rows = db_conn.execute("SELECT created_at FROM predictions").fetchall()
    start = min(r["created_at"] for r in all_rows)[:10] + "T00:00:00Z"
    end_dt = pd.Timestamp(start) + pd.Timedelta(days=1)
    metrics = compute_period_metrics(db_conn, start, end_dt.isoformat())

    assert "home_win" in metrics["by_market"]
    assert "English Premier League" in metrics["by_league"]


def test_drift_detection_flags_a_real_brier_regression(db_conn):
    """Manually insert two windows of predictions+evaluations with
    deliberately different Brier scores to prove detect_drift's math,
    without depending on real accumulated history (this environment has
    none yet)."""
    import uuid
    from datetime import datetime, timezone

    def insert(day: str, calibrated_p: float, outcome: int):
        pid = f"pred_{uuid.uuid4().hex[:12]}"
        snapshot_db.save_prediction(db_conn, {
            "prediction_id": pid, "match_id": "m1", "home_team": "A", "away_team": "B",
            "league": "L", "market": "home_win", "prediction_timestamp": day, "feature_snapshot_timestamp": day,
            "feature_snapshot_json": "{}", "champion_model": "elo", "model_provenance": "BASELINE_MODEL",
            "calibration_version": "none", "raw_probability": calibrated_p, "calibrated_probability": calibrated_p,
            "confidence_score": 50.0, "confidence_tier": "medium", "confidence_components_json": "{}",
            "data_sufficiency": 50, "model_agreement_level": "high", "model_agreement_spread": 0.01,
            "ood_severity": "none", "ood_reasons_json": "[]", "meets_confidence_threshold": 1, "threshold_used": 0.4,
            "betting_status": "UNAVAILABLE", "betting_reason": "no odds", "pipeline_version": "test",
            "created_at": day,
        })
        evaluate_prediction(db_conn, pid, outcome)

    # Baseline window: well-calibrated (p=0.9 for a hit) -> low Brier.
    for i in range(10):
        insert(f"2024-01-0{i%9+1}T00:00:00Z", 0.9, 1)
    # Recent window: badly miscalibrated (p=0.9 but actually a miss) -> high Brier.
    for i in range(10):
        insert(f"2024-02-0{i%9+1}T00:00:00Z", 0.9, 0)

    result = detect_drift(
        db_conn, recent_start="2024-02-01T00:00:00Z", recent_end="2024-03-01T00:00:00Z",
        baseline_start="2024-01-01T00:00:00Z", baseline_end="2024-02-01T00:00:00Z",
    )
    assert result["drift_detected"] is True
    assert result["brier_delta"] > 0

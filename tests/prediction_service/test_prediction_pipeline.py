"""
Regression tests for src/prediction_service/prediction_pipeline.py —
Phase 3 §4/§13/§14/§19: every stage must fail closed, timestamps are a
hard rule, and every outcome (success or refusal) must be persisted.
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from src.prediction_service import snapshot_db
from src.prediction_service.prediction_pipeline import predict, PredictionRefused


@pytest.fixture()
def real_matches():
    return pd.read_csv("data/real_historical/matches.csv")


@pytest.fixture()
def db_conn(tmp_path, monkeypatch):
    """An isolated, throwaway predictions.db per test so tests never
    collide with each other or with real production data."""
    test_db_path = tmp_path / "test_predictions.db"
    monkeypatch.setattr(snapshot_db, "_DB_PATH", test_db_path)
    conn = snapshot_db.get_connection()
    yield conn
    conn.close()


def test_successful_prediction_end_to_end(real_matches, db_conn):
    result = predict(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, conn=db_conn,
    )
    assert result.champion_model in ("elo", "poisson_real_data", "dixon_coles", "frequency")
    assert 0.0 <= result.raw_probability <= 1.0
    assert 0.0 <= result.calibrated_probability <= 1.0
    assert 0.0 <= result.confidence.score <= 100.0
    assert result.betting_recommendation.status == "UNAVAILABLE"  # no odds supplied

    saved = snapshot_db.get_prediction(db_conn, result.prediction_id)
    assert saved is not None
    assert saved["champion_model"] == result.champion_model


def test_prediction_timestamp_at_or_after_kickoff_is_refused(real_matches, db_conn):
    with pytest.raises(PredictionRefused) as exc_info:
        predict(
            "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
            prediction_timestamp="2025-05-25T15:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
            historical_matches=real_matches, conn=db_conn,
        )
    assert exc_info.value.stage_failed == "timestamp_validation"
    assert "INVALID_PREDICTION_TIME" in exc_info.value.reason


def test_missing_kickoff_timestamp_is_refused(real_matches, db_conn):
    with pytest.raises(PredictionRefused) as exc_info:
        predict(
            "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
            prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp=None,
            historical_matches=real_matches, conn=db_conn,
        )
    assert exc_info.value.stage_failed == "timestamp_validation"


def test_empty_historical_data_is_refused(db_conn):
    empty = pd.DataFrame(columns=["match_id", "date", "league", "season", "home_team", "away_team", "home_goals", "away_goals"])
    with pytest.raises(PredictionRefused) as exc_info:
        predict(
            "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
            prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
            historical_matches=empty, conn=db_conn,
        )
    assert exc_info.value.stage_failed == "data_availability"


def test_unknown_market_with_no_champion_is_refused(real_matches, db_conn):
    with pytest.raises(PredictionRefused) as exc_info:
        predict(
            "Arsenal FC", "Chelsea FC", "English Premier League", "not_a_real_market",
            prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
            historical_matches=real_matches, conn=db_conn,
        )
    assert exc_info.value.stage_failed == "champion_selection"


def test_true_cold_start_team_is_refused_not_low_confidence(real_matches, db_conn):
    """A team with ZERO real history is an extrapolation, not a
    prediction — this must hard-refuse (Phase 3 §10/§19), not just return
    a low-confidence number."""
    with pytest.raises(PredictionRefused) as exc_info:
        predict(
            "Totally Brand New FC 99999", "Chelsea FC", "English Premier League", "home_win",
            prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
            historical_matches=real_matches, conn=db_conn,
        )
    assert exc_info.value.stage_failed == "ood_check"


def test_same_team_twice_is_refused_via_feature_generation(real_matches, db_conn):
    with pytest.raises(PredictionRefused) as exc_info:
        predict(
            "Arsenal FC", "Arsenal FC", "English Premier League", "home_win",
            prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
            historical_matches=real_matches, conn=db_conn,
        )
    assert exc_info.value.stage_failed == "feature_generation"


def test_every_refusal_is_logged_to_no_prediction_log(real_matches, db_conn):
    with pytest.raises(PredictionRefused):
        predict(
            "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
            prediction_timestamp="2025-05-25T16:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
            historical_matches=real_matches, conn=db_conn,
        )
    rows = db_conn.execute("SELECT * FROM no_prediction_log").fetchall()
    assert len(rows) == 1
    assert rows[0]["stage_failed"] == "timestamp_validation"


def test_synthetic_model_can_never_become_the_live_champion(real_matches, db_conn):
    """Structural regression test for the Phase 1/2/3 provenance
    discipline: no market's champion is ever the synthetic-trained model,
    checked by actually running the pipeline for every core market."""
    for market in ["home_win", "draw", "away_win", "over_1_5", "over_2_5", "over_3_5", "btts"]:
        try:
            result = predict(
                "Arsenal FC", "Chelsea FC", "English Premier League", market,
                prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
                historical_matches=real_matches, conn=db_conn,
            )
        except PredictionRefused:
            continue
        assert "synthetic" not in result.champion_model.lower()
        assert result.model_provenance != "SYNTHETIC_EXPERIMENTAL"


def test_odds_supplied_produces_a_real_betting_recommendation(real_matches, db_conn):
    result = predict(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, odds=2.5, odds_timestamp="2025-05-25T09:00:00Z", conn=db_conn,
    )
    assert result.betting_recommendation.status in ("BET", "NO_BET")
    assert result.betting_recommendation.expected_value is not None


def test_bet_recommendation_downgraded_to_no_bet_when_below_evidence_threshold(real_matches, db_conn, monkeypatch):
    """Even a positive-EV bet must not be recommended if the calibrated
    probability doesn't clear the evidence-backed threshold (Phase 3 §16)."""
    from src.prediction_service import thresholds as thresholds_mod
    monkeypatch.setattr(thresholds_mod, "get_threshold", lambda market: 0.99)  # impossible to clear

    result = predict(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, odds=5.0, odds_timestamp="2025-05-25T09:00:00Z", conn=db_conn,
    )
    assert result.meets_confidence_threshold is False
    assert result.betting_recommendation.status == "NO_BET"

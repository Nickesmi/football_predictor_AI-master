"""
Regression tests for champion_challenger.py and shadow_mode.py (Phase 3 §11/§12).
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.prediction_service import snapshot_db
from src.prediction_service.champion_challenger import evaluate_promotion
from src.prediction_service.shadow_mode import predict_with_shadow, evaluate_shadow_predictions, shadow_mode_summary


@pytest.fixture()
def real_matches():
    return pd.read_csv("data/real_historical/matches.csv")


@pytest.fixture()
def db_conn(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_db, "_DB_PATH", tmp_path / "test_predictions.db")
    conn = snapshot_db.get_connection()
    yield conn
    conn.close()


def _fake_backtest(champion_brier_per_fold, challenger_brier_per_fold, challenger_ece, market="home_win"):
    folds = []
    for i, (cb, xb) in enumerate(zip(champion_brier_per_fold, challenger_brier_per_fold)):
        folds.append({
            "is_final_frozen_holdout": i == len(champion_brier_per_fold) - 1,
            "train_seasons": ["2015-16"], "test_season": "2016-17",
            "models": {
                "elo": {market: {"brier": cb, "ece": 0.02}},
                "frequency": {market: {"brier": xb, "ece": challenger_ece}},
            },
        })
    return {"folds": folds}


# ── champion_challenger ──────────────────────────────────────────────

def test_promotion_refused_when_challenger_never_beats_champion():
    backtest = _fake_backtest([0.20, 0.20, 0.20, 0.20], [0.22, 0.22, 0.22, 0.22], challenger_ece=0.02)
    decision = evaluate_promotion("home_win", "elo", "frequency", backtest,
                                   leakage_tests_passed=True, full_regression_suite_passed=True)
    assert decision.promoted is False
    assert decision.criteria["beats_champion_on_frozen_holdout"] is False


def test_promotion_refused_without_leakage_tests_confirmed():
    backtest = _fake_backtest([0.20, 0.20, 0.20, 0.20], [0.10, 0.10, 0.10, 0.10], challenger_ece=0.02)
    decision = evaluate_promotion("home_win", "elo", "frequency", backtest,
                                   leakage_tests_passed=False, full_regression_suite_passed=True)
    assert decision.promoted is False
    assert decision.criteria["leakage_tests_passed"] is False


def test_promotion_refused_without_regression_suite_confirmed():
    backtest = _fake_backtest([0.20, 0.20, 0.20, 0.20], [0.10, 0.10, 0.10, 0.10], challenger_ece=0.02)
    decision = evaluate_promotion("home_win", "elo", "frequency", backtest,
                                   leakage_tests_passed=True, full_regression_suite_passed=False)
    assert decision.promoted is False
    assert decision.criteria["regression_suite_passed"] is False


def test_promotion_refused_when_win_is_only_in_final_fold():
    """A challenger that only wins the frozen holdout (3 earlier folds it
    LOSES) must fail the stability criterion — never promote on one fold."""
    backtest = _fake_backtest(
        champion_brier_per_fold=[0.10, 0.10, 0.10, 0.20],
        challenger_brier_per_fold=[0.30, 0.30, 0.30, 0.05],
        challenger_ece=0.02,
    )
    decision = evaluate_promotion("home_win", "elo", "frequency", backtest,
                                   leakage_tests_passed=True, full_regression_suite_passed=True)
    assert decision.criteria["stable_across_folds"] is False
    assert decision.promoted is False


def test_promotion_refused_for_poor_calibration_even_if_brier_is_better():
    backtest = _fake_backtest([0.20, 0.20, 0.20, 0.20], [0.10, 0.10, 0.10, 0.10], challenger_ece=0.25)
    decision = evaluate_promotion("home_win", "elo", "frequency", backtest,
                                   leakage_tests_passed=True, full_regression_suite_passed=True)
    assert decision.criteria["calibration_acceptable"] is False
    assert decision.promoted is False


def test_real_backtest_xgboost_does_not_dethrone_elo_for_home_win():
    """Integration check against the ACTUAL Phase 2/3 backtest results:
    XGBoost must not be promotable over Elo for home_win, consistent with
    REAL_DATA_BACKTEST_REPORT.md's honest finding."""
    import json
    from pathlib import Path
    backtest = json.loads(Path("data/real_historical/backtest_results.json").read_text())
    decision = evaluate_promotion("home_win", champion="elo", challenger="xgboost_real_data_calibrated",
                                   backtest=backtest, leakage_tests_passed=True, full_regression_suite_passed=True)
    assert decision.promoted is False


# ── shadow_mode ──────────────────────────────────────────────────────

def test_shadow_mode_never_changes_the_returned_production_prediction(real_matches, db_conn):
    from src.prediction_service.prediction_pipeline import predict as production_predict

    direct_result = production_predict(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, conn=db_conn,
    )
    shadow_comparison = predict_with_shadow(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, challenger_model="xgboost_real_data_calibrated", conn=db_conn,
    )
    assert shadow_comparison.production_result.calibrated_probability == direct_result.calibrated_probability
    assert shadow_comparison.production_result.champion_model == direct_result.champion_model


def test_shadow_predictions_are_logged_and_scoreable(real_matches, db_conn):
    comp = predict_with_shadow(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, challenger_model="xgboost_real_data_calibrated", conn=db_conn,
    )
    unevaluated = snapshot_db.get_unevaluated_shadow_predictions(db_conn)
    assert len(unevaluated) == 1

    result = evaluate_shadow_predictions(db_conn, {comp.production_result.match_id: 1})
    assert result["scored"] == 1
    assert len(snapshot_db.get_unevaluated_shadow_predictions(db_conn)) == 0


def test_shadow_summary_reports_small_sample_as_directional_only(real_matches, db_conn):
    comp = predict_with_shadow(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, challenger_model="xgboost_real_data_calibrated", conn=db_conn,
    )
    evaluate_shadow_predictions(db_conn, {comp.production_result.match_id: 1})
    summary = shadow_mode_summary(db_conn, "xgboost_real_data_calibrated")
    assert summary["n_evaluated"] == 1
    assert "directional only" in summary["note"]


def test_shadow_summary_with_no_data_does_not_claim_a_winner(db_conn):
    summary = shadow_mode_summary(db_conn, "xgboost_real_data_calibrated")
    assert summary["n_evaluated"] == 0
    assert summary["challenger_better"] is None

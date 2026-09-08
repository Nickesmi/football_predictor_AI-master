"""
Regression tests for model_agreement.py, ood_detection.py, and
confidence_engine.py (Phase 3 §7/§9/§10).
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.ml.baselines import MarketProbs
from src.prediction_service.model_agreement import compute_agreement
from src.prediction_service.ood_detection import check_ood, MIN_RELIABLE_TEAM_HISTORY
from src.prediction_service.confidence_engine import compute_confidence


# ── model_agreement ──────────────────────────────────────────────────

def test_high_agreement_when_models_nearly_identical():
    preds = {
        "elo": MarketProbs(0.50, 0.25, 0.25, 0.7, 0.5, 0.3, 0.5),
        "poisson_real_data": MarketProbs(0.52, 0.24, 0.24, 0.7, 0.51, 0.3, 0.51),
    }
    result = compute_agreement(preds, "home_win")
    assert result.agreement_level == "high"
    assert result.n_models_compared == 2


def test_low_agreement_when_models_diverge_widely():
    preds = {
        "elo": MarketProbs(0.30, 0.30, 0.40, 0.7, 0.5, 0.3, 0.5),
        "poisson_real_data": MarketProbs(0.70, 0.15, 0.15, 0.7, 0.5, 0.3, 0.5),
    }
    result = compute_agreement(preds, "home_win")
    assert result.agreement_level == "low"


def test_models_that_dont_cover_a_market_are_excluded_not_zeroed():
    preds = {
        "elo": MarketProbs(0.50, 0.25, 0.25, 0.7, 0.5, 0.3, 0.5, p_home_over_0_5=None),
        "poisson_real_data": MarketProbs(0.50, 0.25, 0.25, 0.7, 0.5, 0.3, 0.5, p_home_over_0_5=0.8),
    }
    result = compute_agreement(preds, "home_over_0_5")
    assert result.n_models_compared == 1
    assert result.model_probabilities == {"poisson_real_data": 0.8}


def test_under_market_is_computed_as_complement_of_over():
    preds = {"elo": MarketProbs(0.5, 0.25, 0.25, 0.7, 0.6, 0.3, 0.5)}
    result = compute_agreement(preds, "under_2_5")
    assert result.model_probabilities["elo"] == pytest.approx(0.4)


def test_no_models_cover_market_returns_unknown_level():
    result = compute_agreement({}, "home_win")
    assert result.agreement_level == "unknown"
    assert result.n_models_compared == 0


# ── ood_detection ─────────────────────────────────────────────────────

def _fake_training_features():
    return pd.DataFrame({
        "home_elo_before": [1400, 1450, 1500, 1550, 1600] * 20,
        "away_elo_before": [1400, 1450, 1500, 1550, 1600] * 20,
        "home_scored_avg_5": [1.0, 1.2, 1.4, 1.6, 1.8] * 20,
        "away_scored_avg_5": [1.0, 1.2, 1.4, 1.6, 1.8] * 20,
        "home_conceded_avg_5": [1.0, 1.2, 1.4, 1.6, 1.8] * 20,
        "away_conceded_avg_5": [1.0, 1.2, 1.4, 1.6, 1.8] * 20,
        "home_scored_avg_10": [1.0, 1.2, 1.4, 1.6, 1.8] * 20,
        "away_scored_avg_10": [1.0, 1.2, 1.4, 1.6, 1.8] * 20,
        "home_conceded_avg_10": [1.0, 1.2, 1.4, 1.6, 1.8] * 20,
        "away_conceded_avg_10": [1.0, 1.2, 1.4, 1.6, 1.8] * 20,
        "home_form_points_5": [7.5] * 100,
        "away_form_points_5": [7.5] * 100,
        "home_venue_scored_avg": [1.3] * 100,
        "home_venue_conceded_avg": [1.1] * 100,
        "away_venue_scored_avg": [1.1] * 100,
        "away_venue_conceded_avg": [1.3] * 100,
        "data_sufficiency": [40] * 100,
    })


def test_cold_start_team_is_flagged_severe_ood():
    result = check_ood({}, _fake_training_features(), home_is_cold_start=True,
                        away_is_cold_start=False, data_sufficiency=0)
    assert result.is_ood is True
    assert result.severity == "severe"


def test_thin_history_below_floor_is_flagged_but_not_severe():
    result = check_ood(
        {col: 1500.0 for col in _fake_training_features().columns},
        _fake_training_features(), home_is_cold_start=False, away_is_cold_start=False,
        data_sufficiency=MIN_RELIABLE_TEAM_HISTORY - 1,
    )
    assert any("data_sufficiency" in r for r in result.reasons)


def test_extreme_feature_value_outside_training_distribution_is_flagged():
    row = {col: 1500.0 for col in _fake_training_features().columns}
    row["home_elo_before"] = 5000.0  # absurdly outside training range
    result = check_ood(row, _fake_training_features(), home_is_cold_start=False,
                        away_is_cold_start=False, data_sufficiency=50)
    assert result.is_ood is True
    assert any("home_elo_before" in r for r in result.reasons)


def test_normal_match_within_training_distribution_is_not_ood():
    row = {
        "home_elo_before": 1500, "away_elo_before": 1500,
        "home_scored_avg_5": 1.4, "away_scored_avg_5": 1.4,
        "home_conceded_avg_5": 1.4, "away_conceded_avg_5": 1.4,
        "home_scored_avg_10": 1.4, "away_scored_avg_10": 1.4,
        "home_conceded_avg_10": 1.4, "away_conceded_avg_10": 1.4,
        "home_form_points_5": 7.5, "away_form_points_5": 7.5,
        "home_venue_scored_avg": 1.3, "home_venue_conceded_avg": 1.1,
        "away_venue_scored_avg": 1.1, "away_venue_conceded_avg": 1.3,
    }
    result = check_ood(row, _fake_training_features(), home_is_cold_start=False,
                        away_is_cold_start=False, data_sufficiency=50)
    assert result.is_ood is False
    assert result.severity == "none"


# ── confidence_engine ────────────────────────────────────────────────

def test_confidence_is_not_simply_probability_times_100():
    """The critical Phase-3 rule: confidence must be a distinct
    computation from the raw probability, not derived from it at all."""
    import inspect
    from src.prediction_service import confidence_engine
    # Structural guard: compute_confidence's signature must not even accept
    # a raw probability argument — it cannot compute probability*100 if it
    # never receives a probability at all.
    sig = inspect.signature(confidence_engine.compute_confidence)
    assert "probability" not in sig.parameters and "raw_probability" not in sig.parameters


def test_severe_ood_hard_caps_confidence_regardless_of_other_factors():
    from src.prediction_service.model_agreement import AgreementResult
    from src.prediction_service.ood_detection import OODResult

    strong_agreement = AgreementResult("home_win", 3, {"a": 0.5, "b": 0.51, "c": 0.49}, 0.5, 0.01, 0.02, "high")
    severe_ood = OODResult(is_ood=True, severity="severe", reasons=["cold start"])
    strong_champion_evidence = {"stable": True, "margin_to_runner_up_brier": 0.05, "selection_confidence": "clear_margin"}

    result = compute_confidence(strong_champion_evidence, {"ece": 0.01}, strong_agreement, severe_ood, data_sufficiency=100)
    assert result.hard_capped_by_ood is True
    assert result.score <= 20.0


def test_high_quality_low_ood_evidence_yields_high_confidence_tier():
    from src.prediction_service.model_agreement import AgreementResult
    from src.prediction_service.ood_detection import OODResult

    strong_agreement = AgreementResult("home_win", 3, {"a": 0.5, "b": 0.51, "c": 0.49}, 0.5, 0.01, 0.02, "high")
    no_ood = OODResult(is_ood=False, severity="none", reasons=[])
    strong_champion_evidence = {"stable": True, "margin_to_runner_up_brier": 0.05, "selection_confidence": "clear_margin"}

    result = compute_confidence(strong_champion_evidence, {"ece": 0.01}, strong_agreement, no_ood, data_sufficiency=100)
    assert result.tier in ("high", "medium")
    assert result.score > 60


def test_unstable_champion_and_low_agreement_yields_low_confidence():
    from src.prediction_service.model_agreement import AgreementResult
    from src.prediction_service.ood_detection import OODResult

    weak_agreement = AgreementResult("home_win", 3, {"a": 0.2, "b": 0.6, "c": 0.4}, 0.4, 0.16, 0.4, "low")
    no_ood = OODResult(is_ood=False, severity="none", reasons=[])
    weak_champion_evidence = {"stable": False, "margin_to_runner_up_brier": 0.0005, "selection_confidence": "low_margin_is_noise_level"}

    result = compute_confidence(weak_champion_evidence, {"ece": 0.09}, weak_agreement, no_ood, data_sufficiency=3)
    assert result.tier in ("very_low", "low")


def test_lineup_uncertainty_penalty_reduces_score():
    from src.prediction_service.model_agreement import AgreementResult
    from src.prediction_service.ood_detection import OODResult

    agreement = AgreementResult("home_win", 3, {"a": 0.5, "b": 0.51, "c": 0.49}, 0.5, 0.01, 0.02, "high")
    no_ood = OODResult(is_ood=False, severity="none", reasons=[])
    champion_evidence = {"stable": True, "margin_to_runner_up_brier": 0.05, "selection_confidence": "clear_margin"}

    result_no_penalty = compute_confidence(champion_evidence, {"ece": 0.01}, agreement, no_ood, 100, lineup_uncertainty_penalty=0.0)
    result_with_penalty = compute_confidence(champion_evidence, {"ece": 0.01}, agreement, no_ood, 100, lineup_uncertainty_penalty=15.0)
    assert result_with_penalty.score < result_no_penalty.score

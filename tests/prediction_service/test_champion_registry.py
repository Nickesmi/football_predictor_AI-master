"""
Regression tests for src/prediction_service/champion_registry.py — champion
selection must be evidence-driven and never let XGBoost win a market it
wasn't statistically validated on (Phase 3 §1 / §11's "never promote on
one lucky fold").
"""

from __future__ import annotations

import pytest

from src.prediction_service.champion_registry import select_champion, _xgb_eligible, ELIGIBLE_MODELS


def _fake_backtest(fold_briers: list[dict], market="home_win"):
    """fold_briers: list of {model_name: brier}, one dict per fold, last is frozen holdout."""
    folds = []
    for i, briers in enumerate(fold_briers):
        models = {name: {market: {"brier": b}} for name, b in briers.items()}
        folds.append({
            "is_final_frozen_holdout": i == len(fold_briers) - 1,
            "models": models,
        })
    return {"folds": folds}


def test_champion_is_best_on_frozen_holdout_when_stable_across_folds():
    backtest = _fake_backtest([
        {"elo": 0.20, "poisson_real_data": 0.22, "frequency": 0.24},
        {"elo": 0.19, "poisson_real_data": 0.23, "frequency": 0.24},
        {"elo": 0.21, "poisson_real_data": 0.22, "frequency": 0.24},
        {"elo": 0.18, "poisson_real_data": 0.23, "frequency": 0.24},  # frozen holdout
    ])
    result = select_champion("home_win", backtest)
    assert result["champion"] == "elo"
    assert result["stable"] is True
    assert result["method"] == "frozen_holdout_best_with_stability_confirmed"


def test_champion_falls_back_to_average_rank_when_frozen_holdout_winner_is_unstable():
    """A model that wins ONLY the frozen holdout, ranking near-last in
    every earlier fold, must not become champion on that fluke alone."""
    backtest = _fake_backtest([
        {"elo": 0.19, "poisson_real_data": 0.20, "frequency": 0.30},  # elo best
        {"elo": 0.19, "poisson_real_data": 0.20, "frequency": 0.30},  # elo best
        {"elo": 0.19, "poisson_real_data": 0.20, "frequency": 0.30},  # elo best
        {"elo": 0.25, "poisson_real_data": 0.10, "frequency": 0.30},  # poisson wins ONLY here
    ])
    result = select_champion("home_win", backtest)
    assert result["stable"] is False
    assert result["champion"] == "elo", "a one-fold fluke must not override 3 folds of consistent evidence"
    assert result["method"] == "fallback_best_average_rank_frozen_holdout_was_unstable"


def test_xgboost_cannot_win_a_market_it_was_not_statistically_validated_on(monkeypatch):
    """Even if xgboost_real_data_calibrated has the numerically lowest
    Brier on the frozen holdout, it must be excluded from champion
    selection unless model_provenance says production_validated=True —
    otherwise 'market-specific champion selection' would be a backdoor
    around the Phase 2 provenance gate."""
    import src.prediction_service.champion_registry as cr
    monkeypatch.setattr(cr, "real_data_model_provenance", lambda market: {"production_validated": False})

    backtest = _fake_backtest([
        {"elo": 0.20, "xgboost_real_data_calibrated": 0.19},
        {"elo": 0.20, "xgboost_real_data_calibrated": 0.19},
        {"elo": 0.20, "xgboost_real_data_calibrated": 0.19},
        {"elo": 0.20, "xgboost_real_data_calibrated": 0.15},  # xgb "wins" frozen holdout by a lot
    ], market="over_2_5")

    result = select_champion("over_2_5", backtest)
    assert result["champion"] == "elo", "unvalidated xgboost must never be selectable as champion"


def test_xgboost_eligible_flag_is_false_for_light_markets_never_trained():
    assert _xgb_eligible("home_over_0_5") is False
    assert _xgb_eligible("away_over_0_5") is False


def test_market_with_no_eligible_model_returns_none_champion():
    backtest = {"folds": [{"is_final_frozen_holdout": True, "models": {}}]}
    result = select_champion("home_win", backtest)
    assert result["champion"] is None
    assert "no eligible model" in result["reason"]


def test_real_registry_file_has_a_champion_for_every_core_market():
    """Integration check against the actual generated registry (requires
    scripts/build_champion_registry.py to have been run — same precondition
    as the rest of the Phase-2/3 test suite depending on real backtest
    artifacts)."""
    from src.prediction_service.champion_registry import load_registry
    registry = load_registry()
    for market in ["home_win", "draw", "away_win", "over_2_5", "btts"]:
        entry = registry["markets"][market]
        assert entry["champion"] in ELIGIBLE_MODELS, f"{market} has no valid champion recorded"

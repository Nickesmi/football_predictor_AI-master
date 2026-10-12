"""
Regression tests for src/ml/model_provenance.py (Phase 2 §7/§14): the
synthetic model must never be presentable as validated, and the real-data
model must only be "production_validated" when it actually beat strong
baselines with statistical significance — not merely because it was
trained on real data.
"""

from __future__ import annotations

import json

import pytest

import src.ml.model_provenance as mp


def test_synthetic_model_is_always_experimental_and_never_validated():
    result = mp.synthetic_model_provenance()
    assert result["provenance"] == "SYNTHETIC_EXPERIMENTAL"
    assert result["production_validated"] is False


def test_real_data_model_not_validated_when_significantly_worse_than_a_baseline(tmp_path, monkeypatch):
    metadata_path = tmp_path / "real_data_model_metadata.json"
    metadata_path.write_text(json.dumps({
        "training_info": {"train_seasons": ["2015-16", "2016-17"]},
        "tested_on_season_frozen_holdout": "2024-25",
    }))
    results_path = tmp_path / "backtest_results.json"
    results_path.write_text(json.dumps({
        "final_holdout_significance_vs_baselines": {
            "home_win__xgb_vs_elo": {"point_estimate": 0.01, "ci_95": [0.001, 0.02], "significant_at_95": True},
            "home_win__xgb_vs_poisson_real_data": {"point_estimate": -0.005, "ci_95": [-0.01, 0.001], "significant_at_95": False},
        }
    }))

    monkeypatch.setattr(mp, "_REAL_MODEL_METADATA", metadata_path)
    monkeypatch.setattr(mp, "_BACKTEST_RESULTS", results_path)

    result = mp.real_data_model_provenance("home_win")
    assert result["provenance"] == "REAL_DATA_TRAINED"
    # Significantly worse than Elo -> must NOT validate, even though it's real-data trained.
    assert result["production_validated"] is False


def test_real_data_model_validated_only_when_beats_baselines_and_not_worse_than_either(tmp_path, monkeypatch):
    metadata_path = tmp_path / "real_data_model_metadata.json"
    metadata_path.write_text(json.dumps({
        "training_info": {"train_seasons": ["2015-16", "2016-17"]},
        "tested_on_season_frozen_holdout": "2024-25",
    }))
    results_path = tmp_path / "backtest_results.json"
    results_path.write_text(json.dumps({
        "final_holdout_significance_vs_baselines": {
            "over_3_5__xgb_vs_elo": {"point_estimate": -0.001, "ci_95": [-0.005, 0.003], "significant_at_95": False},
            "over_3_5__xgb_vs_poisson_real_data": {"point_estimate": -0.01, "ci_95": [-0.02, -0.002], "significant_at_95": True},
        }
    }))

    monkeypatch.setattr(mp, "_REAL_MODEL_METADATA", metadata_path)
    monkeypatch.setattr(mp, "_BACKTEST_RESULTS", results_path)

    result = mp.real_data_model_provenance("over_3_5")
    assert result["production_validated"] is True


def test_real_data_model_missing_metadata_is_not_validated(monkeypatch, tmp_path):
    monkeypatch.setattr(mp, "_REAL_MODEL_METADATA", tmp_path / "definitely_does_not_exist.json")
    result = mp.real_data_model_provenance("home_win")
    assert result["production_validated"] is False


def test_production_gate_in_probability_engine_returns_zero_weight():
    """Structural regression test for the Phase-2 production safety fix:
    _market_weight must return 0.0 unconditionally today (neither the
    synthetic model nor the real-data model has cleared the bar for the
    markets probability_engine actually blends). If this ever legitimately
    changes, this test must be updated alongside a real provenance check,
    not silently."""
    from src.engine.probability_engine import _market_weight

    assert _market_weight(xgb_prediction="anything-truthy", market="home_win", data_quality=100.0) == 0.0
    assert _market_weight(xgb_prediction="anything-truthy", market="over_2_5", data_quality=100.0) == 0.0

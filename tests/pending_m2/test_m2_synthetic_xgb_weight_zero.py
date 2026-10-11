"""Moved verbatim from tests/real_data/test_model_provenance.py (audit branch)."""

from __future__ import annotations


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

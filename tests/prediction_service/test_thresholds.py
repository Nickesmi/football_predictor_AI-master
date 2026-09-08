"""
Regression tests for src/prediction_service/thresholds.py (Phase 3 §16):
thresholds must be learned only from folds 1-3, never the frozen holdout,
and the threshold search must not have off-by-rounding bugs that make a
real, data-supported threshold unreachable.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.prediction_service.thresholds import _find_threshold, _NON_FINAL_FOLDS, TARGET_HIT_RATE_FLOOR


def test_frozen_holdout_season_is_never_among_the_non_final_folds():
    """2024-25 (the walk-forward backtest's frozen final holdout) must
    never appear as a train or test season here."""
    for train_seasons, test_season in _NON_FINAL_FOLDS:
        assert "2024-25" not in train_seasons
        assert test_season != "2024-25"


def test_non_final_folds_are_chronologically_valid():
    from scripts.walk_forward_backtest import SEASON_ORDER
    for train_seasons, test_season in _NON_FINAL_FOLDS:
        assert max(SEASON_ORDER.index(s) for s in train_seasons) < SEASON_ORDER.index(test_season)


def test_threshold_search_finds_a_real_threshold_even_when_rounding_would_hide_it():
    """Regression test for the exact bug caught during Phase 3 development:
    three raw probabilities (0.7757, 0.7779, 0.7780) all round to display
    value 0.78, but a naive `p >= round(t, 2)` comparison excluded all of
    them because none is actually >= 0.78. The search must use the real
    unrounded values as candidates."""
    p = np.array([0.77572724] * 900 + [0.77788204] * 900 + [0.77800713] * 900)
    y = np.array([1] * 750 + [0] * 150 + [1] * 750 + [0] * 150 + [1] * 750 + [0] * 150)  # ~0.83 hit rate throughout

    result = _find_threshold(p, y)
    assert result is not None, "a real, data-supported threshold must be found, not silently missed"
    assert result["pooled_n"] >= 40


def test_no_threshold_returned_when_hit_rate_floor_is_never_reached():
    p = np.array([0.5] * 100)
    y = np.array([1] * 40 + [0] * 60)  # 40% hit rate, below the 55% floor
    result = _find_threshold(p, y)
    assert result is None


def test_no_threshold_returned_when_sample_size_is_too_small():
    p = np.array([0.9] * 10)
    y = np.array([1] * 9 + [0] * 1)  # 90% hit rate but only 10 samples
    result = _find_threshold(p, y)
    assert result is None


def test_threshold_prefers_lowest_qualifying_value_to_maximize_coverage():
    # p=0.5 slice (first 50): 20 hits / 30 misses. p=0.8 slice (last 50):
    # 45 hits / 5 misses. Pooled at t=0.5 (all 100): 65/100 = 0.65 hit
    # rate. Pooled at t=0.8 (last 50 only): 45/50 = 0.90 hit rate. Both
    # clear the 0.55 floor with n>=40, so the LOWER threshold (0.5, wider
    # coverage) must be the one returned.
    p = np.array([0.5] * 50 + [0.8] * 50)
    y = np.array([1] * 20 + [0] * 30 + [1] * 45 + [0] * 5)
    result = _find_threshold(p, y)
    assert result is not None
    assert result["threshold"] == pytest.approx(0.5)
    assert result["pooled_hit_rate"] == pytest.approx(0.65)


def test_real_market_thresholds_file_never_exceeds_hit_rate_floor_promise():
    """Integration check against the actual generated thresholds file."""
    from src.prediction_service.thresholds import load_thresholds
    thresholds = load_thresholds()
    for market, entry in thresholds["markets"].items():
        if entry.get("threshold") is not None:
            assert entry["pooled_hit_rate"] >= TARGET_HIT_RATE_FLOOR - 1e-9
            assert entry["pooled_n"] >= 40

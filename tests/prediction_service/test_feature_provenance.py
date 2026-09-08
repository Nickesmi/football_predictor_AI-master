"""
Regression tests for per-feature provenance tracking (Phase 4 §5),
added to src.prediction_service.feature_engine.generate_features().
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.prediction_service.data_contract import FeatureStatus
from src.prediction_service.feature_engine import generate_features
from src.ml.point_in_time import FEATURE_COLUMNS


def _toy_history():
    rows = [
        ("m1", "2024-01-01", "L", "S", "A", "B", 2, 0),
        ("m2", "2024-01-08", "L", "S", "B", "A", 1, 1),
    ]
    return pd.DataFrame(rows, columns=["match_id", "date", "league", "season", "home_team", "away_team", "home_goals", "away_goals"])


def test_every_feature_column_has_a_provenance_record():
    snap = generate_features("A", "B", "2024-02-01", "L", _toy_history())
    names = {p.feature_name for p in snap.provenance}
    assert names == set(FEATURE_COLUMNS)


def test_known_team_features_have_a_real_source_timestamp_before_prediction():
    snap = generate_features("A", "B", "2024-02-01", "L", _toy_history())
    home_scored = next(p for p in snap.provenance if p.feature_name == "home_scored_avg_5")
    assert home_scored.status == FeatureStatus.KNOWN
    assert home_scored.source_timestamp is not None
    assert pd.Timestamp(home_scored.source_timestamp) < pd.Timestamp("2024-02-01")


def test_cold_start_team_features_are_marked_unknown_not_given_a_fake_timestamp():
    snap = generate_features("Brand New FC", "A", "2024-02-01", "L", _toy_history())
    home_scored = next(p for p in snap.provenance if p.feature_name == "home_scored_avg_5")
    assert home_scored.status == FeatureStatus.UNKNOWN
    assert home_scored.source_timestamp is None
    # But the away team (real history) must still be KNOWN.
    away_scored = next(p for p in snap.provenance if p.feature_name == "away_scored_avg_5")
    assert away_scored.status == FeatureStatus.KNOWN


def test_provenance_self_check_would_reject_any_future_source_timestamp():
    """Structural guard: generate_features() calls assert_available_before
    on every provenance record before returning — if point_in_time.py ever
    regressed and let a future match leak in, this would raise instead of
    silently returning a bad snapshot."""
    snap = generate_features("A", "B", "2024-02-01", "L", _toy_history())
    for record in snap.provenance:
        record.assert_available_before(snap.prediction_timestamp)  # must not raise

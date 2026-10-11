"""
Regression tests for src/prediction_service/data_contract.py (Phase 4 §3/§5/§9).
"""

from __future__ import annotations

import pytest

from src.prediction_service.data_contract import (
    DataMode, ProviderHealthStatus, FeatureStatus, FeatureProvenance, OddsRecord, OddsStatus,
    LineupRecord, LineupAvailabilityStatus,
)


def test_every_provider_failure_mode_is_a_distinct_status():
    """§9: a network failure must never collapse into the same bucket as
    'no matches today' (UNAVAILABLE) or a stale cache (STALE_DATA)."""
    statuses = {s.value for s in ProviderHealthStatus}
    assert {"NETWORK_FAILURE", "AUTH_FAILURE", "RATE_LIMITED", "STALE_DATA",
            "INVALID_RESPONSE", "DEGRADED", "AVAILABLE", "UNAVAILABLE"} == statuses


def test_feature_provenance_rejects_source_timestamp_after_prediction_time():
    feature = FeatureProvenance(
        feature_name="home_elo_before", feature_value=1500.0, source="test",
        source_timestamp="2025-06-01T00:00:00Z", computed_timestamp="2025-05-01T00:00:00Z",
        status=FeatureStatus.KNOWN,
    )
    with pytest.raises(ValueError, match="leakage"):
        feature.assert_available_before("2025-05-01T00:00:00Z")


def test_feature_provenance_allows_source_timestamp_before_prediction_time():
    feature = FeatureProvenance(
        feature_name="home_elo_before", feature_value=1500.0, source="test",
        source_timestamp="2025-04-01T00:00:00Z", computed_timestamp="2025-05-01T00:00:00Z",
        status=FeatureStatus.KNOWN,
    )
    feature.assert_available_before("2025-05-01T00:00:00Z")  # must not raise


def test_unknown_status_feature_is_never_leakage_checked_it_simply_cannot_be_trusted():
    feature = FeatureProvenance(
        feature_name="x", feature_value=None, source="test", source_timestamp=None,
        computed_timestamp="2025-05-01T00:00:00Z", status=FeatureStatus.UNKNOWN,
    )
    feature.assert_available_before("2020-01-01T00:00:00Z")  # must not raise — nothing to check


def test_odds_unavailable_factory_never_fabricates_a_price():
    rec = OddsRecord.unavailable("fixture123")
    assert rec.status == OddsStatus.UNAVAILABLE
    assert rec.odds is None
    assert rec.source is None


def test_lineup_unknown_factory_is_not_no_injuries():
    rec = LineupRecord.unknown("fixture123")
    assert rec.status == LineupAvailabilityStatus.UNKNOWN


def test_data_mode_has_no_implicit_default_the_enum_forces_a_choice():
    assert set(DataMode) == {DataMode.LIVE, DataMode.REPLAY, DataMode.MANUAL, DataMode.UNAVAILABLE}

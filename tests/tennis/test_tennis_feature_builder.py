"""
test_tennis_feature_builder.py
================================
Tests for the V1 tennis feature builder.
Verifies graceful degradation, correct feature names, and data quality scoring.
"""
import pytest
from src.tennis.ml.tennis_feature_builder import (
    build_features,
    _rank_bucket,
    _tournament_tier,
)
from src.tennis.data.tennis_provider import _normalize_surface


class TestRankBucket:
    def test_top10(self):     assert _rank_bucket(5) == 4
    def test_top50(self):     assert _rank_bucket(30) == 3
    def test_top100(self):    assert _rank_bucket(75) == 2
    def test_outside100(self): assert _rank_bucket(200) == 1
    def test_unknown(self):   assert _rank_bucket(None) == 0


class TestTournamentTier:
    def test_grand_slam(self):   assert _tournament_tier("Wimbledon Grand Slam") == 4
    def test_atp_1000(self):     assert _tournament_tier("ATP 1000 Masters") == 3
    def test_challenger(self):   assert _tournament_tier("ATP Challenger") == 0
    def test_none(self):         assert _tournament_tier(None) == 1  # default


class TestBuildFeatures:
    def test_returns_all_required_keys(self, tennis_db):
        features = build_features(
            tennis_db, "Federer", "Nadal", "grass",
            tournament="Wimbledon", rank_1=5, rank_2=2
        )
        required = [
            "elo_1", "elo_2", "elo_diff",
            "rank_1", "rank_2", "rank_diff",
            "rank_bucket_1", "rank_bucket_2",
            "win_rate_last5_p1", "win_rate_last10_p1",
            "win_rate_last5_p2", "win_rate_last10_p2",
            "surface_win_rate_p1", "surface_win_rate_p2",
            "h2h_total", "h2h_win_p1", "h2h_recent3",
            "fatigue_matches_p1", "fatigue_sets_p1",
            "fatigue_matches_p2", "fatigue_sets_p2",
            "tournament_tier", "best_of", "surface",
            "data_quality_score", "missing_features",
        ]
        for key in required:
            assert key in features, f"Missing feature: {key}"

    def test_new_players_degrade_gracefully(self, tennis_db):
        """Should not raise for players with no history."""
        features = build_features(tennis_db, "Unknown Player A", "Unknown Player B", "hard")
        assert features["data_quality_score"] == 25
        assert features["missing_features"] == [
            "elo_p1_new_player",
            "elo_p2_new_player",
            "rank_p1",
            "rank_p2",
            "form_last5_p1",
            "form_last10_p1",
            "form_last5_p2",
            "form_last10_p2",
            "surface_form_p1",
            "surface_form_p2",
            "h2h",
        ]
        assert features["win_rate_last5_p1"] is None
        assert features["surface_win_rate_p2"] is None
        assert features["h2h_total"] == 0

    def test_elo_defaults_for_new_player(self, tennis_db):
        features = build_features(tennis_db, "NewPlayer1", "NewPlayer2", "clay")
        assert features["elo_1"] == 1500.0
        assert features["elo_2"] == 1500.0
        assert features["elo_diff"] == 0.0

    def test_no_serve_stats_in_v1(self, tennis_db):
        """Serve/return stats must NOT appear in V1 features."""
        features = build_features(tennis_db, "P1", "P2", "hard")
        forbidden = ["ace_rate", "double_fault_rate", "first_serve_pct", "break_conversion"]
        for key in forbidden:
            assert key not in features, f"Forbidden feature present: {key}"

    def test_data_quality_ranges_0_to_100(self, tennis_db):
        features = build_features(tennis_db, "P1", "P2", "hard")
        assert 0 <= features["data_quality_score"] <= 100

    def test_surface_stored_in_features(self, tennis_db):
        features = build_features(tennis_db, "P1", "P2", "clay")
        assert features["surface"] == "clay"

    def test_fatigue_zero_for_new_player(self, tennis_db):
        features = build_features(tennis_db, "BrandNew", "AlsoNew", "grass")
        assert features["fatigue_matches_p1"] == 0
        assert features["fatigue_matches_p2"] == 0

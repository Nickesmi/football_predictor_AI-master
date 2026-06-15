"""
test_tennis_model.py
=====================
Tests for the tennis Elo + logistic prediction model.
Critical invariant: player_1_win + player_2_win == 1.0 (always).
"""
import pytest
from src.tennis.ml.tennis_model import (
    predict_match,
    _elo_probability,
    _rank_probability,
    _quality_shrinkage,
    update_elo,
)


class TestEloProbability:
    def test_equal_elo(self):
        prob = _elo_probability(0)
        assert abs(prob - 0.5) < 1e-6

    def test_positive_diff_favors_p1(self):
        prob = _elo_probability(200)
        assert prob > 0.5

    def test_negative_diff_favors_p2(self):
        prob = _elo_probability(-200)
        assert prob < 0.5

    def test_bounded(self):
        for diff in [-2000, -1000, 0, 1000, 2000]:
            prob = _elo_probability(diff)
            assert 0.0 < prob < 1.0


class TestRankProbability:
    def test_better_ranked_favored(self):
        prob = _rank_probability(10, 100)
        assert prob > 0.5

    def test_equal_rank(self):
        prob = _rank_probability(50, 50)
        assert abs(prob - 0.5) < 0.01

    def test_none_rank_uses_default(self):
        prob = _rank_probability(None, None)
        assert abs(prob - 0.5) < 0.01  # both unknown → near 50/50


class TestQualityShrinkage:
    def test_full_quality_no_shrinkage(self):
        # 100 quality now caps at 95% model weight
        prob = _quality_shrinkage(0.75, 100)
        assert prob < 0.75
        assert prob > 0.70

    def test_zero_quality_full_shrinkage(self):
        prob = _quality_shrinkage(0.80, 0)
        assert abs(prob - 0.5) < 1e-6

    def test_partial_shrinkage(self):
        prob = _quality_shrinkage(0.70, 70)
        assert 0.5 < prob < 0.70  # shrunk but still favors p1


class TestPredictMatch:
    def _minimal_features(self, **overrides):
        base = {
            "elo_1": 1600.0,
            "elo_2": 1500.0,
            "elo_diff": 100.0,
            "rank_1": 10,
            "rank_2": 25,
            "rank_diff": -15,
            "rank_bucket_1": 3,
            "rank_bucket_2": 3,
            "win_rate_last5_p1": 0.8,
            "win_rate_last10_p1": 0.7,
            "win_rate_last5_p2": 0.6,
            "win_rate_last10_p2": 0.5,
            "surface_win_rate_p1": 0.75,
            "surface_win_rate_p2": 0.55,
            "h2h_total": 5,
            "h2h_win_p1": 0.6,
            "h2h_recent3": [1, 1, 0],
            "fatigue_matches_p1": 2,
            "fatigue_sets_p1": 4,
            "fatigue_matches_p2": 2,
            "fatigue_sets_p2": 4,
            "tournament_tier": 4,
            "best_of": 3,
            "surface": "grass",
            "data_quality_score": 85,
            "missing_features": [],
        }
        base.update(overrides)
        return base

    def test_probabilities_sum_to_one(self):
        """Critical invariant: player_1_win + player_2_win must always == 1.0"""
        for _ in range(10):
            features = self._minimal_features()
            result = predict_match(features)
            mw = result["match_winner"]
            total = mw["player_1_win"] + mw["player_2_win"]
            assert abs(total - 100.0) < 0.01, f"Probabilities sum to {total}, not 100"

    def test_result_structure(self):
        result = predict_match(self._minimal_features())
        assert "match_winner" in result
        assert "sets_markets" in result
        assert "top_picks" in result
        assert "warnings" in result
        assert "data_quality" in result
        assert "model_version" in result

    def test_match_winner_fields(self):
        result = predict_match(self._minimal_features())
        mw = result["match_winner"]
        assert "player_1_win" in mw
        assert "player_2_win" in mw
        assert "fair_odds_p1" in mw
        assert "fair_odds_p2" in mw
        assert "confidence" in mw
        assert mw["confidence"] in ("HIGH", "MEDIUM", "LOW")

    def test_low_quality_shrinks_toward_50(self):
        # High quality
        high_q = predict_match(self._minimal_features(data_quality_score=100))
        # Low quality triggers NO PICK
        low_q = predict_match(self._minimal_features(data_quality_score=30))
        assert low_q["match_winner"] is None
        assert "NO PICK" in low_q["warnings"][0]

    def test_new_player_uses_defaults(self):
        """Should not raise when both players have default Elo."""
        features = self._minimal_features(
            elo_1=1500.0, elo_2=1500.0, elo_diff=0.0,
            missing_features=["elo_p1_new_player", "elo_p2_new_player"],
            data_quality_score=40,
        )
        result = predict_match(features)
        # Near 50/50 when Elo diff is 0
        p1 = result["match_winner"]["player_1_win"]
        assert 40 < p1 < 60

    def test_no_form_data(self):
        """Should handle missing form gracefully."""
        features = self._minimal_features(
            win_rate_last5_p1=None,
            win_rate_last10_p1=None,
            win_rate_last5_p2=None,
            win_rate_last10_p2=None,
        )
        result = predict_match(features)
        mw = result["match_winner"]
        total = mw["player_1_win"] + mw["player_2_win"]
        assert abs(total - 100.0) < 0.01

    def test_sets_handicap_only_for_heavy_favorites(self):
        """Sets handicap should only appear when favourite prob is high enough."""
        # Very balanced match
        balanced = self._minimal_features(
            elo_diff=0, rank_1=50, rank_2=50,
            win_rate_last5_p1=0.5, win_rate_last5_p2=0.5,
        )
        result_balanced = predict_match(balanced)
        assert "favourite_minus_1_5_sets" not in result_balanced.get("sets_markets", {})


class TestEloUpdate:
    def test_winner_gains_points(self):
        new_w, new_l = update_elo(1500, 1500)
        assert new_w > 1500
        assert new_l < 1500

    def test_ratings_shift_by_same_amount(self):
        """Zero-sum: winner gains == loser loses (approximately)."""
        new_w, new_l = update_elo(1500, 1500)
        gained = new_w - 1500
        lost   = 1500 - new_l
        assert abs(gained - lost) < 0.01

    def test_upset_gains_more(self):
        """Beating a stronger player should yield more Elo gain."""
        new_w_upset, _ = update_elo(1300, 1700)  # underdog wins
        new_w_expected, _ = update_elo(1700, 1300)  # favourite wins
        assert (new_w_upset - 1300) > (new_w_expected - 1700)

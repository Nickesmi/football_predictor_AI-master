"""
test_tennis_provider.py
========================
Tests for tennis data provider normalization and STALE logic.
"""
import pytest
from src.tennis.data.tennis_provider import (
    _normalize_match,
    _normalize_status,
    _normalize_surface,
    apply_live_overlay,
    mark_all_stale,
)


class TestNormalizeStatus:
    def test_not_started(self):
        assert _normalize_status("notstarted") == "NS"
        assert _normalize_status("scheduled") == "NS"

    def test_finished(self):
        assert _normalize_status("finished") == "FT"
        assert _normalize_status("FT") == "FT"
        assert _normalize_status("ended") == "FT"

    def test_live(self):
        assert _normalize_status("inprogress") == "LIVE"
        assert _normalize_status("playing") == "LIVE"

    def test_unknown_defaults_ns(self):
        assert _normalize_status("some_unknown_status") == "NS"


class TestNormalizeSurface:
    def test_clay(self):
        assert _normalize_surface("Clay") == "clay"
        assert _normalize_surface("CLAY court") == "clay"

    def test_grass(self):
        assert _normalize_surface("Grass") == "grass"

    def test_hard(self):
        assert _normalize_surface("Hard") == "hard"
        assert _normalize_surface("Indoor") == "hard"

    def test_unknown(self):
        assert _normalize_surface("") == "unknown"


class TestNormalizeMatch:
    def test_basic_normalization(self):
        raw = {
            "id": "match_42",
            "homeTeam": {"name": "Roger Federer", "ranking": 5},
            "awayTeam": {"name": "Rafael Nadal", "ranking": 3},
            "startTimestamp": "1749999600",  # unix ts (string)
            "status": {"type": "notstarted"},
            "tournament": "Wimbledon",
            "surface": "Grass",
        }
        result = _normalize_match(raw)

        assert result["match_id"] == "match_42"
        assert result["player_1"] == "Roger Federer"
        assert result["player_2"] == "Rafael Nadal"
        assert result["rank_1"] == 5
        assert result["rank_2"] == 3
        assert result["surface"] == "grass"
        assert result["status"] == "NS"
        assert result["sport"] == "tennis"
        assert result["provider"] is not None
        assert result["is_stale"] == False

    def test_schema_completeness(self):
        """All required fields must be present in normalized output."""
        required_keys = [
            "sport", "match_id", "provider", "date", "start_time",
            "tournament", "surface", "player_1", "player_2",
            "rank_1", "rank_2", "status", "sets_1", "sets_2",
            "games_1", "games_2", "point_score", "is_stale",
            "provider_error", "last_live_update",
        ]
        result = _normalize_match({"id": "x", "homeTeam": {"name": "P1"}, "awayTeam": {"name": "P2"}})
        for key in required_keys:
            assert key in result, f"Missing required key: {key}"

    def test_missing_players_handled(self):
        """Should not raise on minimal input."""
        result = _normalize_match({})
        assert result["player_1"] == ""
        assert result["player_2"] == ""


class TestApplyLiveOverlay:
    def test_overlay_only_touches_allowed_fields(self, sample_match):
        """Live overlay must ONLY update: status, sets, games, point_score, last_live_update, is_stale"""
        base = [dict(sample_match)]
        live = [{
            **sample_match,
            "match_id": "test_001",
            "status": "LIVE",
            "sets_1": 1,
            "sets_2": 0,
            "games_1": 3,
            "games_2": 2,
            "tournament": "INJECTED_TOURNAMENT",  # must NOT be applied
        }]
        result = apply_live_overlay(base, live)

        assert result[0]["status"] == "LIVE"
        assert result[0]["sets_1"] == 1
        assert result[0]["tournament"] == "Wimbledon"      # unchanged
        assert result[0]["is_stale"] == False

    def test_missing_live_match_unchanged(self, sample_match):
        """Matches not in live feed remain unchanged."""
        base = [dict(sample_match)]
        live = []
        result = apply_live_overlay(base, live)
        assert result[0]["status"] == "NS"


class TestMarkAllStale:
    def test_marks_all_matches_stale(self, sample_match):
        matches = [dict(sample_match), dict(sample_match)]
        result = mark_all_stale(matches, "timeout")
        assert all(m["is_stale"] for m in result)
        assert all(m["provider_error"] == "timeout" for m in result)

    def test_original_not_mutated(self, sample_match):
        matches = [dict(sample_match)]
        mark_all_stale(matches, "err")
        assert matches[0]["is_stale"] == False  # original unchanged

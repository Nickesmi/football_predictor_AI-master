"""
Tests for the data fetching layer (Issue #1).

These tests use mocked API responses so they run without
an API key and without network access.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.data.api_client import APIFootballClient
from src.data.api_football_fetcher import APIFootballFetcher
from src.models.match import MatchResult, TeamMatchSet, GoalEvent, CardEvent


# ==================================================================
# Fixtures  –  sample API responses
# ==================================================================

SAMPLE_FIXTURE_HOME = {
    "fixture": {
        "id": 1001,
        "date": "2024-10-05T15:00:00+00:00",
        "status": {"long": "Match Finished", "short": "FT", "elapsed": 90},
    },
    "league": {
        "id": 39,
        "name": "Premier League",
        "season": 2024,
        "round": "Regular Season - 7",
    },
    "teams": {
        "home": {"id": 33, "name": "Manchester United"},
        "away": {"id": 49, "name": "Chelsea"},
    },
    "goals": {"home": 2, "away": 1},
    "score": {
        "halftime": {"home": 1, "away": 0},
        "fulltime": {"home": 2, "away": 1},
    },
    "events": [
        {
            "type": "Goal",
            "detail": "Normal Goal",
            "time": {"elapsed": 23},
            "player": {"name": "M. Rashford"},
            "assist": {"name": "B. Fernandes"},
            "team": {"id": 33},
        },
        {
            "type": "Goal",
            "detail": "Normal Goal",
            "time": {"elapsed": 55},
            "player": {"name": "B. Fernandes"},
            "assist": {"name": None},
            "team": {"id": 33},
        },
        {
            "type": "Goal",
            "detail": "Normal Goal",
            "time": {"elapsed": 78},
            "player": {"name": "C. Palmer"},
            "assist": {"name": "N. Jackson"},
            "team": {"id": 49},
        },
        {
            "type": "Card",
            "detail": "Yellow Card",
            "time": {"elapsed": 30},
            "player": {"name": "Casemiro"},
            "assist": {"name": None},
            "team": {"id": 33},
        },
        {
            "type": "Card",
            "detail": "Yellow Card",
            "time": {"elapsed": 65},
            "player": {"name": "M. Caicedo"},
            "assist": {"name": None},
            "team": {"id": 49},
        },
    ],
    "statistics": [
        {
            "team": {"id": 33},
            "statistics": [
                {"type": "Corner Kicks", "value": 5},
                {"type": "Total Shots", "value": 12},
                {"type": "Shots on Goal", "value": 6},
                {"type": "Fouls", "value": 8},
                {"type": "Yellow Cards", "value": 1},
                {"type": "Red Cards", "value": 0},
                {"type": "Ball Possession", "value": "55%"},
            ],
        },
        {
            "team": {"id": 49},
            "statistics": [
                {"type": "Corner Kicks", "value": 3},
                {"type": "Total Shots", "value": 8},
                {"type": "Shots on Goal", "value": 4},
                {"type": "Fouls", "value": 10},
                {"type": "Yellow Cards", "value": 1},
                {"type": "Red Cards", "value": 0},
                {"type": "Ball Possession", "value": "45%"},
            ],
        },
    ],
}

SAMPLE_FIXTURE_AWAY = {
    "fixture": {
        "id": 1002,
        "date": "2024-10-19T12:30:00+00:00",
        "status": {"long": "Match Finished", "short": "FT", "elapsed": 90},
    },
    "league": {
        "id": 39,
        "name": "Premier League",
        "season": 2024,
        "round": "Regular Season - 8",
    },
    "teams": {
        "home": {"id": 40, "name": "Liverpool"},
        "away": {"id": 33, "name": "Manchester United"},
    },
    "goals": {"home": 3, "away": 0},
    "score": {
        "halftime": {"home": 1, "away": 0},
        "fulltime": {"home": 3, "away": 0},
    },
    "events": [],
    "statistics": [],
}

# Not finished – should be filtered out
SAMPLE_FIXTURE_NOT_STARTED = {
    "fixture": {
        "id": 1003,
        "date": "2024-11-02T15:00:00+00:00",
        "status": {"long": "Not Started", "short": "NS", "elapsed": None},
    },
    "league": {
        "id": 39,
        "name": "Premier League",
        "season": 2024,
        "round": "Regular Season - 10",
    },
    "teams": {
        "home": {"id": 33, "name": "Manchester United"},
        "away": {"id": 42, "name": "Arsenal"},
    },
    "goals": {"home": None, "away": None},
    "score": {
        "halftime": {"home": None, "away": None},
        "fulltime": {"home": None, "away": None},
    },
    "events": [],
    "statistics": [],
}


def _make_api_response(*fixtures) -> dict:
    """Wrap fixtures in an API-Football v3 response envelope."""
    return {
        "get": "fixtures",
        "parameters": {},
        "errors": [],
        "results": len(fixtures),
        "paging": {"current": 1, "total": 1},
        "response": list(fixtures),
    }


# ==================================================================
# MatchResult model tests
# ==================================================================


class TestMatchResult:
    """Tests for the MatchResult dataclass."""

    def test_derived_fields(self):
        m = MatchResult(
            match_id="1",
            match_date=date(2024, 10, 5),
            league_id="39",
            league_name="Premier League",
            season="2024",
            home_score_ft=2,
            away_score_ft=1,
            home_score_ht=1,
            away_score_ht=0,
        )
        assert m.total_goals_ft == 3
        assert m.total_goals_ht == 1
        assert m.btts is True
        assert m.home_win is True
        assert m.away_win is False
        assert m.draw is False
        assert m.ft_result == "1"
        assert m.ht_result == "1"
        assert m.over_2_5 is True
        assert m.over_3_5 is False

    def test_draw_match(self):
        m = MatchResult(
            match_id="2",
            match_date=date(2024, 10, 5),
            league_id="39",
            league_name="Premier League",
            season="2024",
            home_score_ft=1,
            away_score_ft=1,
            home_score_ht=0,
            away_score_ht=1,
        )
        assert m.draw is True
        assert m.ft_result == "X"
        assert m.ht_result == "2"
        assert m.btts is True

    def test_clean_sheet(self):
        m = MatchResult(
            match_id="3",
            match_date=date(2024, 10, 5),
            league_id="39",
            league_name="Premier League",
            season="2024",
            home_score_ft=0,
            away_score_ft=2,
        )
        assert m.btts is False
        assert m.away_clean_sheet is True
        assert m.home_clean_sheet is False

    def test_no_halftime_score(self):
        m = MatchResult(
            match_id="4",
            match_date=date(2024, 10, 5),
            league_id="39",
            league_name="Premier League",
            season="2024",
            home_score_ft=1,
            away_score_ft=0,
        )
        assert m.ht_result == "?"
        assert m.total_goals_ht == 0


# ==================================================================
# APIFootballFetcher  –  parsing tests
# ==================================================================


class TestAPIFootballFetcher:
    """Tests for the fetcher / parser layer."""

    def setup_method(self):
        self.mock_client = MagicMock(spec=APIFootballClient)
        self.fetcher = APIFootballFetcher(client=self.mock_client)

    def test_parse_home_matches(self):
        """Fetching home matches filters correctly."""
        self.mock_client.get.return_value = _make_api_response(
            SAMPLE_FIXTURE_HOME,
            SAMPLE_FIXTURE_AWAY,
            SAMPLE_FIXTURE_NOT_STARTED,
        )

        result = self.fetcher.fetch_team_home_matches(
            team_id=33, league_id=39, season=2024
        )

        assert isinstance(result, TeamMatchSet)
        assert result.context == "home"
        assert result.total_matches == 1  # Only fixture 1001
        assert result.matches[0].home_team_name == "Manchester United"
        assert result.matches[0].home_score_ft == 2
        assert result.matches[0].away_score_ft == 1

    def test_parse_away_matches(self):
        """Fetching away matches filters correctly."""
        self.mock_client.get.return_value = _make_api_response(
            SAMPLE_FIXTURE_HOME,
            SAMPLE_FIXTURE_AWAY,
            SAMPLE_FIXTURE_NOT_STARTED,
        )

        result = self.fetcher.fetch_team_away_matches(
            team_id=33, league_id=39, season=2024
        )

        assert isinstance(result, TeamMatchSet)
        assert result.context == "away"
        assert result.total_matches == 1  # Only fixture 1002
        assert result.matches[0].away_team_name == "Manchester United"

    def test_unfinished_matches_filtered(self):
        """Matches with status != FT are excluded."""
        self.mock_client.get.return_value = _make_api_response(
            SAMPLE_FIXTURE_NOT_STARTED,
        )

        result = self.fetcher.fetch_team_home_matches(
            team_id=33, league_id=39, season=2024
        )

        assert result.total_matches == 0

    def test_goals_parsed(self):
        """Goal events are parsed correctly."""
        self.mock_client.get.return_value = _make_api_response(
            SAMPLE_FIXTURE_HOME,
        )

        result = self.fetcher.fetch_team_home_matches(
            team_id=33, league_id=39, season=2024
        )
        match = result.matches[0]

        assert len(match.goals) == 3
        assert match.goals[0].scorer == "M. Rashford"
        assert match.goals[0].assist == "B. Fernandes"
        assert match.goals[0].is_home is True
        assert match.goals[0].half == "1st Half"
        assert match.goals[2].scorer == "C. Palmer"
        assert match.goals[2].is_home is False

    def test_cards_parsed(self):
        """Card events are parsed correctly."""
        self.mock_client.get.return_value = _make_api_response(
            SAMPLE_FIXTURE_HOME,
        )

        result = self.fetcher.fetch_team_home_matches(
            team_id=33, league_id=39, season=2024
        )
        match = result.matches[0]

        assert len(match.cards) == 2
        assert match.cards[0].player == "Casemiro"
        assert match.cards[0].card_type == "yellow"
        assert match.cards[0].is_home is True

    def test_statistics_parsed(self):
        """Match statistics are parsed correctly."""
        self.mock_client.get.return_value = _make_api_response(
            SAMPLE_FIXTURE_HOME,
        )

        result = self.fetcher.fetch_team_home_matches(
            team_id=33, league_id=39, season=2024
        )
        stats = result.matches[0].statistics

        assert hasattr(stats, 'corners_home')
        assert stats.corners_home == 5
        assert stats.corners_away == 3
        assert stats.possession_home == "55%"
        assert stats.yellow_cards_home == 1

    def test_halftime_scores_parsed(self):
        """Half-time scores are extracted from the score object."""
        self.mock_client.get.return_value = _make_api_response(
            SAMPLE_FIXTURE_HOME,
        )

        result = self.fetcher.fetch_team_home_matches(
            team_id=33, league_id=39, season=2024
        )
        match = result.matches[0]

        assert match.home_score_ht == 1
        assert match.away_score_ht == 0
        assert match.ht_result == "1"

    def test_fetch_match_context(self):
        """fetch_match_context returns both home and away sets."""
        self.mock_client.get.return_value = _make_api_response(
            SAMPLE_FIXTURE_HOME,
            SAMPLE_FIXTURE_AWAY,
        )

        home_set, away_set = self.fetcher.fetch_match_context(
            home_team_id=33,
            away_team_id=49,
            league_id=39,
            season=2024,
        )

        assert home_set.context == "home"
        assert away_set.context == "away"


# ==================================================================
# API Client cache tests
# ==================================================================


class TestAPIClientCache:
    """Tests for the cache layer."""

    def test_cache_hit(self, tmp_path):
        """Cached responses are returned without hitting the network."""
        client = APIFootballClient(
            api_key="test-key",
            cache_dir=tmp_path,
            cache_ttl=3600,
        )

        # Pre-populate cache
        cache_key = client._make_cache_key("fixtures", {"team": 33})
        cache_data = _make_api_response(SAMPLE_FIXTURE_HOME)
        client._write_cache(cache_key, cache_data)

        # Should return from cache without HTTP call
        with patch.object(client._session, "get") as mock_get:
            result = client.get("fixtures", team=33)
            mock_get.assert_not_called()

        assert result["results"] == 1

    def test_clear_cache(self, tmp_path):
        """clear_cache removes all cached files."""
        client = APIFootballClient(api_key="test", cache_dir=tmp_path)
        (tmp_path / "abc.json").write_text("{}")
        (tmp_path / "def.json").write_text("{}")

        removed = client.clear_cache()
        assert removed == 2
        assert list(tmp_path.glob("*.json")) == []


# ==================================================================
# Search helpers
# ==================================================================


class TestSearchHelpers:
    """Tests for team/league search methods."""

    def setup_method(self):
        self.mock_client = MagicMock(spec=APIFootballClient)
        self.fetcher = APIFootballFetcher(client=self.mock_client)

    def test_search_team(self):
        self.mock_client.get.return_value = {
            "response": [
                {
                    "team": {
                        "id": 33,
                        "name": "Manchester United",
                        "code": "MUN",
                        "country": "England",
                        "logo": "https://example.com/mun.png",
                    }
                }
            ]
        }

        results = self.fetcher.search_team("Manchester United")
        assert len(results) == 1
        assert results[0]["id"] == 33
        assert results[0]["name"] == "Manchester United"

    def test_search_league(self):
        self.mock_client.get.return_value = {
            "response": [
                {
                    "league": {
                        "id": 39,
                        "name": "Premier League",
                        "type": "League",
                        "logo": "https://example.com/pl.png",
                    },
                    "country": {"name": "England"},
                    "seasons": [{"year": 2023}, {"year": 2024}],
                }
            ]
        }

        results = self.fetcher.search_league("Premier League", country="England")
        assert len(results) == 1
        assert results[0]["id"] == 39
        assert results[0]["seasons"] == [2023, 2024]

    def test_get_league_teams(self):
        self.mock_client.get.return_value = {
            "response": [
                {"team": {"id": 33, "name": "Manchester United", "code": "MUN", "country": "England", "logo": ""}},
                {"team": {"id": 49, "name": "Chelsea", "code": "CHE", "country": "England", "logo": ""}},
            ]
        }

        results = self.fetcher.get_league_teams(league_id=39, season=2024)
        assert len(results) == 2

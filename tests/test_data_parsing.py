"""
Unit Tests for Data Parsing Logic (Issue #5 — Part 1).

Deep tests for the API-Football JSON parsing layer:
  - _parse_single_fixture edge cases
  - _parse_statistics with missing/malformed data
  - GoalEvent / CardEvent half detection
  - MatchResult derived properties exhaustively
  - Date parsing, status filtering, team ID mapping
"""

from __future__ import annotations

from datetime import date

import pytest

from src.data.api_football_fetcher import APIFootballFetcher
from src.models.match import (
    CardEvent,
    GoalEvent,
    MatchResult,
    MatchStatistics,
    TeamMatchSet,
)


# ==================================================================
# Realistic API fixture builder
# ==================================================================

def _api_fixture(
    fixture_id: int = 1001,
    status: str = "FT",
    date_str: str = "2024-10-15T20:00:00+00:00",
    home_id: int = 33,
    home_name: str = "Man United",
    away_id: int = 49,
    away_name: str = "Chelsea",
    league_id: int = 39,
    league_name: str = "Premier League",
    season: int = 2024,
    round_name: str = "Regular Season - 8",
    goals_home: int = 2,
    goals_away: int = 1,
    ht_home: int = 1,
    ht_away: int = 0,
    events: list = None,
    statistics: list = None,
) -> dict:
    """Build a realistic API-Football v3 fixture JSON."""
    return {
        "fixture": {
            "id": fixture_id,
            "date": date_str,
            "status": {"short": status},
        },
        "league": {
            "id": league_id,
            "name": league_name,
            "season": season,
            "round": round_name,
        },
        "teams": {
            "home": {"id": home_id, "name": home_name},
            "away": {"id": away_id, "name": away_name},
        },
        "goals": {"home": goals_home, "away": goals_away},
        "score": {
            "halftime": {"home": ht_home, "away": ht_away},
            "fulltime": {"home": goals_home, "away": goals_away},
        },
        "events": events or [],
        "statistics": statistics or [],
    }


def _api_event(
    type_: str = "Goal",
    detail: str = "Normal Goal",
    minute: int = 55,
    player_name: str = "Player",
    team_id: int = 33,
    assist_name: str = None,
) -> dict:
    """Build an API event JSON."""
    event = {
        "type": type_,
        "detail": detail,
        "time": {"elapsed": minute},
        "player": {"name": player_name},
        "assist": {"name": assist_name},
        "team": {"id": team_id},
    }
    return event


def _api_statistics(
    corners_home: int = 5, corners_away: int = 3,
    shots_home: int = 12, shots_away: int = 8,
    fouls_home: int = 10, fouls_away: int = 13,
    yellows_home: int = 2, yellows_away: int = 3,
    reds_home: int = 0, reds_away: int = 0,
    possession_home: str = "55%", possession_away: str = "45%",
) -> list:
    """Build API statistics JSON for both teams."""
    def _make_stats(corners, shots, fouls, yellows, reds, poss):
        return {
            "statistics": [
                {"type": "Corner Kicks", "value": corners},
                {"type": "Total Shots", "value": shots},
                {"type": "Shots on Goal", "value": int(shots * 0.4)},
                {"type": "Fouls", "value": fouls},
                {"type": "Yellow Cards", "value": yellows},
                {"type": "Red Cards", "value": reds},
                {"type": "Ball Possession", "value": poss},
            ]
        }
    return [
        _make_stats(corners_home, shots_home, fouls_home, yellows_home, reds_home, possession_home),
        _make_stats(corners_away, shots_away, fouls_away, yellows_away, reds_away, possession_away),
    ]


# ==================================================================
# Fixture status filtering
# ==================================================================


class TestFixtureStatusFiltering:
    """Tests that only finished matches are parsed."""

    def setup_method(self):
        self.fetcher = APIFootballFetcher.__new__(APIFootballFetcher)

    def test_ft_status_parsed(self):
        fix = _api_fixture(status="FT")
        result = self.fetcher._parse_single_fixture(fix)
        assert result.match_id == '1001'

    def test_aet_status_parsed(self):
        fix = _api_fixture(status="AET")
        result = self.fetcher._parse_single_fixture(fix)
        assert result.match_id == '1001'

    def test_pen_status_parsed(self):
        fix = _api_fixture(status="PEN")
        result = self.fetcher._parse_single_fixture(fix)
        assert result.match_id == '1001'

    def test_ns_status_rejected(self):
        fix = _api_fixture(status="NS")
        result = self.fetcher._parse_single_fixture(fix)
        assert result is None

    def test_1h_status_rejected(self):
        fix = _api_fixture(status="1H")
        result = self.fetcher._parse_single_fixture(fix)
        assert result is None

    def test_pst_status_rejected(self):
        fix = _api_fixture(status="PST")
        result = self.fetcher._parse_single_fixture(fix)
        assert result is None

    def test_canc_status_rejected(self):
        fix = _api_fixture(status="CANC")
        result = self.fetcher._parse_single_fixture(fix)
        assert result is None

    def test_empty_status_rejected(self):
        fix = _api_fixture(status="")
        result = self.fetcher._parse_single_fixture(fix)
        assert result is None


# ==================================================================
# Date & metadata parsing
# ==================================================================


class TestMetadataParsing:
    def setup_method(self):
        self.fetcher = APIFootballFetcher.__new__(APIFootballFetcher)

    def test_date_parsing(self):
        fix = _api_fixture(date_str="2024-11-23T15:00:00+00:00")
        result = self.fetcher._parse_single_fixture(fix)
        assert result.match_date == date(2024, 11, 23)

    def test_team_ids_mapped(self):
        fix = _api_fixture(home_id=100, away_id=200)
        result = self.fetcher._parse_single_fixture(fix)
        assert result.home_team_id == "100"
        assert result.away_team_id == "200"

    def test_league_info_mapped(self):
        fix = _api_fixture(league_id=140, league_name="La Liga", season=2023)
        result = self.fetcher._parse_single_fixture(fix)
        assert result.league_id == "140"
        assert result.league_name == "La Liga"
        assert result.season == "2023"

    def test_round_mapped(self):
        fix = _api_fixture(round_name="Matchday 15")
        result = self.fetcher._parse_single_fixture(fix)
        assert result.round == "Matchday 15"

    def test_scores_mapped(self):
        fix = _api_fixture(goals_home=3, goals_away=2, ht_home=1, ht_away=1)
        result = self.fetcher._parse_single_fixture(fix)
        assert result.home_score_ft == 3
        assert result.away_score_ft == 2
        assert result.home_score_ht == 1
        assert result.away_score_ht == 1


# ==================================================================
# Event parsing (goals & cards)
# ==================================================================


class TestGoalEventParsing:
    def setup_method(self):
        self.fetcher = APIFootballFetcher.__new__(APIFootballFetcher)

    def test_first_half_goal(self):
        events = [_api_event(minute=23, player_name="Rashford", team_id=33)]
        fix = _api_fixture(home_id=33, events=events)
        result = self.fetcher._parse_single_fixture(fix)
        assert len(result.goals) == 1
        assert result.goals[0].half == "1st Half"
        assert result.goals[0].minute == 23
        assert result.goals[0].scorer == "Rashford"
        assert result.goals[0].is_home is True

    def test_second_half_goal(self):
        events = [_api_event(minute=78, player_name="Sterling", team_id=49)]
        fix = _api_fixture(home_id=33, away_id=49, events=events)
        result = self.fetcher._parse_single_fixture(fix)
        assert result.goals[0].half == "2nd Half"
        assert result.goals[0].is_home is False

    def test_45th_minute_is_first_half(self):
        events = [_api_event(minute=45, team_id=33)]
        fix = _api_fixture(home_id=33, events=events)
        result = self.fetcher._parse_single_fixture(fix)
        assert result.goals[0].half == "1st Half"

    def test_46th_minute_is_second_half(self):
        events = [_api_event(minute=46, team_id=33)]
        fix = _api_fixture(home_id=33, events=events)
        result = self.fetcher._parse_single_fixture(fix)
        assert result.goals[0].half == "2nd Half"

    def test_missed_penalty_not_counted(self):
        events = [_api_event(detail="Missed Penalty", minute=30)]
        fix = _api_fixture(events=events)
        result = self.fetcher._parse_single_fixture(fix)
        assert len(result.goals) == 0

    def test_own_goal_counted(self):
        events = [_api_event(detail="Own Goal", minute=60, team_id=33)]
        fix = _api_fixture(home_id=33, events=events)
        result = self.fetcher._parse_single_fixture(fix)
        assert len(result.goals) == 1

    def test_multiple_goals_ordered(self):
        events = [
            _api_event(minute=10, player_name="A", team_id=33),
            _api_event(minute=25, player_name="B", team_id=49),
            _api_event(minute=60, player_name="C", team_id=33),
        ]
        fix = _api_fixture(home_id=33, away_id=49, events=events)
        result = self.fetcher._parse_single_fixture(fix)
        assert len(result.goals) == 3
        assert result.goals[0].scorer == "A"
        assert result.goals[1].is_home is False


class TestCardEventParsing:
    def setup_method(self):
        self.fetcher = APIFootballFetcher.__new__(APIFootballFetcher)

    def test_yellow_card_parsed(self):
        events = [_api_event(type_="Card", detail="Yellow Card", minute=30)]
        fix = _api_fixture(events=events)
        result = self.fetcher._parse_single_fixture(fix)
        assert len(result.cards) == 1
        assert result.cards[0].card_type == "yellow"

    def test_red_card_parsed(self):
        events = [_api_event(type_="Card", detail="Red Card", minute=88)]
        fix = _api_fixture(events=events)
        result = self.fetcher._parse_single_fixture(fix)
        assert result.cards[0].card_type == "red"

    def test_second_yellow_classified_as_yellow(self):
        """'Second Yellow card' contains 'yellow' so parser classifies as yellow.
        The subsequent red card would be a separate event from the API."""
        events = [_api_event(type_="Card", detail="Second Yellow card", minute=70)]
        fix = _api_fixture(events=events)
        result = self.fetcher._parse_single_fixture(fix)
        assert result.cards[0].card_type == "yellow"

    def test_card_half_detection(self):
        events = [
            _api_event(type_="Card", detail="Yellow Card", minute=20),
            _api_event(type_="Card", detail="Yellow Card", minute=65),
        ]
        fix = _api_fixture(events=events)
        result = self.fetcher._parse_single_fixture(fix)
        assert result.cards[0].half == "1st Half"
        assert result.cards[1].half == "2nd Half"


# ==================================================================
# Statistics parsing
# ==================================================================


class TestStatisticsParsing:
    def test_full_statistics(self):
        stats = _api_statistics(
            corners_home=6, corners_away=4,
            yellows_home=3, yellows_away=2,
            reds_home=1, reds_away=0,
        )
        result = APIFootballFetcher._parse_statistics(stats)
        assert result.corners_home == 6
        assert result.corners_home == 6
        assert result.corners_away == 4
        assert result.yellow_cards_home == 3
        assert result.yellow_cards_away == 2
        assert result.red_cards_home == 1

    def test_empty_statistics_returns_none(self):
        assert APIFootballFetcher._parse_statistics([]) is None

    def test_single_team_returns_none(self):
        stats = [{"statistics": []}]
        assert APIFootballFetcher._parse_statistics(stats) is None

    def test_none_values_default_to_zero(self):
        stats = [
            {"statistics": [{"type": "Corner Kicks", "value": None}]},
            {"statistics": [{"type": "Corner Kicks", "value": None}]},
        ]
        result = APIFootballFetcher._parse_statistics(stats)
        assert result.corners_home == 0
        assert result.corners_away == 0

    def test_percentage_values_stripped(self):
        stats = [
            {"statistics": [
                {"type": "Ball Possession", "value": "62%"},
                {"type": "Corner Kicks", "value": 5},
            ]},
            {"statistics": [
                {"type": "Ball Possession", "value": "38%"},
                {"type": "Corner Kicks", "value": 3},
            ]},
        ]
        result = APIFootballFetcher._parse_statistics(stats)
        assert result.possession_home == "62%"
        assert result.possession_away == "38%"

    def test_missing_stat_type_defaults_to_zero(self):
        stats = [
            {"statistics": [{"type": "Corner Kicks", "value": 5}]},
            {"statistics": [{"type": "Corner Kicks", "value": 3}]},
        ]
        result = APIFootballFetcher._parse_statistics(stats)
        # Yellow cards not in stats → should default to 0
        assert result.yellow_cards_home == 0


# ==================================================================
# MatchResult derived properties
# ==================================================================


class TestMatchResultDerived:
    def test_btts_true(self):
        m = MatchResult(match_id="1", match_date=date(2024, 1, 1),
                        league_id="1", league_name="L", season="2024",
                        home_score_ft=2, away_score_ft=1)
        assert m.btts is True

    def test_btts_false_nil_nil(self):
        m = MatchResult(match_id="1", match_date=date(2024, 1, 1),
                        league_id="1", league_name="L", season="2024",
                        home_score_ft=0, away_score_ft=0)
        assert m.btts is False

    def test_btts_false_one_side(self):
        m = MatchResult(match_id="1", match_date=date(2024, 1, 1),
                        league_id="1", league_name="L", season="2024",
                        home_score_ft=3, away_score_ft=0)
        assert m.btts is False

    def test_total_goals(self):
        m = MatchResult(match_id="1", match_date=date(2024, 1, 1),
                        league_id="1", league_name="L", season="2024",
                        home_score_ft=3, away_score_ft=2)
        assert m.total_goals_ft == 5

    def test_ht_goals(self):
        m = MatchResult(match_id="1", match_date=date(2024, 1, 1),
                        league_id="1", league_name="L", season="2024",
                        home_score_ht=1, away_score_ht=2)
        assert m.total_goals_ht == 3

    def test_ht_goals_none(self):
        m = MatchResult(match_id="1", match_date=date(2024, 1, 1),
                        league_id="1", league_name="L", season="2024")
        assert m.total_goals_ht == 0

    def test_home_win(self):
        m = MatchResult(match_id="1", match_date=date(2024, 1, 1),
                        league_id="1", league_name="L", season="2024",
                        home_score_ft=2, away_score_ft=1)
        assert m.home_win is True
        assert m.away_win is False
        assert m.draw is False
        assert m.ft_result == "1"

    def test_away_win(self):
        m = MatchResult(match_id="1", match_date=date(2024, 1, 1),
                        league_id="1", league_name="L", season="2024",
                        home_score_ft=0, away_score_ft=2)
        assert m.away_win is True
        assert m.ft_result == "2"

    def test_draw(self):
        m = MatchResult(match_id="1", match_date=date(2024, 1, 1),
                        league_id="1", league_name="L", season="2024",
                        home_score_ft=1, away_score_ft=1)
        assert m.draw is True
        assert m.ft_result == "X"

    def test_clean_sheets(self):
        m = MatchResult(match_id="1", match_date=date(2024, 1, 1),
                        league_id="1", league_name="L", season="2024",
                        home_score_ft=1, away_score_ft=0)
        assert m.home_clean_sheet is True
        assert m.away_clean_sheet is False

    def test_over_thresholds(self):
        m = MatchResult(match_id="1", match_date=date(2024, 1, 1),
                        league_id="1", league_name="L", season="2024",
                        home_score_ft=2, away_score_ft=1)
        assert m.over_1_5 is True
        assert m.over_2_5 is True
        assert m.over_3_5 is False

    def test_ht_result_home(self):
        m = MatchResult(match_id="1", match_date=date(2024, 1, 1),
                        league_id="1", league_name="L", season="2024",
                        home_score_ht=2, away_score_ht=0)
        assert m.ht_result == "1"

    def test_ht_result_away(self):
        m = MatchResult(match_id="1", match_date=date(2024, 1, 1),
                        league_id="1", league_name="L", season="2024",
                        home_score_ht=0, away_score_ht=1)
        assert m.ht_result == "2"

    def test_ht_result_draw(self):
        m = MatchResult(match_id="1", match_date=date(2024, 1, 1),
                        league_id="1", league_name="L", season="2024",
                        home_score_ht=1, away_score_ht=1)
        assert m.ht_result == "X"

    def test_ht_result_unknown(self):
        m = MatchResult(match_id="1", match_date=date(2024, 1, 1),
                        league_id="1", league_name="L", season="2024")
        assert m.ht_result == "?"


# ==================================================================
# Malformed / incomplete fixture data
# ==================================================================


class TestMalformedData:
    def setup_method(self):
        self.fetcher = APIFootballFetcher.__new__(APIFootballFetcher)

    def test_null_goals_treated_as_zero(self):
        fix = _api_fixture(goals_home=None, goals_away=None)
        # goals.get("home", 0) or 0 handles None
        result = self.fetcher._parse_single_fixture(fix)
        assert result.home_score_ft == 0
        assert result.away_score_ft == 0

    def test_null_ht_scores(self):
        fix = _api_fixture()
        fix["score"]["halftime"] = {"home": None, "away": None}
        result = self.fetcher._parse_single_fixture(fix)
        assert result.home_score_ht is None
        assert result.away_score_ht is None

    def test_missing_events_key(self):
        fix = _api_fixture()
        del fix["events"]
        result = self.fetcher._parse_single_fixture(fix)
        assert result.match_id == '1001'
        assert len(result.goals) == 0
        assert len(result.cards) == 0

    def test_event_with_null_time(self):
        events = [{
            "type": "Goal",
            "detail": "Normal Goal",
            "time": {"elapsed": None},
            "player": {"name": "Test"},
            "assist": {"name": None},
            "team": {"id": 33},
        }]
        fix = _api_fixture(home_id=33, events=events)
        result = self.fetcher._parse_single_fixture(fix)
        assert len(result.goals) == 1
        assert result.goals[0].minute == 0

    def test_fixtures_response_sorting(self):
        """_parse_fixtures should sort by date ascending."""
        raw = {
            "response": [
                _api_fixture(fixture_id=1, date_str="2024-11-20T20:00:00+00:00"),
                _api_fixture(fixture_id=2, date_str="2024-10-05T20:00:00+00:00"),
                _api_fixture(fixture_id=3, date_str="2024-12-01T20:00:00+00:00"),
            ]
        }
        results = self.fetcher._parse_fixtures(raw)
        assert len(results) == 3
        assert results[0].match_date < results[1].match_date
        assert results[1].match_date < results[2].match_date

    def test_parse_fixtures_skips_bad_entries(self):
        """Bad entries should be skipped, not crash the whole parse."""
        raw = {
            "response": [
                _api_fixture(fixture_id=1),   # good
                {"fixture": {"id": 999}},     # bad — no status
                _api_fixture(fixture_id=3),   # good
            ]
        }
        results = self.fetcher._parse_fixtures(raw)
        # Bad entry returns None (no status), so 2 results
        assert len(results) == 2

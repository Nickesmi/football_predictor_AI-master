"""Parser and convenience fetcher for API-Football fixture data."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from src.data.api_client import APIFootballClient
from src.models.match import (
    CardEvent,
    GoalEvent,
    MatchResult,
    MatchStatistics,
    TeamMatchSet,
)


FINISHED_STATUSES = {"FT", "AET", "PEN"}


class APIFootballFetcher:
    """Fetch and normalize API-Football v3 data into app domain models."""

    def __init__(self, client: Optional[APIFootballClient] = None):
        self.client = client or APIFootballClient()

    def fetch_team_home_matches(
        self,
        team_id: int | str,
        league_id: int | str,
        season: int | str,
    ) -> TeamMatchSet:
        data = self.client.get("fixtures", team=team_id, league=league_id, season=season)
        matches = [
            match
            for match in self._parse_fixtures(data)
            if match.home_team_id == str(team_id)
        ]
        return self._make_match_set(matches, team_id, league_id, season, "home")

    def fetch_team_away_matches(
        self,
        team_id: int | str,
        league_id: int | str,
        season: int | str,
    ) -> TeamMatchSet:
        data = self.client.get("fixtures", team=team_id, league=league_id, season=season)
        matches = [
            match
            for match in self._parse_fixtures(data)
            if match.away_team_id == str(team_id)
        ]
        return self._make_match_set(matches, team_id, league_id, season, "away")

    def fetch_match_context(
        self,
        home_team_id: int | str,
        away_team_id: int | str,
        league_id: int | str,
        season: int | str,
    ) -> tuple[TeamMatchSet, TeamMatchSet]:
        return (
            self.fetch_team_home_matches(home_team_id, league_id, season),
            self.fetch_team_away_matches(away_team_id, league_id, season),
        )

    def search_team(self, query: str) -> list[dict[str, Any]]:
        data = self.client.get("teams", search=query)
        results = []
        for item in data.get("response", []):
            team = item.get("team", {})
            results.append(
                {
                    "id": team.get("id"),
                    "name": team.get("name", ""),
                    "code": team.get("code"),
                    "country": team.get("country"),
                    "logo": team.get("logo"),
                }
            )
        return results

    def search_league(
        self,
        query: str,
        country: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"search": query}
        if country:
            params["country"] = country
        data = self.client.get("leagues", **params)
        results = []
        for item in data.get("response", []):
            league = item.get("league", {})
            results.append(
                {
                    "id": league.get("id"),
                    "name": league.get("name", ""),
                    "type": league.get("type"),
                    "logo": league.get("logo"),
                    "country": item.get("country", {}).get("name"),
                    "seasons": [
                        season.get("year")
                        for season in item.get("seasons", [])
                        if season.get("year") is not None
                    ],
                }
            )
        return results

    def get_league_teams(
        self,
        league_id: int | str,
        season: int | str,
    ) -> list[dict[str, Any]]:
        data = self.client.get("teams", league=league_id, season=season)
        results = []
        for item in data.get("response", []):
            team = item.get("team", {})
            results.append(
                {
                    "id": team.get("id"),
                    "name": team.get("name", ""),
                    "code": team.get("code"),
                    "country": team.get("country"),
                    "logo": team.get("logo"),
                }
            )
        return results

    @staticmethod
    def _parse_fixtures(raw: dict[str, Any]) -> list[MatchResult]:
        matches = []
        for fixture in raw.get("response", []):
            parsed = APIFootballFetcher._parse_single_fixture(fixture)
            if parsed is not None:
                matches.append(parsed)
        return sorted(matches, key=lambda match: match.match_date)

    @staticmethod
    def _parse_single_fixture(fixture: dict[str, Any]) -> Optional[MatchResult]:
        try:
            status = (
                fixture.get("fixture", {})
                .get("status", {})
                .get("short", "")
            )
            if status not in FINISHED_STATUSES:
                return None

            fixture_info = fixture.get("fixture", {})
            league = fixture.get("league", {})
            teams = fixture.get("teams", {})
            home = teams.get("home", {})
            away = teams.get("away", {})
            goals = fixture.get("goals", {})
            score = fixture.get("score", {})
            halftime = score.get("halftime", {}) or {}

            match_date = _parse_date(fixture_info.get("date", ""))
            home_id = home.get("id")
            away_id = away.get("id")

            return MatchResult(
                match_id=str(fixture_info.get("id", "")),
                match_date=match_date,
                league_id=str(league.get("id", "")),
                league_name=league.get("name", ""),
                season=str(league.get("season", "")),
                round=league.get("round", ""),
                home_team_id=str(home_id or ""),
                home_team_name=home.get("name", ""),
                away_team_id=str(away_id or ""),
                away_team_name=away.get("name", ""),
                home_score_ft=goals.get("home") or 0,
                away_score_ft=goals.get("away") or 0,
                home_score_ht=halftime.get("home"),
                away_score_ht=halftime.get("away"),
                goals=APIFootballFetcher._parse_goals(
                    fixture.get("events", []),
                    home_id,
                ),
                cards=APIFootballFetcher._parse_cards(
                    fixture.get("events", []),
                    home_id,
                ),
                statistics=APIFootballFetcher._parse_statistics(
                    fixture.get("statistics", [])
                ),
                status=status,
            )
        except Exception:
            return None

    @staticmethod
    def _parse_goals(events: list[dict[str, Any]], home_team_id: Any) -> list[GoalEvent]:
        goals = []
        for event in events or []:
            if event.get("type") != "Goal":
                continue
            detail = (event.get("detail") or "").lower()
            if "missed penalty" in detail:
                continue
            minute = _safe_int(event.get("time", {}).get("elapsed"))
            goals.append(
                GoalEvent(
                    minute=minute,
                    scorer=event.get("player", {}).get("name") or "",
                    assist=event.get("assist", {}).get("name"),
                    is_home=event.get("team", {}).get("id") == home_team_id,
                    half=_half_from_minute(minute),
                )
            )
        return goals

    @staticmethod
    def _parse_cards(events: list[dict[str, Any]], home_team_id: Any) -> list[CardEvent]:
        cards = []
        for event in events or []:
            if event.get("type") != "Card":
                continue
            detail = (event.get("detail") or "").lower()
            card_type = "yellow" if "yellow" in detail else "red"
            minute = _safe_int(event.get("time", {}).get("elapsed"))
            cards.append(
                CardEvent(
                    minute=minute,
                    player=event.get("player", {}).get("name") or "",
                    card_type=card_type,
                    is_home=event.get("team", {}).get("id") == home_team_id,
                    half=_half_from_minute(minute),
                )
            )
        return cards

    @staticmethod
    def _parse_statistics(stats: list[dict[str, Any]]) -> Optional[MatchStatistics]:
        if not stats or len(stats) < 2:
            return None

        home_stats = _stats_map(stats[0].get("statistics", []))
        away_stats = _stats_map(stats[1].get("statistics", []))

        return MatchStatistics(
            corners_home=_number_stat(home_stats, "Corner Kicks"),
            corners_away=_number_stat(away_stats, "Corner Kicks"),
            shots_total_home=_number_stat(home_stats, "Total Shots"),
            shots_total_away=_number_stat(away_stats, "Total Shots"),
            shots_on_target_home=_number_stat(home_stats, "Shots on Goal"),
            shots_on_target_away=_number_stat(away_stats, "Shots on Goal"),
            fouls_home=_number_stat(home_stats, "Fouls"),
            fouls_away=_number_stat(away_stats, "Fouls"),
            yellow_cards_home=_number_stat(home_stats, "Yellow Cards"),
            yellow_cards_away=_number_stat(away_stats, "Yellow Cards"),
            red_cards_home=_number_stat(home_stats, "Red Cards"),
            red_cards_away=_number_stat(away_stats, "Red Cards"),
            possession_home=home_stats.get("Ball Possession"),
            possession_away=away_stats.get("Ball Possession"),
        )

    @staticmethod
    def _make_match_set(
        matches: list[MatchResult],
        team_id: int | str,
        league_id: int | str,
        season: int | str,
        context: str,
    ) -> TeamMatchSet:
        first = matches[0] if matches else None
        if first and context == "home":
            team_name = first.home_team_name
        elif first:
            team_name = first.away_team_name
        else:
            team_name = ""

        return TeamMatchSet(
            team_id=str(team_id),
            team_name=team_name,
            league_id=str(league_id),
            league_name=first.league_name if first else "",
            season=str(season),
            context=context,
            matches=matches,
        )


def _parse_date(value: str):
    if not value:
        raise ValueError("fixture date missing")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _half_from_minute(minute: int) -> str:
    return "1st Half" if minute <= 45 else "2nd Half"


def _stats_map(statistics: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        item.get("type", ""): item.get("value")
        for item in statistics or []
    }


def _number_stat(stats: dict[str, Any], key: str) -> int:
    value = stats.get(key)
    if value is None:
        return 0
    if isinstance(value, str):
        value = value.replace("%", "").strip()
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

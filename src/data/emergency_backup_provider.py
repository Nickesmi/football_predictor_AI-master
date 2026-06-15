"""
High-level data fetcher for Football Predictor AI.

This module is the **primary entry-point** for Issue #1:
  "Implement reliable data fetching for a given match
   (Team A Home vs Team B Away in League L).
   Fetch ALL Home matches of Team A in League L,
   and ALL Away matches of Team B in League L."

Architecture:
  APIFootballClient  (raw HTTP + cache)
       ↓
  APIFootballFetcher (maps JSON → MatchResult / TeamMatchSet)
       ↓
  Consumer code (feature engineering in Issue #2)
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from src.config import logger
from src.data.api_client import APIFootballClient
from src.models.match import (
    CardEvent,
    GoalEvent,
    MatchResult,
    MatchStatistics,
    TeamMatchSet,
)


class APIFootballFetcher:
    """
    Fetches and parses football match data from the API-Football v3 API.

    All public methods return domain model objects (MatchResult, TeamMatchSet)
    that are fully decoupled from the raw API JSON shape.
    """

    def __init__(self, client: Optional[APIFootballClient] = None):
        self._client = client or APIFootballClient()

    # ==================================================================
    # PUBLIC  –  Core fetchers required by Issue #1
    # ==================================================================

    def fetch_team_home_matches(
        self,
        team_id: int,
        league_id: int,
        season: int,
    ) -> TeamMatchSet:
        """
        Fetch ALL *finished* HOME matches of a team in a given league & season.

        This answers the first part of the issue:
        "Fetch ALL Home matches of Team A in League L."
        """
        logger.info(
            "Fetching HOME matches: team=%d, league=%d, season=%d",
            team_id, league_id, season,
        )

        raw = self._client.get(
            "fixtures",
            team=team_id,
            league=league_id,
            season=season,
            status="FT",       # Only finished matches
        )

        all_matches = self._parse_fixtures(raw)

        # Filter: keep only matches where this team is HOME
        home_matches = [
            m for m in all_matches if m.home_team_id == str(team_id)
        ]

        # Grab team name from first match if available
        team_name = home_matches[0].home_team_name if home_matches else f"Team#{team_id}"
        league_name = home_matches[0].league_name if home_matches else f"League#{league_id}"

        result = TeamMatchSet(
            team_id=str(team_id),
            team_name=team_name,
            league_id=str(league_id),
            league_name=league_name,
            season=str(season),
            context="home",
            matches=home_matches,
        )

        logger.info("Fetched %d home matches for %s", result.total_matches, result.team_name)
        return result

    def fetch_team_away_matches(
        self,
        team_id: int,
        league_id: int,
        season: int,
    ) -> TeamMatchSet:
        """
        Fetch ALL *finished* AWAY matches of a team in a given league & season.

        This answers the second part of the issue:
        "Fetch ALL Away matches of Team B in League L."
        """
        logger.info(
            "Fetching AWAY matches: team=%d, league=%d, season=%d",
            team_id, league_id, season,
        )

        raw = self._client.get(
            "fixtures",
            team=team_id,
            league=league_id,
            season=season,
            status="FT",
        )

        all_matches = self._parse_fixtures(raw)

        # Filter: keep only matches where this team is AWAY
        away_matches = [
            m for m in all_matches if m.away_team_id == str(team_id)
        ]

        team_name = away_matches[0].away_team_name if away_matches else f"Team#{team_id}"
        league_name = away_matches[0].league_name if away_matches else f"League#{league_id}"

        result = TeamMatchSet(
            team_id=str(team_id),
            team_name=team_name,
            league_id=str(league_id),
            league_name=league_name,
            season=str(season),
            context="away",
            matches=away_matches,
        )

        logger.info("Fetched %d away matches for %s", result.total_matches, result.team_name)
        return result

    def fetch_match_context(
        self,
        home_team_id: int,
        away_team_id: int,
        league_id: int,
        season: int,
    ) -> tuple[TeamMatchSet, TeamMatchSet]:
        """
        Convenience method: fetch ALL home matches of Team A AND
        ALL away matches of Team B in one call.

        Returns:
            (home_team_matches, away_team_matches)
        """
        home_matches = self.fetch_team_home_matches(home_team_id, league_id, season)
        away_matches = self.fetch_team_away_matches(away_team_id, league_id, season)
        return home_matches, away_matches

    # ==================================================================
    # PUBLIC  –  Lookup helpers
    # ==================================================================

    def search_team(self, name: str) -> list[dict]:
        """
        Search for a team by name. Returns a list of matching team dicts.

        Each dict contains: id, name, code, country, logo, etc.
        """
        raw = self._client.get("teams", search=name)
        teams = raw.get("response", [])
        results = []
        for entry in teams:
            team_info = entry.get("team", {})
            results.append({
                "id": team_info.get("id"),
                "name": team_info.get("name"),
                "code": team_info.get("code"),
                "country": team_info.get("country"),
                "logo": team_info.get("logo"),
            })
        return results

    def search_league(self, name: str, country: Optional[str] = None) -> list[dict]:
        """
        Search for a league/competition by name (and optionally country).

        Each dict contains: id, name, type, country, logo, season info.
        """
        params: dict = {"search": name}
        if country:
            params["country"] = country
        raw = self._client.get("leagues", **params)
        leagues = raw.get("response", [])
        results = []
        for entry in leagues:
            league_info = entry.get("league", {})
            country_info = entry.get("country", {})
            seasons = entry.get("seasons", [])
            available_seasons = [s.get("year") for s in seasons]
            results.append({
                "id": league_info.get("id"),
                "name": league_info.get("name"),
                "type": league_info.get("type"),
                "country": country_info.get("name"),
                "logo": league_info.get("logo"),
                "seasons": available_seasons,
            })
        return results

    def get_league_teams(self, league_id: int, season: int) -> list[dict]:
        """
        Get all teams participating in a league for a given season.

        Returns list of dicts with: id, name, code, country, logo.
        """
        raw = self._client.get("teams", league=league_id, season=season)
        teams = raw.get("response", [])
        results = []
        for entry in teams:
            team_info = entry.get("team", {})
            results.append({
                "id": team_info.get("id"),
                "name": team_info.get("name"),
                "code": team_info.get("code"),
                "country": team_info.get("country"),
                "logo": team_info.get("logo"),
            })
        return results

    # ==================================================================
    # PRIVATE  –  Parsing / mapping
    # ==================================================================

    def _parse_fixtures(self, raw: dict) -> list[MatchResult]:
        """
        Parse the raw API-Football v3 /fixtures response into a list
        of MatchResult domain objects.
        """
        fixtures = raw.get("response", [])
        results: list[MatchResult] = []

        for fixture in fixtures:
            try:
                match = self._parse_single_fixture(fixture)
                if match is not None:
                    results.append(match)
            except Exception as exc:
                fixture_id = fixture.get("fixture", {}).get("id", "?")
                logger.warning("Failed to parse fixture %s: %s", fixture_id, exc)

        # Sort by date ascending
        results.sort(key=lambda m: m.match_date)
        return results

    def _parse_single_fixture(self, fixture: dict) -> Optional[MatchResult]:
        """Parse one fixture entry from the API response."""
        fix = fixture.get("fixture", {})
        league = fixture.get("league", {})
        teams = fixture.get("teams", {})
        goals = fixture.get("goals", {})
        score = fixture.get("score", {})
        events = fixture.get("events", [])
        statistics_list = fixture.get("statistics", [])

        # Extract fixture status
        status_info = fix.get("status", {})
        status_short = status_info.get("short", "")

        # Only process finished matches
        if status_short not in ("FT", "AET", "PEN"):
            return None

        # Parse date
        date_str = fix.get("date", "")[:10]  # "2024-01-15T20:00:00+00:00" → "2024-01-15"
        match_date = date.fromisoformat(date_str) if date_str else date.today()

        # Half-time scores
        ht = score.get("halftime", {})
        ht_home = ht.get("home")
        ht_away = ht.get("away")

        # Parse events (goals + cards)
        goal_events = []
        card_events = []
        for event in events:
            event_type = event.get("type", "").lower()
            event_detail = event.get("detail", "").lower()
            event_time = event.get("time", {}).get("elapsed", 0) or 0
            player_name = event.get("player", {}).get("name", "Unknown")
            assist_name = event.get("assist", {}).get("name")
            team_info = event.get("team", {})
            is_home_team = str(team_info.get("id", "")) == str(teams.get("home", {}).get("id", ""))
            half = "1st Half" if event_time <= 45 else "2nd Half"

            if event_type == "goal" and "missed" not in event_detail:
                goal_events.append(GoalEvent(
                    minute=event_time,
                    scorer=player_name,
                    assist=assist_name,
                    is_home=is_home_team,
                    half=half,
                ))
            elif event_type == "card":
                card_type = "yellow" if "yellow" in event_detail else "red"
                card_events.append(CardEvent(
                    minute=event_time,
                    player=player_name,
                    card_type=card_type,
                    is_home=is_home_team,
                    half=half,
                ))

        # Parse match statistics
        match_stats = self._parse_statistics(statistics_list)

        return MatchResult(
            match_id=str(fix.get("id", "")),
            match_date=match_date,
            league_id=str(league.get("id", "")),
            league_name=league.get("name", ""),
            season=str(league.get("season", "")),
            round=league.get("round", ""),
            home_team_id=str(teams.get("home", {}).get("id", "")),
            home_team_name=teams.get("home", {}).get("name", ""),
            away_team_id=str(teams.get("away", {}).get("id", "")),
            away_team_name=teams.get("away", {}).get("name", ""),
            home_score_ft=goals.get("home", 0) or 0,
            away_score_ft=goals.get("away", 0) or 0,
            home_score_ht=ht_home,
            away_score_ht=ht_away,
            goals=goal_events,
            cards=card_events,
            statistics=match_stats,
            status=status_short,
        )

    @staticmethod
    def _parse_statistics(statistics_list: list) -> Optional[MatchStatistics]:
        """
        Parse the statistics array from the API response.

        The API returns statistics as a list of two entries
        (one per team), each containing a list of stat dicts.
        """
        if not statistics_list or len(statistics_list) < 2:
            return None

        def _get_stat(team_stats: list, stat_type: str) -> str:
            """Find a stat value by type name."""
            for stat in team_stats:
                if stat.get("type", "").lower() == stat_type.lower():
                    val = stat.get("value")
                    return str(val) if val is not None else "0"
            return "0"

        def _safe_int(val: str) -> int:
            """Convert a stat value to int, stripping % signs etc."""
            try:
                return int(str(val).replace("%", "").strip())
            except (ValueError, TypeError):
                return 0

        home_stats = statistics_list[0].get("statistics", [])
        away_stats = statistics_list[1].get("statistics", [])

        return MatchStatistics(
            corners_home=_safe_int(_get_stat(home_stats, "Corner Kicks")),
            corners_away=_safe_int(_get_stat(away_stats, "Corner Kicks")),
            shots_total_home=_safe_int(_get_stat(home_stats, "Total Shots")),
            shots_total_away=_safe_int(_get_stat(away_stats, "Total Shots")),
            shots_on_target_home=_safe_int(_get_stat(home_stats, "Shots on Goal")),
            shots_on_target_away=_safe_int(_get_stat(away_stats, "Shots on Goal")),
            fouls_home=_safe_int(_get_stat(home_stats, "Fouls")),
            fouls_away=_safe_int(_get_stat(away_stats, "Fouls")),
            yellow_cards_home=_safe_int(_get_stat(home_stats, "Yellow Cards")),
            yellow_cards_away=_safe_int(_get_stat(away_stats, "Yellow Cards")),
            red_cards_home=_safe_int(_get_stat(home_stats, "Red Cards")),
            red_cards_away=_safe_int(_get_stat(away_stats, "Red Cards")),
            possession_home=_get_stat(home_stats, "Ball Possession"),
            possession_away=_get_stat(away_stats, "Ball Possession"),
        )

    def fetch_fixtures(self, date_str: str) -> list[dict]:
        logger.info(f"EMERGENCY BACKUP: Fetching fixtures for {date_str} via API-Football")
        raw = self._client.get("fixtures", date=date_str)
        return raw.get("response", [])

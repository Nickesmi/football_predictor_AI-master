"""
Live Service Module.

Provides business logic and data access for the live adaptive pipeline, including
odds scanning, ELO rankings, team state tracking, match ingestion, and match history.
"""

import sqlite3
from typing import Any, Dict, List, Optional
from src.engine.odds_scanner import scan_live_odds as _scan_live_odds
from src.engine.live_updater import get_ingestion_stats, on_match_finished
from src.db.team_state import get_all_team_states, get_team_state as get_live_state


def scan_live_odds() -> Dict[str, Any]:
    """
    Trigger a live odds scan across supported bookmakers to evaluate executable bets.

    Returns:
        Dictionary containing scan summary and executable bet opportunities.
    """
    return _scan_live_odds()


def get_live_system_status(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Retrieve the current status and ingestion statistics of the live adaptive pipeline.

    Args:
        conn: Active SQLite database connection.

    Returns:
        Dictionary with pipeline status ('active', 'cold_start', or 'error') and ingestion stats.
    """
    try:
        stats = get_ingestion_stats(conn)
        return {
            "status": "active" if stats["total_matches"] > 0 else "cold_start",
            "engine": "Live Adaptive Pipeline v1.0",
            **stats,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_tracked_teams(conn: sqlite3.Connection, league: Optional[str] = None) -> Dict[str, Any]:
    """
    Retrieve all tracked team states including current ELO ratings and form metrics.

    Args:
        conn: Active SQLite database connection.
        league: Optional league filter.

    Returns:
        Dictionary containing team count and detailed state list.
    """
    try:
        states = get_all_team_states(conn, league)
        return {
            "count": len(states),
            "teams": [
                {
                    "team": s.team_name,
                    "league": s.league,
                    "venue": s.venue,
                    "elo": s.elo,
                    "attack_rating": s.attack_rating,
                    "defense_rating": s.defense_rating,
                    "rolling_scored": s.rolling_scored,
                    "rolling_conceded": s.rolling_conceded,
                    "form_last5": s.form_last5,
                    "win_streak": s.win_streak,
                    "matches_played": s.matches_played,
                    "rest_days": s.rest_days,
                    "last_match": s.last_match_date,
                }
                for s in states
            ],
        }
    except Exception as e:
        return {"error": str(e)}


def get_elo_rankings(conn: sqlite3.Connection, league: Optional[str] = None) -> Dict[str, Any]:
    """
    Retrieve team ELO rankings sorted descending by ELO rating.

    Args:
        conn: Active SQLite database connection.
        league: Optional league filter.

    Returns:
        Dictionary containing ranking count and ordered rankings list.
    """
    try:
        query = """SELECT team_name, league, elo, rolling_scored, rolling_conceded,
                          form_last5, win_streak, matches_played
                   FROM team_state WHERE venue = 'home'"""
        params: List[Any] = []
        if league:
            query += " AND league = ?"
            params.append(league)
        query += " ORDER BY elo DESC"
        rows = conn.execute(query, params).fetchall()
        return {
            "count": len(rows),
            "rankings": [
                {
                    "rank": i + 1,
                    "team": r["team_name"],
                    "league": r["league"],
                    "elo": r["elo"],
                    "form_last5": r["form_last5"],
                    "matches_played": r["matches_played"],
                }
                for i, r in enumerate(rows)
            ],
        }
    except Exception as e:
        return {"error": str(e)}


def get_match_history(
    conn: sqlite3.Connection,
    team: Optional[str] = None,
    league: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """
    Retrieve recent match history from the ingested database with ELO change tracking.

    Args:
        conn: Active SQLite database connection.
        team: Optional team name substring filter.
        league: Optional league filter.
        limit: Maximum number of match records to return (default 20).

    Returns:
        Dictionary containing match count and match history list.
    """
    try:
        query = "SELECT * FROM match_history WHERE 1=1"
        params: List[Any] = []
        if team:
            query += " AND (home_team LIKE ? OR away_team LIKE ?)"
            params.extend([f"%{team}%", f"%{team}%"])
        if league:
            query += " AND league = ?"
            params.append(league)
        query += " ORDER BY match_date DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return {
            "count": len(rows),
            "matches": [
                {
                    "match_id": r["match_id"],
                    "date": r["match_date"],
                    "league": r["league"],
                    "home": r["home_team"],
                    "away": r["away_team"],
                    "score": f"{r['home_goals']}-{r['away_goals']}",
                    "home_elo_change": round(r["home_elo_after"] - r["home_elo_before"], 1)
                    if r["home_elo_after"]
                    else None,
                    "away_elo_change": round(r["away_elo_after"] - r["away_elo_before"], 1)
                    if r["away_elo_after"]
                    else None,
                }
                for r in rows
            ],
        }
    except Exception as e:
        return {"error": str(e)}


def get_team_live_state(
    conn: sqlite3.Connection, team_name: str, league: str = ""
) -> Dict[str, Any]:
    """
    Retrieve detailed live state for a specific team across home and away venues.

    Args:
        conn: Active SQLite database connection.
        team_name: Exact name of the team.
        league: Optional league name.

    Returns:
        Dictionary containing home and away venue state metrics or error suggestion.
    """
    try:
        results = {}
        for venue in ["home", "away"]:
            state = get_live_state(conn, team_name, league, venue)
            if state:
                results[venue] = {
                    "elo": state.elo,
                    "attack_rating": state.attack_rating,
                    "defense_rating": state.defense_rating,
                    "rolling_scored": state.rolling_scored,
                    "rolling_conceded": state.rolling_conceded,
                    "rolling_xg": state.rolling_xg,
                    "rolling_xga": state.rolling_xga,
                    "rolling_corners": state.rolling_corners,
                    "rolling_cards": state.rolling_cards,
                    "form_last5": state.form_last5,
                    "form_last10": state.form_last10,
                    "win_streak": state.win_streak,
                    "unbeaten_streak": state.unbeaten_streak,
                    "matches_last_14d": state.matches_last_14d,
                    "rest_days": state.rest_days,
                    "matches_played": state.matches_played,
                    "last_match_date": state.last_match_date,
                }

        if not results:
            return {
                "error": f"No live state found for '{team_name}'",
                "suggestion": "Visit Results page to ingest match data",
            }

        return {"team": team_name, "league": league, "states": results}
    except Exception as e:
        return {"error": str(e)}


def manual_ingest_match(
    conn: sqlite3.Connection,
    match_id: str,
    match_date: str,
    league: str,
    home_team: str,
    away_team: str,
    home_goals: int,
    away_goals: int,
    home_xg: Optional[float] = None,
    away_xg: Optional[float] = None,
    home_corners: Optional[int] = None,
    away_corners: Optional[int] = None,
    home_cards: Optional[int] = None,
    away_cards: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Manually ingest a match result into the live adaptive pipeline.

    Args:
        conn: Active SQLite database connection.
        match_id: Unique match identifier.
        match_date: ISO date string of match kickoff.
        league: League name.
        home_team: Home team name.
        away_team: Away team name.
        home_goals: Goals scored by home team.
        away_goals: Goals scored by away team.
        home_xg: Optional expected goals for home team.
        away_xg: Optional expected goals for away team.
        home_corners: Optional corners for home team.
        away_corners: Optional corners for away team.
        home_cards: Optional cards for home team.
        away_cards: Optional cards for away team.

    Returns:
        Dictionary indicating ingestion success or error message.
    """
    try:
        return on_match_finished(
            conn=conn,
            match_id=match_id,
            match_date=match_date,
            league=league,
            home_team=home_team,
            away_team=away_team,
            home_goals=home_goals,
            away_goals=away_goals,
            home_xg=home_xg,
            away_xg=away_xg,
            home_corners=home_corners,
            away_corners=away_corners,
            home_cards=home_cards,
            away_cards=away_cards,
        )
    except Exception as e:
        return {"error": str(e)}

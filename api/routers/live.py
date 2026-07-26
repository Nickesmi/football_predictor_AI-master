"""
Live Router.

Handles endpoints for the live adaptive pipeline including real-time odds scanning,
team state monitoring, ELO rankings, match history, and manual match ingestion.
Strictly delegates business logic to live_service.
"""

import sqlite3
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException
from api.services import live_service

router = APIRouter(prefix="/api/live", tags=["Live Adaptive Pipeline"])


def _get_db() -> sqlite3.Connection:
    """Dependency to retrieve database connection."""
    from src.db.database import get_db

    return get_db()


@router.get("/scan")
def trigger_live_scan() -> Dict[str, Any]:
    """Trigger a live odds scan across supported bookmakers to evaluate executable bets."""
    try:
        return live_service.scan_live_odds()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
def get_live_system_status(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """Get the status of the live adaptive pipeline."""
    return live_service.get_live_system_status(conn)


@router.get("/teams")
def get_tracked_teams(
    league: Optional[str] = None, conn: sqlite3.Connection = Depends(_get_db)
) -> Dict[str, Any]:
    """Get all tracked team states with current ELO and form."""
    return live_service.get_tracked_teams(conn, league=league)


@router.get("/elo-rankings")
def get_elo_rankings(
    league: Optional[str] = None, conn: sqlite3.Connection = Depends(_get_db)
) -> Dict[str, Any]:
    """Get ELO rankings sorted by rating descending."""
    return live_service.get_elo_rankings(conn, league=league)


@router.get("/match-history")
def get_match_history_api(
    team: Optional[str] = None,
    league: Optional[str] = None,
    limit: int = 20,
    conn: sqlite3.Connection = Depends(_get_db),
) -> Dict[str, Any]:
    """Get recent match history from the ingested database."""
    return live_service.get_match_history(conn, team=team, league=league, limit=limit)


@router.get("/team/{team_name}")
def get_team_live_state(
    team_name: str, league: str = "", conn: sqlite3.Connection = Depends(_get_db)
) -> Dict[str, Any]:
    """Get detailed live state for a specific team."""
    return live_service.get_team_live_state(conn, team_name=team_name, league=league)


@router.post("/ingest")
def manual_ingest_match(
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
    conn: sqlite3.Connection = Depends(_get_db),
) -> Dict[str, Any]:
    """Manually ingest a match result into the live pipeline."""
    return live_service.manual_ingest_match(
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

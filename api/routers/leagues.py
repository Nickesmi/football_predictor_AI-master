"""
Leagues Router.

Handles endpoints for supported tracked leagues, historical competition lists,
and league reliability profiles.
Strictly delegates all business logic to league_service.
"""

import sqlite3
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException
from api.services import league_service

router = APIRouter(prefix="/api", tags=["Leagues & Competitions"])


def _get_db() -> sqlite3.Connection:
    """Dependency to retrieve database connection."""
    from src.db.database import get_db

    return get_db()


@router.get("/leagues")
def get_supported_leagues() -> List[Dict[str, str]]:
    """Retrieve supported tracked football leagues."""
    return league_service.get_supported_leagues()


@router.get("/competitions")
def get_competitions(
    limit: int = 200, conn: sqlite3.Connection = Depends(_get_db)
) -> Dict[str, Any]:
    """List historical competitions encountered by the platform."""
    try:
        return league_service.get_competitions(conn, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/leagues/profiles")
def get_league_profiles(conn: sqlite3.Connection = Depends(_get_db)) -> List[Dict[str, Any]]:
    """Retrieve all league reliability profiles."""
    return league_service.get_league_profiles(conn)

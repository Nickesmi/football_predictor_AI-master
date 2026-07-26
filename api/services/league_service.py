"""
League Service Module.

Provides business logic and data processing for supported football leagues,
historical competition tracking, and league reliability profiling.
"""

import sqlite3
from typing import Any, Dict, List
from src.config import TOP_LEAGUES
from src.db.competition_tracker import list_competitions, get_competition_stats


def get_supported_leagues() -> List[Dict[str, str]]:
    """
    Retrieve the list of supported tracked leagues and competitions.

    Returns:
        List of dictionaries containing tournament ID (str) and name (str).
    """
    return [{"id": str(k), "name": v} for k, v in TOP_LEAGUES.items()]


def get_competitions(conn: sqlite3.Connection, limit: int = 200) -> Dict[str, Any]:
    """
    List historical competitions encountered by the platform along with aggregate statistics.

    Args:
        conn: Active SQLite database connection.
        limit: Maximum number of competitions to return (default 200).

    Returns:
        Dictionary containing total competition count, aggregate stats, and competition list.
    """
    comps = list_competitions(conn, limit=limit)
    stats = get_competition_stats(conn)
    return {"total": len(comps), "stats": stats, "competitions": comps}


def get_league_profiles(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """
    Retrieve all league reliability profiles ordered by reliability score descending.

    Args:
        conn: Active SQLite database connection.

    Returns:
        List of dictionaries representing league profile records.
    """
    rows = conn.execute(
        "SELECT * FROM league_profiles ORDER BY reliability_score DESC"
    ).fetchall()
    return [dict(r) for r in rows]

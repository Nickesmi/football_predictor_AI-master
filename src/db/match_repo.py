"""
Repository for match CRUD operations.
"""

import sqlite3
from typing import Optional


def upsert_match(conn: sqlite3.Connection, match: dict) -> None:
    """Insert or update a match record."""
    conn.execute(
        """INSERT INTO matches (id, date, kickoff, home_team, away_team,
                                league_name, league_id, status, home_goals, away_goals)
           VALUES (:id, :date, :kickoff, :home_team, :away_team,
                   :league_name, :league_id, :status, :home_goals, :away_goals)
           ON CONFLICT(id) DO UPDATE SET
               status = excluded.status,
               home_goals = excluded.home_goals,
               away_goals = excluded.away_goals""",
        match,
    )
    conn.commit()


def get_matches_by_date(conn: sqlite3.Connection, date: str) -> list[dict]:
    """Fetch all matches for a given date."""
    rows = conn.execute(
        "SELECT * FROM matches WHERE date = ? ORDER BY kickoff", (date,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_match_history_by_date(conn: sqlite3.Connection, date: str) -> list[dict]:
    """Fetch all historical matches for a given date."""
    rows = conn.execute(
        "SELECT * FROM match_history WHERE match_date = ? ORDER BY id", (date,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_match_by_id(conn: sqlite3.Connection, match_id: str) -> Optional[dict]:
    """Fetch a single match by ID."""
    row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    return dict(row) if row else None


def update_match_result(
    conn: sqlite3.Connection, match_id: str, home_goals: int, away_goals: int
) -> None:
    """Update a match with final result."""
    conn.execute(
        """UPDATE matches SET status = 'FT', home_goals = ?, away_goals = ?
           WHERE id = ?""",
        (home_goals, away_goals, match_id),
    )
    conn.commit()

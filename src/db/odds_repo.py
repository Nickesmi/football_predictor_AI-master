"""
Repository for odds snapshot operations.
"""

import sqlite3
from typing import Optional


def insert_odds(conn: sqlite3.Connection, snapshot: dict) -> None:
    """Insert an odds snapshot."""
    conn.execute(
        """INSERT INTO odds_snapshots
           (match_id, market, selection, odds, bookmaker, is_opening)
           VALUES (:match_id, :market, :selection, :odds, :bookmaker, :is_opening)""",
        snapshot,
    )
    conn.commit()


def get_odds_for_match(conn: sqlite3.Connection, match_id: str) -> list[dict]:
    """Get all odds snapshots for a match, newest first."""
    rows = conn.execute(
        """SELECT * FROM odds_snapshots
           WHERE match_id = ? ORDER BY timestamp DESC""",
        (match_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_odds(
    conn: sqlite3.Connection, match_id: str, market: str, selection: str
) -> Optional[dict]:
    """Get the most recent odds for a specific market/selection."""
    row = conn.execute(
        """SELECT * FROM odds_snapshots
           WHERE match_id = ? AND market = ? AND selection = ?
           ORDER BY timestamp DESC LIMIT 1""",
        (match_id, market, selection),
    ).fetchone()
    return dict(row) if row else None

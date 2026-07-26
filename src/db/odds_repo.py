"""
Repository for odds snapshot operations.
"""

import sqlite3
from typing import Optional
from src.db.database import retry_on_db_lock


@retry_on_db_lock()
def insert_odds(conn: sqlite3.Connection, snapshot: dict) -> None:
    """Insert an odds snapshot."""
    # Ensure timestamp is provided
    if "timestamp" not in snapshot:
        from datetime import datetime, timezone
        snapshot["timestamp"] = datetime.now(timezone.utc).isoformat()
        
    with conn:
        conn.execute(
            """INSERT INTO odds_snapshots
               (match_id, market, selection, odds, bookmaker, is_opening, implied_probability, timestamp)
               VALUES (:match_id, :market, :selection, :odds, :bookmaker, :is_opening, :implied_probability, :timestamp)""",
            snapshot,
        )


@retry_on_db_lock()
def insert_odds_batch(conn: sqlite3.Connection, snapshots: list[dict]) -> None:
    """Insert multiple odds snapshots atomically in a single transaction."""
    if not snapshots:
        return
    from datetime import datetime, timezone
    now_str = datetime.now(timezone.utc).isoformat()
    for s in snapshots:
        if "timestamp" not in s:
            s["timestamp"] = now_str
            
    with conn:
        conn.executemany(
            """INSERT INTO odds_snapshots
               (match_id, market, selection, odds, bookmaker, is_opening, implied_probability, timestamp)
               VALUES (:match_id, :market, :selection, :odds, :bookmaker, :is_opening, :implied_probability, :timestamp)""",
            snapshots,
        )


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

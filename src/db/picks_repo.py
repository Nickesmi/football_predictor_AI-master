"""
Repository for pick CRUD and analytics queries.
"""

import sqlite3
from typing import Optional


def insert_pick(conn: sqlite3.Connection, pick: dict) -> int:
    """Insert a pick and return its ID."""
    cursor = conn.execute(
        """INSERT INTO picks
           (match_id, market, selection, model_prob, implied_prob, edge,
            odds_at_pick, confidence, league_reliability, grade, stake_units)
           VALUES (:match_id, :market, :selection, :model_prob, :implied_prob,
                   :edge, :odds_at_pick, :confidence, :league_reliability,
                   :grade, :stake_units)""",
        pick,
    )
    conn.commit()
    return cursor.lastrowid


def get_picks_by_date(conn: sqlite3.Connection, date: str) -> list[dict]:
    """Get all picks for matches on a given date."""
    rows = conn.execute(
        """SELECT p.*, m.home_team, m.away_team, m.league_name, m.date, m.kickoff
           FROM picks p
           JOIN matches m ON p.match_id = m.id
           WHERE m.date = ?
           ORDER BY p.edge DESC""",
        (date,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_unsettled_picks(conn: sqlite3.Connection) -> list[dict]:
    """Get all picks that haven't been settled yet."""
    rows = conn.execute(
        """SELECT p.*, m.home_team, m.away_team, m.league_name, m.date,
                  m.home_goals, m.away_goals, m.status
           FROM picks p
           JOIN matches m ON p.match_id = m.id
           WHERE p.result IS NULL AND m.status = 'FT'
           ORDER BY m.date"""
    ).fetchall()
    return [dict(r) for r in rows]


def settle_pick(
    conn: sqlite3.Connection,
    pick_id: int,
    result: str,
    pnl_units: float,
    clv: Optional[float] = None,
) -> None:
    """Mark a pick as won/lost/void with P&L."""
    conn.execute(
        """UPDATE picks SET result = ?, pnl_units = ?, clv = ?
           WHERE id = ?""",
        (result, pnl_units, clv, pick_id),
    )
    conn.commit()


def get_portfolio_summary(conn: sqlite3.Connection) -> dict:
    """Aggregate P&L stats across all settled picks."""
    row = conn.execute(
        """SELECT
               COUNT(*)                                                    AS total_picks,
               COALESCE(SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END), 0) AS wins,
               COALESCE(SUM(CASE WHEN result = 'lost' THEN 1 ELSE 0 END), 0) AS losses,
               COALESCE(SUM(CASE WHEN result IS NULL THEN 1 ELSE 0 END), 0)  AS pending,
               COALESCE(SUM(pnl_units), 0.0)                               AS total_pnl,
               COALESCE(SUM(stake_units), 0.0)                              AS total_staked,
               COALESCE(AVG(clv), 0.0)                                      AS avg_clv
           FROM picks"""
    ).fetchone()
    d = dict(row)
    total_staked = d["total_staked"] if d["total_staked"] else 1
    d["roi_pct"] = round(float(d["total_pnl"]) / float(total_staked) * 100, 2)
    d["hit_rate"] = round(int(d["wins"]) / max(int(d["wins"]) + int(d["losses"]), 1) * 100, 1)
    return d


def get_league_pnl(conn: sqlite3.Connection) -> list[dict]:
    """P&L breakdown by league."""
    rows = conn.execute(
        """SELECT m.league_name,
                  COUNT(*)                                          AS picks,
                  SUM(CASE WHEN p.result = 'won' THEN 1 ELSE 0 END) AS wins,
                  COALESCE(SUM(p.pnl_units), 0)                     AS pnl,
                  COALESCE(SUM(p.stake_units), 0)                    AS staked
           FROM picks p
           JOIN matches m ON p.match_id = m.id
           WHERE p.result IS NOT NULL
           GROUP BY m.league_name
           ORDER BY pnl DESC"""
    ).fetchall()
    return [dict(r) for r in rows]

"""
Repository for pick CRUD and analytics queries.
"""

import sqlite3
from typing import Optional
from src.db.database import retry_on_db_lock

# Every model_source value estimate_probabilities() (src/engine/probability_engine.py)
# can actually produce for a genuine automated pipeline pick: "poisson", "fallback"
# (the poisson path's own internal exception fallback), and "hybrid" (xgboost-blended).
# This is an ALLOWLIST, not a blocklist of known-bad values (e.g. "manual") — a new,
# unanticipated model_source value (a future model, a test tag, anything) is excluded
# by default rather than accidentally counted, which is the correct failure direction
# for a frozen experimental sample.
_AUTOMATED_PIPELINE_MODEL_SOURCES = ("poisson", "fallback", "hybrid")


def get_experiment_progress(conn: sqlite3.Connection) -> dict:
    """
    Canonical count of genuine automated gated-pipeline picks that have reached
    a real win/loss outcome — the population baseline_contract.md's 1,000-pick
    evidence gate is measured against.

    Excludes, by construction (not by trying to blocklist every bad case):
    - Anything not produced by estimate_probabilities() (model_source must be in
      _AUTOMATED_PIPELINE_MODEL_SOURCES) — this alone excludes:
        - manual entries (api/services/portfolio_service.py::place_pick sets
          model_source='manual')
        - collection_worker.py's raw "NO PICK" placeholder INSERT, which sets no
          model_source at all and so falls back to the schema default 'unknown'
        - any pick whose model_source is missing/unrecognized for any other reason
    - result NOT IN ('won', 'lost') — a pending (NULL) or voided pick is a real
      row but contributes no win/loss evidence, so it doesn't count toward the
      1,000 either way.

    Legacy prediction_log rows and qualification_log rows can never appear here
    at all — this function only ever queries the picks table.
    """
    placeholders = ",".join("?" for _ in _AUTOMATED_PIPELINE_MODEL_SOURCES)

    settled_eligible = conn.execute(
        f"""SELECT COUNT(*) FROM picks
            WHERE result IN ('won', 'lost')
              AND model_source IN ({placeholders})""",
        _AUTOMATED_PIPELINE_MODEL_SOURCES,
    ).fetchone()[0]

    excluded_voided = conn.execute(
        f"""SELECT COUNT(*) FROM picks
            WHERE result = 'void'
              AND model_source IN ({placeholders})""",
        _AUTOMATED_PIPELINE_MODEL_SOURCES,
    ).fetchone()[0]

    excluded_non_pipeline = conn.execute(
        f"""SELECT COUNT(*) FROM picks
            WHERE model_source NOT IN ({placeholders})""",
        _AUTOMATED_PIPELINE_MODEL_SOURCES,
    ).fetchone()[0]

    pending_eligible = conn.execute(
        f"""SELECT COUNT(*) FROM picks
            WHERE result IS NULL
              AND model_source IN ({placeholders})""",
        _AUTOMATED_PIPELINE_MODEL_SOURCES,
    ).fetchone()[0]

    return {
        "settled_eligible": settled_eligible,
        "target": 1000,
        "progress_pct": round(min(100.0, settled_eligible / 1000.0 * 100.0), 2),
        "excluded_voided": excluded_voided,
        "excluded_non_pipeline": excluded_non_pipeline,
        "pending_eligible": pending_eligible,
    }


@retry_on_db_lock()
def insert_pick(conn: sqlite3.Connection, pick: dict) -> int:
    """Insert a pick and return its ID."""
    with conn:
        cursor = conn.execute(
            """INSERT INTO picks
               (match_id, market, selection, model_prob, implied_prob, edge,
                odds_at_pick, confidence, league_reliability, grade, stake_units,
                model_source)
               VALUES (:match_id, :market, :selection, :model_prob, :implied_prob,
                       :edge, :odds_at_pick, :confidence, :league_reliability,
                       :grade, :stake_units, :model_source)""",
            {**pick, "model_source": pick.get("model_source", "unknown")},
        )
        return cursor.lastrowid


@retry_on_db_lock()
def insert_picks_batch(conn: sqlite3.Connection, picks: list[dict]) -> None:
    """Insert multiple picks atomically in a single transaction."""
    if not picks:
        return
    with conn:
        conn.executemany(
            """INSERT INTO picks
               (match_id, market, selection, model_prob, implied_prob, edge,
                odds_at_pick, confidence, league_reliability, grade, stake_units,
                model_source)
               VALUES (:match_id, :market, :selection, :model_prob, :implied_prob,
                       :edge, :odds_at_pick, :confidence, :league_reliability,
                       :grade, :stake_units, :model_source)""",
            [{**p, "model_source": p.get("model_source", "unknown")} for p in picks],
        )


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


@retry_on_db_lock()
def settle_pick(
    conn: sqlite3.Connection,
    pick_id: int,
    result: str,
    pnl_units: float,
    clv: Optional[float] = None,
) -> None:
    """Mark a pick as won/lost/void with P&L."""
    with conn:
        conn.execute(
            """UPDATE picks SET result = ?, pnl_units = ?, clv = ?
               WHERE id = ?""",
            (result, pnl_units, clv, pick_id),
        )


@retry_on_db_lock()
def update_closing_odds(
    conn: sqlite3.Connection,
    pick_id: int,
    closing_odds: float,
    clv_pct: float,
) -> None:
    """Update closing odds and computed CLV percentage before match start."""
    with conn:
        conn.execute(
            """UPDATE picks SET closing_odds = ?, clv_pct = ?
               WHERE id = ?""",
            (closing_odds, clv_pct, pick_id),
        )



def get_portfolio_summary(conn: sqlite3.Connection) -> dict:
    """Aggregate P&L stats across all settled picks."""
    row = conn.execute(
        """SELECT
               COUNT(*)                                                    AS total_picks,
               COALESCE(SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END), 0) AS wins,
               COALESCE(SUM(CASE WHEN result = 'lost' THEN 1 ELSE 0 END), 0) AS losses,
               COALESCE(SUM(CASE WHEN result = 'void' THEN 1 ELSE 0 END), 0) AS voided,
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

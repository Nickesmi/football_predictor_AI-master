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
    Canonical count of settled picks that belong to the official frozen football
    baseline — the population baseline_contract.md's 1,000-pick gate is measured on.

    A settled (won/lost) pick counts only if ALL hold:
    - model_source is a genuine automated pipeline source (allowlist; excludes
      manual picks, the collection worker's "NO PICK" placeholder, unknown tags);
    - it was produced under the ACTIVE baseline: baseline_version, model_version,
      calibration_version and selection_policy_version equal the active manifest;
    - its provenance is complete (code_revision, runtime, input_data_timestamp,
      inputs_json present).
    Without an active baseline (BASELINE_FREEZE unset) nothing counts.
    Pending and voided picks never count. Legacy prediction_log and
    qualification_log rows cannot appear (different tables).
    """
    from src.baseline import state as baseline

    sources = ",".join("?" for _ in _AUTOMATED_PIPELINE_MODEL_SOURCES)
    version = baseline.active_version()
    manifest = None
    if version is not None:
        try:
            manifest = baseline.load_manifest(version)
        except (OSError, ValueError):
            manifest = None

    if manifest is None:
        official_clause, official_params = "0", ()
    else:
        complete = " AND ".join(f"{c} IS NOT NULL" for c in (
            "code_revision", "runtime", "input_data_timestamp", "inputs_json",
        ))
        official_clause = (
            "baseline_version = ? AND model_version = ? AND calibration_version = ? "
            f"AND selection_policy_version = ? AND {complete}"
        )
        official_params = (
            manifest["baseline_version"], manifest["model_version"],
            manifest["calibration_version"], manifest["selection_policy_version"],
        )

    def count(where: str, params: tuple = ()) -> int:
        return conn.execute(
            f"SELECT COUNT(*) FROM picks WHERE model_source IN ({sources}) AND {where}",
            (*_AUTOMATED_PIPELINE_MODEL_SOURCES, *params),
        ).fetchone()[0]

    settled_eligible = count(f"result IN ('won', 'lost') AND {official_clause}", official_params)
    settled_pipeline = count("result IN ('won', 'lost')")
    excluded_non_pipeline = conn.execute(
        f"SELECT COUNT(*) FROM picks WHERE model_source NOT IN ({sources})",
        _AUTOMATED_PIPELINE_MODEL_SOURCES,
    ).fetchone()[0]

    return {
        "baseline_version": manifest["baseline_version"] if manifest else None,
        "settled_eligible": settled_eligible,
        "target": 1000,
        "progress_pct": round(min(100.0, settled_eligible / 1000.0 * 100.0), 2),
        "excluded_voided": count(f"result = 'void' AND {official_clause}", official_params),
        "excluded_non_pipeline": excluded_non_pipeline,
        "excluded_missing_or_mismatched_provenance": settled_pipeline - settled_eligible,
        "pending_eligible": count(f"result IS NULL AND {official_clause}", official_params),
    }


PROVENANCE_COLUMNS = (
    "baseline_version", "model_version", "calibration_version", "selection_policy_version",
    "code_revision", "runtime", "input_data_timestamp", "inputs_json",
)

_INSERT_PICK_SQL = f"""INSERT INTO picks
               (match_id, market, selection, model_prob, implied_prob, edge,
                odds_at_pick, confidence, league_reliability, grade, stake_units,
                model_source, {", ".join(PROVENANCE_COLUMNS)})
               VALUES (:match_id, :market, :selection, :model_prob, :implied_prob,
                       :edge, :odds_at_pick, :confidence, :league_reliability,
                       :grade, :stake_units, :model_source,
                       {", ".join(":" + c for c in PROVENANCE_COLUMNS)})"""


def _pick_row(pick: dict) -> dict:
    row = {**pick, "model_source": pick.get("model_source", "unknown")}
    for column in PROVENANCE_COLUMNS:
        row.setdefault(column, None)
    return row


@retry_on_db_lock()
def insert_pick(conn: sqlite3.Connection, pick: dict) -> int:
    """Insert a pick and return its ID."""
    with conn:
        cursor = conn.execute(
            _INSERT_PICK_SQL,
            _pick_row(pick),
        )
        return cursor.lastrowid


@retry_on_db_lock()
def insert_picks_batch(conn: sqlite3.Connection, picks: list[dict]) -> None:
    """Insert multiple picks atomically in a single transaction."""
    if not picks:
        return
    with conn:
        conn.executemany(
            _INSERT_PICK_SQL,
            [_pick_row(p) for p in picks],
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

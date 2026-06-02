from __future__ import annotations
"""
SQLite database connection and schema initialization.
"""

import sqlite3
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("football_predictor")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
HOME_DB_PATH = Path.home() / ".football_predictor" / "engine.db"
REPO_DB_PATH = _PROJECT_ROOT / "data" / "engine.db"

_connection: Optional[sqlite3.Connection] = None


def _resolve_db_path() -> tuple[Path, str]:
    if HOME_DB_PATH.exists():
        return HOME_DB_PATH, "home"
    return REPO_DB_PATH, "repo"


def get_db_path() -> Path:
    return _resolve_db_path()[0]


def get_db_source() -> str:
    return _resolve_db_path()[1]


def get_db() -> sqlite3.Connection:
    """Get or create a SQLite connection (singleton per process)."""
    global _connection
    if _connection is None:
        db_path, db_source = _resolve_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _connection = sqlite3.connect(str(db_path), check_same_thread=False)
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA journal_mode=WAL")
        _connection.execute("PRAGMA foreign_keys=ON")
        init_db(_connection)
        logger.info(f"SQLite database initialized at {db_path} ({db_source})")
    return _connection


def get_db_debug_info() -> dict[str, Any]:
    conn = get_db()
    matches_count = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    history_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='match_history'",
    ).fetchone()
    match_history_count = 0
    if history_table:
        match_history_count = conn.execute(
            "SELECT COUNT(*) FROM match_history",
        ).fetchone()[0]
    return {
        "db_path": str(get_db_path()),
        "db_source": get_db_source(),
        "matches_count": matches_count,
        "match_history_count": match_history_count,
        "fixture_source": "match_history" if match_history_count else ("matches" if matches_count else "none"),
    }


def get_match_history_date_coverage() -> dict[str, Any]:
    conn = get_db()
    history_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='match_history'",
    ).fetchone()
    if not history_table:
        return {
            "first_date": None,
            "last_date": None,
            "available_dates": [],
            "total_match_history_rows": 0,
        }

    rows = conn.execute(
        "SELECT match_date, COUNT(*) as total FROM match_history GROUP BY match_date ORDER BY match_date ASC",
    ).fetchall()
    available_dates = [row["match_date"] for row in rows if row["match_date"]]
    total_rows = sum(row["total"] for row in rows)
    return {
        "first_date": available_dates[0] if available_dates else None,
        "last_date": available_dates[-1] if available_dates else None,
        "available_dates": available_dates,
        "total_match_history_rows": total_rows,
    }


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS matches (
            id              TEXT PRIMARY KEY,
            date            DATE NOT NULL,
            kickoff         TEXT,
            home_team       TEXT NOT NULL,
            away_team       TEXT NOT NULL,
            league_name     TEXT NOT NULL,
            league_id       INTEGER,
            status          TEXT DEFAULT 'NS',
            home_goals      INTEGER,
            away_goals      INTEGER,
            total_corners   INTEGER,
            total_cards     INTEGER,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS odds_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id        TEXT NOT NULL REFERENCES matches(id),
            market          TEXT NOT NULL,
            selection       TEXT NOT NULL,
            odds            REAL NOT NULL,
            bookmaker       TEXT DEFAULT 'sofascore',
            timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_opening      BOOLEAN DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS picks (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id        TEXT NOT NULL REFERENCES matches(id),
            market          TEXT NOT NULL,
            selection       TEXT NOT NULL,
            model_prob      REAL NOT NULL,
            implied_prob    REAL NOT NULL,
            edge            REAL NOT NULL,
            odds_at_pick    REAL NOT NULL,
            confidence      REAL DEFAULT 0.5,
            league_reliability REAL DEFAULT 0.5,
            grade           TEXT NOT NULL,
            stake_units     REAL DEFAULT 0.0,
            result          TEXT,
            pnl_units       REAL,
            clv             REAL,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS league_profiles (
            league_id       INTEGER PRIMARY KEY,
            name            TEXT NOT NULL,
            avg_home_goals  REAL DEFAULT 1.5,
            avg_away_goals  REAL DEFAULT 1.1,
            draw_pct        REAL DEFAULT 0.25,
            home_advantage  REAL DEFAULT 0.3,
            btts_pct        REAL DEFAULT 0.50,
            reliability_score REAL DEFAULT 5.0,
            min_edge_threshold REAL DEFAULT 0.05,
            max_stake_units REAL DEFAULT 1.0
        );

        CREATE INDEX IF NOT EXISTS idx_odds_match ON odds_snapshots(match_id);
        CREATE INDEX IF NOT EXISTS idx_picks_match ON picks(match_id);
        CREATE INDEX IF NOT EXISTS idx_picks_date  ON picks(created_at);
        CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(date);
        
        CREATE TABLE IF NOT EXISTS daily_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            predictions_json TEXT,
            UNIQUE(match_id, generated_at)
        );

        CREATE TABLE IF NOT EXISTS daily_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            actual_home_goals INTEGER,
            actual_away_goals INTEGER,
            predictions_json TEXT,
            hit BOOLEAN,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()

    _seed_league_profiles(conn)


def _seed_league_profiles(conn: sqlite3.Connection) -> None:
    """Insert default league profiles if table is empty."""
    count = conn.execute("SELECT COUNT(*) FROM league_profiles").fetchone()[0]
    if count > 0:
        return

    profiles = [
        # (league_id, name, home_g, away_g, draw%, home_adv, btts%, reliability, min_edge, max_stake)
        (17,  "Premier League",      1.55, 1.20, 0.23, 0.35, 0.52, 9.0, 0.04, 2.0),
        (8,   "LaLiga",              1.48, 1.07, 0.25, 0.41, 0.48, 9.0, 0.04, 2.0),
        (23,  "Serie A",             1.50, 1.10, 0.24, 0.40, 0.50, 9.0, 0.04, 2.0),
        (35,  "Bundesliga",          1.65, 1.30, 0.22, 0.35, 0.55, 9.0, 0.04, 2.0),
        (34,  "Ligue 1",             1.50, 1.10, 0.24, 0.38, 0.48, 8.5, 0.04, 2.0),
        (18,  "Championship",        1.45, 1.15, 0.26, 0.30, 0.50, 7.5, 0.055, 1.25),
        (37,  "Eredivisie",          1.70, 1.40, 0.20, 0.35, 0.58, 7.5, 0.055, 1.25),
        (238, "Primeira Liga",       1.45, 1.10, 0.24, 0.38, 0.48, 7.5, 0.055, 1.25),
        (38,  "Belgian Pro League",  1.40, 1.10, 0.26, 0.30, 0.48, 6.5, 0.065, 1.0),
        (52,  "Süper Lig",           1.48, 1.25, 0.22, 0.33, 0.52, 6.5, 0.065, 1.0),
        (36,  "Scottish Premiership",1.50, 1.15, 0.24, 0.35, 0.50, 6.5, 0.065, 1.0),
        (7,   "Champions League",    1.55, 1.25, 0.20, 0.30, 0.52, 9.0, 0.04, 2.0),
        (679, "Europa League",       1.45, 1.15, 0.24, 0.28, 0.50, 8.0, 0.05, 1.5),
        (17015,"Conference League",  1.50, 1.20, 0.22, 0.28, 0.50, 7.0, 0.06, 1.0),
    ]
    conn.executemany(
        """INSERT OR IGNORE INTO league_profiles
           (league_id, name, avg_home_goals, avg_away_goals, draw_pct,
            home_advantage, btts_pct, reliability_score, min_edge_threshold, max_stake_units)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        profiles,
    )
    conn.commit()
    logger.info(f"Seeded {len(profiles)} league profiles")

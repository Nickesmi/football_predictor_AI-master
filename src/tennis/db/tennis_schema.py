"""
tennis_schema.py
================
SQLite table definitions for the tennis prediction engine.
All tables are prefixed with `tennis_` to ensure zero overlap with football tables.
Football tables (matches, predictions, odds_snapshots, etc.) are NEVER modified here.
"""

from __future__ import annotations
import sqlite3
import logging

logger = logging.getLogger("football_predictor.tennis")

TENNIS_SCHEMA_SQL = """
-- ── Core match record (structural baseline, populated by daily refresh) ──────
CREATE TABLE IF NOT EXISTS tennis_matches (
    match_id        TEXT PRIMARY KEY,
    date            TEXT NOT NULL,
    start_time      TEXT,
    tournament      TEXT,
    surface         TEXT,
    player_1        TEXT NOT NULL,
    player_2        TEXT NOT NULL,
    rank_1          INTEGER,
    rank_2          INTEGER,
    status          TEXT DEFAULT 'NS',
    sets_1          INTEGER DEFAULT 0,
    sets_2          INTEGER DEFAULT 0,
    games_1         INTEGER DEFAULT 0,
    games_2         INTEGER DEFAULT 0,
    provider        TEXT,
    is_stale        INTEGER DEFAULT 0,
    last_live_update TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- ── Prediction outputs per market ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tennis_predictions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id                TEXT NOT NULL,
    prediction_time         TEXT NOT NULL,
    market_type             TEXT NOT NULL,
    selection               TEXT NOT NULL,
    predicted_probability   REAL NOT NULL,
    fair_odds               REAL,
    confidence_score        REAL,
    model_version           TEXT DEFAULT 'v1.0-elo',
    features_json           TEXT,
    result                  INTEGER,          -- NULL until settled: 1=win, 0=loss
    created_at              TEXT DEFAULT (datetime('now'))
);

-- ── Settled match results (from daily refresh ONLY — never from live provider) ──
CREATE TABLE IF NOT EXISTS tennis_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id        TEXT NOT NULL UNIQUE,
    winner          TEXT NOT NULL,
    sets_1          INTEGER NOT NULL,
    sets_2          INTEGER NOT NULL,
    settled_at      TEXT NOT NULL,
    settlement_source TEXT DEFAULT 'daily_refresh',   -- always 'daily_refresh'
    created_at      TEXT DEFAULT (datetime('now'))
);

-- ── Odds snapshots at prediction time ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tennis_odds_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id        TEXT NOT NULL,
    market          TEXT NOT NULL,
    selection       TEXT NOT NULL,
    odds            REAL NOT NULL,
    bookmaker       TEXT,
    captured_at     TEXT DEFAULT (datetime('now'))
);

-- ── Running Elo ratings per player per surface ────────────────────────────────
CREATE TABLE IF NOT EXISTS tennis_player_state (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name     TEXT NOT NULL,
    surface         TEXT NOT NULL,               -- 'hard' | 'clay' | 'grass' | 'overall'
    elo             REAL NOT NULL DEFAULT 1500.0,
    matches_played  INTEGER DEFAULT 0,
    last_match_date TEXT,
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(player_name, surface)
);

-- ── Governance audit log ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tennis_baseline_contract (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event           TEXT NOT NULL,
    detail          TEXT,
    settled_count   INTEGER,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_tennis_matches_date   ON tennis_matches(date);
CREATE INDEX IF NOT EXISTS idx_tennis_matches_status ON tennis_matches(status);
CREATE INDEX IF NOT EXISTS idx_tennis_preds_match    ON tennis_predictions(match_id);
CREATE INDEX IF NOT EXISTS idx_tennis_player_surface ON tennis_player_state(player_name, surface);
"""


def init_tennis_db(conn: sqlite3.Connection) -> None:
    """
    Initialize tennis tables in the shared engine.db.
    Safe to call multiple times (CREATE TABLE IF NOT EXISTS).
    Football tables are never touched.
    """
    conn.executescript(TENNIS_SCHEMA_SQL)
    conn.commit()
    logger.info("Tennis database schema initialized.")

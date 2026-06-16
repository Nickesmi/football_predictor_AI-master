from __future__ import annotations
"""
SQLite database connection and schema initialization.
"""

import sqlite3
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("football_predictor")

import sys

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_ENV_DB_PATH = os.getenv("FOOTBALL_PREDICTOR_DB_PATH")

if _ENV_DB_PATH:
    DB_PATH = Path(_ENV_DB_PATH).expanduser()
elif getattr(sys, 'frozen', False):
    DB_PATH = Path.home() / ".football_predictor" / "engine.db"
else:
    DB_PATH = _PROJECT_ROOT / "data" / "engine.db"

_connection: Optional[sqlite3.Connection] = None


def get_db() -> sqlite3.Connection:
    """Get or create a SQLite connection (singleton per process)."""
    global _connection
    if _connection is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _connection = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA journal_mode=WAL")
        _connection.execute("PRAGMA foreign_keys=ON")
        init_db(_connection)
        logger.info(f"SQLite database initialized at {DB_PATH}")
    return _connection


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
            provider        TEXT DEFAULT 'sofascore',
            data_quality_score REAL,
            is_main_fixture BOOLEAN,
            is_stale        BOOLEAN DEFAULT 0,
            provider_error  TEXT,
            last_live_update TIMESTAMP,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS pending_settlements (
            event_id        TEXT PRIMARY KEY,
            first_ft_seen   TIMESTAMP NOT NULL,
            provider        TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            confirmed       BOOLEAN DEFAULT 0,
            attempts        INTEGER DEFAULT 0,
            last_check      TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS provider_health_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            provider        TEXT NOT NULL,
            request_id      TEXT,
            endpoint        TEXT,
            success         INTEGER NOT NULL,
            latency_ms      INTEGER,
            fixture_count   INTEGER DEFAULT 0,
            odds_count      INTEGER DEFAULT 0,
            statistics_count INTEGER DEFAULT 0,
            lineups_count    INTEGER DEFAULT 0,
            live_updates     INTEGER DEFAULT 0,
            error_message   TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS odds_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id        TEXT NOT NULL REFERENCES matches(id),
            market          TEXT NOT NULL,
            selection       TEXT NOT NULL,
            odds            REAL NOT NULL,
            bookmaker       TEXT DEFAULT 'sofascore',
            implied_probability REAL,
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
            opening_odds    REAL,
            closing_odds    REAL,
            clv_pct         REAL,
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

        -- ═══ Logo Registry ═══
        CREATE TABLE IF NOT EXISTS team_logo_registry (
            team_id         TEXT PRIMARY KEY,
            team_name       TEXT NOT NULL,
            provider        TEXT,
            logo_url        TEXT,
            local_path      TEXT,
            etag            TEXT,
            width           INTEGER,
            height          INTEGER,
            file_size       INTEGER,
            sharpness_score REAL,
            quality_score   REAL,
            quality_grade   TEXT,
            logo_source_rank INTEGER,
            upgrade_reason  TEXT,
            recheck_after_days INTEGER,
            last_downloaded TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- ═══ PHASE 3: Prediction Logging ═══
        -- Every prediction from the results evaluation flow is logged here.
        -- This is the raw data source for calibration + backtesting.
        CREATE TABLE IF NOT EXISTS prediction_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id        TEXT NOT NULL,
            match_date      DATE NOT NULL,
            home_team       TEXT NOT NULL,
            away_team       TEXT NOT NULL,
            league_name     TEXT NOT NULL,
            market          TEXT NOT NULL,
            market_type     TEXT NOT NULL,   -- goals, result, btts, cs, combo, handicap, corners, cards
            predicted_prob  REAL NOT NULL,   -- model probability (0-100)
            actual_outcome  INTEGER,         -- 1 = hit, 0 = miss, NULL = unsettled
            tier            INTEGER,         -- which tier (1-6) this pick was in
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_predlog_date ON prediction_log(match_date);
        CREATE INDEX IF NOT EXISTS idx_predlog_type ON prediction_log(market_type);
        CREATE INDEX IF NOT EXISTS idx_predlog_match ON prediction_log(match_id);

        -- ═══ PHASE 3: Calibration Curves (Isotonic) ═══
        -- Stores per-market-type calibration mappings.
        -- Each row maps a predicted probability to a calibrated probability.
        CREATE TABLE IF NOT EXISTS calibration_curves (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            market_type     TEXT NOT NULL,
            raw_prob        REAL NOT NULL,   -- predicted probability (0-1)
            calibrated_prob REAL NOT NULL,   -- actual observed rate (0-1)
            sample_count    INTEGER NOT NULL, -- how many samples in this bucket
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_calcurve_type ON calibration_curves(market_type);

        -- ═══ PHASE 3: Daily Performance Tracking ═══
        -- Aggregated daily stats for ROI dashboard + CLV tracking.
        CREATE TABLE IF NOT EXISTS daily_performance (
            date            DATE PRIMARY KEY,
            total_predictions INTEGER DEFAULT 0,
            total_settled   INTEGER DEFAULT 0,
            total_correct   INTEGER DEFAULT 0,
            total_wrong     INTEGER DEFAULT 0,
            accuracy_pct    REAL DEFAULT 0.0,
            avg_predicted_prob REAL DEFAULT 0.0,
            avg_actual_rate REAL DEFAULT 0.0,
            calibration_gap REAL DEFAULT 0.0,  -- predicted - actual (positive = overconfident)
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- ═══ PHASE 3: Isotonic Calibration Models ═══
        -- Stores fitted isotonic regression models per market type.
        -- fitted_json contains the piecewise-linear breakpoints.
        CREATE TABLE IF NOT EXISTS calibration_models (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            market_type     TEXT NOT NULL,
            fitted_json     TEXT NOT NULL,
            samples         INTEGER NOT NULL,
            brier_score     REAL,
            log_loss        REAL,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- ═══ PHASE 4: Live Adaptive Pipeline ═══
        -- Persistent team state that evolves after every match.
        -- This is THE core table that makes predictions dynamic.
        CREATE TABLE IF NOT EXISTS team_state (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            team_name       TEXT NOT NULL,
            team_name_lower TEXT NOT NULL,
            league          TEXT NOT NULL,
            venue           TEXT NOT NULL,       -- 'home' | 'away' | 'overall'

            -- ELO Rating (evolves after every match)
            elo             REAL DEFAULT 1500.0,

            -- Strength Ratings (attack/defense relative to league avg)
            attack_rating   REAL DEFAULT 1.0,
            defense_rating  REAL DEFAULT 1.0,

            -- Rolling Averages (exponentially weighted, recent matches)
            rolling_scored      REAL DEFAULT 1.2,
            rolling_conceded    REAL DEFAULT 1.2,
            rolling_xg          REAL DEFAULT 1.2,
            rolling_xga         REAL DEFAULT 1.2,
            rolling_corners     REAL DEFAULT 5.0,
            rolling_cards       REAL DEFAULT 2.0,

            -- Form Metrics
            form_last5      REAL DEFAULT 0.5,    -- win% from last 5 matches
            form_last10     REAL DEFAULT 0.5,    -- win% from last 10 matches
            win_streak      INTEGER DEFAULT 0,
            unbeaten_streak INTEGER DEFAULT 0,

            -- Fatigue
            matches_last_14d INTEGER DEFAULT 0,
            rest_days        INTEGER DEFAULT 7,

            -- Meta
            matches_played  INTEGER DEFAULT 0,
            last_match_date DATE,
            last_match_id   TEXT,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(team_name_lower, league, venue)
        );

        CREATE INDEX IF NOT EXISTS idx_teamstate_name ON team_state(team_name_lower);
        CREATE INDEX IF NOT EXISTS idx_teamstate_league ON team_state(league);

        -- Match History — every finished match fed back into the system
        CREATE TABLE IF NOT EXISTS match_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id        TEXT UNIQUE NOT NULL,
            match_date      DATE NOT NULL,
            league          TEXT NOT NULL,
            home_team       TEXT NOT NULL,
            away_team       TEXT NOT NULL,
            home_goals      INTEGER NOT NULL,
            away_goals      INTEGER NOT NULL,
            home_xg         REAL,
            away_xg         REAL,
            home_corners    INTEGER,
            away_corners    INTEGER,
            home_cards      INTEGER,
            away_cards      INTEGER,

            -- ELO snapshots at time of ingestion
            home_elo_before REAL,
            away_elo_before REAL,
            home_elo_after  REAL,
            away_elo_after  REAL,

            ingested_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_matchhist_date ON match_history(match_date);
        CREATE INDEX IF NOT EXISTS idx_matchhist_team ON match_history(home_team);

        -- ═══ PHASE 5: Scoreline Intelligence Reform v2 ═══
        -- Store full probability matrix at prediction time
        CREATE TABLE IF NOT EXISTS scoreline_predictions (
            fixture_id      TEXT PRIMARY KEY,
            matrix_json     TEXT NOT NULL,
            predicted_home_xg REAL,
            predicted_away_xg REAL,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Log hit rates and xG error at settlement time
        CREATE TABLE IF NOT EXISTS scoreline_learning_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fixture_id      TEXT UNIQUE NOT NULL,
            league_name     TEXT,
            season          TEXT,
            match_date      DATE,
            
            predicted_top_score TEXT,
            predicted_top_probability REAL,
            
            actual_score    TEXT,
            actual_rank     INTEGER,
            actual_probability REAL,
            
            top1_hit        BOOLEAN,
            top3_hit        BOOLEAN,
            top5_hit        BOOLEAN,
            top10_hit       BOOLEAN,
            
            predicted_home_xg REAL,
            predicted_away_xg REAL,
            actual_home_goals INTEGER,
            actual_away_goals INTEGER,
            home_goal_error   REAL,
            away_goal_error   REAL,
            total_goal_error  REAL,
            
            confidence_bucket TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE INDEX IF NOT EXISTS idx_scoreline_league ON scoreline_learning_log(league_name);
        CREATE INDEX IF NOT EXISTS idx_scoreline_date ON scoreline_learning_log(match_date);

        -- ═══ BILQE: Qualification Log ═══
        -- Tracks every layer's contribution per pick for ROI/Brier/CLV reporting.
        CREATE TABLE IF NOT EXISTS qualification_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id        TEXT NOT NULL,
            match_date      DATE NOT NULL,
            home_team       TEXT NOT NULL,
            away_team       TEXT NOT NULL,
            league_name     TEXT NOT NULL,
            market          TEXT NOT NULL,
            pick_quality_score REAL NOT NULL,
            tier            TEXT NOT NULL,
            data_quality_score REAL,
            model_consensus_score REAL,
            calibration_score REAL,
            edge_score      REAL,
            volatility_score REAL,
            xg_reality_score REAL,
            clv_adjustment  REAL,
            roi_adjustment  REAL,
            rejection_reason TEXT,
            actual_outcome  INTEGER,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_quallog_date ON qualification_log(match_date);
        CREATE INDEX IF NOT EXISTS idx_quallog_tier ON qualification_log(tier);
        CREATE INDEX IF NOT EXISTS idx_quallog_league ON qualification_log(league_name);

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

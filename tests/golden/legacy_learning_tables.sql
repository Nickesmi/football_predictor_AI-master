-- DDL captured verbatim from the live data/engine.db (2026-10-11); no CREATE statement existed in git history.
-- Only change: IF NOT EXISTS, so applying it is idempotent.

CREATE TABLE IF NOT EXISTS model_confidence_adjustments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            league_name     TEXT NOT NULL UNIQUE,
            country         TEXT DEFAULT '',
            sample_count    INTEGER DEFAULT 0,
            avg_confidence  REAL DEFAULT 0.0,
            actual_hit_rate REAL DEFAULT 0.0,
            adjustment_factor REAL DEFAULT 1.0,  -- shrinkage factor (applied when sample_count >= 100)
            overconfidence  REAL DEFAULT 0.0,    -- avg_confidence - actual_hit_rate
            brier_score     REAL DEFAULT 0.0,
            reliability_score REAL DEFAULT 0.0,  -- 0-100 composite reliability score
            confidence_penalty REAL DEFAULT 0.0, -- pp to subtract when overconfident
            confidence_boost   REAL DEFAULT 0.0, -- pp to add when underconfident
            status          TEXT DEFAULT 'insufficient_data', -- 'trusted', 'developing', 'insufficient_data'
            last_updated    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

CREATE INDEX IF NOT EXISTS idx_confadj_league ON model_confidence_adjustments(league_name);

CREATE TABLE IF NOT EXISTS prediction_errors (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,

            -- Match identity
            fixture_id          TEXT NOT NULL,
            match_date          DATE NOT NULL,
            league_name         TEXT NOT NULL,
            country             TEXT DEFAULT '',
            competition_type    TEXT DEFAULT 'men',  -- men/women/youth/friendly/international

            -- Teams
            home_team           TEXT NOT NULL,
            away_team           TEXT NOT NULL,

            -- Prediction (1X2 result market)
            predicted_result    TEXT NOT NULL,       -- 'home' | 'draw' | 'away'
            home_win_pct        REAL NOT NULL,
            draw_pct            REAL NOT NULL,
            away_win_pct        REAL NOT NULL,
            confidence          REAL NOT NULL,       -- max(home_win_pct, draw_pct, away_win_pct)
            confidence_bucket   TEXT NOT NULL,       -- '60-65%', '65-70%', ... '90-95%', '95-100%'

            -- Actual outcome
            actual_result       TEXT,               -- 'home' | 'draw' | 'away' (NULL = not yet settled)
            home_goals          INTEGER,
            away_goals          INTEGER,

            -- Evaluation
            correct             INTEGER,            -- 1 = correct, 0 = wrong, NULL = unsettled
            confidence_error    REAL,               -- abs(confidence - correct*100)
            probability_error   REAL,               -- Brier score contribution: (p - y)^2
            score_error         REAL,               -- abs(predicted_total - actual_total)

            settled_at          TIMESTAMP,
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(fixture_id)
        );

CREATE INDEX IF NOT EXISTS idx_prederr_bucket  ON prediction_errors(confidence_bucket);

CREATE INDEX IF NOT EXISTS idx_prederr_correct ON prediction_errors(correct);

CREATE INDEX IF NOT EXISTS idx_prederr_country ON prediction_errors(country);

CREATE INDEX IF NOT EXISTS idx_prederr_date    ON prediction_errors(match_date);

CREATE INDEX IF NOT EXISTS idx_prederr_league  ON prediction_errors(league_name);

CREATE INDEX IF NOT EXISTS idx_prederr_type    ON prediction_errors(competition_type);

CREATE TABLE IF NOT EXISTS team_learning_state (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            team_name       TEXT NOT NULL UNIQUE,
            league          TEXT NOT NULL,
            rating          REAL DEFAULT 1500.0,
            momentum_score  REAL DEFAULT 0.0,
            volatility_score REAL DEFAULT 0.0,
            matches_played  INTEGER DEFAULT 0,
            home_matches    INTEGER DEFAULT 0,
            away_matches    INTEGER DEFAULT 0,
            home_points     INTEGER DEFAULT 0,
            away_points     INTEGER DEFAULT 0,
            home_goal_diff  INTEGER DEFAULT 0,
            away_goal_diff  INTEGER DEFAULT 0,
            last_updated    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

CREATE INDEX IF NOT EXISTS idx_teamlearning_league ON team_learning_state(league);

CREATE INDEX IF NOT EXISTS idx_teamlearning_rating ON team_learning_state(rating DESC);

CREATE TABLE IF NOT EXISTS team_rating_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fixture_id      TEXT NOT NULL,
            match_date      DATE NOT NULL,
            team_name       TEXT NOT NULL,
            opponent        TEXT NOT NULL,
            rating_before   REAL NOT NULL,
            rating_after    REAL NOT NULL,
            momentum_before REAL NOT NULL,
            momentum_after  REAL NOT NULL,
            expected_result REAL NOT NULL,
            actual_result   REAL NOT NULL,
            goal_diff       INTEGER DEFAULT 0,
            rating_change   REAL NOT NULL,
            matches_played  INTEGER NOT NULL,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

CREATE INDEX IF NOT EXISTS idx_teamrating_fix ON team_rating_history(fixture_id);

CREATE INDEX IF NOT EXISTS idx_teamrating_team ON team_rating_history(team_name);

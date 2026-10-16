"""Shadow tables. Every row is an observation only: the CHECK constraints make counting impossible."""
from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_cycles (
    cycle_id        TEXT PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    discovery_ran   INTEGER NOT NULL DEFAULT 0,
    report_json     TEXT
);

CREATE TABLE IF NOT EXISTS shadow_fixtures (
    fixture_id      TEXT PRIMARY KEY REFERENCES matches(id),
    provider        TEXT NOT NULL,
    league_id       INTEGER,
    season          INTEGER,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    kickoff_utc     TEXT,
    inserted_by_shadow INTEGER NOT NULL DEFAULT 0,
    last_odds_fetch_at TEXT
);

CREATE TABLE IF NOT EXISTS shadow_observations (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id              TEXT NOT NULL,
    fixture_id            TEXT NOT NULL REFERENCES matches(id),
    decided_at            TEXT NOT NULL,
    kickoff               TEXT NOT NULL,
    minutes_to_kickoff    REAL NOT NULL,
    league_name           TEXT,
    code_revision         TEXT,
    model_version         TEXT,
    calibration_version   TEXT,
    legacy_policy_version TEXT NOT NULL,
    v1_policy_version     TEXT NOT NULL,
    probability_inputs_json TEXT,
    raw_probabilities_json  TEXT,
    forecast_json         TEXT,
    data_quality          REAL,
    model_source          TEXT,
    quote_status          TEXT,
    quote_age_minutes     REAL,
    quote_bucket          TEXT,
    candidates_json       TEXT NOT NULL,
    legacy_json           TEXT NOT NULL,
    v1_json               TEXT NOT NULL,
    v1_assuming_frozen_json TEXT NOT NULL,
    bilqe_json            TEXT,
    error                 TEXT,
    is_shadow             INTEGER NOT NULL DEFAULT 1 CHECK (is_shadow = 1),
    counts_toward_baseline INTEGER NOT NULL DEFAULT 0 CHECK (counts_toward_baseline = 0),
    UNIQUE (fixture_id)
);

CREATE TABLE IF NOT EXISTS shadow_outcomes (
    fixture_id      TEXT PRIMARY KEY REFERENCES matches(id),
    status          TEXT NOT NULL,
    home_goals_ft   INTEGER NOT NULL,
    away_goals_ft   INTEGER NOT NULL,
    source          TEXT NOT NULL,
    recorded_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shadow_selection_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id      TEXT NOT NULL REFERENCES matches(id),
    policy          TEXT NOT NULL,
    market          TEXT NOT NULL,
    selection       TEXT NOT NULL,
    odds_taken      REAL NOT NULL,
    bookmaker       TEXT,
    result          TEXT NOT NULL CHECK (result IN ('won', 'lost')),
    pnl_flat_units  REAL NOT NULL,
    closing_odds    REAL,
    clv_pct         REAL,
    closing_observed_at TEXT,
    settled_at      TEXT NOT NULL,
    counts_toward_baseline INTEGER NOT NULL DEFAULT 0 CHECK (counts_toward_baseline = 0),
    UNIQUE (fixture_id, policy, market, selection)
);
"""


def ensure_shadow_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)

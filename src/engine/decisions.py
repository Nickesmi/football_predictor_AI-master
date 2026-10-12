"""Structured record of every official-pipeline decision (M2.5).

Each pipeline run writes, per not-yet-started fixture:
  * one match-level row: ACTIONABLE, NO_ACTIONABLE_BET or NO_FORECAST;
  * one row per priced selection: PICK or REJECTED, with every reason.

A fixture without a fresh real bookmaker price still gets its forecast
(forecast_json) but NO_ACTIONABLE_BET. Records are informational: they never
change which selections pass, and they are never counted as picks.
"""
from __future__ import annotations

import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS selection_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    decided_at      TEXT NOT NULL,
    match_id        TEXT NOT NULL REFERENCES matches(id),
    market          TEXT,
    selection       TEXT,
    decision        TEXT NOT NULL CHECK (decision IN
                        ('ACTIONABLE', 'NO_ACTIONABLE_BET', 'NO_FORECAST', 'PICK', 'REJECTED')),
    reasons_json    TEXT NOT NULL,
    model_prob      REAL,
    implied_prob    REAL,
    edge            REAL,
    odds            REAL,
    bookmaker       TEXT,
    odds_provider   TEXT,
    odds_type       TEXT,
    odds_observed_at TEXT,
    grade           TEXT,
    stake_units     REAL,
    forecast_json   TEXT,
    code_revision   TEXT,
    baseline_version TEXT
);
CREATE INDEX IF NOT EXISTS idx_selection_decisions_match ON selection_decisions(match_id, run_id);
"""

NO_REAL_ODDS = {"code": "NO_REAL_ODDS",
                "detail": "no fresh pre-match bookmaker price for 1X2, O/U 2.5 or BTTS"}
NO_POSITIVE_EDGE = "NO_POSITIVE_EDGE"
GRADE_PASS = "GRADE_PASS"
ALL_SELECTIONS_REJECTED = {"code": "ALL_SELECTIONS_REJECTED"}
PROBABILITY_ENGINE_FAILED = "PROBABILITY_ENGINE_FAILED"


def ensure_decision_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def candidate_fields(c: dict) -> dict:
    return {key: c.get(key) for key in (
        "model_prob", "implied_prob", "edge", "odds", "bookmaker", "odds_provider", "odds_type", "odds_observed_at",
    )}


def record(conn: sqlite3.Connection, rows: list[dict]) -> None:
    columns = ("run_id", "decided_at", "match_id", "market", "selection", "decision", "reasons_json",
               "model_prob", "implied_prob", "edge", "odds", "bookmaker", "odds_provider", "odds_type",
               "odds_observed_at", "grade", "stake_units", "forecast_json", "code_revision", "baseline_version")
    with conn:
        conn.executemany(
            f"INSERT INTO selection_decisions ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
            [tuple(json.dumps(row.get(c), sort_keys=True) if c in ("reasons_json", "forecast_json") and row.get(c) is not None
                   else row.get(c) for c in columns) for row in rows],
        )

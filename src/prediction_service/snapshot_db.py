"""
Prediction snapshot persistence (Phase 3 §3/§17).

Deliberately a SEPARATE, dedicated SQLite database
(data/real_historical/predictions.db) rather than new tables bolted onto
the legacy production schema in src/db/database.py. Two reasons:
  1. This whole real-data prediction service is new, offline-validated
     infrastructure (Phase 2/3) sitting alongside the legacy live pipeline
     (Phase 1) — mixing their schemas would risk corrupting the legacy
     system's tables while iterating here.
  2. Every prediction this service makes must be fully reproducible from
     its own snapshot alone; keeping that self-contained makes that
     guarantee easy to verify and test in isolation.

Every prediction (successful or refused) is recorded — including refusals
(Phase 3 §19's "NO PREDICTION" cases) — via log_refusal(), so the
monitoring layer (monitoring.py) can report a true no-prediction rate,
not just a survivorship-biased view of predictions that succeeded.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = _PROJECT_ROOT / "data" / "real_historical" / "predictions.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    prediction_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    league TEXT NOT NULL,
    market TEXT NOT NULL,
    prediction_timestamp TEXT NOT NULL,
    feature_snapshot_timestamp TEXT NOT NULL,
    feature_snapshot_json TEXT NOT NULL,
    champion_model TEXT NOT NULL,
    model_provenance TEXT NOT NULL,
    calibration_version TEXT,
    raw_probability REAL NOT NULL,
    calibrated_probability REAL NOT NULL,
    confidence_score REAL NOT NULL,
    confidence_tier TEXT NOT NULL,
    confidence_components_json TEXT NOT NULL,
    data_sufficiency INTEGER NOT NULL,
    model_agreement_level TEXT NOT NULL,
    model_agreement_spread REAL,
    ood_severity TEXT NOT NULL,
    ood_reasons_json TEXT NOT NULL,
    meets_confidence_threshold INTEGER,
    threshold_used REAL,
    betting_status TEXT,
    betting_reason TEXT,
    pipeline_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS no_prediction_log (
    request_id TEXT PRIMARY KEY,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    league TEXT,
    market TEXT,
    prediction_timestamp TEXT,
    stage_failed TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS post_match_evaluations (
    prediction_id TEXT PRIMARY KEY REFERENCES predictions(prediction_id),
    actual_outcome INTEGER NOT NULL,
    correct INTEGER NOT NULL,
    brier_contribution REAL NOT NULL,
    log_loss_contribution REAL NOT NULL,
    evaluated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shadow_predictions (
    shadow_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    market TEXT NOT NULL,
    prediction_timestamp TEXT NOT NULL,
    champion_model TEXT NOT NULL,
    champion_probability REAL NOT NULL,
    challenger_model TEXT NOT NULL,
    challenger_probability REAL NOT NULL,
    actual_outcome INTEGER,
    champion_brier_contribution REAL,
    challenger_brier_contribution REAL,
    evaluated_at TEXT,
    created_at TEXT NOT NULL
);
"""


def get_connection() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def save_prediction(conn: sqlite3.Connection, record: dict) -> None:
    columns = [
        "prediction_id", "match_id", "home_team", "away_team", "league", "market",
        "prediction_timestamp", "feature_snapshot_timestamp", "feature_snapshot_json",
        "champion_model", "model_provenance", "calibration_version",
        "raw_probability", "calibrated_probability",
        "confidence_score", "confidence_tier", "confidence_components_json",
        "data_sufficiency", "model_agreement_level", "model_agreement_spread",
        "ood_severity", "ood_reasons_json",
        "meets_confidence_threshold", "threshold_used",
        "betting_status", "betting_reason", "pipeline_version", "created_at",
    ]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT OR REPLACE INTO predictions ({', '.join(columns)}) VALUES ({placeholders})",
        [record.get(c) for c in columns],
    )
    conn.commit()


def log_refusal(conn: sqlite3.Connection, record: dict) -> None:
    columns = ["request_id", "home_team", "away_team", "league", "market",
               "prediction_timestamp", "stage_failed", "reason", "created_at"]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT OR REPLACE INTO no_prediction_log ({', '.join(columns)}) VALUES ({placeholders})",
        [record.get(c) for c in columns],
    )
    conn.commit()


def save_post_match_evaluation(conn: sqlite3.Connection, record: dict) -> None:
    columns = ["prediction_id", "actual_outcome", "correct", "brier_contribution",
               "log_loss_contribution", "evaluated_at"]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT OR REPLACE INTO post_match_evaluations ({', '.join(columns)}) VALUES ({placeholders})",
        [record.get(c) for c in columns],
    )
    conn.commit()


def get_prediction(conn: sqlite3.Connection, prediction_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM predictions WHERE prediction_id = ?", (prediction_id,)).fetchone()
    return dict(row) if row else None


def save_shadow_prediction(conn: sqlite3.Connection, record: dict) -> None:
    columns = ["shadow_id", "match_id", "home_team", "away_team", "market", "prediction_timestamp",
               "champion_model", "champion_probability", "challenger_model", "challenger_probability",
               "actual_outcome", "champion_brier_contribution", "challenger_brier_contribution",
               "evaluated_at", "created_at"]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT OR REPLACE INTO shadow_predictions ({', '.join(columns)}) VALUES ({placeholders})",
        [record.get(c) for c in columns],
    )
    conn.commit()


def get_unevaluated_shadow_predictions(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM shadow_predictions WHERE actual_outcome IS NULL").fetchall()
    return [dict(r) for r in rows]

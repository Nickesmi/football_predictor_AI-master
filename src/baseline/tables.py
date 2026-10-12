"""Table routing for frozen mode.

Production readers of learned state read the frozen snapshot of the active
baseline; automatic writers are redirected to *_shadow tables, so learning
can continue without touching what official predictions use.
"""
from __future__ import annotations

import re
import sqlite3

from src import config

FROZEN_TABLES = ("calibration_models", "model_confidence_adjustments")
_VERSION_RE = re.compile(config._BASELINE_VERSION_PATTERN)


def _version() -> str | None:
    version = config.BASELINE_FREEZE
    if version is not None and not _VERSION_RE.match(version):
        raise RuntimeError(f"Invalid baseline version {version!r}")
    return version


def read_source(table: str) -> str:
    """FROM-clause source for `table`: production table, or the active frozen snapshot."""
    assert table in FROZEN_TABLES, table
    version = _version()
    if version is None:
        return table
    return f"(SELECT * FROM {table}_frozen WHERE baseline_version = '{version}')"


def write_target(table: str) -> str:
    """Table automatic writers must use: production, or the shadow table when frozen."""
    assert table in FROZEN_TABLES, table
    return table if _version() is None else f"{table}_shadow"


def ensure_baseline_tables(conn: sqlite3.Connection) -> None:
    """Create frozen/shadow/registry tables. Additive and idempotent (called from init_db)."""
    for table in FROZEN_TABLES:
        ddl_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if ddl_row is None:
            continue
        ddl = ddl_row[0]
        shadow_ddl = re.sub(
            rf"^CREATE TABLE\s+(IF NOT EXISTS\s+)?{table}\b",
            f"CREATE TABLE IF NOT EXISTS {table}_shadow", ddl, count=1,
        )
        conn.execute(shadow_ddl)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {table}_frozen AS "
            f"SELECT '' AS baseline_version, * FROM {table} WHERE 0"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_frozen_version ON {table}_frozen(baseline_version)"
        )
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS baseline_versions (
            baseline_version TEXT PRIMARY KEY,
            manifest_json    TEXT NOT NULL,
            manifest_sha256  TEXT NOT NULL,
            frozen_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS baseline_frozen_artifacts (
            baseline_version TEXT NOT NULL,
            artifact         TEXT NOT NULL,
            payload_json     TEXT NOT NULL,
            sha256           TEXT NOT NULL,
            PRIMARY KEY (baseline_version, artifact)
        );
    """)

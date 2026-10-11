"""init_db() migration for the four error/team-learning tables.

Their DDL used to exist only in the live database. tests/golden/
legacy_learning_tables.sql is that DDL captured verbatim from data/engine.db.
"""
import os
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

from src.db.database import init_db

LEGACY_SQL = Path(__file__).parent / "golden" / "legacy_learning_tables.sql"
TABLES = ("model_confidence_adjustments", "prediction_errors", "team_learning_state", "team_rating_history")


def _norm(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"\bIF NOT EXISTS\s+", "", sql)
    return re.sub(r"\s+", " ", sql).strip().rstrip(";").strip()


def _objects(conn, tables=TABLES):
    marks = ",".join("?" * len(tables))
    rows = conn.execute(
        f"SELECT type, name, sql FROM sqlite_master WHERE tbl_name IN ({marks}) AND sql IS NOT NULL",
        tables,
    ).fetchall()
    return {(t, n): _norm(s) for t, n, s in rows}


def _expected_objects():
    out = {}
    for stmt in re.sub(r"--[^\n]*", "", LEGACY_SQL.read_text()).split(";"):
        stmt = _norm(stmt)
        m = re.match(r"CREATE (TABLE|INDEX) (\w+)", stmt)
        if m:
            out[(m.group(1).lower(), m.group(2))] = stmt
    return out


def _row_counts(conn):
    return {
        name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def test_fresh_db_gets_exact_live_schema_for_the_four_tables():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    assert _objects(conn) == _expected_objects()
    assert len(_expected_objects()) == 15


def test_init_db_is_idempotent():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    before = _objects(conn)
    init_db(conn)
    assert _objects(conn) == before


def test_existing_db_with_data_is_left_untouched():
    conn = sqlite3.connect(":memory:")
    conn.executescript(LEGACY_SQL.read_text())
    conn.execute(
        "INSERT INTO model_confidence_adjustments (league_name, sample_count, adjustment_factor) VALUES ('Serie A', 140, 0.93)"
    )
    conn.execute(
        "INSERT INTO prediction_errors (fixture_id, match_date, league_name, home_team, away_team, predicted_result,"
        " home_win_pct, draw_pct, away_win_pct, confidence, confidence_bucket) "
        "VALUES ('f1', '2026-09-01', 'Serie A', 'Inter', 'Milan', 'home', 52.0, 26.0, 22.0, 52.0, '50-55%')"
    )
    conn.commit()
    schema_before = _objects(conn)
    rows_before = conn.execute("SELECT * FROM model_confidence_adjustments").fetchall()

    init_db(conn)

    assert _objects(conn) == schema_before
    assert conn.execute("SELECT * FROM model_confidence_adjustments").fetchall() == rows_before
    assert conn.execute("SELECT COUNT(*) FROM prediction_errors").fetchone()[0] == 1


@pytest.mark.skipif(not os.getenv("M0_EXISTING_DB_COPY"), reason="set M0_EXISTING_DB_COPY to a COPY of a real engine.db")
def test_real_database_copy_schema_and_rows_unchanged(tmp_path):
    work = tmp_path / "engine_copy.db"
    shutil.copy(os.environ["M0_EXISTING_DB_COPY"], work)
    conn = sqlite3.connect(work)
    all_tables = tuple(n for (n,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    schema_before, rows_before = _objects(conn, all_tables), _row_counts(conn)

    init_db(conn)

    assert _objects(conn, all_tables) == schema_before
    assert _row_counts(conn) == rows_before
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

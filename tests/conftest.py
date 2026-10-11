"""conftest.py — shared fixtures for the football prediction test suite."""
import sqlite3

import pytest

from src.db.database import init_db


@pytest.fixture
def engine_db():
    """In-memory SQLite DB with the full engine schema initialized."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    yield conn
    conn.close()

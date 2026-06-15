"""
conftest.py — Tennis test fixtures
"""
import pytest
import sqlite3

from src.tennis.db.tennis_schema import init_tennis_db


@pytest.fixture
def tennis_db():
    """In-memory SQLite DB with tennis schema initialized."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    # Also create provider_health_log (used by routes)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS provider_health_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            provider        TEXT NOT NULL,
            request_id      TEXT,
            endpoint        TEXT,
            success         INTEGER NOT NULL,
            latency_ms      INTEGER,
            fixture_count   INTEGER DEFAULT 0,
            error_message   TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    init_tennis_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def sample_match():
    return {
        "match_id": "test_001",
        "sport": "tennis",
        "provider": "rapidapi_tennis",
        "date": "2026-06-15",
        "start_time": "14:00",
        "tournament": "Wimbledon",
        "surface": "grass",
        "player_1": "Novak Djokovic",
        "player_2": "Carlos Alcaraz",
        "rank_1": 2,
        "rank_2": 1,
        "status": "NS",
        "sets_1": 0,
        "sets_2": 0,
        "games_1": 0,
        "games_2": 0,
        "point_score": None,
        "is_stale": False,
        "provider_error": None,
        "last_live_update": None,
    }

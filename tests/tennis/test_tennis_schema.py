"""
test_tennis_schema.py
======================
Tests that tennis schema isolation is preserved:
- football tables are never touched
- tennis tables are created correctly
"""
import pytest
import sqlite3
from src.tennis.db.tennis_schema import init_tennis_db

EXPECTED_TENNIS_TABLES = {
    "tennis_matches",
    "tennis_predictions",
    "tennis_results",
    "tennis_odds_snapshots",
    "tennis_player_state",
    "tennis_baseline_contract",
}

# The tables that football uses — must NEVER be touched by tennis schema
FOOTBALL_TABLES = {
    "matches",
    "predictions",
    "odds_snapshots",
}


class TestTennisSchema:
    def test_tennis_tables_created(self, tennis_db):
        tables = {row[0] for row in tennis_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        for expected in EXPECTED_TENNIS_TABLES:
            assert expected in tables, f"Tennis table missing: {expected}"

    def test_football_tables_not_created_by_tennis_schema(self):
        """Tennis schema must not create football tables."""
        conn = sqlite3.connect(":memory:")
        init_tennis_db(conn)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        for ft in FOOTBALL_TABLES:
            assert ft not in tables, f"Tennis schema created football table: {ft}"
        conn.close()

    def test_schema_is_idempotent(self, tennis_db):
        """Calling init_tennis_db twice must not raise."""
        init_tennis_db(tennis_db)  # second call
        tables = {row[0] for row in tennis_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "tennis_matches" in tables

    def test_settlement_source_column_exists(self, tennis_db):
        """tennis_results must have settlement_source column defaulting to 'daily_refresh'."""
        cols = {row[1] for row in tennis_db.execute(
            "PRAGMA table_info(tennis_results)"
        ).fetchall()}
        assert "settlement_source" in cols

    def test_match_winner_insert(self, tennis_db):
        tennis_db.execute("""
            INSERT INTO tennis_matches
              (match_id, date, player_1, player_2, status)
            VALUES ('m1', '2026-06-15', 'Federer', 'Nadal', 'NS')
        """)
        tennis_db.commit()
        row = tennis_db.execute(
            """
            SELECT match_id, date, player_1, player_2, status, sets_1, sets_2
            FROM tennis_matches
            WHERE match_id='m1'
            """
        ).fetchone()
        assert dict(row) == {
            "match_id": "m1",
            "date": "2026-06-15",
            "player_1": "Federer",
            "player_2": "Nadal",
            "status": "NS",
            "sets_1": 0,
            "sets_2": 0,
        }
        assert row["player_1"] == "Federer"

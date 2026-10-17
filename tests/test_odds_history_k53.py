"""M3.3 Package C: odds-history identity with revisions (K53), point-in-time reads, migration, rollback."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.odds import store
from src.odds.model import Quote, iso_utc

NOW = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
KICKOFF = "2030-01-01T15:00:00+00:00"
WINDOW = timedelta(minutes=240)


def _ago(m):
    return iso_utc(NOW - timedelta(minutes=m))


def _fixture(conn, fid="fx"):
    conn.execute("INSERT INTO matches (id, date, home_team, away_team, league_name, status) "
                 "VALUES (?, '2030-01-01', 'A', 'B', 'L', 'NS')", (fid,))


def _q(price, observed, book="bet365", sel="home"):
    return Quote(provider="api_football", provider_event_id="e", bookmaker=book, market="1X2", selection=sel,
                 line="", decimal_odds=price, observed_at=observed, commence_time=KICKOFF)


def _rows(conn):
    return [tuple(r) for r in conn.execute(
        "SELECT bookmaker, decimal_odds, observed_at, fetched_at, revision FROM odds_quotes ORDER BY id")]


@pytest.fixture
def db(engine_db):
    _fixture(engine_db)
    return engine_db


def test_identical_timestamp_changed_price_is_a_new_revision(db):
    assert store.store_quotes(db, "fx", [_q(2.0, _ago(100))], _ago(90)) == 1
    assert store.store_quotes(db, "fx", [_q(1.8, _ago(100))], _ago(30)) == 1
    assert [(r[1], r[4]) for r in _rows(db)] == [(2.0, 0), (1.8, 1)]


def test_price_reverting_under_the_same_stamp_is_still_recorded(db):
    for price, fetched in ((2.0, 90), (1.8, 60), (2.0, 30)):
        store.store_quotes(db, "fx", [_q(price, _ago(100))], _ago(fetched))
    assert [(r[1], r[4]) for r in _rows(db)] == [(2.0, 0), (1.8, 1), (2.0, 2)]


def test_different_timestamp_unchanged_price_is_a_new_observation(db):
    store.store_quotes(db, "fx", [_q(2.0, _ago(200))], _ago(190))
    store.store_quotes(db, "fx", [_q(2.0, _ago(50))], _ago(40))
    assert [(r[1], r[2], r[4]) for r in _rows(db)] == [(2.0, _ago(200), 0), (2.0, _ago(50), 0)]


def test_duplicate_provider_responses_insert_nothing(db):
    payload = [_q(2.0, _ago(100)), _q(4.0, _ago(100), sel="draw")]
    assert store.store_quotes(db, "fx", payload, _ago(90)) == 2
    assert store.store_quotes(db, "fx", payload, _ago(80)) == 0
    assert store.store_quotes(db, "fx", payload, _ago(70)) == 0
    assert len(_rows(db)) == 2 and _rows(db)[0][3] == _ago(90)   # first collection time preserved


def test_bookmakers_are_separate_identities(db):
    store.store_quotes(db, "fx", [_q(2.0, _ago(100), book="bet365"), _q(2.0, _ago(100), book="pinnacle")], _ago(90))
    assert {r[0] for r in _rows(db)} == {"bet365", "pinnacle"}


def test_as_of_reads_the_revision_known_at_decision_time(db):
    store.store_quotes(db, "fx", [_q(2.0, _ago(100))], _ago(90))
    store.store_quotes(db, "fx", [_q(1.8, _ago(100))], _ago(20))       # same stamp, collected later
    early = store.actionable_quotes(db, "fx", NOW - timedelta(minutes=60), WINDOW)
    late = store.actionable_quotes(db, "fx", NOW, WINDOW)
    assert [q["decimal_odds"] for q in early] == [2.0] and [q["decimal_odds"] for q in late] == [1.8]


def test_a_later_collection_never_enters_an_earlier_decision(db):
    store.store_quotes(db, "fx", [_q(2.0, _ago(100))], _ago(5))   # fetched 5 min before NOW
    assert store.actionable_quotes(db, "fx", NOW - timedelta(minutes=10), WINDOW) == []


def test_one_row_per_bookmaker_selection_is_returned(db):
    for price, fetched in ((2.0, 90), (1.9, 60), (1.8, 30)):
        store.store_quotes(db, "fx", [_q(price, _ago(100))], _ago(fetched))
    assert len(store.actionable_quotes(db, "fx", NOW, WINDOW)) == 1


def test_closing_reconstruction_uses_the_last_collected_revision(db):
    from src.shadow import cycle

    store.store_quotes(db, "fx", [_q(2.10, _ago(200))], _ago(190))
    store.store_quotes(db, "fx", [_q(1.95, iso_utc(datetime.fromisoformat(KICKOFF) - timedelta(minutes=40)))],
                       iso_utc(datetime.fromisoformat(KICKOFF) - timedelta(minutes=35)))
    store.store_quotes(db, "fx", [_q(1.90, iso_utc(datetime.fromisoformat(KICKOFF) - timedelta(minutes=40)))],
                       iso_utc(datetime.fromisoformat(KICKOFF) - timedelta(minutes=10)))
    pick = {"market": "1X2", "selection": "home", "bookmaker": "bet365", "odds": 2.10,
            "odds_observed_at": _ago(200), "odds_fetched_at": _ago(190)}
    closing, _at = cycle._closing(db, "fx", pick)
    assert closing == 1.90


def _old_table(conn):
    conn.execute("DROP TABLE odds_quotes")
    conn.execute("""CREATE TABLE odds_quotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, fixture_id TEXT NOT NULL REFERENCES matches(id),
        odds_type TEXT NOT NULL CHECK (odds_type IN ('bookmaker', 'synthetic_test')), provider TEXT NOT NULL,
        provider_event_id TEXT NOT NULL, bookmaker TEXT NOT NULL, market TEXT NOT NULL, selection TEXT NOT NULL,
        line TEXT NOT NULL DEFAULT '', decimal_odds REAL NOT NULL CHECK (decimal_odds > 1.0),
        implied_probability REAL NOT NULL, raw_market TEXT, raw_selection TEXT, observed_at TEXT NOT NULL,
        commence_time TEXT NOT NULL, fetched_at TEXT NOT NULL,
        UNIQUE (fixture_id, provider, bookmaker, market, selection, line, observed_at))""")
    for i, (price, obs) in enumerate(((2.0, _ago(200)), (1.9, _ago(100)))):
        conn.execute("INSERT INTO odds_quotes (fixture_id, odds_type, provider, provider_event_id, bookmaker, market, "
                     "selection, line, decimal_odds, implied_probability, observed_at, commence_time, fetched_at) "
                     "VALUES ('fx', 'bookmaker', 'api_football', 'e', 'bet365', '1X2', 'home', '', ?, ?, ?, ?, ?)",
                     (price, round(1 / price, 6), obs, KICKOFF, obs))
    conn.commit()


def test_migration_preserves_every_row_and_is_idempotent(db):
    _old_table(db)
    before = store._quote_checksum(db)
    result = store.migrate_quote_revisions(db)
    assert result["migrated"] and result["rows"] == 2
    assert store._quote_checksum(db) == before                                  # no price rewritten
    assert {r[4] for r in _rows(db)} == {0}
    assert store.migrate_quote_revisions(db) == {"migrated": False}
    store.store_quotes(db, "fx", [_q(1.85, _ago(100))], _ago(5))               # K53 now possible
    assert [r[4] for r in _rows(db)] == [0, 0, 1]


def test_rollback_archives_revisions_instead_of_deleting(db):
    _old_table(db)
    store.migrate_quote_revisions(db)
    store.store_quotes(db, "fx", [_q(1.85, _ago(100))], _ago(5))
    result = store.rollback_quote_revisions(db)
    assert result == {"rolled_back": True, "archived_revisions": 1}
    cols = {r[1] for r in db.execute("PRAGMA table_info(odds_quotes)")}
    assert "revision" not in cols and len(_rows_old(db)) == 2
    assert db.execute("SELECT decimal_odds FROM odds_quotes_revisions_archive").fetchone()[0] == 1.85


def _rows_old(conn):
    return conn.execute("SELECT decimal_odds FROM odds_quotes").fetchall()


def test_init_db_upgrades_an_old_table_in_place(tmp_path):
    from src.db.database import init_db

    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute("INSERT INTO matches (id, date, home_team, away_team, league_name) VALUES ('fx','2030-01-01','A','B','L')")
    _old_table(conn)
    init_db(conn)
    assert "revision" in {r[1] for r in conn.execute("PRAGMA table_info(odds_quotes)")}
    assert conn.execute("SELECT COUNT(*) FROM odds_quotes").fetchone()[0] == 2

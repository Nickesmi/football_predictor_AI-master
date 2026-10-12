"""Storage for real odds quotes, crowd-vote signals, fixture identity and provider quota use.

odds_quotes accepts only bookmaker prices and, in the test environment only,
explicitly labelled synthetic test prices (enforced by a CHECK constraint and
by store_quotes). Crowd votes live in crowd_vote_signals as shares, never as
prices. Duplicate deliveries of the same price are ignored by a unique key.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.odds.model import MARKETS, OddsType, Quote, iso_utc

SCHEMA = """
CREATE TABLE IF NOT EXISTS fixture_identity (
    provider            TEXT NOT NULL,
    provider_event_id   TEXT NOT NULL,
    fixture_id          TEXT NOT NULL REFERENCES matches(id),
    provider_home       TEXT,
    provider_away       TEXT,
    commence_time       TEXT NOT NULL,
    method              TEXT NOT NULL,
    mapped_at           TEXT NOT NULL,
    PRIMARY KEY (provider, provider_event_id, fixture_id)
);

CREATE TABLE IF NOT EXISTS odds_quotes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id          TEXT NOT NULL REFERENCES matches(id),
    odds_type           TEXT NOT NULL CHECK (odds_type IN ('bookmaker', 'synthetic_test')),
    provider            TEXT NOT NULL,
    provider_event_id   TEXT NOT NULL,
    bookmaker           TEXT NOT NULL,
    market              TEXT NOT NULL,
    selection           TEXT NOT NULL,
    line                TEXT NOT NULL DEFAULT '',
    decimal_odds        REAL NOT NULL CHECK (decimal_odds > 1.0),
    implied_probability REAL NOT NULL,
    raw_market          TEXT,
    raw_selection       TEXT,
    observed_at         TEXT NOT NULL,
    commence_time       TEXT NOT NULL,
    fetched_at          TEXT NOT NULL,
    UNIQUE (fixture_id, provider, bookmaker, market, selection, line, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_odds_quotes_fixture ON odds_quotes(fixture_id, market, selection);

CREATE TABLE IF NOT EXISTS crowd_vote_signals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id          TEXT NOT NULL REFERENCES matches(id),
    source              TEXT NOT NULL,
    market              TEXT NOT NULL,
    selection           TEXT NOT NULL,
    votes               INTEGER NOT NULL,
    share               REAL NOT NULL,
    observed_at         TEXT NOT NULL,
    UNIQUE (fixture_id, source, market, selection, observed_at)
);

CREATE TABLE IF NOT EXISTS provider_quota_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    provider            TEXT NOT NULL,
    endpoint            TEXT NOT NULL,
    cost                INTEGER NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('ok', 'error', 'refused')),
    http_status         INTEGER,
    requests_remaining  TEXT,
    requests_used       TEXT,
    detail              TEXT,
    at                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provider_quota_day ON provider_quota_log(provider, at);
"""

_LIVE_DB = (Path(__file__).resolve().parents[2] / "data" / "engine.db").resolve()


class SyntheticOddsRefused(RuntimeError):
    """Synthetic test prices may only exist in an isolated test database."""


def ensure_odds_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _database_path(conn: sqlite3.Connection) -> Path | None:
    row = conn.execute("PRAGMA database_list").fetchone()
    return Path(row[2]).resolve() if row and row[2] else None


def synthetic_odds_allowed(conn: sqlite3.Connection) -> bool:
    """Only in APP_ENV=test, never while the baseline is frozen, never on the production database."""
    from src.baseline import state as baseline

    return (
        os.getenv("APP_ENV", "").strip().lower() == "test"
        and not baseline.is_frozen()
        and _database_path(conn) != _LIVE_DB
    )


def store_quotes(conn: sqlite3.Connection, fixture_id: str, quotes: list[Quote], fetched_at: str) -> int:
    """Insert quotes for one fixture; returns how many were new (duplicates are ignored)."""
    if any(q.odds_type is OddsType.SYNTHETIC_TEST for q in quotes) and not synthetic_odds_allowed(conn):
        raise SyntheticOddsRefused("synthetic test odds can only be stored in an isolated test database")
    before = conn.total_changes
    conn.executemany(
        """INSERT OR IGNORE INTO odds_quotes
               (fixture_id, odds_type, provider, provider_event_id, bookmaker, market, selection, line,
                decimal_odds, implied_probability, raw_market, raw_selection, observed_at,
                commence_time, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (fixture_id, q.odds_type.value, q.provider, q.provider_event_id, q.bookmaker, q.market,
             q.selection, q.line, q.decimal_odds, q.implied_probability, q.raw_market, q.raw_selection,
             iso_utc(q.observed_at), iso_utc(q.commence_time), iso_utc(fetched_at))
            for q in quotes
        ],
    )
    return conn.total_changes - before


def actionable_quotes(
    conn: sqlite3.Connection,
    fixture_id: str,
    now: datetime,
    max_age: timedelta,
    include_synthetic: bool = False,
) -> list[dict]:
    """Latest pre-match quote per (provider, bookmaker, market, selection, line) that is still fresh.

    A quote is actionable only when the match has not started, the price was
    observed before kickoff and no longer ago than max_age. Older prices of the
    same bookmaker are superseded by its latest one, never maximised over.
    """
    now_text = iso_utc(now)
    oldest = iso_utc(now - max_age)
    types = ("bookmaker", "synthetic_test") if include_synthetic else ("bookmaker",)
    rows = conn.execute(
        f"""SELECT q.* FROM odds_quotes q
            JOIN (
                SELECT provider, bookmaker, market, selection, line, MAX(observed_at) AS latest
                FROM odds_quotes
                WHERE fixture_id = ? AND odds_type IN ({','.join('?' * len(types))}) AND observed_at <= ?
                GROUP BY provider, bookmaker, market, selection, line
            ) last
              ON q.provider = last.provider AND q.bookmaker = last.bookmaker AND q.market = last.market
             AND q.selection = last.selection AND q.line = last.line AND q.observed_at = last.latest
            WHERE q.fixture_id = ? AND q.odds_type IN ({','.join('?' * len(types))})
              AND q.observed_at >= ? AND q.observed_at < q.commence_time AND q.commence_time > ?
            ORDER BY q.market, q.selection, q.line, q.decimal_odds DESC, q.bookmaker""",
        (fixture_id, *types, now_text, fixture_id, *types, oldest, now_text),
    ).fetchall()
    return [dict(r) for r in rows]


def store_crowd_votes(conn: sqlite3.Connection, fixture_id: str, source: str, votes: dict, observed_at: str) -> int:
    """Store 1X2 crowd votes as shares. These are opinions, never prices."""
    total = sum(int(votes.get(s) or 0) for s in MARKETS["1X2"])
    if total <= 0:
        return 0
    before = conn.total_changes
    conn.executemany(
        """INSERT OR IGNORE INTO crowd_vote_signals
               (fixture_id, source, market, selection, votes, share, observed_at)
           VALUES (?, ?, '1X2', ?, ?, ?, ?)""",
        [(fixture_id, source, s, int(votes.get(s) or 0), round(int(votes.get(s) or 0) / total, 4), iso_utc(observed_at))
         for s in MARKETS["1X2"]],
    )
    return conn.total_changes - before


def requests_used_today(conn: sqlite3.Connection, provider: str, now: datetime | None = None) -> int:
    day = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT COALESCE(SUM(cost), 0) FROM provider_quota_log WHERE provider = ? AND status != 'refused' AND at >= ?",
        (provider, day),
    ).fetchone()
    return int(row[0])


def log_request(conn: sqlite3.Connection, provider: str, endpoint: str, cost: int, status: str, at: str,
                http_status: int | None = None, remaining: str | None = None, used: str | None = None,
                detail: str = "") -> None:
    conn.execute(
        """INSERT INTO provider_quota_log
               (provider, endpoint, cost, status, http_status, requests_remaining, requests_used, detail, at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (provider, endpoint, cost, status, http_status, remaining, used, detail[:500], iso_utc(at)),
    )

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
    revision            INTEGER NOT NULL DEFAULT 0,
    UNIQUE (fixture_id, provider, bookmaker, market, selection, line, observed_at, revision)
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


QUOTE_KEY = ("fixture_id", "provider", "bookmaker", "market", "selection", "line")


def ensure_odds_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    migrate_quote_revisions(conn)


def _has_revision(conn: sqlite3.Connection) -> bool:
    return "revision" in {r[1] for r in conn.execute("PRAGMA table_info(odds_quotes)")}


def _quote_checksum(conn: sqlite3.Connection, table: str = "odds_quotes") -> str:
    import hashlib

    cols = ("id", "fixture_id", "odds_type", "provider", "provider_event_id", "bookmaker", "market", "selection",
            "line", "decimal_odds", "implied_probability", "raw_market", "raw_selection", "observed_at",
            "commence_time", "fetched_at")
    h = hashlib.sha256()
    for row in conn.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY id"):
        h.update(repr(tuple(row)).encode())
    return h.hexdigest()


def migrate_quote_revisions(conn: sqlite3.Connection) -> dict:
    """K53: rebuild a pre-M3.3 odds_quotes (unique key without price) with the revision column.

    Every existing row is copied unchanged with revision 0 (the old key allowed exactly one
    row per provider stamp, so no conflict is possible). The copy is verified by checksum
    before the old table is dropped. Idempotent; a no-op on new tables.
    """
    if _has_revision(conn):
        return {"migrated": False}
    before = _quote_checksum(conn)
    count = conn.execute("SELECT COUNT(*) FROM odds_quotes").fetchone()[0]
    new_ddl = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS odds_quotes"):SCHEMA.index(");", SCHEMA.index(
        "CREATE TABLE IF NOT EXISTS odds_quotes")) + 2].replace("IF NOT EXISTS odds_quotes", "odds_quotes_m33")
    cols = ("id, fixture_id, odds_type, provider, provider_event_id, bookmaker, market, selection, line, "
            "decimal_odds, implied_probability, raw_market, raw_selection, observed_at, commence_time, fetched_at")
    conn.commit()  # no half-finished caller transaction inside the rebuild
    conn.execute("BEGIN")
    try:
        conn.execute(new_ddl)
        conn.execute(f"INSERT INTO odds_quotes_m33 ({cols}, revision) SELECT {cols}, 0 FROM odds_quotes")
        if _quote_checksum(conn, "odds_quotes_m33") != before:
            raise RuntimeError("odds_quotes migration checksum mismatch; nothing changed")
        conn.execute("DROP TABLE odds_quotes")
        conn.execute("ALTER TABLE odds_quotes_m33 RENAME TO odds_quotes")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_odds_quotes_fixture ON odds_quotes(fixture_id, market, selection)")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"migrated": True, "rows": count, "checksum": before}


def rollback_quote_revisions(conn: sqlite3.Connection) -> dict:
    """Return odds_quotes to the pre-M3.3 key. Rows with revision > 0 cannot fit that key: they are moved,
    unchanged, to odds_quotes_revisions_archive (never deleted)."""
    if not _has_revision(conn):
        return {"rolled_back": False}
    cols = ("id, fixture_id, odds_type, provider, provider_event_id, bookmaker, market, selection, line, "
            "decimal_odds, implied_probability, raw_market, raw_selection, observed_at, commence_time, fetched_at")
    conn.commit()
    conn.execute("BEGIN")
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS odds_quotes_revisions_archive AS SELECT * FROM odds_quotes WHERE 0")
        conn.execute("INSERT INTO odds_quotes_revisions_archive SELECT * FROM odds_quotes WHERE revision > 0")
        archived = conn.execute("SELECT changes()").fetchone()[0]
        old_ddl = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS odds_quotes"):SCHEMA.index(");", SCHEMA.index(
            "CREATE TABLE IF NOT EXISTS odds_quotes")) + 2]
        old_ddl = (old_ddl.replace("IF NOT EXISTS odds_quotes", "odds_quotes_pre_m33")
                   .replace("    revision            INTEGER NOT NULL DEFAULT 0,\n", "")
                   .replace("observed_at, revision)", "observed_at)"))
        conn.execute(old_ddl)
        conn.execute(f"INSERT INTO odds_quotes_pre_m33 ({cols}) SELECT {cols} FROM odds_quotes WHERE revision = 0")
        conn.execute("DROP TABLE odds_quotes")
        conn.execute("ALTER TABLE odds_quotes_pre_m33 RENAME TO odds_quotes")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_odds_quotes_fixture ON odds_quotes(fixture_id, market, selection)")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"rolled_back": True, "archived_revisions": archived}


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
    new = 0
    for q in quotes:
        observed = iso_utc(q.observed_at)
        key = (fixture_id, q.provider, q.bookmaker, q.market, q.selection, q.line)
        same_stamp = conn.execute(
            """SELECT decimal_odds, revision FROM odds_quotes
               WHERE fixture_id = ? AND provider = ? AND bookmaker = ? AND market = ? AND selection = ? AND line = ?
                 AND observed_at = ? ORDER BY revision DESC LIMIT 1""",
            (*key, observed),
        ).fetchone()
        if same_stamp is not None and abs(same_stamp[0] - q.decimal_odds) < 1e-9:
            continue  # identical delivery of the latest price under this stamp
        revision = 0 if same_stamp is None else same_stamp[1] + 1  # price changed under an unchanged stamp (K53)
        conn.execute(
            """INSERT INTO odds_quotes
                   (fixture_id, odds_type, provider, provider_event_id, bookmaker, market, selection, line,
                    decimal_odds, implied_probability, raw_market, raw_selection, observed_at,
                    commence_time, fetched_at, revision)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fixture_id, q.odds_type.value, q.provider, q.provider_event_id, q.bookmaker, q.market,
             q.selection, q.line, q.decimal_odds, q.implied_probability, q.raw_market, q.raw_selection,
             observed, iso_utc(q.commence_time), iso_utc(fetched_at), revision),
        )
        new += 1
    return new


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

    ``now`` is the decision time. Only quotes this system had already collected
    by then (``fetched_at <= now``) exist for that decision, so replaying an
    earlier prediction can never use a price that was fetched later.
    """
    now_text = iso_utc(now)
    oldest = iso_utc(now - max_age)
    types = ("bookmaker", "synthetic_test") if include_synthetic else ("bookmaker",)
    # Latest row per bookmaker selection among the rows we had fetched by `now`: newest provider stamp,
    # then the latest collected revision under that stamp (K53). Older rows are superseded.
    rows = conn.execute(
        f"""SELECT * FROM (
                SELECT q.*, ROW_NUMBER() OVER (
                    PARTITION BY provider, bookmaker, market, selection, line
                    ORDER BY observed_at DESC, fetched_at DESC, revision DESC) AS rn
                FROM odds_quotes q
                WHERE fixture_id = ? AND odds_type IN ({','.join('?' * len(types))})
                  AND observed_at <= ? AND fetched_at <= ?
            ) WHERE rn = 1 AND observed_at >= ? AND observed_at < commence_time AND commence_time > ?
            ORDER BY market, selection, line, decimal_odds DESC, bookmaker""",
        (fixture_id, *types, now_text, now_text, oldest, now_text),
    ).fetchall()
    return [{k: r[k] for k in r.keys() if k != "rn"} for r in rows]


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

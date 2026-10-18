"""Shared API-Football request ledger across all consumers (M3.4).

Each consumer (shadow cycle, settlement tool, experiment tool, demo) logs requests in its own
database, so a per-database daily budget cannot see the others. When API_FOOTBALL_LEDGER points
at a file, every budgeted request is reserved here BEFORE it is sent (atomic, persistent), and the
daily budget is checked against the total of all consumers for the UTC day. The provider's own
remaining-requests header (ODDS_MIN_REMAINING) remains the second, global guard.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ENV = "API_FOOTBALL_LEDGER"


def path() -> Path | None:
    value = os.getenv(ENV, "").strip()
    return Path(value).expanduser() if value else None


def _conn(p: Path) -> sqlite3.Connection:
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=30)
    conn.execute("CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, "
                 "day TEXT NOT NULL, provider TEXT NOT NULL, consumer TEXT NOT NULL, endpoint TEXT NOT NULL, "
                 "cost INTEGER NOT NULL, status TEXT NOT NULL)")
    return conn


def reserve(provider: str, endpoint: str, cost: int, budget: int, consumer: str,
            now: datetime | None = None) -> tuple[bool, int]:
    """Atomically reserve `cost` against today's shared budget. Returns (allowed, used_before)."""
    p = path()
    if p is None:
        return True, 0
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    day = moment.strftime("%Y-%m-%d")
    conn = _conn(p)
    try:
        conn.execute("BEGIN IMMEDIATE")
        used = conn.execute("SELECT COALESCE(SUM(cost), 0) FROM ledger WHERE day = ? AND provider = ? "
                            "AND status = 'reserved'", (day, provider)).fetchone()[0]
        allowed = used + cost <= budget
        conn.execute("INSERT INTO ledger (at, day, provider, consumer, endpoint, cost, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (moment.isoformat(), day, provider, consumer, endpoint, cost, "reserved" if allowed else "refused"))
        conn.commit()
        return allowed, used
    finally:
        conn.close()


def used_today(provider: str, now: datetime | None = None) -> int | None:
    p = path()
    if p is None or not p.exists():
        return None
    day = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")
    conn = _conn(p)
    try:
        return conn.execute("SELECT COALESCE(SUM(cost), 0) FROM ledger WHERE day = ? AND provider = ? "
                            "AND status = 'reserved'", (day, provider)).fetchone()[0]
    finally:
        conn.close()

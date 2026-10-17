"""Hard API-Football request cap for the local demo (owner-authorised 2026-10-17).

install(usage_db, cap) patches the process so that:
  * every HTTP request to *.api-sports.io (except the media.api-sports.io logo CDN)
    made through http.client (urllib and requests/urllib3 both use it) is counted
    BEFORE it is sent, in a persistent SQLite file, so retries and failures count
    and a restart cannot reset the allowance;
  * once `cap` requests are recorded, further requests raise DemoQuotaExceeded
    and nothing is sent;
  * a DNS lookup of an api-sports.io host from any other code path (a client that
    bypasses http.client) is refused, so nothing can reach the API uncounted.
"""
from __future__ import annotations

import http.client
import socket
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

HARD_MAXIMUM = 5
_lock = threading.Lock()
_local = threading.local()
_state: dict = {}


class DemoQuotaExceeded(RuntimeError):
    """The demo's API-Football allowance is used up; the request was not sent."""


def _is_api_football(host: str | None) -> bool:
    h = (host or "").lower().rstrip(".")
    return h.endswith("api-sports.io") and not h.startswith("media.")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(_state["path"])
    conn.execute("CREATE TABLE IF NOT EXISTS demo_api_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, "
                 "host TEXT NOT NULL, path TEXT NOT NULL, allowed INTEGER NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS demo_fetched_dates (date TEXT PRIMARY KEY, at TEXT NOT NULL)")
    return conn


def used() -> int:
    conn = _db()
    try:
        return conn.execute("SELECT COUNT(*) FROM demo_api_usage WHERE allowed = 1").fetchone()[0]
    finally:
        conn.close()


def _reserve(host: str, path: str) -> bool:
    """Atomically record one attempt; True if within the cap."""
    with _lock:
        conn = _db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            n = conn.execute("SELECT COUNT(*) FROM demo_api_usage WHERE allowed = 1").fetchone()[0]
            allowed = n < _state["cap"]
            conn.execute("INSERT INTO demo_api_usage (at, host, path, allowed) VALUES (?, ?, ?, ?)",
                         (datetime.now(timezone.utc).isoformat(), host, path.split("?")[0] + "?" + _redact(path),
                          int(allowed)))
            conn.commit()
            return allowed
        finally:
            conn.close()


def _redact(path: str) -> str:
    return path.split("?", 1)[1] if "?" in path else ""


def date_already_fetched(date_str: str) -> bool:
    conn = _db()
    try:
        return conn.execute("SELECT 1 FROM demo_fetched_dates WHERE date = ?", (date_str,)).fetchone() is not None
    finally:
        conn.close()


def mark_date_fetched(date_str: str) -> None:
    conn = _db()
    try:
        conn.execute("INSERT OR IGNORE INTO demo_fetched_dates VALUES (?, ?)",
                     (date_str, datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()


def install(usage_db: Path, cap: int) -> None:
    if not 0 <= cap <= HARD_MAXIMUM:
        raise ValueError(f"demo cap must be between 0 and {HARD_MAXIMUM}")
    if _state.get("installed"):
        _state.update(path=str(usage_db), cap=cap)
        return
    _state.update(path=str(usage_db), cap=cap, installed=True)
    _db().close()

    original_putrequest = http.client.HTTPConnection.putrequest
    original_getaddrinfo = socket.getaddrinfo

    def putrequest(self, method, url, *args, **kwargs):
        if _is_api_football(self.host):
            if not _reserve(self.host, url):
                raise DemoQuotaExceeded(f"demo API-Football allowance of {_state['cap']} requests is used up")
            _local.permit = True   # one DNS lookup for this counted request (new connection)
        return original_putrequest(self, method, url, *args, **kwargs)

    def getaddrinfo(host, *args, **kwargs):
        if _is_api_football(host if isinstance(host, str) else None):
            if not getattr(_local, "permit", False):
                raise socket.gaierror(f"demo quota guard: {host} may only be reached through a counted request")
            _local.permit = False
        return original_getaddrinfo(host, *args, **kwargs)

    http.client.HTTPConnection.putrequest = putrequest
    socket.getaddrinfo = getaddrinfo

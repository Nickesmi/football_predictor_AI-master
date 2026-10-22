"""API-Football request policy for the local demo (owner-authorised 2026-10-17; grants added 2026-10-23).

install(usage_db, cap, ...) patches the process so that:
  * every HTTP request to *.api-sports.io (except the media.api-sports.io logo CDN)
    made through http.client (urllib and requests/urllib3 both use it) is counted
    BEFORE it is sent, in a persistent SQLite file, so retries and failures count
    and a restart cannot reset the allowance;
  * a DNS lookup of an api-sports.io host from any other code path (a client that
    bypasses http.client) is refused, so nothing can reach the API uncounted.

Allowances, in order:
  1. The legacy lifetime cap (`cap`, at most 5), recorded in `demo_api_usage` exactly as before.
     It is never reset; on the original demo it is used up (5/5).
  2. Owner grants (`demo_allowance_grants`, written only by scripts/demo_allowance.py): each grant
     authorizes a total number of requests and a per-UTC-day limit, optionally until an expiry.
     Requests under a grant are recorded in `demo_requests`. There is no grant by default, so no
     new request is possible until the owner authorizes one. Grants are append-only; revoking
     one sets `revoked_at`.

Guards on every grant request (each refusal is recorded, and nothing is sent):
  * explicit_only: with install(..., explicit_only=True) a request is allowed only inside
    `explicit()`, i.e. an explicit user action, at most `max_requests` sends per action;
    page loads, background tasks and other code paths are refused;
  * the provider's own remaining count reported today (UTC) must stay above `provider_floor`;
  * shared ledger: when `ledger` is configured (src/odds/ledger.py), the request is reserved there
    too (consumer "demo") against `ledger_budget`, so the daily total of all consumers holds;
  * same key as the shadow: if `shadow_key_file` holds the same key as the demo (SHA-256 digests
    are compared in memory; the key is never logged or stored), a grant request is refused unless
    the shared ledger is configured.
All checks and the insert happen in one IMMEDIATE transaction, so several processes sharing the
usage file cannot exceed an allowance.
"""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import os
import socket
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

HARD_MAXIMUM = 5            # legacy lifetime cap
GRANT_TOTAL_MAX = 200       # one grant can never authorize more than this
GRANT_DAILY_MAX = 20        # nor more than this per UTC day
DEFAULT_PROVIDER_FLOOR = 30
_lock = threading.Lock()
_local = threading.local()
_state: dict = {}


class DemoQuotaExceeded(RuntimeError):
    """The demo's API-Football allowance does not permit this request; the request was not sent."""


def _now() -> datetime:
    return _state.get("clock", lambda: datetime.now(timezone.utc))()


def _is_api_football(host: str | None) -> bool:
    h = (host or "").lower().rstrip(".")
    return h.endswith("api-sports.io") and not h.startswith("media.")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(_state["path"], timeout=30)
    conn.execute("CREATE TABLE IF NOT EXISTS demo_api_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, "
                 "host TEXT NOT NULL, path TEXT NOT NULL, allowed INTEGER NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS demo_fetched_dates (date TEXT PRIMARY KEY, at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS demo_allowance_grants (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "granted_at TEXT NOT NULL, granted_by TEXT NOT NULL, total INTEGER NOT NULL, "
                 "daily_limit INTEGER NOT NULL, expires_at TEXT, note TEXT NOT NULL, revoked_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS demo_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, "
                 "day TEXT NOT NULL, host TEXT NOT NULL, path TEXT NOT NULL, grant_id INTEGER, "
                 "allowed INTEGER NOT NULL, reason TEXT, http_status INTEGER, provider_remaining INTEGER, "
                 "outcome TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS demo_fixture_updates (date TEXT NOT NULL, at TEXT NOT NULL, "
                 "fixtures INTEGER NOT NULL, request_id INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS demo_refresh_inflight (date TEXT PRIMARY KEY, started_at TEXT NOT NULL)")
    return conn


# ── Read-only views ──────────────────────────────────────────────────────────

def used() -> int:
    """Requests sent under the legacy lifetime cap."""
    conn = _db()
    try:
        return conn.execute("SELECT COUNT(*) FROM demo_api_usage WHERE allowed = 1").fetchone()[0]
    finally:
        conn.close()


def _active_grants(conn, now: datetime) -> list[dict]:
    rows = conn.execute("SELECT id, total, daily_limit, expires_at, granted_at, note FROM demo_allowance_grants "
                        "WHERE revoked_at IS NULL ORDER BY id").fetchall()
    out = []
    for gid, total, daily, expires, granted_at, note in rows:
        if expires and datetime.fromisoformat(expires) <= now:
            continue
        spent = conn.execute("SELECT COUNT(*) FROM demo_requests WHERE grant_id = ? AND allowed = 1",
                             (gid,)).fetchone()[0]
        out.append({"id": gid, "total": total, "daily_limit": daily, "expires_at": expires, "granted_at": granted_at,
                    "note": note, "used": spent, "remaining": max(0, total - spent)})
    return out


def _provider_remaining_today(conn, day: str) -> int | None:
    row = conn.execute("SELECT provider_remaining FROM demo_requests WHERE day = ? AND provider_remaining IS NOT NULL "
                       "ORDER BY id DESC LIMIT 1", (day,)).fetchone()
    return None if row is None else int(row[0])


def _same_key_as_shadow() -> bool | None:
    """True/False if both keys are known; None if the shadow key file exists but cannot be read."""
    path = _state.get("shadow_key_file")
    if not path or not Path(path).exists():
        return False
    try:
        shadow = ""
        for line in Path(path).read_text().splitlines():
            if line.strip().startswith("API_FOOTBALL_KEY="):
                shadow = line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None
    if not shadow:
        return False
    ours = _state.get("api_key") or ""
    return bool(ours) and hashlib.sha256(ours.encode()).digest() == hashlib.sha256(shadow.encode()).digest()


def _grant_blockers(conn, now: datetime) -> tuple[dict | None, list[str], dict]:
    """The grant a request would use, and every reason it cannot be used (empty list = allowed)."""
    day = now.strftime("%Y-%m-%d")
    grants = _active_grants(conn, now)
    used_today = conn.execute("SELECT COUNT(*) FROM demo_requests WHERE day = ? AND allowed = 1",
                              (day,)).fetchone()[0]
    usable = [g for g in grants if g["remaining"] > 0]
    grant = usable[0] if usable else None
    daily_limit = min(GRANT_DAILY_MAX, grant["daily_limit"]) if grant else 0
    remaining = _provider_remaining_today(conn, day)
    floor = _state.get("provider_floor", DEFAULT_PROVIDER_FLOOR)
    reasons = []
    if not grants:
        reasons.append("no owner-authorized allowance (scripts/demo_allowance.py grant)")
    elif grant is None:
        reasons.append("authorized allowance used up")
    elif used_today >= daily_limit:
        reasons.append(f"daily limit {daily_limit} reached (resets 00:00 UTC)")
    if remaining is not None and remaining <= floor:
        reasons.append(f"provider reports {remaining} requests left today (floor {floor})")
    same = _same_key_as_shadow()
    if same is not False and not _state.get("ledger"):
        reasons.append("same API key as the shadow experiment: the shared ledger is required"
                       if same else "shadow key file unreadable: the shared ledger is required")
    info = {"day": day, "used_today": used_today, "daily_limit": daily_limit,
            "remaining_today": max(0, daily_limit - used_today) if grant else 0,
            "remaining_total": sum(g["remaining"] for g in grants), "provider_remaining_today": remaining,
            "provider_floor": floor, "same_key_as_shadow": same, "grants": grants}
    return grant, reasons, info


def status() -> dict:
    """Everything the interface shows before a refresh. Sends nothing."""
    now = _now()
    conn = _db()
    try:
        legacy_used = conn.execute("SELECT COUNT(*) FROM demo_api_usage WHERE allowed = 1").fetchone()[0]
        legacy_refused = conn.execute("SELECT COUNT(*) FROM demo_api_usage WHERE allowed = 0").fetchone()[0]
        _, reasons, info = _grant_blockers(conn, now)
        last = conn.execute("SELECT at, outcome, http_status FROM demo_requests WHERE allowed = 1 "
                            "ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    legacy_left = max(0, _state.get("cap", 0) - legacy_used)
    return {"mode": "demo", "cost_per_refresh": 1, "can_refresh": legacy_left > 0 or not reasons,
            "blocked_reasons": [] if legacy_left > 0 else reasons,
            "legacy": {"cap": _state.get("cap", 0), "used": legacy_used, "refused": legacy_refused,
                       "remaining": legacy_left},
            "explicit_only": bool(_state.get("explicit_only")),
            "shared_ledger": bool(_state.get("ledger")), "ledger_budget": _state.get("ledger_budget"),
            "last_request": None if last is None else {"at": last[0], "outcome": last[1], "http_status": last[2]},
            **info}


# ── Recording ────────────────────────────────────────────────────────────────

def _redact(path: str) -> str:
    return path.split("?", 1)[1] if "?" in path else ""


def _logged_path(path: str) -> str:
    return path.split("?")[0] + "?" + _redact(path)


def _has_grants(conn) -> bool:
    return conn.execute("SELECT COUNT(*) FROM demo_allowance_grants").fetchone()[0] > 0


def _reserve(host: str, path: str) -> bool:
    """Atomically record one attempt; True if an allowance permits sending it."""
    now = _now()
    _local.request_id = None
    with _lock:
        conn = _db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            n = conn.execute("SELECT COUNT(*) FROM demo_api_usage WHERE allowed = 1").fetchone()[0]
            if n < _state["cap"]:
                ok = _shared_ledger_ok(path, now)
                conn.execute("INSERT INTO demo_api_usage (at, host, path, allowed) VALUES (?, ?, ?, ?)",
                             (now.isoformat(), host, _logged_path(path), int(ok)))
                conn.commit()
                return ok
            if not _has_grants(conn):
                # No grant has ever been made: the legacy behaviour (the refusal goes in the legacy table).
                conn.execute("INSERT INTO demo_api_usage (at, host, path, allowed) VALUES (?, ?, ?, 0)",
                             (now.isoformat(), host, _logged_path(path)))
                conn.commit()
                return False
            grant, reasons, _ = _grant_blockers(conn, now)
            if not reasons and not _shared_ledger_ok(path, now):
                reasons = ["shared ledger refused: daily total of all consumers reached"]
            cur = conn.execute("INSERT INTO demo_requests (at, day, host, path, grant_id, allowed, reason, outcome) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (now.isoformat(), now.strftime("%Y-%m-%d"), host, _logged_path(path),
                                None if reasons else grant["id"], int(not reasons), "; ".join(reasons) or None,
                                "refused" if reasons else "sent"))
            conn.commit()
            _local.request_id = None if reasons else cur.lastrowid
            return not reasons
        finally:
            conn.close()


def _shared_ledger_ok(path: str, now: datetime) -> bool:
    if not _state.get("ledger"):
        return True
    from src.odds import ledger

    previous = os.environ.get(ledger.ENV)
    os.environ[ledger.ENV] = str(_state["ledger"])
    try:
        ok, _ = ledger.reserve("api_football", path.split("?")[0], 1, int(_state["ledger_budget"]), "demo", now)
        return ok
    finally:
        if previous is None:
            os.environ.pop(ledger.ENV, None)
        else:
            os.environ[ledger.ENV] = previous


def _record_block(host: str, path: str, reason: str) -> None:
    now = _now()
    conn = _db()
    try:
        conn.execute("INSERT INTO demo_requests (at, day, host, path, grant_id, allowed, reason, outcome) "
                     "VALUES (?, ?, ?, ?, NULL, 0, ?, 'refused')",
                     (now.isoformat(), now.strftime("%Y-%m-%d"), host, _logged_path(path), reason))
        conn.commit()
    finally:
        conn.close()


def _record_outcome(request_id: int | None, http_status: int | None, remaining: str | None, outcome: str) -> None:
    if not request_id:
        return
    try:
        value = int(float(remaining)) if remaining not in (None, "") else None
    except ValueError:
        value = None
    conn = _db()
    try:
        conn.execute("UPDATE demo_requests SET http_status = ?, provider_remaining = ?, outcome = ? WHERE id = ?",
                     (http_status, value, outcome, request_id))
        conn.commit()
    finally:
        conn.close()


def record_fixture_update(date_str: str, fixtures: int) -> None:
    conn = _db()
    try:
        rid = conn.execute("SELECT MAX(id) FROM demo_requests WHERE allowed = 1").fetchone()[0]
        conn.execute("INSERT INTO demo_fixture_updates (date, at, fixtures, request_id) VALUES (?, ?, ?, ?)",
                     (date_str, _now().isoformat(), fixtures, rid))
        conn.commit()
    finally:
        conn.close()


def last_fixture_update(date_str: str) -> str | None:
    """When fixtures for a date were last fetched successfully: a refresh recorded here, or a legacy
    fetch whose request was allowed (the legacy table also marked refused attempts as fetched).
    None when unknown; cache file times are not used, because every page load rewrites the file."""
    conn = _db()
    try:
        row = conn.execute("SELECT MAX(at) FROM demo_fixture_updates WHERE date = ?", (date_str,)).fetchone()[0]
        if row:
            return row
        legacy = conn.execute(
            "SELECT f.at FROM demo_fetched_dates f WHERE f.date = ? AND EXISTS (SELECT 1 FROM demo_api_usage u "
            "WHERE u.allowed = 1 AND u.path LIKE '%date=' || f.date || '%')", (date_str,)).fetchone()
        return legacy[0] if legacy else None
    finally:
        conn.close()


def date_already_fetched(date_str: str) -> bool:
    conn = _db()
    try:
        return conn.execute("SELECT 1 FROM demo_fetched_dates WHERE date = ?", (date_str,)).fetchone() is not None
    finally:
        conn.close()


def mark_date_fetched(date_str: str) -> None:
    conn = _db()
    try:
        conn.execute("INSERT OR IGNORE INTO demo_fetched_dates VALUES (?, ?)", (date_str, _now().isoformat()))
        conn.commit()
    finally:
        conn.close()


# ── Explicit user actions ────────────────────────────────────────────────────

REFRESH_STALE_SECONDS = 120


def begin_refresh(date_str: str) -> bool:
    """Claim the refresh of a date across processes; False if another refresh of it is running."""
    now = _now()
    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT started_at FROM demo_refresh_inflight WHERE date = ?", (date_str,)).fetchone()
        if row and (now - datetime.fromisoformat(row[0])).total_seconds() < REFRESH_STALE_SECONDS:
            conn.rollback()
            return False
        conn.execute("INSERT OR REPLACE INTO demo_refresh_inflight (date, started_at) VALUES (?, ?)",
                     (date_str, now.isoformat()))
        conn.commit()
        return True
    finally:
        conn.close()


def end_refresh(date_str: str) -> None:
    conn = _db()
    try:
        conn.execute("DELETE FROM demo_refresh_inflight WHERE date = ?", (date_str,))
        conn.commit()
    finally:
        conn.close()


@contextlib.contextmanager
def explicit(max_requests: int = 1):
    """An explicit user action: inside it, at most `max_requests` API-Football sends are permitted."""
    _local.explicit_left = max_requests
    try:
        yield
    finally:
        _local.explicit_left = 0


# ── Installation ─────────────────────────────────────────────────────────────

def install(usage_db: Path, cap: int, *, explicit_only: bool = False, provider_floor: int = DEFAULT_PROVIDER_FLOOR,
            ledger: Path | None = None, ledger_budget: int | None = None, shadow_key_file: Path | None = None,
            api_key: str | None = None, clock=None) -> None:
    if not 0 <= cap <= HARD_MAXIMUM:
        raise ValueError(f"demo cap must be between 0 and {HARD_MAXIMUM}")
    if ledger is not None and not (ledger_budget and 0 < ledger_budget <= 70):
        raise ValueError("a shared ledger needs ledger_budget between 1 and 70 (the shadow's daily ceiling)")
    settings = dict(path=str(usage_db), cap=cap, explicit_only=explicit_only, provider_floor=max(0, provider_floor),
                    ledger=None if ledger is None else str(ledger), ledger_budget=ledger_budget,
                    shadow_key_file=None if shadow_key_file is None else str(shadow_key_file), api_key=api_key)
    if clock is not None:
        settings["clock"] = clock
    if _state.get("installed"):
        _state.update(settings)
        return
    _state.update(settings, installed=True)
    _db().close()

    original_putrequest = http.client.HTTPConnection.putrequest
    original_getresponse = http.client.HTTPConnection.getresponse
    original_getaddrinfo = socket.getaddrinfo

    def putrequest(self, method, url, *args, **kwargs):
        if _is_api_football(self.host):
            if _state.get("explicit_only"):
                left = getattr(_local, "explicit_left", 0)
                if left <= 0:
                    _record_block(self.host, url, "not an explicit user action")
                    raise DemoQuotaExceeded("demo: API-Football is reached only through an explicit "
                                            "'Refresh fixtures' action")
                _local.explicit_left = left - 1
            if not _reserve(self.host, url):
                raise DemoQuotaExceeded("demo API-Football allowance does not permit this request "
                                        "(see /api/demo/status)")
            _local.permit = True   # one DNS lookup for this counted request (new connection)
            self._demo_request_id = getattr(_local, "request_id", None)
        return original_putrequest(self, method, url, *args, **kwargs)

    def getresponse(self, *args, **kwargs):
        rid = getattr(self, "_demo_request_id", None)
        try:
            response = original_getresponse(self, *args, **kwargs)
        except Exception:
            _record_outcome(rid, None, None, "error")
            raise
        if rid:
            _record_outcome(rid, response.status, response.getheader("x-ratelimit-requests-remaining"),
                            "ok" if 200 <= response.status < 300 else "http_error")
            self._demo_request_id = None
        return response

    def getaddrinfo(host, *args, **kwargs):
        if _is_api_football(host if isinstance(host, str) else None):
            if not getattr(_local, "permit", False):
                raise socket.gaierror(f"demo quota guard: {host} may only be reached through a counted request")
            _local.permit = False
        return original_getaddrinfo(host, *args, **kwargs)

    http.client.HTTPConnection.putrequest = putrequest
    http.client.HTTPConnection.getresponse = getresponse
    socket.getaddrinfo = getaddrinfo


# ── Owner grants (written by scripts/demo_allowance.py only) ─────────────────

def grant(usage_db: Path, total: int, daily_limit: int, note: str, granted_by: str,
          expires_at: str | None = None) -> int:
    if not 1 <= total <= GRANT_TOTAL_MAX:
        raise ValueError(f"total must be between 1 and {GRANT_TOTAL_MAX}")
    if not 1 <= daily_limit <= min(GRANT_DAILY_MAX, total):
        raise ValueError(f"daily limit must be between 1 and {min(GRANT_DAILY_MAX, total)}")
    if not note.strip():
        raise ValueError("a note (why the allowance is granted) is required")
    if expires_at:
        datetime.fromisoformat(expires_at)
    _state["path"] = str(usage_db)
    conn = _db()
    try:
        cur = conn.execute("INSERT INTO demo_allowance_grants (granted_at, granted_by, total, daily_limit, expires_at, "
                           "note) VALUES (?, ?, ?, ?, ?, ?)",
                           (datetime.now(timezone.utc).isoformat(), granted_by, total, daily_limit, expires_at,
                            note.strip()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def revoke(usage_db: Path, grant_id: int) -> bool:
    _state["path"] = str(usage_db)
    conn = _db()
    try:
        n = conn.execute("UPDATE demo_allowance_grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                         (datetime.now(timezone.utc).isoformat(), grant_id)).rowcount
        conn.commit()
        return n == 1
    finally:
        conn.close()

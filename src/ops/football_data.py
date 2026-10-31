"""football-data.org fixtures for the local demo (owner-chosen 2026-10-31, API-Football account suspended).

Documented API v4 with a personal free-tier token (FOOTBALL_DATA_TOKEN in the project's .env, never
logged): 12 competitions, 10 requests a minute. One request per refresh: GET /v4/matches for the UTC
dates that cover one display-time-zone day (dateTo is exclusive), filtered to that day by kick-off.

Safe use:
  * only an explicit refresh sends (api/demo_mode.py); the network guard admits the host only inside
    `explicit()` (src/ops/demo_network.py);
  * at least MIN_INTERVAL seconds between requests and at most DAILY_CAP requests per display day,
    both counted before sending in <workdir>/football_data_requests.db (a restart cannot reset them);
  * a failed, refused or empty answer never replaces a cached day that has fixtures.

Cache: <cache dir>/fd-fixtures-<display date>.json, separate from the API-Football files
(fixtures-<date>.json), which stay untouched. Fixtures use the frontend's fixture shape with
provider "football_data" and ids prefixed "fd-", so they never collide with API-Football ids.
"""
from __future__ import annotations

import json
import os
import sqlite3
import ssl
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api import fixture_time

HOST = "api.football-data.org"
BASE = f"https://{HOST}/v4"
PROVIDER = "football_data"
LABEL = "football-data.org"
MIN_INTERVAL = 7.0          # seconds; the free tier allows 10 requests a minute
DAILY_CAP = 40              # requests per display day, a local ceiling well inside the free tier
REFRESH_AFTER = 15 * 60     # a cached day may be refreshed again after this many seconds (scores)

# Competition names as the analysis service knows them (LEAGUE_NAME_MAP); others keep the provider name.
LEAGUE_NAMES = {"PL": "Premier League", "PD": "La Liga", "SA": "Serie A", "BL1": "Bundesliga", "FL1": "Ligue 1",
                "DED": "Eredivisie", "PPL": "Primeira Liga", "ELC": "Championship", "BSA": "Brasileirão",
                "CL": "UEFA Champions League", "WC": "World Cup", "EC": "Euro Championship"}
STATUS = {"SCHEDULED": "NS", "TIMED": "NS", "IN_PLAY": "LIVE", "PAUSED": "HT", "FINISHED": "FT",
          "AWARDED": "AWD", "POSTPONED": "PST", "SUSPENDED": "SUSP", "CANCELLED": "CANC"}

_state: dict = {"db": None, "cache_dir": None}
_lock = threading.Lock()


def install(db_path: Path, cache_dir: Path) -> None:
    _state.update(db=Path(db_path), cache_dir=Path(cache_dir))
    with _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS fd_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, "
                  "display_date TEXT NOT NULL, day TEXT NOT NULL, allowed INTEGER NOT NULL, http_status INTEGER, "
                  "outcome TEXT, fixtures INTEGER, reason TEXT)")


def installed() -> bool:
    return _state["cache_dir"] is not None


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(_state["db"], timeout=30)


def token() -> str:
    return os.environ.get("FOOTBALL_DATA_TOKEN", "").strip()


def _now() -> datetime:
    from src.ops import demo_quota

    return demo_quota._now()


# ── Cache ────────────────────────────────────────────────────────────────────

def cache_path(display_date: str) -> Path:
    return _state["cache_dir"] / f"fd-fixtures-{display_date}.json"


def read_cache(display_date: str) -> dict | None:
    try:
        data = json.loads(cache_path(display_date).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("fixtures"), list) else None


def _write_cache(display_date: str, payload: dict) -> None:
    path = cache_path(display_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def refreshable(display_date: str, tz: str) -> bool:
    """A day can be requested when it is not cached, or when its snapshot is older than REFRESH_AFTER and
    was taken before the day was over (+3 h for late finishes): a finished day is never requested again."""
    cached = read_cache(display_date)
    if cached is None:
        return True
    fetched = datetime.fromisoformat(cached["fetched_at"])
    _, end = fixture_time.day_window(display_date, tz)
    return (_now() - fetched).total_seconds() >= REFRESH_AFTER and fetched < end + timedelta(hours=3)


# ── Allowance ────────────────────────────────────────────────────────────────

def status(tz: str) -> dict:
    day = fixture_time.today(tz, _now())
    with _conn() as c:
        used = c.execute("SELECT COUNT(*) FROM fd_requests WHERE day = ? AND allowed = 1", (day,)).fetchone()[0]
    reasons = []
    if not token():
        reasons.append("no football-data.org token is configured (FOOTBALL_DATA_TOKEN)")
    if used >= DAILY_CAP:
        reasons.append(f"daily football-data.org limit of {DAILY_CAP} requests reached")
    return {"provider": PROVIDER, "provider_label": LABEL, "day": day, "used_today": used, "daily_limit": DAILY_CAP,
            "remaining_today": max(0, DAILY_CAP - used), "remaining_total": max(0, DAILY_CAP - used),
            "min_interval_seconds": MIN_INTERVAL, "can_refresh": not reasons, "blocked_reasons": reasons}


def _reserve(display_date: str, tz: str) -> tuple[int | None, str | None]:
    """Count the request before sending: (row id, None) or (None, refusal reason)."""
    now = _now()
    st = status(tz)
    reason = st["blocked_reasons"][0] if st["blocked_reasons"] else None
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        last = c.execute("SELECT MAX(at) FROM fd_requests WHERE allowed = 1").fetchone()[0]
        if reason is None and last:
            wait = MIN_INTERVAL - (now - datetime.fromisoformat(last)).total_seconds()
            if wait > 0:
                reason = f"rate limit: wait {wait:.0f} s between football-data.org requests"
        cur = c.execute("INSERT INTO fd_requests (at, display_date, day, allowed, reason) VALUES (?, ?, ?, ?, ?)",
                        (now.isoformat(), display_date, st["day"], int(reason is None), reason))
    return (None, reason) if reason else (cur.lastrowid, None)


def _finish(row: int, http_status: int | None, outcome: str, fixtures: int | None, reason: str | None) -> None:
    with _conn() as c:
        c.execute("UPDATE fd_requests SET http_status = ?, outcome = ?, fixtures = ?, reason = ? WHERE id = ?",
                  (http_status, outcome, fixtures, reason, row))


def last_attempt(display_date: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT at, allowed, http_status, outcome, reason FROM fd_requests WHERE display_date = ? "
                        "ORDER BY id DESC LIMIT 1", (display_date,)).fetchone()
    if row is None:
        return None
    state = {"ok": "fresh", "empty": "no_fixtures"}.get(row[3] or "", "provider_error")
    if not row[1] or row[2] == 429:
        state = "quota_exhausted"
    return {"at": row[0], "state": state, "http_status": row[2], "error": None if state == "fresh" else row[4]}


# ── Conversion ───────────────────────────────────────────────────────────────

def to_fixture(m: dict, tz: str) -> dict | None:
    try:
        utc = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).astimezone(timezone.utc)
    except (KeyError, ValueError, AttributeError):
        return None
    local = utc.astimezone(fixture_time.zone(tz))
    comp, area = m.get("competition") or {}, m.get("area") or {}
    home, away = m.get("homeTeam") or {}, m.get("awayTeam") or {}
    status = STATUS.get(m.get("status") or "", "NS")
    score = m.get("score") or {}
    full, half = score.get("fullTime") or {}, score.get("halfTime") or {}
    played = status in ("LIVE", "HT", "FT", "AWD")
    if not home.get("name") or not away.get("name"):
        return None                                      # e.g. knockout slots without teams yet

    def team(t: dict) -> dict:
        return {"id": f"fd-{t.get('id')}", "name": t["name"], "short_name": t.get("shortName") or t["name"],
                "logo": t.get("crest") or ""}

    return {
        "id": f"fd-{m.get('id')}",
        "provider_match_id": str(m.get("id")),
        "date": local.date().isoformat(),
        "time": local.strftime("%H:%M"),
        "kickoff_utc": utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "display_timezone": tz,
        "time_basis": "utc",
        "status": status,
        "home_goals": full.get("home") if played else None,
        "away_goals": full.get("away") if played else None,
        "fh_home_goals": half.get("home") if played else None,
        "fh_away_goals": half.get("away") if played else None,
        "league": {"id": f"fd-{comp.get('id')}", "code": comp.get("code"),
                   "name": LEAGUE_NAMES.get(comp.get("code"), comp.get("name") or "Unknown"),
                   "provider_name": comp.get("name"), "country": area.get("name", ""), "logo": comp.get("emblem") or ""},
        "home_team": team(home),
        "away_team": team(away),
        "season": (m.get("season") or {}).get("startDate", "")[:4] or None,
        "round": f"Matchday {m['matchday']}" if m.get("matchday") else (m.get("stage") or ""),
        "source": PROVIDER,
        "provider": PROVIDER,
        "scoreline_source": PROVIDER if played and full.get("home") is not None else None,
        "provider_updated_at": m.get("lastUpdated"),
    }


# ── Request ──────────────────────────────────────────────────────────────────

def refresh(display_date: str, tz: str) -> dict:
    """One explicit request for one display day. Returns {state, fixtures, error, http_status} where state is
    fresh | no_fixtures | quota_exhausted | provider_error; the cache changes only on a usable answer."""
    from src.ops import demo_network

    with _lock:
        row, refused = _reserve(display_date, tz)
        if refused:
            return {"state": "quota_exhausted", "fixtures": None, "error": refused, "http_status": None}
        start, end = fixture_time.day_window(display_date, tz)
        url = f"{BASE}/matches?dateFrom={start.date().isoformat()}&dateTo={(end.date() + timedelta(days=1)).isoformat()}"
        req = urllib.request.Request(url, headers={"X-Auth-Token": token(), "Accept": "application/json",
                                                   "User-Agent": "FootballPredictorDemo/1.0 (personal, local)"})
        http_status, error, matches = None, None, None
        try:
            import certifi

            with demo_network.explicit(HOST):
                with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context(cafile=certifi.where())) as r:
                    http_status = r.status
                    matches = json.loads(r.read()).get("matches")
            if not isinstance(matches, list):
                error, matches = "unexpected response (no match list)", None
        except urllib.error.HTTPError as e:
            http_status = e.code
            try:
                error = json.loads(e.read()).get("message") or f"HTTP {e.code}"
            except ValueError:
                error = f"HTTP {e.code}"
        except Exception as e:   # network, TLS, JSON
            error = f"{type(e).__name__}: {e}"
        if matches is None:
            state = "quota_exhausted" if http_status == 429 else "provider_error"
            _finish(row, http_status, "error", None, error)
            return {"state": state, "fixtures": None, "error": error, "http_status": http_status}
        fixtures, seen = [], set()
        for m in matches:
            f = to_fixture(m, tz)
            if f and f["id"] not in seen and start.strftime("%Y-%m-%dT%H:%M:%SZ") <= f["kickoff_utc"] < end.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"):
                seen.add(f["id"])
                fixtures.append(f)
        fixtures.sort(key=lambda f: (f["kickoff_utc"], f["league"]["name"]))
        old = read_cache(display_date)
        if not fixtures and old and old["fixtures"]:
            error = "provider returned no fixtures for a day that has cached fixtures; the cache was kept"
            _finish(row, http_status, "error", 0, error)
            return {"state": "provider_error", "fixtures": None, "error": error, "http_status": http_status}
        _write_cache(display_date, {"provider": PROVIDER, "date": display_date, "display_timezone": tz,
                                    "fetched_at": _now().isoformat(), "fixtures": fixtures})
        _finish(row, http_status, "ok" if fixtures else "empty", len(fixtures), None)
        return {"state": "fresh" if fixtures else "no_fixtures", "fixtures": fixtures, "error": None,
                "http_status": http_status}

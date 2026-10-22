"""Local demo mode: cached fixtures by default, API-Football only on an explicit refresh.

Loaded only by scripts/demo_server.py (never by the production app). install() makes the fixture
endpoints read the demo's own cache and database without contacting API-Football, and adds:

    GET  /api/demo/status                     allowance, limits, display time zone (sends nothing)
    GET  /api/demo/fixture-cache/{date}       state of a date (sends nothing)
    POST /api/demo/refresh-fixtures/{date}    the only path that can send: one counted request, for a date
                                              not fetched yet, when an owner-authorized allowance remains

Dates are calendar days in one explicit display time zone (default Europe/Istanbul). Kick-offs are
handled in UTC (api/fixture_time.py); the cache's storage dates are never shown as such.

Every fixture response states what the list is:
    cached           fixtures from the cache (the snapshot time is `last_updated_at`)
    fresh            (refresh only) fixtures just retrieved from API-Football
    no_fixtures      the provider was asked successfully and has nothing scheduled
    quota_exhausted  nothing cached, and no authorized request remains (or the provider's limit was hit)
    provider_error   nothing cached; the last refresh failed (network or provider); `error` says why
    not_cached       nothing cached yet; a refresh is possible
A refused, failed or incomplete request is never recorded as a successful fetch, and never
replaces cached fixtures.

Quota rules live in src/ops/demo_quota.py; this module never decides an allowance itself.
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException

from api import fixture_time
from src.ops import demo_network, demo_quota

router = APIRouter()
_explicit = threading.local()
_original: dict = {}
_settings = {"display_tz": fixture_time.DEFAULT_DISPLAY_TZ}
PROVIDER_LIMIT_WORDS = ("request limit", "requests limit", "reached the limit", "too many requests", "429")


def _fs():
    import api.services.fixtures_service as fs

    return fs


def display_tz() -> str:
    return _settings["display_tz"]


def _today() -> str:
    return fixture_time.today(display_tz(), demo_quota._now())


def _storage_file(storage_date: str) -> list:
    path = _fs()._get_cache_path(storage_date)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _own_fixtures(date_str: str) -> int:
    """API-Football fixtures that a fetch for this date stored under it (any local label: kick-offs near
    midnight carry the neighbouring day's label), excluding overflow copied in from other days."""
    return sum(1 for f in _storage_file(date_str) if isinstance(f, dict)
               and f.get("provider") == "api_football" and not f.get("_overflow"))


def fetched(date_str: str) -> bool:
    """True once a fetch for the date succeeded: its fixtures are stored, or none were scheduled.
    Refused or failed attempts never count (the legacy 'fetched dates' table is not trusted)."""
    return _own_fixtures(date_str) > 0 or demo_quota.fetched_with_no_fixtures(date_str)


def _neighbours(date_str: str) -> tuple[str, str]:
    d = date.fromisoformat(date_str)
    return (d - timedelta(days=1)).isoformat(), (d + timedelta(days=1)).isoformat()


def day_fixtures(date_str: str, background_tasks: BackgroundTasks | None) -> list[dict]:
    """The display-time-zone day, from the storage date (through the fixture service, which also adds
    stored results) and the raw cache of the neighbouring storage dates (for kick-offs near midnight)."""
    result = _original["service"](date_str, background_tasks or BackgroundTasks(), force_refresh=False)
    own = result if isinstance(result, list) else result.get("fixtures", [])
    before, after = _neighbours(date_str)
    return fixture_time.day_view(date_str, display_tz(),
                                 {before: _storage_file(before), date_str: own, after: _storage_file(after)})


def cache_state(date_str: str, fixtures: list | None = None) -> dict:
    quota = demo_quota.status()
    today = _today()
    count = len(fixtures) if fixtures is not None else None
    last = demo_quota.last_refresh_attempt(date_str)
    if count:
        state = "cached"
    elif demo_quota.fetched_with_no_fixtures(date_str):
        state = "no_fixtures"
    elif last and last["state"] in ("quota_exhausted", "provider_error"):
        state = last["state"]
    elif not quota["can_refresh"]:
        state = "quota_exhausted"
    else:
        state = "not_cached"
    unknown = sum(1 for f in fixtures or [] if f.get("time_basis") == "unknown")
    return {"date": date_str, "state": state, "fetched": fetched(date_str), "cached_fixtures": count,
            "unknown_time_basis": unknown, "last_updated_at": demo_quota.last_fixture_update(date_str),
            "last_attempt": last, "period": "past" if date_str < today else ("today" if date_str == today else "upcoming"),
            "display_timezone": display_tz(), "today": today, "data_source": "cache", "live": False, "quota": quota}


MESSAGES = {
    "no_fixtures": "No fixtures are scheduled for {date} (provider checked {when}).",
    "quota_exhausted": "No cached fixtures for {date}, and no authorized API-Football request remains. "
                       "Cached dates stay available.",
    "provider_error": "No cached fixtures for {date}. The last refresh failed: {error}. Cached dates stay available.",
    "not_cached": "No cached fixtures for {date}. Use 'Refresh fixtures' to request it (1 API-Football request).",
}


def _validate(date_str: str) -> None:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {date_str!r}. Use YYYY-MM-DD.")


def install(app=None, display_timezone: str = fixture_time.DEFAULT_DISPLAY_TZ) -> None:
    fixture_time.zone(display_timezone)   # an unknown zone fails here, at start-up
    _settings["display_tz"] = display_timezone
    fs = _fs()
    if _original:
        return
    _original.update(fetch=fs._fetch_api_football_fixtures, service=fs.get_fixtures_by_date_service)

    def cache_only_fetch(date_str: str, request_tz: str | None = None) -> list[dict]:
        if getattr(_explicit, "date", None) != date_str:
            fs._LAST_API_FOOTBALL_ERROR = None   # a page load never contacts the provider; that is not an error
            fs.logger.info(f"[DEMO] cache-only: no API-Football request for {date_str}")
            return []
        with demo_quota.explicit(max_requests=1):
            return _original["fetch"](date_str, request_tz=display_tz())

    def demo_fixtures(date_str: str, background_tasks: BackgroundTasks, force_refresh: bool = False):
        fixtures = day_fixtures(date_str, background_tasks)
        state = cache_state(date_str, fixtures)
        payload = {"fixtures": fixtures, "demo": state}
        if state["state"] != "cached":
            when = state["last_updated_at"] or "earlier"
            error = (state["last_attempt"] or {}).get("error") or "unknown error"
            payload["message"] = MESSAGES[state["state"]].format(date=date_str, when=when, error=error)
        return payload

    fs._fetch_api_football_fixtures = cache_only_fetch
    fs.get_fixtures_by_date_service = demo_fixtures
    if app is not None:
        app.include_router(router)


@router.get("/api/demo/status")
def demo_status():
    return {**demo_quota.status(), "today": _today(), "display_timezone": display_tz(),
            "external_requests_blocked": demo_network.blocked_counts()}


@router.get("/api/demo/fixture-cache/{date_str}")
def demo_cache(date_str: str):
    _validate(date_str)
    return cache_state(date_str, day_fixtures(date_str, None))


def _classify(attempts: list[dict], error: str | None, own: int) -> tuple[str, str | None]:
    if not attempts:
        return ("fresh", None) if own else ("provider_error", error or "no request was made")
    last = attempts[-1]
    text = (error or "").lower()
    if not last["allowed"]:
        return "quota_exhausted", last["reason"] or "allowance refused"
    if last["http_status"] == 429 or any(w in text for w in PROVIDER_LIMIT_WORDS):
        return "quota_exhausted", error or "provider request limit reached (HTTP 429)"
    if last["outcome"] == "error" or (last["http_status"] or 0) >= 400 or error:
        return "provider_error", error or f"HTTP {last['http_status']}"
    return ("fresh", None) if own else ("no_fixtures", None)


@router.post("/api/demo/refresh-fixtures/{date_str}")
def refresh_fixtures(date_str: str, background_tasks: BackgroundTasks):
    _validate(date_str)
    if fetched(date_str):
        state = cache_state(date_str, day_fixtures(date_str, background_tasks))
        raise HTTPException(status_code=409, detail={"error": "already_fetched", "state": state["state"], "cache": state,
                                                     "message": f"{date_str} was already fetched; no request sent."})
    quota = demo_quota.status()
    if not quota["can_refresh"]:
        raise HTTPException(status_code=403, detail={"error": "no_allowance", "state": "quota_exhausted",
                                                     "reasons": quota["blocked_reasons"], "quota": quota,
                                                     "message": "No authorized request remains; nothing was sent."})
    if not demo_quota.begin_refresh(date_str):
        raise HTTPException(status_code=409, detail={"error": "in_progress",
                                                     "message": f"A refresh of {date_str} is already running."})
    fs = _fs()
    marks = demo_quota.request_marks()
    try:
        _explicit.date = date_str
        fs._LAST_API_FOOTBALL_ERROR = None
        _original["service"](date_str, background_tasks, force_refresh=False)
        error = fs._LAST_API_FOOTBALL_ERROR
    finally:
        _explicit.date = None
        demo_quota.end_refresh(date_str)
    own = _own_fixtures(date_str)
    state, reason = _classify(demo_quota.requests_after(marks), error, own)
    demo_quota.record_refresh_attempt(date_str, state, reason, own)
    if state in ("fresh", "no_fixtures"):
        demo_quota.record_fixture_update(date_str, own)
        demo_quota.mark_date_fetched(date_str)
    cache = cache_state(date_str, day_fixtures(date_str, background_tasks))
    body = {"ok": state in ("fresh", "no_fixtures"), "state": state, "date": date_str, "error": reason, "cache": cache,
            "quota": cache["quota"]}
    if state == "fresh":
        body["message"] = f"Fixtures updated: {cache['cached_fixtures']} for {date_str}."
        return body
    if state == "no_fixtures":
        body["message"] = f"No fixtures are scheduled for {date_str}."
        return body
    body["message"] = (f"Refresh did not return fixtures ({state.replace('_', ' ')}): {reason}. "
                       "Cached fixtures are unchanged.")
    raise HTTPException(status_code=429 if state == "quota_exhausted" else 502, detail=body)

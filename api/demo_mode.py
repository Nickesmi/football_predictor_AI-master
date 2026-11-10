"""Local demo mode: cached fixtures by default, API-Football only on an explicit refresh.

APP_MODE=live (2026-11-05, src/ops/app_mode.py): the same endpoints, but yesterday, today and tomorrow are
fetched automatically when due (page load or the live scheduler), within a daily request budget instead of
owner grants; a date can be refreshed again; manual refreshes are refused only shortly after an update.

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

Fixture provider "football_data" (2026-10-31, API-Football account suspended): a day already fetched from
API-Football keeps its cache and is never requested again; any other day is read from, and refreshed
through, football-data.org (src/ops/football_data.py, its own cache files and limits). API-Football is
then never contacted.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException

from api import fixture_time
from src.ops import demo_network, demo_quota, football_data

router = APIRouter()
_explicit = threading.local()
_original: dict = {}
_settings = {"display_tz": fixture_time.DEFAULT_DISPLAY_TZ, "provider": "api_football", "mode": None}


def live() -> bool:
    """APP_MODE=live (src/ops/app_mode.py) with API-Football as the provider."""
    m = _settings.get("mode")
    return bool(m is not None and m.live and _settings["provider"] == "api_football")
PROVIDER_LIMIT_WORDS = ("request limit", "requests limit", "reached the limit", "too many requests", "429")


def _fs():
    import api.services.fixtures_service as fs

    return fs


def display_tz() -> str:
    return _settings["display_tz"]


def _fd(date_str: str | None = None) -> bool:
    """football-data.org serves this day (provider football_data, and no API-Football fetch of the day)."""
    return _settings["provider"] == football_data.PROVIDER and (date_str is None or not fetched(date_str))


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
    if not fetched(date_str):
        # football-data.org days (fetched while API-Football was suspended) stay visible in either mode
        # until an API-Football fetch of the day replaces them.
        cached = football_data.read_cache(date_str) if football_data.installed() else None
        if cached or _fd(date_str):
            return list(cached["fixtures"]) if cached else []
    result = _original["service"](date_str, background_tasks or BackgroundTasks(), force_refresh=False)
    own = result if isinstance(result, list) else result.get("fixtures", [])
    before, after = _neighbours(date_str)
    return fixture_time.day_view(date_str, display_tz(),
                                 {before: _storage_file(before), date_str: own, after: _storage_file(after)})


def _fd_cache_state(date_str: str, fixtures: list | None) -> dict:
    tz, today = display_tz(), _today()
    cached, last = football_data.read_cache(date_str), football_data.last_attempt(date_str)
    quota = demo_status()
    if fixtures:
        state = "cached"
    elif cached is not None:
        state = "no_fixtures"
    elif last and last["state"] in ("quota_exhausted", "provider_error"):
        state = last["state"]
    elif not quota["can_refresh"]:
        state = "quota_exhausted"
    else:
        state = "not_cached"
    return {"date": date_str, "state": state, "fetched": not football_data.refreshable(date_str, tz),
            "cached_fixtures": len(fixtures) if fixtures is not None else None, "unknown_time_basis": 0,
            "last_updated_at": cached["fetched_at"] if cached else None, "last_attempt": last,
            "period": "past" if date_str < today else ("today" if date_str == today else "upcoming"),
            "display_timezone": tz, "today": today, "data_source": "cache", "live": False,
            "provider": football_data.PROVIDER, "provider_label": football_data.LABEL, "quota": quota}


def cache_state(date_str: str, fixtures: list | None = None) -> dict:
    if _fd(date_str):
        return _fd_cache_state(date_str, fixtures)
    quota = demo_status() if _fd() else demo_quota.status()
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
    fd_cached = football_data.read_cache(date_str) if football_data.installed() and not fetched(date_str) else None
    updated = fd_cached["fetched_at"] if fd_cached else demo_quota.last_fixture_update(date_str)
    return {"date": date_str, "state": state, "fetched": fetched(date_str), "cached_fixtures": count,
            "unknown_time_basis": unknown, "last_updated_at": updated,
            "provider": football_data.PROVIDER if fd_cached else "api_football",
            "last_attempt": last, "period": "past" if date_str < today else ("today" if date_str == today else "upcoming"),
            "display_timezone": display_tz(), "today": today, "data_source": "cache", "live": live(),
            "mode": "live" if live() else "demo", "refresh": _due(date_str, fixtures)[1] if live() else None,
            "quota": quota}


MESSAGES = {
    "no_fixtures": "No fixtures are scheduled for {date} (provider checked {when}).",
    "quota_exhausted": "No cached fixtures for {date}, and no authorized API-Football request remains. "
                       "Cached dates stay available.",
    "provider_error": "No cached fixtures for {date}. The last refresh failed: {error}. Cached dates stay available.",
    "not_cached": "No cached fixtures for {date}. Use 'Refresh fixtures' to request it (1 API-Football request).",
}
FD_MESSAGES = {
    "no_fixtures": "No fixtures in football-data.org's competitions for {date} (checked {when}).",
    "quota_exhausted": "No cached fixtures for {date}: {error}. Cached dates stay available.",
    "provider_error": "No cached fixtures for {date}. The last football-data.org refresh failed: {error}. "
                      "Cached dates stay available.",
    "not_cached": "No cached fixtures for {date}. Use 'Refresh fixtures' to request it (1 football-data.org request).",
}


def _validate(date_str: str) -> None:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {date_str!r}. Use YYYY-MM-DD.")


def install(app=None, display_timezone: str = fixture_time.DEFAULT_DISPLAY_TZ,
            fixture_provider: str = "api_football", mode=None) -> None:
    """mode: src.ops.app_mode.ModeSettings (None = demo)."""
    fixture_time.zone(display_timezone)   # an unknown zone fails here, at start-up
    if fixture_provider not in ("api_football", football_data.PROVIDER):
        raise ValueError(f"unknown fixture provider {fixture_provider!r}")
    if mode is not None and mode.live and fixture_provider != "api_football":
        raise ValueError("APP_MODE=live needs --fixture-provider api_football")
    _settings["display_tz"] = display_timezone
    _settings["provider"] = fixture_provider
    _settings["mode"] = mode
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
        if live() and _due(date_str)[0]:
            _refresh(date_str, background_tasks, automatic=True)   # outcome is recorded; the cache is shown
        fixtures = day_fixtures(date_str, background_tasks)
        state = cache_state(date_str, fixtures)
        payload = {"fixtures": fixtures, "demo": state}
        if state["state"] != "cached":
            when = state["last_updated_at"] or "earlier"
            error = (state["last_attempt"] or {}).get("error") or "unknown error"
            texts = FD_MESSAGES if state.get("provider") == football_data.PROVIDER else MESSAGES
            payload["message"] = texts[state["state"]].format(date=date_str, when=when, error=error)
        return payload

    _original["convert"] = fs._api_football_to_fixture

    def convert_in_display_tz(raw: dict, requested_date: str = "") -> dict:
        # The shared converter labels fixtures with Asia/Tehran date/time. A kick-off at 23:30-23:59 in
        # Istanbul is the next Tehran day, and the service's page-load rewrite of a day's cache keeps only
        # fixtures labelled with that day, which silently dropped those matches. Label by the display time
        # zone from the UTC kick-off instead.
        fixture = _original["convert"](raw, requested_date)
        if fixture.get("kickoff_utc"):
            local = datetime.fromisoformat(fixture["kickoff_utc"].replace("Z", "+00:00")).astimezone(
                fixture_time.zone(display_tz()))
            fixture["date"], fixture["time"] = local.date().isoformat(), local.strftime("%H:%M")
        return fixture

    _original["read_cache"] = fs._read_fixture_cache

    def read_cache(date_str: str):
        if getattr(_explicit, "bypass", None) == date_str:
            return None                     # live refresh of this date (see _refresh)
        return _original["read_cache"](date_str)

    fs._read_fixture_cache = read_cache
    fs._fetch_api_football_fixtures = cache_only_fetch
    fs.get_fixtures_by_date_service = demo_fixtures
    fs._api_football_to_fixture = convert_in_display_tz
    if app is not None:
        app.include_router(router)


@router.get("/api/demo/status")
def demo_status():
    base = {"today": _today(), "display_timezone": display_tz(), "external_requests_blocked": demo_network.blocked_counts()}
    if _fd():
        return {"mode": "demo", "explicit_only": True, "cost_per_refresh": 1, "legacy": None,
                **football_data.status(display_tz()), **base,
                "api_football": "disabled: account suspended; days fetched earlier stay cached"}
    return {**demo_quota.status(), "provider": "api_football", "provider_label": "API-Football", **base}


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


def _fd_refresh(date_str: str):
    if not football_data.refreshable(date_str, display_tz()):
        state = cache_state(date_str, day_fixtures(date_str, None))
        raise HTTPException(status_code=409, detail={
            "error": "already_fetched", "state": state["state"], "cache": state,
            "message": f"{date_str} is cached and was updated less than {football_data.REFRESH_AFTER // 60} min ago "
                       "(or after the day ended); no request sent."})
    quota = demo_status()
    if not quota["can_refresh"]:
        raise HTTPException(status_code=403, detail={"error": "no_allowance", "state": "quota_exhausted",
                                                     "reasons": quota["blocked_reasons"], "quota": quota,
                                                     "message": f"{quota['blocked_reasons'][0]}; nothing was sent."})
    result = football_data.refresh(date_str, display_tz())
    state, reason = result["state"], result["error"]
    cache = cache_state(date_str, day_fixtures(date_str, None))
    body = {"ok": state in ("fresh", "no_fixtures"), "state": state, "date": date_str, "error": reason, "cache": cache,
            "quota": cache["quota"], "provider": football_data.PROVIDER}
    if state == "fresh":
        body["message"] = f"Fixtures updated from football-data.org: {cache['cached_fixtures']} for {date_str}."
        return body
    if state == "no_fixtures":
        body["message"] = f"No fixtures in football-data.org's competitions for {date_str}."
        return body
    body["message"] = (f"Refresh did not return fixtures ({state.replace('_', ' ')}): {reason}. "
                       "Cached fixtures are unchanged.")
    raise HTTPException(status_code=429 if state == "quota_exhausted" else 502, detail=body)


def _due(date_str: str, fixtures: list | None = None) -> tuple[bool, str]:
    from src.ops import app_mode

    if not live():
        return False, "demo mode"
    if fixtures is None:
        fixtures = _storage_file(date_str)
    return app_mode.refresh_due(_settings["mode"], date_str, _today(), demo_quota.last_fixture_update(date_str),
                                fixtures, demo_quota._now(), demo_quota.last_refresh_attempt(date_str))


def _manual_too_soon(date_str: str) -> str | None:
    from src.ops import app_mode

    m = _settings["mode"]
    age = app_mode._age_minutes(demo_quota.last_fixture_update(date_str), demo_quota._now())
    if age is not None and age < m.manual_min_minutes:
        return (f"{date_str} was updated {int(age)} min ago; a manual refresh is possible "
                f"{m.manual_min_minutes} min after the last update. No request sent.")
    return None


@router.post("/api/demo/refresh-fixtures/{date_str}")
def refresh_fixtures(date_str: str, background_tasks: BackgroundTasks):
    _validate(date_str)
    if _fd(date_str):
        return _fd_refresh(date_str)
    if live():
        too_soon = _manual_too_soon(date_str)
        if too_soon:
            state = cache_state(date_str, day_fixtures(date_str, background_tasks))
            raise HTTPException(status_code=409, detail={"error": "recently_updated", "state": state["state"],
                                                         "cache": state, "message": too_soon})
    elif fetched(date_str):
        state = cache_state(date_str, day_fixtures(date_str, background_tasks))
        raise HTTPException(status_code=409, detail={"error": "already_fetched", "state": state["state"], "cache": state,
                                                     "message": f"{date_str} was already fetched; no request sent."})
    return _refresh(date_str, background_tasks, automatic=False)


def _refresh(date_str: str, background_tasks: BackgroundTasks, automatic: bool):
    """One counted API-Football request for a date. Manual: raises HTTPException on any failure.
    Automatic (live page load or scheduler): never raises; the outcome is recorded and returned."""
    quota = demo_quota.status()
    if automatic and not quota["can_refresh"]:
        return {"ok": False, "state": "quota_exhausted", "date": date_str, "error": quota["blocked_reasons"][0]}
    if not quota["can_refresh"]:
        raise HTTPException(status_code=403, detail={"error": "no_allowance", "state": "quota_exhausted",
                                                     "reasons": quota["blocked_reasons"], "quota": quota,
                                                     "message": "No authorized request remains; nothing was sent."})
    if not demo_quota.begin_refresh(date_str):
        if automatic:
            return {"ok": False, "state": "in_progress", "date": date_str, "error": None}
        raise HTTPException(status_code=409, detail={"error": "in_progress",
                                                     "message": f"A refresh of {date_str} is already running."})
    fs = _fs()
    marks = demo_quota.request_marks()
    # Live mode refreshes a date that is already cached: the fixture service never re-fetches a cached
    # API-Football day and skips fixtures whose id it already holds, so statuses and results would never
    # change. The service therefore reads no cache for this one call; if the request does not succeed, the
    # previous cache file is put back unchanged.
    path = fs._get_cache_path(date_str)
    previous = path.read_bytes() if live() and path.exists() else None
    try:
        _explicit.date = date_str
        _explicit.bypass = date_str if live() else None
        fs._LAST_API_FOOTBALL_ERROR = None
        _original["service"](date_str, background_tasks, force_refresh=False)
        error = fs._LAST_API_FOOTBALL_ERROR
    finally:
        _explicit.date = None
        _explicit.bypass = None
        demo_quota.end_refresh(date_str)
    own = _own_fixtures(date_str)
    attempts = demo_quota.requests_after(marks)
    state, reason = _classify(attempts, error, own)
    if live() and state == "fresh" and not attempts:
        state, reason = "provider_error", "no request was sent"
    if previous is not None and state != "fresh":
        path.write_bytes(previous)        # a failed, refused or empty live refresh never replaces the cache
        own = _own_fixtures(date_str)
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
    if automatic:
        return body
    raise HTTPException(status_code=429 if state == "quota_exhausted" else 502, detail=body)


# ── Live scheduler (APP_MODE=live): yesterday, today and tomorrow, whenever they are due ─────────────

_scheduler: dict = {}


def live_tick() -> list[dict]:
    """One pass: refresh each of yesterday/today/tomorrow that is due. Returns what was done (no keys)."""
    if not live():
        return []
    d = date.fromisoformat(_today())
    out = []
    ahead = _settings["mode"].days_ahead
    for day in [(d + timedelta(days=i)).isoformat() for i in range(-1, ahead + 1)]:
        due, why = _due(day)
        if due:
            r = _refresh(day, BackgroundTasks(), automatic=True)
            out.append({"date": day, "reason": why, "state": r.get("state")})
    hist = team_history_pass()
    if hist:
        out.append({"team_history": hist})
    return out


def team_history_pass(max_requests: int | None = None) -> dict | None:
    """Live mode with TEAM_HISTORY_FETCH=on: fetch history for teams in the next 7 days of cached fixtures
    with thin stored history (src/ops/team_history_fetch.py). Off by default; the budget applies."""
    from src.ops import team_history_fetch as thf

    if not live() or not thf.enabled():
        return None
    from api.services import fixtures_service
    from src.db.database import get_db

    n = max_requests if max_requests is not None else int(os.environ.get("TEAM_HISTORY_PER_TICK") or 10)
    rep = thf.prefetch_upcoming(get_db(), fixtures_service._FIXTURE_CACHE_DIR, _today(), days=7, max_requests=n)
    if rep.get("inserted"):
        from api.services.match_analysis_service import _ANALYSIS_CACHE
        _ANALYSIS_CACHE.clear()
    return rep


def start_live_scheduler(interval_seconds: int = 600) -> bool:
    """A daemon thread calling live_tick() every `interval_seconds` (live mode only)."""
    if not live() or _scheduler.get("thread"):
        return False
    stop = threading.Event()

    def loop():
        from src.config import logger

        while not stop.wait(5 if not _scheduler.get("ran") else interval_seconds):
            _scheduler["ran"] = True
            try:
                done = live_tick()
                if done:
                    logger.info(f"[LIVE] scheduler: {done}")
            except Exception as e:   # recorded, never fatal
                logger.warning(f"[LIVE] scheduler pass failed: {type(e).__name__}: {e}")

    t = threading.Thread(target=loop, name="live-fixture-scheduler", daemon=True)
    _scheduler.update(thread=t, stop=stop)
    t.start()
    return True

"""Local demo mode: cached fixtures by default, API-Football only on an explicit refresh.

Loaded only by scripts/demo_server.py (never by the production app). install() makes the fixture
endpoints read the demo's own cache and database without contacting API-Football, and adds:

    GET  /api/demo/status                     allowance, limits and why a refresh is blocked (sends nothing)
    GET  /api/demo/fixture-cache/{date}       cache state and last successful update for a date (sends nothing)
    POST /api/demo/refresh-fixtures/{date}    the only path that can send: one counted request, for a date
                                              that is not cached yet, when an owner-authorized allowance remains

Quota rules live in src/ops/demo_quota.py; this module never decides an allowance itself.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException

from src.ops import demo_quota

router = APIRouter()
_explicit = threading.local()
_original: dict = {}


def _fs():
    import api.services.fixtures_service as fs

    return fs


def _today() -> str:
    return _fs()._get_istanbul_today()


def cache_state(date_str: str) -> dict:
    """What the demo holds for a date. A date counts as cached once its own cache file has
    API-Football fixtures for it; such a date is never requested again."""
    fs = _fs()
    path = fs._get_cache_path(date_str)
    fixtures = 0
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            fixtures = sum(1 for f in data if isinstance(f, dict) and f.get("date") == date_str
                           and f.get("provider") == "api_football" and not f.get("_overflow"))
        except (OSError, ValueError):
            fixtures = 0
    updated = demo_quota.last_fixture_update(date_str)   # never the file time: page loads rewrite the file
    today = _today()
    return {"date": date_str, "cached": fixtures > 0, "cached_fixtures": fixtures,
            "last_updated_at": updated if fixtures else None,
            "period": "past" if date_str < today else ("today" if date_str == today else "upcoming"),
            "data_source": "cache", "live": False}


def _validate(date_str: str) -> None:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {date_str!r}. Use YYYY-MM-DD.")


def install(app=None) -> None:
    fs = _fs()
    if _original:
        return
    _original.update(fetch=fs._fetch_api_football_fixtures, service=fs.get_fixtures_by_date_service)

    def cache_only_fetch(date_str: str) -> list[dict]:
        if getattr(_explicit, "date", None) != date_str:
            fs._LAST_API_FOOTBALL_ERROR = None   # a page load never contacts the provider; that is not an error
            fs.logger.info(f"[DEMO] cache-only: no API-Football request for {date_str}")
            return []
        with demo_quota.explicit(max_requests=1):
            return _original["fetch"](date_str)

    def demo_fixtures(date_str: str, background_tasks: BackgroundTasks, force_refresh: bool = False):
        result = _original["service"](date_str, background_tasks, force_refresh=False)
        fixtures = result if isinstance(result, list) else result.get("fixtures", [])
        state = cache_state(date_str)
        payload = {"fixtures": fixtures, "demo": {**state, "quota": demo_quota.status()}}
        if not fixtures:
            payload["message"] = (f"No cached fixtures for {date_str}. Cached dates stay available; use "
                                  "'Refresh fixtures' to request this date (1 API-Football request).")
        return payload

    fs._fetch_api_football_fixtures = cache_only_fetch
    fs.get_fixtures_by_date_service = demo_fixtures
    if app is not None:
        app.include_router(router)


@router.get("/api/demo/status")
def demo_status():
    return {**demo_quota.status(), "today": _today()}


@router.get("/api/demo/fixture-cache/{date_str}")
def demo_cache(date_str: str):
    _validate(date_str)
    return cache_state(date_str)


@router.post("/api/demo/refresh-fixtures/{date_str}")
def refresh_fixtures(date_str: str, background_tasks: BackgroundTasks):
    _validate(date_str)
    before = cache_state(date_str)
    if before["cached"]:
        raise HTTPException(status_code=409, detail={"error": "already_cached", "cache": before,
                                                     "message": f"{date_str} is already cached; no request sent."})
    quota = demo_quota.status()
    if not quota["can_refresh"]:
        raise HTTPException(status_code=403, detail={"error": "no_allowance", "reasons": quota["blocked_reasons"],
                                                     "quota": quota, "message": "No authorized request remains; "
                                                     "nothing was sent."})
    if not demo_quota.begin_refresh(date_str):
        raise HTTPException(status_code=409, detail={"error": "in_progress",
                                                     "message": f"A refresh of {date_str} is already running."})
    fs = _fs()
    try:
        _explicit.date = date_str
        _original["service"](date_str, background_tasks, force_refresh=False)
        error = fs._LAST_API_FOOTBALL_ERROR
    finally:
        _explicit.date = None
        demo_quota.end_refresh(date_str)
    after = cache_state(date_str)
    if after["cached"]:
        demo_quota.record_fixture_update(date_str, after["cached_fixtures"])
        demo_quota.mark_date_fetched(date_str)
        after = cache_state(date_str)
    body = {"ok": after["cached"], "date": date_str, "cache": after, "error": None if after["cached"] else error,
            "quota": demo_quota.status()}
    if not after["cached"]:
        body["message"] = f"Refresh did not return fixtures: {error or 'the provider returned none'}"
        raise HTTPException(status_code=502, detail=body)
    return body

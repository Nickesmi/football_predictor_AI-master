"""
System Service (WebSockets, Background Tasks, Health).

Manages real-time WebSocket connections, background tasks (live score broadcast and nightly retrain),
and system health diagnostics.
"""

import time
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from fastapi import WebSocket, WebSocketDisconnect
from src.config import logger, APIFOOTBALL_API_KEY, TOP_LEAGUES

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)
        for dead in dead_connections:
            self.disconnect(dead)

manager = ConnectionManager()

# ── Background Tasks ─────────────────────────────────────────────────────────
async def start_background_tasks():
    asyncio.create_task(_nightly_retrain_loop())
    asyncio.create_task(_broadcast_live_scores_loop())
    asyncio.create_task(_daily_fixture_refresh_loop())

async def _broadcast_live_scores_loop():
    from src.data.sofascore_provider import fetch_live_matches
    while True:
        try:
            if manager.active_connections:
                live_data, err = fetch_live_matches()
                if live_data and not err:
                    await manager.broadcast({"type": "LIVE_SCORE_UPDATE", "data": live_data})
        except Exception as e:
            logger.error(f"Error in live scores broadcast loop: {e}")
        await asyncio.sleep(15)


async def _daily_fixture_refresh_loop():
    """
    Quota-efficient API-Football fixture refresh with full Istanbul timezone support.

    STRATEGY:
      - Each fixture is converted to Istanbul (UTC+3) time before being cached.
      - One UTC API call may write to TWO Istanbul-date cache files: the matching
        day AND the next day (for late-UTC fixtures that cross midnight in Istanbul).
      - On startup: fetch yesterday UTC + today UTC + tomorrow UTC, but only
        if the target Istanbul-date cache is missing.
        - yesterday UTC  ->  fills 00:xx-02:xx IST slots for today (e.g. Brazil 22:00 UTC)
        - today UTC      ->  fills 03:xx-23:59 IST slots for today
        - tomorrow UTC   ->  fills all IST slots for tomorrow
      - At 00:05 Istanbul each night: fetch yesterday UTC (new today's early-IST
        matches) and tomorrow UTC (new day's schedule). Uses ~2 requests/night.

    Free plan budget (100 req/day):
      First startup: up to 3 requests. Each midnight: 2 requests.
      Weekly total : ~17 requests out of 700 available.
    """
    import urllib.request
    import json
    from collections import defaultdict

    def _istanbul_date(offset_days: int = 0) -> str:
        tz = timezone(timedelta(hours=3))
        return (datetime.now(tz).date() + timedelta(days=offset_days)).isoformat()

    def _has_valid_api_football_cache(ist_date: str) -> bool:
        """True when an Istanbul-date cache has full API-Football fixtures (at least one >= 03:00 IST)."""
        from api.services.fixtures_service import _get_cache_path
        path = _get_cache_path(ist_date)
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list) or len(data) == 0:
                return False
            has_api = any(
                isinstance(f, dict) and (
                    f.get("provider") == "api_football" or
                    f.get("source") in ("api_football", "apifootball")
                )
                for f in data
            )
            has_after_3am = any(
                isinstance(f, dict) and (f.get("time") or "00:00") >= "03:00" and (
                    f.get("provider") == "api_football" or f.get("source") in ("api_football", "apifootball")
                )
                for f in data
            )
            return has_api and has_after_3am
        except Exception:
            return False

    def _fetch_and_cache(utc_date: str) -> dict:
        """
        Fetch all fixtures for utc_date from API-Football.
        Converts each to Istanbul time and writes them into the correct
        Istanbul-date cache files (may be today AND tomorrow for late-UTC matches).
        Returns a dict {istanbul_date: count}.
        """
        from src.config import APIFOOTBALL_API_KEY as KEY
        if not KEY:
            logger.warning("[AUTO-REFRESH] No API-Football key configured.")
            return {}
        url = f"https://v3.football.api-sports.io/fixtures?date={utc_date}"
        try:
            req = urllib.request.Request(
                url, headers={"x-apisports-key": KEY, "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
            errors = data.get("errors")
            if errors and errors != [] and errors != {}:
                logger.warning(f"[AUTO-REFRESH] API-Football error for UTC {utc_date}: {errors}")
                return {}
            raw_fixtures = data.get("response", [])
            if not raw_fixtures:
                logger.info(f"[AUTO-REFRESH] 0 fixtures from API-Football for UTC {utc_date}")
                return {}

            from api.services.fixtures_service import (
                _api_football_to_fixture, _write_fixture_cache, _track_competitions_from_fixtures, _get_cache_path
            )

            # Group by Istanbul date — no filter, let each fixture land on its real date
            by_ist = defaultdict(list)
            seen_ids = set()
            for raw in raw_fixtures:
                f = _api_football_to_fixture(raw, "")  # Istanbul conversion inside
                fid = f.get("id")
                if fid and fid not in seen_ids:
                    seen_ids.add(fid)
                    by_ist[f["date"]].append(f)

            written = {}
            for ist_date, fixtures in by_ist.items():
                fixtures.sort(key=lambda x: x.get("time") or "TBD")

                # Merge with existing cache to avoid losing other-source data
                existing_path = _get_cache_path(ist_date)
                if existing_path.exists():
                    try:
                        existing = json.loads(existing_path.read_text(encoding="utf-8"))
                        existing_ids = {f.get("id") for f in existing}
                        new_only = [f for f in fixtures if f.get("id") not in existing_ids]
                        if new_only:
                            merged = sorted(existing + new_only, key=lambda x: x.get("time") or "TBD")
                            _write_fixture_cache(ist_date, merged)
                            logger.info(f"[AUTO-REFRESH] +{len(new_only)} fixtures -> Istanbul {ist_date} (UTC {utc_date})")
                        else:
                            _write_fixture_cache(ist_date, fixtures)
                    except Exception:
                        _write_fixture_cache(ist_date, fixtures)
                else:
                    _write_fixture_cache(ist_date, fixtures)
                    logger.info(f"[AUTO-REFRESH] {len(fixtures)} fixtures -> Istanbul {ist_date} (UTC {utc_date})")

                _track_competitions_from_fixtures(fixtures)
                written[ist_date] = len(fixtures)

            return written
        except Exception as e:
            logger.error(f"[AUTO-REFRESH] Fetch failed for UTC {utc_date}: {e}")
            return {}

    def _prewarm(ist_date: str):
        try:
            from api.services.prediction_service import precompute_predictions_for_date
            precompute_predictions_for_date(ist_date)
            logger.info(f"[AUTO-REFRESH] Predictions pre-warmed for {ist_date}")
        except Exception as e:
            logger.warning(f"[AUTO-REFRESH] Pre-warm skipped for {ist_date}: {e}")

    def _utc_date(offset_days: int = 0) -> str:
        """Current UTC date + offset_days."""
        return (datetime.now(timezone.utc).date() + timedelta(days=offset_days)).isoformat()

    def _ist_has_full_coverage(ist_date: str) -> bool:
        """True when the IST cache for ist_date has API-Football fixtures at or after 03:00 IST.
        This indicates the UTC same-day data has been fetched (not just the previous-UTC-day overflow)."""
        from api.services.fixtures_service import _get_cache_path
        path = _get_cache_path(ist_date)
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list) or len(data) == 0:
                return False
            return any(
                isinstance(f, dict)
                and (f.get("provider") == "api_football" or f.get("source") in ("api_football", "apifootball"))
                and (f.get("time") or "00:00") >= "03:00"
                for f in data
            )
        except Exception:
            return False

    # ── Startup ──
    # On startup fetch the following UTC dates:
    #   UTC yesterday  → IST before-3am slots for today (late-UTC matches)
    #   UTC today      → IST daytime slots for today + IST before-3am for tomorrow
    #   UTC tomorrow   → IST daytime slots for tomorrow
    # Each covers a different IST cache file.
    await asyncio.sleep(3)
    loop = asyncio.get_event_loop()

    for utc_offset in [-1, 0, 1]:
        utc_date = _utc_date(utc_offset)
        # For UTC offset 0 and +1, skip only if the IST date (same as UTC +3h) already has full coverage
        ist_equivalent = _istanbul_date(utc_offset)
        if utc_offset >= 0 and _ist_has_full_coverage(ist_equivalent):
            logger.info(f"[AUTO-REFRESH] IST {ist_equivalent} already has full API-Football coverage — skipping UTC {utc_date}")
            continue
        written = await loop.run_in_executor(None, _fetch_and_cache, utc_date)
        for ist_date in written:
            await loop.run_in_executor(None, _prewarm, ist_date)

    # ── Hourly loop: checks every hour for newly-unlocked UTC dates ──
    # The free plan rolling 3-day window shifts at midnight UTC.
    # e.g. when UTC date changes from Jul 19 → Jul 20, "Jul 20" UTC becomes accessible.
    # This loop wakes every hour and fetches any UTC dates whose IST caches are incomplete.
    while True:
        tz = timezone(timedelta(hours=3))
        now = datetime.now(tz)
        # Wake at HH:05 every hour
        target = now.replace(minute=5, second=0, microsecond=0)
        if now >= target:
            target += timedelta(hours=1)
        sleep_secs = (target - now).total_seconds()
        logger.info(f"[AUTO-REFRESH] Next check in {sleep_secs/60:.0f}min")
        await asyncio.sleep(sleep_secs)

        # Fetch UTC yesterday, today, tomorrow (UTC) each tick
        for utc_offset in [-1, 0, 1]:
            utc_date = _utc_date(utc_offset)
            ist_equivalent = _istanbul_date(utc_offset)
            if utc_offset >= 0 and _ist_has_full_coverage(ist_equivalent):
                continue
            logger.info(f"[AUTO-REFRESH] Fetching UTC {utc_date} (IST target: {ist_equivalent})")
            written = await loop.run_in_executor(None, _fetch_and_cache, utc_date)
            for ist_date in written:
                await loop.run_in_executor(None, _prewarm, ist_date)


async def _nightly_retrain_loop():
    while True:
        now = datetime.now()
        target = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        sleep_seconds = (target - now).total_seconds()
        logger.info(f"Nightly retrain scheduled in {sleep_seconds:.0f} seconds (at 03:00 local time)")
        await asyncio.sleep(sleep_seconds)

        try:
            lock_file = Path(".cache/retrain.lock")
            lock_file.parent.mkdir(exist_ok=True)
            should_run = True
            if lock_file.exists():
                try:
                    last_run = float(lock_file.read_text())
                    if time.time() - last_run < 3600:
                        should_run = False
                        logger.info("Nightly retrain already executed by another worker.")
                except Exception:
                    pass

            if should_run:
                lock_file.write_text(str(time.time()))
                logger.info("Starting automated nightly engine retrain...")
                from src.db.database import get_db
                conn = get_db()
                from src.engine.isotonic_calibrator import get_isotonic_calibrator
                cal = get_isotonic_calibrator(conn)
                cal.fit_all(conn)
                from src.db.error_intelligence import rebuild_confidence_adjustments
                rebuild_confidence_adjustments(conn)
                logger.info("Nightly engine retrain complete.")
        except Exception as e:
            logger.error(f"Nightly retrain failed: {e}")

def health_check_service() -> dict:
    """Return system health diagnostic information."""
    return {
        "status": "ok",
        "data_source": "sofascore",
        "analysis_mode": "live" if APIFOOTBALL_API_KEY else "per_match_poisson",
        "engine": "Hybrid Poisson Goals + Corners + Cards v5.0",
        "leagues": list(TOP_LEAGUES.values()),
    }

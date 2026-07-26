"""
Fixtures & Precomputation Service.

Provides business logic and caching for daily fixtures, live scoreline merging,
and background prediction pre-warm.
"""

import os
import sys
import time
import json
import re
import ssl
import certifi
import unicodedata
import logging
import zoneinfo
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional
from fastapi import BackgroundTasks, HTTPException
from src.config import logger, TOP_LEAGUES, APIFOOTBALL_API_KEY, APIFOOTBALL_HOST
from src.db.database import get_db
from api.services.logo_service import resolve_team_logo as _resolve_team_logo
from api.services.match_analysis_service import _compute_match_analysis, _ANALYSIS_CACHE
from src.data.sofascore_fetcher import fetch_fixtures_by_date
from src.data.live_score_provider import fetch_live_scores

_PREDICTION_STATUS: dict[str, dict] = {}

# ─── Fixture cache ─────────────────────────────────────────────────────────
# We cache API-Football responses per-date so the UI is instant on repeat
# loads, and we avoid burning through the daily API quota.

_FIXTURE_CACHE_DIR = Path(".cache")
_FIXTURE_CACHE_TTL = 5 * 60   # 5 minutes — fast refresh for live scores


def _get_cache_path(date_str: str) -> Path:
    _FIXTURE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _FIXTURE_CACHE_DIR / f"fixtures-{date_str}.json"


def _get_istanbul_today() -> str:
    """Return the current date in Europe/Istanbul (UTC+3) timezone as a YYYY-MM-DD string."""
    import zoneinfo
    from datetime import datetime
    try:
        return datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul")).date().isoformat()
    except Exception:
        from datetime import timezone, timedelta
        return datetime.now(timezone(timedelta(hours=3))).date().isoformat()


def _read_fixture_cache(date_str: str) -> Optional[list]:
    """Return cached fixture list or None if missing / expired."""
    path = _get_cache_path(date_str)
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    today = _get_istanbul_today()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return None


            
        has_live = any(
            isinstance(f, dict) and "LIVE" in f.get("status", "") 
            for f in data
        )
        has_ht = any(
            isinstance(f, dict) and "HT" in f.get("status", "")
            for f in data
        )
        has_unfinished = any(
            isinstance(f, dict) and f.get("status", "") not in ["FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"]
            for f in data
        )

        if date_str < today:
            # If it's a past date but still contains unfinished matches (LIVE/HT/NS/TBD/etc.), force expire!
            if has_unfinished:
                logger.info(f"Fixture cache for {date_str} contains unfinished matches. Forcing expiration to get FT results.")
                return None
            return data

        # Static Daily Fetch Mode: provider data is cached for 24 hours to preserve quota.
        is_api_football = any(f.get("provider") == "api_football" or f.get("source") in ["api_football", "apifootball"] for f in data if isinstance(f, dict))
        if is_api_football:
            # API-Football data: never expire future/today dates — it won't change.
            # Only past dates with unfinished matches are already expired above.
            is_future_or_today = date_str >= today
            if is_future_or_today:
                return data  # serve forever until a new fetch explicitly replaces it
            effective_ttl = 86400  # 24 hours for past dates not yet fully finished
        elif has_live or has_ht:
            effective_ttl = 900
        else:
            effective_ttl = 900

        if age > effective_ttl:
            logger.debug(f"Fixture cache expired for {date_str} (age={age:.0f}s, has_live={has_live})")
            return None

        return data
    except Exception as e:
        logger.warning(f"Fixture cache read failed for {date_str}: {e}")
    return None


def _write_fixture_cache(date_str: str, fixtures: list) -> None:
    """Write the processed fixture list to disk cache."""
    try:
        path = _get_cache_path(date_str)
        tmp_path = path.with_suffix(f"{path.suffix}.tmp.{os.getpid()}")
        tmp_path.write_text(json.dumps(fixtures, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception as e:
        logger.warning(f"Fixture cache write failed for {date_str}: {e}")


# ─── API-Football v3 fetcher ────────────────────────────────────────────────

_LAST_API_FOOTBALL_ERROR: Optional[str] = None


def _fetch_api_football_fixtures(date_str: str) -> list[dict]:
    """Fetch scheduled/live fixtures from API-Football v3 for a date."""
    global _LAST_API_FOOTBALL_ERROR
    _LAST_API_FOOTBALL_ERROR = None

    if not APIFOOTBALL_API_KEY:
        _LAST_API_FOOTBALL_ERROR = "No API-Football key is configured."
        logger.warning("APIFOOTBALL_API_KEY not set; API-Football fixture fetch unavailable")
        return []

    url = f"https://v3.football.api-sports.io/fixtures?date={date_str}&timezone=Europe/Istanbul"
    req = urllib.request.Request(url, headers={
        "x-apisports-key": APIFOOTBALL_API_KEY,
        "Accept": "application/json",
    })
    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        resp = urllib.request.urlopen(req, timeout=20, context=ctx)
        data = json.loads(resp.read())
        errors = data.get("errors", {})
        if errors:
            if isinstance(errors, dict):
                if "access" in errors:
                    _LAST_API_FOOTBALL_ERROR = str(errors["access"])
                elif "requests" in errors:
                    _LAST_API_FOOTBALL_ERROR = str(errors["requests"])
                else:
                    _LAST_API_FOOTBALL_ERROR = "; ".join(str(v) for v in errors.values())
            else:
                _LAST_API_FOOTBALL_ERROR = str(errors)
            logger.warning(f"API-Football Error for {date_str}: {errors}. Returning empty list.")
            return []

        fixtures = data.get("response", [])
        logger.info(f"API-Football returned {len(fixtures)} fixtures for {date_str}")
        return fixtures
    except Exception as e:
        _LAST_API_FOOTBALL_ERROR = str(e)
        logger.error(f"API-Football fetch failed for {date_str}: {e}. Returning empty list.")
        return []


def _api_football_to_fixture(raw: dict, requested_date: str = "") -> dict:
    """Convert a single API-Football v3 fixture dict to the frontend shape."""
    fix = raw.get("fixture", {})
    league = raw.get("league", {})
    teams = raw.get("teams", {})
    goals = raw.get("goals", {})
    score = raw.get("score", {})

    api_date_iso = fix.get("date", "")
    # Default fallback (used if conversion fails)
    date_str = requested_date or (api_date_iso[:10] if api_date_iso else "")
    time_str = api_date_iso[11:16] if len(api_date_iso) >= 16 else "TBD"

    # Convert UTC kick-off time to Istanbul (UTC+3) so that late-UTC matches
    # (e.g. Brazil 22:00 UTC = Istanbul 01:00 next day) appear on the correct
    # calendar date and show the correct local time for Istanbul users.
    if api_date_iso:
        try:
            from datetime import timezone as _tz, timedelta as _td
            _ist = _tz(_td(hours=3))
            _dt = datetime.fromisoformat(api_date_iso.replace("Z", "+00:00"))
            if _dt.tzinfo is None:
                _dt = _dt.replace(tzinfo=_tz.utc)
            _dt_ist = _dt.astimezone(_ist)
            date_str = _dt_ist.strftime("%Y-%m-%d")
            time_str = _dt_ist.strftime("%H:%M")
        except Exception:
            pass  # keep UTC fallback values above

    status_short = fix.get("status", {}).get("short", "")
    if status_short in ("FT", "AET", "PEN"):
        display_status = "FT"
    elif status_short == "HT":
        display_status = "HT"
    elif status_short in ("1H", "2H", "ET", "P", "LIVE", "BT"):
        display_status = "LIVE"
    elif status_short == "NS":
        display_status = "NS"
    elif status_short in ("PST", "CANC", "ABD", "SUSP", "INT", "AWD", "WO"):
        display_status = status_short
    else:
        display_status = status_short or "NS"

    halftime = score.get("halftime") or {}
    fulltime = score.get("fulltime") or {}
    is_live = display_status == "LIVE"
    is_finished = display_status == "FT"

    if is_finished:
        home_goals = fulltime.get("home") if fulltime.get("home") is not None else goals.get("home")
        away_goals = fulltime.get("away") if fulltime.get("away") is not None else goals.get("away")
    elif is_live:
        home_goals = goals.get("home")
        away_goals = goals.get("away")
    else:
        home_goals = None
        away_goals = None

    home = teams.get("home", {})
    away = teams.get("away", {})
    league_id = str(league.get("id", ""))
    home_id = str(home.get("id", ""))
    away_id = str(away.get("id", ""))

    return {
        "id": str(fix.get("id", "")),
        "date": date_str,
        "time": time_str,
        "status": display_status,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "fh_home_goals": halftime.get("home") if display_status in ("LIVE", "HT", "FT") else None,
        "fh_away_goals": halftime.get("away") if display_status in ("LIVE", "HT", "FT") else None,
        "league": {
            "id": league_id,
            "name": league.get("name", "Unknown"),
            "country": league.get("country", ""),
            "logo": league.get("logo", ""),
        },
        "home_team": {
            "id": home_id,
            "name": home.get("name", "Unknown"),
            "logo": home.get("logo") or _resolve_team_logo(home_id, home.get("name", ""), ""),
        },
        "away_team": {
            "id": away_id,
            "name": away.get("name", "Unknown"),
            "logo": away.get("logo") or _resolve_team_logo(away_id, away.get("name", ""), ""),
        },
        "league_id_apifootball": league_id,
        "season": league.get("season"),
        "round": league.get("round", ""),
        "source": "api_football",
        "provider": "api_football",
        "scoreline_source": "api_football" if home_goals is not None and away_goals is not None else None,
    }



def get_today_fixtures_service(background_tasks: BackgroundTasks):
    return get_fixtures_by_date_service(_get_istanbul_today(), background_tasks)


def _fetch_sofascore_fixtures(date_str: str) -> list[dict]:
    """Fetch scheduled events from SofaScore API.
    
    Delegates to the RapidAPI wrapper to bypass Cloudflare reliably.
    """
    from src.data.sofascore_fetcher import fetch_fixtures_by_date
    events = fetch_fixtures_by_date(date_str)
    
    if events:
        logger.info(f"SofaScore returned {len(events)} events for {date_str} via RapidAPI")
    else:
        logger.error(f"SofaScore returned no events for {date_str} via RapidAPI")
        
    return events


def _sofascore_to_fixture(ev: dict, requested_date: str = "") -> dict:
    """Convert a SofaScore event dict to the exact frontend fixture structure."""
    home = ev.get("homeTeam", {})
    away = ev.get("awayTeam", {})
    ut = ev.get("tournament", {}).get("uniqueTournament", {})
    ut_id = ut.get("id", 0)
    
    # Kickoff time from timestamp
    import datetime
    import zoneinfo
    start_ts = ev.get("startTimestamp")
    time_str = "TBD"
    date_str = requested_date or ""
    if start_ts:
        try:
            # Convert to Europe/Istanbul (Turkey time, UTC+3) for the time display
            dt = datetime.datetime.fromtimestamp(start_ts, zoneinfo.ZoneInfo("Europe/Istanbul"))
            time_str = dt.strftime("%H:%M")
            if not requested_date:
                date_str = dt.strftime("%Y-%m-%d")
        except Exception:
            try:
                dt = datetime.datetime.fromtimestamp(start_ts, datetime.timezone(datetime.timedelta(hours=3)))
                time_str = dt.strftime("%H:%M")
                if not requested_date:
                    date_str = dt.strftime("%Y-%m-%d")
            except Exception:
                pass
                
    # Status
    status_info = ev.get("status", {})
    stype = status_info.get("type", "")
    if stype == "finished":
        display_status = "FT"
    elif stype == "inprogress":
        display_status = "LIVE"
    else:
        display_status = "NS"
        
    is_live_or_finished = display_status != "NS"
    
    # Goals
    home_score = ev.get("homeScore", {})
    away_score = ev.get("awayScore", {})
    
    # Halftime goals
    fh_home = home_score.get("period1") if is_live_or_finished else None
    fh_away = away_score.get("period1") if is_live_or_finished else None

    # Fallback to current if they are missing but finished
    # We prioritize 'normaltime' (90 mins) to ignore overtime/penalties
    is_live = display_status == "LIVE"
    is_finished = display_status == "FT"

    if is_finished:
        if "normaltime" in home_score and home_score.get("normaltime") is not None:
            home_goals = home_score.get("normaltime")
            away_goals = away_score.get("normaltime")
        else:
            home_goals = home_score.get("current")
            away_goals = away_score.get("current")
    elif is_live:
        home_goals = home_score.get("current")
        away_goals = away_score.get("current")
    else:
        home_goals = None
        away_goals = None

    home_name = home.get("name", "Unknown")
    away_name = away.get("name", "Unknown")
    
    LOGO_OVERRIDES = {
        "Sweden U21": "https://flagcdn.com/w160/se.png",
        "Finland U21": "https://flagcdn.com/w160/fi.png",
    }
    
    home_logo = LOGO_OVERRIDES.get(home_name, _resolve_team_logo(str(home.get('id', 0)), home_name, f"/api/image/team/{home.get('id', 0)}"))
    away_logo = LOGO_OVERRIDES.get(away_name, _resolve_team_logo(str(away.get('id', 0)), away_name, f"/api/image/team/{away.get('id', 0)}"))

    return {
        "id":           str(ev.get("id", "")),
        "date":         date_str,
        "time":         time_str,
        "status":       display_status,
        "home_goals":   home_goals,
        "away_goals":   away_goals,
        "fh_home_goals": fh_home,
        "fh_away_goals": fh_away,
        "league": {
            "id":      str(ut_id),
            "name":    ut.get("name", "Unknown"),
            "country": ev.get("tournament", {}).get("category", {}).get("name", ""),
            # Direct SofaScore CDN — full resolution, no proxy needed
            "logo":    f"https://api.sofascore.app/api/v1/unique-tournament/{ut_id}/image",
        },
        "home_team": {
            "id":   str(home.get("id", "")),
            "name": home_name,
            "logo": home_logo,
        },
        "away_team": {
            "id":   str(away.get("id", "")),
            "name": away_name,
            "logo": away_logo,
        },
        "league_id_apifootball": None,
        "season":    None,
        "source":    "sofascore",
    }


def _normalize_team_for_scoreline_match(name: str) -> str:
    """Normalize provider team names for cross-provider scoreline matching."""
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()
    normalized = normalized.replace("&", "and")
    normalized = normalized.replace("turkiye", "turkey")
    normalized = normalized.replace("caboverde", "capeverde")
    normalized = normalized.replace("united", "utd")
    normalized = re.sub(r"\b(fc|cf|afc|sc|club|de|the)\b", "", normalized)
    normalized = re.sub(r"[^a-z0-9]+", "", normalized)
    return normalized


def _fixture_scoreline_key(home_name: str, away_name: str) -> str:
    return (
        f"{_normalize_team_for_scoreline_match(home_name)}|"
        f"{_normalize_team_for_scoreline_match(away_name)}"
    )


def _build_sofascore_scoreline_map(date_str: str) -> dict[str, dict]:
    """
    Fetch the day's SofaScore schedule and return frontend-shaped fixtures by
    normalized home/away names. Used as the scoreline authority for daily rows.
    """
    raw_sofa = _fetch_sofascore_fixtures(date_str)
    scorelines = {}

    for ev in raw_sofa:
        fixture = _sofascore_to_fixture(ev, date_str)
        # SofaScore's daily schedule is already grouped by the selected calendar
        # date. Keep that grouping for the UI instead of dropping late-night
        # matches that convert to the next local timezone date.
        fixture["date"] = date_str

        key = _fixture_scoreline_key(
            fixture.get("home_team", {}).get("name", ""),
            fixture.get("away_team", {}).get("name", ""),
        )
        if key != "|":
            scorelines[key] = fixture

    logger.info(f"SofaScore scoreline map built for {date_str}: {len(scorelines)} matches")
    return scorelines


def _fixture_has_scoreline(fixture: dict) -> bool:
    return (
        fixture.get("home_goals") is not None
        and fixture.get("away_goals") is not None
    )


def _coerce_fixture_list(payload) -> list[dict]:
    """Normalize fixture endpoint responses into a clean list of fixture dicts."""
    if isinstance(payload, dict):
        payload = payload.get("fixtures", [])
    if not isinstance(payload, list):
        return []
    return [fixture for fixture in payload if isinstance(fixture, dict)]


def _apply_sofascore_scoreline(base: dict, sofa: dict) -> dict:
    """
    Merge only score/status fields from SofaScore while preserving the base
    fixture identity, league/team logos, and analysis-facing metadata.
    """
    if not sofa or (sofa.get("status") == "NS" and not _fixture_has_scoreline(sofa)):
        return base

    base["status"] = sofa.get("status", base.get("status"))
    if _fixture_has_scoreline(sofa):
        base["home_goals"] = sofa.get("home_goals")
        base["away_goals"] = sofa.get("away_goals")
    if sofa.get("fh_home_goals") is not None:
        base["fh_home_goals"] = sofa.get("fh_home_goals")
    if sofa.get("fh_away_goals") is not None:
        base["fh_away_goals"] = sofa.get("fh_away_goals")
    base["provider"] = "sofascore"
    base["scoreline_source"] = "sofascore"
    base["last_live_update"] = datetime.utcnow().isoformat() + "Z"
    base["is_stale"] = False
    base["provider_error"] = None
    return base





def _persist_finished_fixture_result(fixture: dict) -> bool:
    """Persist a finished fixture result to matches and match_history."""
    if fixture.get("status") not in ("FT", "AET", "PEN") or not _fixture_has_scoreline(fixture):
        return False

    try:
        from src.db.database import get_db
        from src.engine.live_updater import on_match_finished

        conn = get_db()
        match_id = str(fixture.get("id", ""))
        league = fixture.get("league", {})
        home = fixture.get("home_team", {})
        away = fixture.get("away_team", {})
        home_goals = int(fixture.get("home_goals"))
        away_goals = int(fixture.get("away_goals"))

        conn.execute(
            """INSERT INTO matches (
                   id, date, kickoff, home_team, away_team, league_name, league_id,
                   status, home_goals, away_goals, provider, is_stale,
                   provider_error, last_live_update
               )
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?)
               ON CONFLICT(id) DO UPDATE SET
                   status = excluded.status,
                   home_goals = excluded.home_goals,
                   away_goals = excluded.away_goals,
                   provider = excluded.provider,
                   is_stale = 0,
                   provider_error = NULL,
                   last_live_update = excluded.last_live_update""",
            (
                match_id,
                fixture.get("date"),
                fixture.get("time"),
                home.get("name", "Unknown"),
                away.get("name", "Unknown"),
                league.get("name", "Unknown"),
                league.get("id") or fixture.get("league_id_apifootball"),
                fixture.get("status"),
                home_goals,
                away_goals,
                fixture.get("scoreline_source") or fixture.get("provider") or fixture.get("source") or "live",
                datetime.utcnow().isoformat() + "Z",
            ),
        )
        conn.commit()

        on_match_finished(
            conn,
            match_id=match_id,
            match_date=fixture.get("date"),
            league=league.get("name", "Unknown"),
            home_team=home.get("name", "Unknown"),
            away_team=away.get("name", "Unknown"),
            home_goals=home_goals,
            away_goals=away_goals,
        )
        return True
    except Exception as e:
        logger.warning(
            "Failed to persist finished fixture result for %s: %s",
            fixture.get("id"),
            e,
        )
        return False


def _persist_finished_fixture_results(fixtures: list[dict]) -> int:
    persisted = 0
    for fixture in fixtures:
        if _persist_finished_fixture_result(fixture):
            persisted += 1
    if persisted:
        logger.info(f"Persisted {persisted} finished fixture results")
    return persisted


def _db_match_to_fixture(row) -> dict:
    """Convert a persisted match row into the frontend fixture shape."""
    return {
        "id": str(row["id"]),
        "date": row["date"],
        "time": row["kickoff"] or "TBD",
        "status": row["status"] or "FT",
        "home_goals": row["home_goals"],
        "away_goals": row["away_goals"],
        "fh_home_goals": None,
        "fh_away_goals": None,
        "league": {
            "id": str(row["league_id"] or ""),
            "name": row["league_name"] or "Unknown",
            "country": "",
            "logo": f"/api/image/tournament/{row['league_id']}" if row["league_id"] else "",
        },
        "home_team": {
            "id": "",
            "name": row["home_team"] or "Unknown",
            "logo": "",
        },
        "away_team": {
            "id": "",
            "name": row["away_team"] or "Unknown",
            "logo": "",
        },
        "league_id_apifootball": None,
        "season": None,
        "source": row["provider"] or "db",
        "scoreline_source": "matches_db",
    }


def _build_db_scoreline_maps(date_str: str) -> tuple[dict[str, dict], dict[str, dict]]:
    """Load finished scorelines persisted in the matches table."""
    try:
        from src.db.database import get_db

        conn = get_db()
        rows = conn.execute(
            """SELECT id, date, kickoff, home_team, away_team, league_name, league_id,
                      status, home_goals, away_goals, provider
               FROM matches
               WHERE date = ?
                 AND status IN ('FT', 'AET', 'PEN')
                 AND home_goals IS NOT NULL
                 AND away_goals IS NOT NULL
                 AND COALESCE(provider, '') != 'espn'
               ORDER BY kickoff""",
            (date_str,),
        ).fetchall()
    except Exception as e:
        logger.warning(f"Finished match DB overlay failed for {date_str}: {e}")
        return {}, {}

    by_id = {}
    by_key = {}
    for row in rows:
        fixture = _db_match_to_fixture(row)
        by_id[fixture["id"]] = fixture
        key = _fixture_scoreline_key(
            fixture["home_team"]["name"],
            fixture["away_team"]["name"],
        )
        if key != "|":
            by_key[key] = fixture

    logger.info(f"Finished match DB overlay built for {date_str}: {len(by_id)} matches")
    return by_id, by_key


def _apply_finished_db_scoreline(base: dict, persisted: dict) -> dict:
    """Overlay persisted finished result fields onto a frontend fixture."""
    if not persisted:
        return base

    base["status"] = persisted.get("status", base.get("status"))
    base["home_goals"] = persisted.get("home_goals")
    base["away_goals"] = persisted.get("away_goals")
    base["scoreline_source"] = persisted.get("scoreline_source", "matches_db")
    base["provider"] = persisted.get("source") or base.get("provider") or base.get("source")
    base["is_stale"] = False
    base["provider_error"] = None
    return base





def get_fixtures_by_date_service(date_str: str, background_tasks: BackgroundTasks, force_refresh: bool = False):
    """
    Live Data Provider Split Architecture
    1. Base fixtures loaded from API-Football
    2. SofaScore fills scorelines/live data only when available
    3. Persist finished scores so past dates keep visible results
    """
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {date_str!r}. Use YYYY-MM-DD.")

    logger.info(f"[DATE REQUEST] selected_date={date_str} force_refresh={force_refresh}")

    # ── 1. Fetch Base Fixtures (API-Football) ──
    # QUOTA PROTECTION: force_refresh only refreshes live-score overlays (SofaScore).
    # The API-Football fixture universe is NEVER re-fetched when a valid cache exists,
    # even if force_refresh=True, to preserve the daily 100-request limit.
    cached = _read_fixture_cache(date_str)
    base_fixtures = []

    # If cached data is already from API-Football, always serve it (quota protection)
    _cached_is_api_football = cached is not None and any(
        isinstance(f, dict) and (
            f.get("provider") == "api_football" or
            f.get("source") in ("api_football", "apifootball")
        )
        for f in cached
    )

    if cached is not None and (_cached_is_api_football or not force_refresh):
        base_fixtures = [f for f in cached if f.get("date") == date_str]

    # ── Istanbul overflow: load the PREVIOUS UTC day's cache ──
    # Late-UTC matches (e.g. Brazil 22:00 UTC = 01:00 Istanbul next day) are stored
    # in the previous UTC day's cache file but belong to the Istanbul date the user
    # is viewing. Pull them in here so they are never invisible.
    try:
        from datetime import date as _date, timedelta as _td2
        _prev_utc = (_date.fromisoformat(date_str) - _td2(days=1)).isoformat()
        _prev_path = _get_cache_path(_prev_utc)
        if _prev_path.exists():
            import json as _json2
            _prev_data = _json2.loads(_prev_path.read_text(encoding="utf-8"))
            _overflow = [
                f for f in _prev_data
                if isinstance(f, dict) and f.get("date") == date_str
            ]
            if _overflow:
                _existing_ids = {f.get("id") for f in base_fixtures}
                for f in _overflow:
                    if f.get("id") not in _existing_ids:
                        base_fixtures.append(f)
                        _existing_ids.add(f.get("id"))
                logger.info(f"[ISTANBUL-OVERFLOW] Added {len(_overflow)} late-UTC fixtures from {_prev_utc} into {date_str}")
    except Exception as _e:
        logger.debug(f"Istanbul overflow check failed: {_e}")
    
    # Check if we already have matches at or after 03:00 IST (meaning the UTC same-day data was already fetched)
    _has_utc_day_fixtures = any(
        (f.get("time") or "00:00") >= "03:00" and f.get("provider") == "api_football"
        for f in base_fixtures
    )

    if not base_fixtures or not _has_utc_day_fixtures:
        if not base_fixtures:
            logger.info(f"[API-FOOTBALL REQUEST] Fetching base fixtures for {date_str}")
        else:
            logger.info(f"[API-FOOTBALL REQUEST] Fetching after-3AM base fixtures for {date_str} (only overflow in cache)")
        try:
            raw_api = _fetch_api_football_fixtures(date_str)
            seen_ids = {f.get("id") for f in base_fixtures}
            new_added = False
            for raw in raw_api:
                f = _api_football_to_fixture(raw, date_str)
                if f["date"] == date_str and f.get("id") not in seen_ids:
                    seen_ids.add(f.get("id"))
                    base_fixtures.append(f)
                    new_added = True

            if new_added:
                base_fixtures.sort(key=lambda x: x.get("time") or "TBD")
                # Merge with the FULL existing cache (which may contain overnight overflow
                # fixtures from the previous UTC day stored under this IST date).
                _existing_full = cached or []
                _existing_ids = {f.get("id") for f in base_fixtures}
                _merged = base_fixtures + [
                    f for f in _existing_full if f.get("id") not in _existing_ids
                ]
                _merged.sort(key=lambda x: x.get("time") or "TBD")
                _write_fixture_cache(date_str, _merged)
                _track_competitions_from_fixtures(base_fixtures)
                # Update base_fixtures to include the overflow so the response is complete
                base_fixtures = _merged
        except Exception as e:
            logger.error(f"API-Football base fixture fetch failed: {e}")

    if not base_fixtures:
        logger.info(f"[SOFASCORE FALLBACK] Fetching daily fixtures for {date_str}")
        try:
            raw_sofa = _fetch_sofascore_fixtures(date_str)
            seen_ids = set()
            for raw in raw_sofa:
                f = _sofascore_to_fixture(raw, date_str)
                if f["date"] == date_str and f["id"] not in seen_ids:
                    seen_ids.add(f["id"])
                    base_fixtures.append(f)

            if base_fixtures:
                base_fixtures.sort(key=lambda x: x["time"])
                _write_fixture_cache(date_str, base_fixtures)
                _track_competitions_from_fixtures(base_fixtures)
        except Exception as e:
            logger.error(f"SofaScore daily fixture fetch failed: {e}")

    if not base_fixtures:
        # Extreme fallback to stale cache if API-Football is down and no cache exists
        stale_cache_path = _get_cache_path(date_str)
        if stale_cache_path.exists():
            try:
                stale_data = json.loads(stale_cache_path.read_text(encoding="utf-8"))
                base_fixtures = [f for f in stale_data if f.get("date") == date_str]
            except Exception:
                pass
                
    db_scorelines_by_id, db_scorelines_by_key = _build_db_scoreline_maps(date_str)

    if not base_fixtures and db_scorelines_by_id:
        base_fixtures = sorted(db_scorelines_by_id.values(), key=lambda x: x.get("time") or "TBD")



    if not base_fixtures:
        message = "No matches found for this date."
        if _LAST_API_FOOTBALL_ERROR:
            message = f"Football API unavailable: {_LAST_API_FOOTBALL_ERROR}"
        return {
            "fixtures": [],
            "message": message,
            "provider_status": {
                "api_football": "unavailable" if _LAST_API_FOOTBALL_ERROR else "empty",
                "api_football_error": _LAST_API_FOOTBALL_ERROR,
            },
        }

    # ── 2. Fetch Daily + Live Updates (SofaScore when available) ──
    logger.info(f"[SOFASCORE SCORELINES] Fetching daily scorelines for {date_str}")
    sofa_scorelines = _build_sofascore_scoreline_map(date_str)

    from src.data.live_score_provider import fetch_live_scores
    logger.info(f"[LIVE SCORES] Fetching live updates from RapidAPI SofaScore for {date_str}")
    live_updates = fetch_live_scores(date_str)
    
    # ── 3. Merge Strategy ──
    # Strict matching by home_team and away_team (normalized)
    live_map = {}
    for lu in live_updates:
        key = _fixture_scoreline_key(lu["home_team"], lu["away_team"])
        live_map[key] = lu

    merged_fixtures = []
    now_iso = datetime.utcnow().isoformat() + "Z"
    cache_path = _get_cache_path(date_str)
    base_age = time.time() - cache_path.stat().st_mtime if cache_path.exists() else 0

    for f in base_fixtures:
        f_key = _fixture_scoreline_key(f["home_team"]["name"], f["away_team"]["name"])
        db_data = db_scorelines_by_id.get(str(f.get("id"))) or db_scorelines_by_key.get(f_key)
        if db_data:
            f = _apply_finished_db_scoreline(f, db_data)

        sofa_data = sofa_scorelines.get(f_key)
        if sofa_data:
            f = _apply_sofascore_scoreline(f, sofa_data)

        live_data = live_map.get(f_key)
        
        # Determine if match should be live
        is_match_today = (date_str == _get_istanbul_today())
        needs_live = is_match_today and f["status"] not in ["FT", "AET", "PEN", "CANC", "PST", "NS"]
        
        if live_data and live_data.get("status") != "NS":
            # Apply STRICT merge rules
            f["status"] = live_data["status"]
            if live_data.get("elapsed", 0) > 0:
                f["elapsed"] = live_data["elapsed"]
            f["home_goals"] = live_data["home_score"]
            f["away_goals"] = live_data["away_score"]
            f["home_team"]["score"] = live_data["home_score"]
            f["away_team"]["score"] = live_data["away_score"]
            f["provider"] = live_data["provider"]
            f["scoreline_source"] = live_data["provider"]
            f["last_live_update"] = live_data["last_live_update"]
            f["is_stale"] = False
            f["provider_error"] = None
        else:
            # Stale Protection
            f["provider"] = f.get("source", "api_football")
            f["last_live_update"] = None
            has_external_scoreline = f.get("scoreline_source") in ("matches_db", "sofascore")
            if (not has_external_scoreline) and ("LIVE" in f.get("status", "") or "HT" in f.get("status", "") or needs_live):
                f["is_stale"] = True
                f["status"] = "STALE"
                f["provider_error"] = "RapidAPI SofaScore timeout or unmapped"
                f["data_age_seconds"] = round(base_age)
            else:
                f["is_stale"] = False
                f["provider_error"] = None
                
        merged_fixtures.append(f)

    _persist_finished_fixture_results(merged_fixtures)
    _write_fixture_cache(date_str, merged_fixtures)
    return merged_fixtures


# ─────────────────────────────────────────────────────────
# GLOBAL COVERAGE HELPERS
# ─────────────────────────────────────────────────────────

def _categorize_competition(name: str, country: str) -> str:
    """Classify a competition by name/country."""
    n = name.lower()
    if any(w in n for w in ['women', 'female', 'ladies', 'wsl']):
        return 'women'
    if any(w in n for w in ['u17', 'u18', 'u19', 'u20', 'u21', 'u23', 'youth', 'under-']):
        return 'youth'
    if any(w in n for w in ['friendly', 'friendlies']):
        return 'friendly'
    if country in ('World', 'Europe', 'South America', 'Asia', 'Africa',
                   'North America', 'Oceania', 'CONMEBOL', 'UEFA', 'AFC', 'CAF', 'CONCACAF'):
        return 'international'
    return 'men'


def _track_competitions_from_fixtures(fixtures: list) -> None:
    """Persist all competitions from a fixture list into the DB."""
    try:
        from src.db.database import get_db
        conn = get_db()
        seen: set = set()
        for f in fixtures:
            lg = f.get("league", {})
            lid = lg.get("id", "")
            if lid and lid not in seen:
                seen.add(lid)
                upsert_competition(
                    conn,
                    league_id=str(lid),
                    name=lg.get("name", ""),
                    country=lg.get("country", ""),
                    logo_url=lg.get("logo", ""),
                )
    except Exception as e:
        logger.warning(f"Competition tracking failed: {e}")


def _precompute_predictions_for_date(date_str: str, fixtures: list) -> None:
    """Background task: pre-warm analysis cache for all fixtures on a date."""
    global _PREDICTION_STATUS, _ANALYSIS_CACHE
    logger.info(f"[PRECOMPUTE] Starting prediction pre-warm for {len(fixtures)} fixtures on {date_str}")
    for f in fixtures:
        fid = f["id"]
        if _PREDICTION_STATUS.get(fid, {}).get("status") == "ready":
            continue  # already computed
        _PREDICTION_STATUS[fid] = {"status": "pending", "computed_at": None}
        try:
            home = f["home_team"]["name"]
            away = f["away_team"]["name"]
            league = f["league"]["name"]
            analysis = _compute_match_analysis(home, away, league, shuffle_tiers=False)
            analysis["match"] = {
                "home_team": home,
                "away_team": away,
                "league_name": league,
                "season": "2024/25",
                "date": _get_istanbul_today(),
            }
            _ANALYSIS_CACHE[fid] = analysis
            from datetime import datetime
            _PREDICTION_STATUS[fid] = {
                "status": "ready",
                "computed_at": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            _PREDICTION_STATUS[fid] = {"status": "error", "computed_at": None, "error": str(e)}
    ready = sum(1 for v in _PREDICTION_STATUS.values() if v["status"] == "ready")
    logger.info(f"[PRECOMPUTE] Done for {date_str}: {ready}/{len(fixtures)} ready")


def precompute_predictions_service(date_str: str, background_tasks: BackgroundTasks):
    """
    Trigger background prediction pre-computation for all fixtures on a date.
    GET /api/fixtures/precompute?date_str=YYYY-MM-DD
    Returns immediately; predictions are computed in the background.
    """
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date_str!r}")

    fixtures = _read_fixture_cache(date_str)
    if not fixtures:
        return {"status": "no_fixtures", "date": date_str, "count": 0}

    # Mark all as pending immediately
    global _PREDICTION_STATUS
    for f in fixtures:
        if f["id"] not in _PREDICTION_STATUS:
            _PREDICTION_STATUS[f["id"]] = {"status": "pending", "computed_at": None}

    background_tasks.add_task(_precompute_predictions_for_date, date_str, fixtures)
    return {
        "status": "queued",
        "date": date_str,
        "fixture_count": len(fixtures),
        "message": f"Pre-computing {len(fixtures)} predictions in background",
    }


def get_prediction_status_service(fixture_id: str):
    """Get pre-computation status for a single fixture."""
    return _PREDICTION_STATUS.get(fixture_id, {"status": "unknown"})

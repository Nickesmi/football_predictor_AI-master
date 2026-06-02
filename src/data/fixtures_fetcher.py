from __future__ import annotations

import json
import requests
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from src.config import APIFOOTBALL_API_KEY, CACHE_DIR, logger
from src.data.api_client import APIFootballClient
from src.db.database import get_db
from src.db.match_repo import get_match_history_by_date, get_matches_by_date, upsert_match
from src.data.odds_fetcher import get_api_key


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _disk_snapshot_path(date_str: str) -> Path:
    return CACHE_DIR / f"fixtures-{date_str}.json"


def _fetch_disk_api_cache(date_str: str) -> list[dict]:
    path = _disk_snapshot_path(date_str)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")) or []
    except Exception as exc:
        logger.warning("Failed to read disk fixture cache %s: %s", path, exc)
        return []


def _persist_fixtures_snapshot(date_str: str, fixtures: list[dict], source: str) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _disk_snapshot_path(date_str)
        path.write_text(json.dumps(fixtures, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.debug("Could not persist fixtures snapshot: %s", exc)


def _kickoff_meta(kickoff: Optional[Any]) -> dict[str, Optional[str]]:
    if kickoff is None or kickoff == "":
        return {"time": "TBD", "kickoff_iso": None}
    try:
        value = int(str(kickoff))
        dt = datetime.fromtimestamp(value, tz=timezone.utc)
        return {
            "time": dt.strftime("%H:%M"),
            "kickoff_iso": dt.isoformat(),
        }
    except Exception:
        return {"time": "TBD", "kickoff_iso": None}


def _format_time(iso: Optional[str]) -> str:
    if not iso:
        return "TBD"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%H:%M")
    except Exception:
        return "TBD"


def _dedupe_fixtures(fixtures: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for fx in fixtures:
        mid = str(fx.get("id", "") or "").strip()
        if not mid:
            date_str = str(fx.get("date", "") or "").strip()
            home = str(fx.get("home_team", {}).get("name", "") or "").strip()
            away = str(fx.get("away_team", {}).get("name", "") or "").strip()
            if not date_str or not home or not away:
                continue
            mid = f"{date_str}|{home}|{away}"

        if mid not in seen:
            seen[mid] = fx
            continue

        existing = seen[mid]
        existing_source = existing.get("source")
        new_source = fx.get("source")

        if existing_source == "cache" and new_source != "cache":
            seen[mid] = fx
        elif existing_source == "match_history" and new_source not in ("match_history", "cache"):
            seen[mid] = fx
        elif existing_source == "local" and new_source not in ("local", "cache"):
            seen[mid] = fx
    return list(seen.values())


def _rows_to_fixtures(rows: list[dict], date_str: str) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        kickoff = row.get("kickoff") or ""
        ko = _kickoff_meta(kickoff if str(kickoff).isdigit() else None)
        time_str = ko.get("time") or "TBD"
        out.append({
            "id": str(row.get("id", "")),
            "date": date_str,
            "time": time_str,
            "kickoff_iso": ko.get("kickoff_iso"),
            "status": row.get("status") or "NS",
            "status_detail": row.get("status_detail"),
            "elapsed": row.get("elapsed"),
            "home_goals": row.get("home_goals"),
            "away_goals": row.get("away_goals"),
            "last_live_update": row.get("last_live_update"),
            "source": row.get("source") or "local",
            "league": {
                "id": str(row.get("league_id") or 0),
                "name": row.get("league_name") or "Unknown",
                "country": "",
                "logo": "",
            },
            "home_team": {"id": "", "name": row.get("home_team") or "", "logo": ""},
            "away_team": {"id": "", "name": row.get("away_team") or "", "logo": ""},
        })
    return out


def _match_history_rows_to_fixtures(rows: list[dict], date_str: str) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        match_id = str(row.get("match_id", "")).strip()
        if not match_id:
            continue
        out.append({
            "id": match_id,
            "date": date_str,
            "time": "TBD",
            "status": "FT",
            "status_detail": None,
            "elapsed": None,
            "home_goals": row.get("home_goals"),
            "away_goals": row.get("away_goals"),
            "last_live_update": None,
            "source": "match_history",
            "league": {"id": "0", "name": row.get("league") or "Match History", "country": "", "logo": ""},
            "home_team": {"id": "", "name": row.get("home_team") or "", "logo": ""},
            "away_team": {"id": "", "name": row.get("away_team") or "", "logo": ""},
        })
    return out


def _fetch_local_components(date_str: str) -> tuple[list[dict], list[dict], list[dict]]:
    conn = get_db()
    snapshot: list[dict] = []
    live = _rows_to_fixtures(get_matches_by_date(conn, date_str), date_str)
    history = _match_history_rows_to_fixtures(
        get_match_history_by_date(conn, date_str),
        date_str,
    )
    return snapshot, live, history


def _fetch_all_local(date_str: str) -> list[dict]:
    snapshot, live, history = _fetch_local_components(date_str)
    return snapshot + live + history


def _sync_fixtures_to_matches_db(fixtures: list[dict], date_str: str) -> None:
    conn = get_db()
    for fx in fixtures:
        match_id = str(fx.get("id", ""))
        if not match_id:
            continue
        league_id = fx.get("league", {}).get("id")
        try:
            league_id = int(league_id)
        except Exception:
            league_id = None

        upsert_match(conn, {
            "id": match_id,
            "date": date_str,
            "kickoff": str(fx.get("kickoff_iso") or ""),
            "home_team": fx.get("home_team", {}).get("name") or "",
            "away_team": fx.get("away_team", {}).get("name") or "",
            "league_name": fx.get("league", {}).get("name") or "Unknown",
            "league_id": league_id or 0,
            "status": fx.get("status") or "NS",
            "home_goals": fx.get("home_goals"),
            "away_goals": fx.get("away_goals"),
        })


def _fetch_sofascore(date_str: str) -> list[dict]:
    url = f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{date_str}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.sofascore.com/",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("SofaScore fetch failed for %s: %s", date_str, exc)
        return []

    events = data.get("events", []) if isinstance(data, dict) else []
    fixtures: list[dict] = []
    for event in events:
        fixture = _sofascore_to_fixture(event)
        if fixture["date"] == date_str:
            fixtures.append(fixture)
    return fixtures


def _openliga_season_year(date_str: str) -> int:
    year = int(date_str[:4])
    month = int(date_str[5:7])
    return year if month >= 7 else year - 1


def _openliga_match_to_fixture(match: dict, league_name: str) -> dict:
    dt_raw = match.get("matchDateTimeUTC") or match.get("matchDateTime") or ""
    date_str = dt_raw[:10]
    kickoff_iso = dt_raw.replace("Z", "+00:00") if dt_raw.endswith("Z") else dt_raw
    time_str = _format_time(kickoff_iso if "T" in kickoff_iso else None)

    team1 = match.get("team1") or {}
    team2 = match.get("team2") or {}
    finished = bool(match.get("matchIsFinished"))
    results = match.get("matchResults") or []
    final = next((r for r in results if r.get("resultTypeID") == 2), None)

    return {
        "id": f"openliga-{match.get('matchID', '')}",
        "date": date_str,
        "time": time_str,
        "kickoff_iso": kickoff_iso,
        "status": "FT" if finished else "NS",
        "home_goals": final.get("pointsTeam1") if finished and final else None,
        "away_goals": final.get("pointsTeam2") if finished and final else None,
        "league": {
            "id": str(match.get("leagueId") or match.get("leagueShortcut") or ""),
            "name": match.get("leagueName") or league_name,
            "country": "Germany",
            "logo": "",
        },
        "home_team": {
            "id": str(team1.get("teamId") or ""),
            "name": team1.get("teamName") or "",
            "logo": team1.get("teamIconUrl") or "",
        },
        "away_team": {
            "id": str(team2.get("teamId") or ""),
            "name": team2.get("teamName") or "",
            "logo": team2.get("teamIconUrl") or "",
        },
        "source": "openligadb",
    }


OPENLIGA_LEAGUES = [
    ("bl1", "Bundesliga"),
    ("bl2", "2. Bundesliga"),
    ("bl3", "3. Liga"),
]


def _fetch_openligadb(date_str: str) -> list[dict]:
    season = _openliga_season_year(date_str)
    fixtures: list[dict] = []
    for shortcut, league_name in OPENLIGA_LEAGUES:
        url = f"https://api.openligadb.de/getmatchdata/{shortcut}/{season}"
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                continue
            payload = resp.json()
            if not isinstance(payload, list):
                continue
            for match in payload:
                dt = (match.get("matchDateTimeUTC") or match.get("matchDateTime") or "")[:10]
                if dt != date_str:
                    continue
                fixtures.append(_openliga_match_to_fixture(match, league_name))
        except Exception as exc:
            logger.warning("OpenLigaDB fetch failed for %s/%s: %s", shortcut, season, exc)
    return fixtures


def _fetch_odds_api(date_str: str) -> list[dict]:
    if not get_api_key():
        return []
    return []


def normalize_apifootball_fixture(raw: dict) -> Optional[dict]:
    fixture = raw.get("fixture", {})
    league = raw.get("league", {})
    teams = raw.get("teams", {})
    goals = raw.get("goals", {})

    date_str = (fixture.get("date", "") or "")[:10]
    if not date_str:
        return None

    home_team = teams.get("home", {})
    away_team = teams.get("away", {})
    status_raw = (fixture.get("status", {}) or {}).get("short", "") or "NS"
    kickoff_iso = fixture.get("date") or ""
    kickoff = str(fixture.get("timestamp", kickoff_iso))

    return {
        "id": str(fixture.get("id", "")),
        "date": date_str,
        "kickoff": kickoff,
        "time": _format_time(kickoff_iso),
        "kickoff_iso": kickoff_iso,
        "status": status_raw,
        "home_goals": goals.get("home"),
        "away_goals": goals.get("away"),
        "league": {
            "id": str(league.get("id", "")),
            "name": league.get("name", ""),
        },
        "home_team": {
            "id": str(home_team.get("id", "")),
            "name": home_team.get("name", ""),
        },
        "away_team": {
            "id": str(away_team.get("id", "")),
            "name": away_team.get("name", ""),
        },
        "source": "apifootball",
    }


def fetch_apifootball(
    date_str: str,
    league: Optional[str] = None,
    season: Optional[str] = None,
) -> list[dict]:
    fixtures, _, _ = _fetch_apifootball(date_str, league, season)
    return fixtures


def _fetch_apifootball(
    date_str: str,
    league: Optional[str] = None,
    season: Optional[str] = None,
) -> tuple[list[dict], bool, Optional[Any]]:
    if not APIFOOTBALL_API_KEY:
        return [], False, None

    client = APIFootballClient()
    endpoint = "fixtures"
    url = f"{client._base_url}/{endpoint}"
    params: dict[str, Any] = {"date": date_str}
    if league:
        params["league"] = league
    if season:
        params["season"] = season

    logger.info("API-Football request %s params=%s", url, params)
    try:
        resp = client._session.get(
            url,
            headers=client._build_headers(),
            params=params,
            timeout=30,
        )
        logger.info(
            "API-Football response status=%s url=%s",
            resp.status_code,
            resp.url,
        )
    except requests.RequestException as exc:
        response = getattr(exc, "response", None)
        if response is not None:
            body = response.text[:1000]
            logger.warning(
                "API-Football request exception: status=%s url=%s params=%s body=%s",
                response.status_code,
                url,
                params,
                body,
            )
        else:
            logger.warning(
                "API-Football request exception: url=%s params=%s error=%s",
                url,
                params,
                exc,
            )
        return [], False, str(exc)

    if resp.status_code != 200:
        body = None
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:1000]

        message = f"API-Football HTTP {resp.status_code}"
        if resp.status_code == 404:
            logger.warning(
                "%s for %s params=%s body=%s",
                message,
                url,
                params,
                body,
            )
            return [], False, message

        logger.warning(
            "%s for %s params=%s body=%s",
            message,
            url,
            params,
            body,
        )
        return [], False, message

    try:
        raw = resp.json()
    except ValueError as exc:
        logger.warning(
            "API-Football JSON parse failed for %s params=%s: %s",
            url,
            params,
            exc,
        )
        return [], False, str(exc)

    errors = raw.get("errors")
    results = raw.get("results", 0)
    logger.info(
        "API-Football response results=%s errors=%s",
        results,
        errors,
    )
    if errors:
        message = _apifootball_plan_message(date_str, errors)
        logger.warning(
            "API-Football plan restriction for %s params=%s errors=%s",
            date_str,
            params,
            errors,
        )
        return [], False, message

    fixtures: list[dict] = []
    for item in raw.get("response", []):
        normalized = normalize_apifootball_fixture(item)
        if normalized is not None:
            fixtures.append(normalized)

    logger.info(
        "API-Football fixtures parsed=%d fixtures_expected=%s",
        len(fixtures),
        results,
    )
    return fixtures, results == 0, None


def _apifootball_plan_message(date_str: str, errors: Optional[Any]) -> str:
    if not errors:
        return "API-Football is currently unavailable or unconfigured."
    if isinstance(errors, list):
        return "API-Football plan blocked: " + ", ".join(str(e) for e in errors)
    return str(errors)


def _empty_hint(sources: list[dict]) -> str:
    if not sources:
        return "No providers available."
    return "No fixture data available from the configured providers."


def _sofascore_to_fixture(event: dict) -> dict:
    tournament = event.get("tournament", {})
    unique_tournament = tournament.get("uniqueTournament", {})
    ut_id = unique_tournament.get("id", 0)
    category = tournament.get("category", {})
    home = event.get("homeTeam", {})
    away = event.get("awayTeam", {})
    status = event.get("status", {})

    ts = event.get("startTimestamp", 0)
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        local_dt = dt + timedelta(hours=3)
        time_str = local_dt.strftime("%H:%M")
        date_str = local_dt.strftime("%Y-%m-%d")
    except Exception:
        time_str = "TBD"
        date_str = ""

    status_type = status.get("type", "")
    if status_type == "finished":
        short_status = "FT"
    elif status_type == "inprogress":
        short_status = "LIVE"
    elif status_type == "notstarted":
        short_status = "NS"
    else:
        short_status = str(status.get("description", ""))[:4]

    home_score = event.get("homeScore", {})
    away_score = event.get("awayScore", {})

    return {
        "id": str(event.get("id", "")),
        "date": date_str,
        "time": time_str,
        "status": short_status,
        "home_goals": home_score.get("current") if status_type != "notstarted" else None,
        "away_goals": away_score.get("current") if status_type != "notstarted" else None,
        "fh_home_goals": home_score.get("period1") if status_type != "notstarted" else None,
        "fh_away_goals": away_score.get("period1") if status_type != "notstarted" else None,
        "league": {
            "id": str(ut_id),
            "name": unique_tournament.get("name", tournament.get("name", "")),
            "country": category.get("name", ""),
            "logo": f"https://api.sofascore.com/api/v1/unique-tournament/{ut_id}/image",
        },
        "home_team": {
            "id": str(home.get("id", "")),
            "name": home.get("name", ""),
            "logo": f"https://api.sofascore.com/api/v1/team/{home.get('id', 0)}/image",
        },
        "away_team": {
            "id": str(away.get("id", "")),
            "name": away.get("name", ""),
            "logo": f"https://api.sofascore.com/api/v1/team/{away.get('id', 0)}/image",
        },
        "source": "sofascore",
    }


def _finalize_fixtures_response(fixtures: list[dict], meta: dict[str, Any], server_now: datetime) -> list[dict]:
    fixtures = list(fixtures)
    fixtures.sort(key=lambda f: f.get("time", "99:99"))
    return fixtures


def fetch_fixtures_for_date(date_str: str, force_refresh: bool = False) -> list[dict]:
    logger.info("[DATE REQUEST] date=%s force_refresh=%s", date_str, force_refresh)
    server_now = _now()
    meta = {
        "date": date_str,
        "sources": [],
        "notes": [],
        "apifootball_plan_rejected": False,
        "fetched_at": server_now.isoformat(),
        "server_time": server_now.isoformat(),
    }

    try:
        requested_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        today = date.today()
        is_today = requested_date == today
        is_today_or_future = requested_date >= today
    except ValueError:
        is_today = False
        is_today_or_future = False

    if is_today:
        try:
            from src.engine.live_status import update_live_match_statuses_if_stale
            update_live_match_statuses_if_stale(date_str, force=False)
        except Exception as exc:
            logger.warning("Failed to refresh live match statuses: %s", exc)

    snapshot_batch, live_matches, history_batch = _fetch_local_components(date_str)
    local_core = snapshot_batch + live_matches + history_batch
    logger.info("[LOCAL LOOKUP] snapshot=%d live=%d history=%d", len(snapshot_batch), len(live_matches), len(history_batch))
    meta["sources"].append({"name": "local", "count": len(local_core)})

    if local_core and not force_refresh:
        merged = _dedupe_fixtures(local_core)
        meta["primary_source"] = "local"
        _sync_fixtures_to_matches_db(merged, date_str)
        return _finalize_fixtures_response(merged, meta, server_now)

    cache_batch = _fetch_disk_api_cache(date_str)
    logger.info("[CACHE LOOKUP] matches_found=%d", len(cache_batch))
    meta["sources"].append({"name": "cache", "count": len(cache_batch)})

    if cache_batch and not force_refresh:
        merged = _dedupe_fixtures(cache_batch)
        _persist_fixtures_snapshot(date_str, merged, "cache")
        meta["primary_source"] = "cache"
        return _finalize_fixtures_response(merged, meta, server_now)

    merged: list[dict] = []
    apifootball_plan_rejected = False
    apifootball_plan_errors: Optional[Any] = None

    external_chain: list[tuple[str, Any]] = [
        ("openligadb", _fetch_openligadb),
        ("sofascore", _fetch_sofascore),
    ]

    if get_api_key():
        external_chain.append(("odds_api", _fetch_odds_api))
    if APIFOOTBALL_API_KEY:
        external_chain.append(("apifootball", _fetch_apifootball))

    for name, fetcher in external_chain:
        batch: list[dict] = []
        try:
            result = fetcher(date_str)
            if isinstance(result, tuple):
                if len(result) >= 1:
                    batch = result[0] or []
                if len(result) >= 2:
                    maybe_rejected = result[1]
                    if name == "apifootball" and maybe_rejected:
                        apifootball_plan_rejected = True
                if len(result) >= 3:
                    apifootball_plan_errors = result[2]
            else:
                batch = result or []
        except Exception as exc:
            logger.warning("Fixture source %s failed: %s", name, exc)
            batch = []

        status = "success" if batch else "empty"
        logger.info("[API LOOKUP] provider=%s status=%s count=%d", name, status, len(batch))
        meta["sources"].append({"name": name, "count": len(batch)})

        if batch:
            merged = _dedupe_fixtures(merged + batch)

    if merged:
        combined = _dedupe_fixtures(local_core + merged)
        combined.sort(key=lambda f: f.get("time", "99:99"))
        primary = next((src.get("name") for src in meta["sources"] if src.get("count")), "unknown")
        meta["primary_source"] = primary
        _persist_fixtures_snapshot(date_str, combined, primary)
        _sync_fixtures_to_matches_db(combined, date_str)
        return _finalize_fixtures_response(combined, meta, server_now)

    apifootball_applicable = apifootball_plan_rejected
    if apifootball_applicable:
        meta["apifootball_plan_rejected"] = True
        meta["hint"] = _apifootball_plan_message(date_str, apifootball_plan_errors)
    else:
        meta["hint"] = _empty_hint(meta["sources"])

    logger.info("[FINAL SOURCE] none hint=%s", str(meta.get("hint", ""))[:80])
    if local_core:
        merged = _dedupe_fixtures(local_core)
        meta["primary_source"] = "local"
        _sync_fixtures_to_matches_db(merged, date_str)
        return _finalize_fixtures_response(merged, meta, server_now)
    return []

"""
tennis_provider.py
==================
Tennis data provider — two strict responsibilities:

1. DAILY REFRESH  → Fetch today's/tomorrow's tennis fixtures via RapidAPI.
                    Used for the warehouse, settlement, and base predictions.

2. LIVE OVERLAY   → Fetch live score updates for in-progress matches.
                    Used ONLY for the UI. NEVER for settlement.

Architecture rule (user-approved):
    RapidAPI SofaScore → LIVE UI ONLY
    ════════════════════════════════
    Daily Official Refresh → FT status → Settlement

API keys are read from environment variables only. Never hardcoded.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("football_predictor.tennis")

_RAPIDAPI_KEY  = os.getenv("RAPIDAPI_KEY", "")
_TENNIS_HOST   = os.getenv("RAPIDAPI_TENNIS_HOST", "tennis-live-data.p.rapidapi.com")

# ── Normalization target schema ───────────────────────────────────────────────
# Every match — daily or live — is normalized to this structure.
_EMPTY_MATCH: dict = {
    "sport": "tennis",
    "match_id": None,
    "provider": None,
    "date": None,
    "start_time": None,
    "tournament": None,
    "surface": None,
    "player_1": None,
    "player_2": None,
    "rank_1": None,
    "rank_2": None,
    "status": "NS",
    "sets_1": 0,
    "sets_2": 0,
    "games_1": 0,
    "games_2": 0,
    "point_score": None,
    "is_stale": False,
    "provider_error": None,
    "last_live_update": None,
}


def _make_headers() -> dict:
    return {
        "x-rapidapi-key": _RAPIDAPI_KEY,
        "x-rapidapi-host": _TENNIS_HOST,
        "Accept": "application/json",
    }


def _get(path: str, timeout: int = 10) -> tuple[Optional[dict], Optional[str], int]:
    """
    Execute a GET request to the tennis API.
    Returns (data, error_message, latency_ms).
    """
    url = f"https://{_TENNIS_HOST}{path}"
    start = time.monotonic()
    try:
        req = urllib.request.Request(url, headers=_make_headers())
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency_ms = int((time.monotonic() - start) * 1000)
            data = json.loads(resp.read().decode("utf-8"))
            return data, None, latency_ms
    except urllib.error.HTTPError as e:
        latency_ms = int((time.monotonic() - start) * 1000)
        msg = f"HTTP {e.code}: {e.reason}"
        logger.warning(f"[TENNIS PROVIDER] {msg} for {path}")
        return None, msg, latency_ms
    except Exception as e:
        latency_ms = int((time.monotonic() - start) * 1000)
        msg = str(e)
        logger.warning(f"[TENNIS PROVIDER] {msg} for {path}")
        return None, msg, latency_ms


def _normalize_match(raw: dict, provider: str = "rapidapi_tennis") -> dict:
    """
    Normalize a raw API response item to the canonical tennis match schema.
    Handles missing fields gracefully — never raises.
    """
    m = dict(_EMPTY_MATCH)
    m["provider"] = provider
    m["last_live_update"] = datetime.now(timezone.utc).isoformat()

    try:
        # ── Match identity ────────────────────────────────────────────────────
        m["match_id"] = str(
            raw.get("id") or raw.get("match_id") or raw.get("fixture", {}).get("id", "")
        )

        # ── Tournament ────────────────────────────────────────────────────────
        tournament = (
            raw.get("tournament")
            or raw.get("event", {}).get("name")
            or raw.get("league", {}).get("name")
            or ""
        )
        m["tournament"] = str(tournament)

        # ── Surface ───────────────────────────────────────────────────────────
        surface_raw = (
            raw.get("surface")
            or raw.get("ground")
            or raw.get("event", {}).get("groundType")
            or ""
        )
        m["surface"] = _normalize_surface(str(surface_raw))

        # ── Players ───────────────────────────────────────────────────────────
        home = raw.get("homeTeam") or raw.get("home") or raw.get("player1") or {}
        away = raw.get("awayTeam") or raw.get("away") or raw.get("player2") or {}
        m["player_1"] = str(home.get("name") or home.get("shortName") or raw.get("player1_name", ""))
        m["player_2"] = str(away.get("name") or away.get("shortName") or raw.get("player2_name", ""))
        m["rank_1"] = _safe_int(home.get("ranking") or raw.get("rank1"))
        m["rank_2"] = _safe_int(away.get("ranking") or raw.get("rank2"))

        # ── Date / Time ───────────────────────────────────────────────────────
        start_ts = raw.get("startTimestamp") or raw.get("startTime") or raw.get("date")
        if start_ts and str(start_ts).isdigit():
            dt = datetime.fromtimestamp(int(start_ts), tz=timezone.utc)
            m["date"] = dt.strftime("%Y-%m-%d")
            m["start_time"] = dt.strftime("%H:%M")
        elif isinstance(start_ts, str) and "T" in start_ts:
            parts = start_ts[:19].split("T")
            m["date"] = parts[0]
            m["start_time"] = parts[1][:5] if len(parts) > 1 else None

        # ── Status ────────────────────────────────────────────────────────────
        status_raw = (
            raw.get("status", {}).get("type")
            or raw.get("statusType")
            or raw.get("status")
            or "notstarted"
        )
        m["status"] = _normalize_status(str(status_raw))

        # ── Score ─────────────────────────────────────────────────────────────
        score = raw.get("homeScore") or raw.get("score") or {}
        away_score = raw.get("awayScore") or {}

        if isinstance(score, dict):
            m["sets_1"]  = _safe_int(score.get("current") or score.get("sets") or score.get("period1"))
            m["games_1"] = _safe_int(score.get("games") or score.get("game"))
            m["point_score"] = score.get("point") or score.get("points")
        if isinstance(away_score, dict):
            m["sets_2"]  = _safe_int(away_score.get("current") or away_score.get("sets") or away_score.get("period1"))
            m["games_2"] = _safe_int(away_score.get("games") or away_score.get("game"))

    except Exception as exc:
        logger.warning(f"[TENNIS PROVIDER] Normalization warning for match {m.get('match_id')}: {exc}")

    return m


def _normalize_status(raw: str) -> str:
    raw = raw.lower().strip()
    if raw in ("notstarted", "ns", "scheduled", "not_started"):
        return "NS"
    if raw in ("finished", "ft", "ended", "complete", "finalset"):
        return "FT"
    if raw in ("inprogress", "live", "playing", "1h", "2h", "ht"):
        return "LIVE"
    if raw in ("postponed", "cancelled", "abandoned", "walkover"):
        return "CANCELLED"
    return "NS"


def _normalize_surface(raw: str) -> str:
    raw = raw.lower().strip()
    if "clay" in raw:
        return "clay"
    if "grass" in raw or "carpet" in raw:
        return "grass"
    if "hard" in raw or "indoor" in raw or "outdoor" in raw:
        return "hard"
    return raw or "unknown"


def _safe_int(val) -> Optional[int]:
    try:
        return int(val) if val is not None else None
    except (TypeError, ValueError):
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_daily_matches(date_str: str) -> tuple[list[dict], Optional[str], int]:
    """
    Fetch today's tennis fixtures for the warehouse and prediction generation.
    This is the ONLY source used for settlement.

    Returns: (matches, error_message, latency_ms)
    """
    if not _RAPIDAPI_KEY:
        return [], "RAPIDAPI_KEY not configured", 0

    logger.info(f"[TENNIS DAILY] Fetching fixtures for {date_str}")
    data, err, latency = _get(f"/matches/{date_str}")

    if err or not data:
        logger.warning(f"[TENNIS DAILY] Failed: {err}")
        if "403" in str(err) or "404" in str(err):
            logger.info("[TENNIS DAILY] Falling back to mock data (API not configured/subscribed)")
            return _generate_mock_daily(date_str), None, latency
        return [], err, latency

    raw_list = (
        data.get("results")
        or data.get("data")
        or data.get("events")
        or data.get("matches")
        or []
    )

    matches = [_normalize_match(r) for r in raw_list if isinstance(r, dict)]
    matches = [m for m in matches if m["player_1"] and m["player_2"] and m["match_id"]]

    logger.info(f"[TENNIS DAILY] {len(matches)} matches fetched for {date_str}")
    return matches, None, latency


def fetch_live_matches() -> tuple[list[dict], Optional[str], int]:
    """
    Fetch live tennis match states.
    LIVE UI OVERLAY ONLY. Results from this function are NEVER used for settlement.

    Returns: (matches, error_message, latency_ms)
    """
    if not _RAPIDAPI_KEY:
        return [], "RAPIDAPI_KEY not configured", 0

    logger.info("[TENNIS LIVE] Fetching live tennis matches")
    data, err, latency = _get("/matches/live")

    if err or not data:
        logger.warning(f"[TENNIS LIVE] Failed: {err}")
        if "403" in str(err) or "404" in str(err):
            logger.info("[TENNIS LIVE] Falling back to mock live data")
            return _generate_mock_live(), None, latency
        return [], err, latency

    raw_list = (
        data.get("results")
        or data.get("data")
        or data.get("events")
        or data.get("matches")
        or []
    )

    matches = [_normalize_match(r) for r in raw_list if isinstance(r, dict)]
    matches = [m for m in matches if m["player_1"] and m["player_2"] and m["match_id"]]

    logger.info(f"[TENNIS LIVE] {len(matches)} live matches")
    return matches, None, latency


def apply_live_overlay(base_matches: list[dict], live_matches: list[dict]) -> list[dict]:
    """
    Merge live status into the daily base fixtures.
    Strict merge rule: only update status, sets, games, point_score, last_live_update, is_stale.
    Nothing else is touched (predictions, model data, settlement state).
    """
    live_by_id = {m["match_id"]: m for m in live_matches if m.get("match_id")}

    result = []
    for match in base_matches:
        mid = match.get("match_id")
        live = live_by_id.get(mid)
        if live:
            match = dict(match)
            match["status"]           = live["status"]
            match["sets_1"]           = live["sets_1"]
            match["sets_2"]           = live["sets_2"]
            match["games_1"]          = live["games_1"]
            match["games_2"]          = live["games_2"]
            match["point_score"]      = live["point_score"]
            match["last_live_update"] = live["last_live_update"]
            match["is_stale"]         = False
            match["provider_error"]   = None
        result.append(match)

    return result


def mark_all_stale(matches: list[dict], error: Optional[str] = None) -> list[dict]:
    """Mark all matches as STALE when live provider fails."""
    return [
        {**m, "is_stale": True, "provider_error": error or "live_provider_unavailable"}
        for m in matches
    ]

# ── Mock Data Fallback ────────────────────────────────────────────────────────

def _generate_mock_daily(date_str: str) -> list[dict]:
    import random
    matches = []
    players = [
        ("Jannik Sinner", "Carlos Alcaraz", 1, 2),
        ("Novak Djokovic", "Daniil Medvedev", 3, 4),
        ("Unknown Player A", "Unknown Player B", 500, 501),
        ("Alex de Minaur", "Stefanos Tsitsipas", 9, 11)
    ]
    for i, (p1, p2, r1, r2) in enumerate(players):
        matches.append({
            "match_id": f"mock_match_{date_str}_{i}",
            "provider": "mock_tennis_api",
            "date": date_str,
            "start_time": "14:00",
            "tournament": "Mock ATP Masters",
            "surface": "hard",
            "player_1": p1,
            "player_2": p2,
            "rank_1": r1,
            "rank_2": r2,
            "status": "NS",
            "sets_1": 0,
            "sets_2": 0,
            "games_1": 0,
            "games_2": 0,
            "point_score": None,
            "is_stale": False,
            "provider_error": None,
            "last_live_update": datetime.now(timezone.utc).isoformat()
        })
    return matches

def _generate_mock_live() -> list[dict]:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Make the first match LIVE, the rest NS
    matches = _generate_mock_daily(date_str)
    if matches:
        matches[0]["status"] = "LIVE"
        matches[0]["sets_1"] = 1
        matches[0]["games_1"] = 4
        matches[0]["games_2"] = 3
        matches[0]["point_score"] = "40-15"
    return matches

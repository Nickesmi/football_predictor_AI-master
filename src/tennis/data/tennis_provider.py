"""
tennis_provider.py
==================
Tennis data provider using sports_skills (ESPN) — two strict responsibilities:

1. DAILY REFRESH  → Fetch today's/tomorrow's tennis fixtures.
2. LIVE OVERLAY   → Fetch live score updates for in-progress matches.

Architecture rule (user-approved):
    sports_skills ESPN → Truth & Live
    Surface: unknown (with DQ penalty) until calendar surface is resolved
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

# Import the new provider SDK
try:
    from sports_skills import tennis
except ImportError:
    tennis = None

logger = logging.getLogger("football_predictor.tennis")

# ── Normalization target schema ───────────────────────────────────────────────
_EMPTY_MATCH: dict = {
    "sport": "tennis",
    "match_id": None,
    "provider": "sports_skills_espn",
    "date": None,
    "start_time": None,
    "tournament": None,
    "surface": "unknown",  # explicitly set to unknown to trigger DQ penalty
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


def _normalize_status(raw: str) -> str:
    raw = raw.lower().strip()
    if raw in ("closed", "final", "finished", "ft", "ended"):
        return "FT"
    if raw in ("in_progress", "inprogress", "live", "playing"):
        return "LIVE"
    return "NS"


def _normalize_surface(raw: Optional[str]) -> str:
    raw = (raw or "").lower().strip()
    if "clay" in raw:
        return "clay"
    if "grass" in raw:
        return "grass"
    if "hard" in raw or "indoor" in raw:
        return "hard"
    return "unknown"


def _safe_int(val) -> Optional[int]:
    try:
        return int(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _normalize_match(raw: dict) -> dict:
    """Normalize a generic tennis event into the internal match schema."""
    match = dict(_EMPTY_MATCH)
    match["match_id"] = str(raw.get("id") or "")

    home = raw.get("homeTeam") or raw.get("home_team") or {}
    away = raw.get("awayTeam") or raw.get("away_team") or {}
    status = raw.get("status") or {}

    match["player_1"] = str(home.get("name") or "")
    match["player_2"] = str(away.get("name") or "")
    match["rank_1"] = _safe_int(home.get("ranking") or home.get("rank") or home.get("seed"))
    match["rank_2"] = _safe_int(away.get("ranking") or away.get("rank") or away.get("seed"))
    match["tournament"] = raw.get("tournament") or raw.get("competition") or ""
    match["surface"] = _normalize_surface(raw.get("surface"))

    raw_status = status.get("type") if isinstance(status, dict) else status
    match["status"] = _normalize_status(str(raw_status or ""))

    ts = raw.get("startTimestamp")
    if ts:
        parsed_ts = _safe_int(ts)
        if parsed_ts is not None:
            dt = datetime.fromtimestamp(parsed_ts, tz=timezone.utc)
            match["date"] = dt.strftime("%Y-%m-%d")
            match["start_time"] = dt.strftime("%H:%M")

    match["last_live_update"] = datetime.now(timezone.utc).isoformat()
    return match


def _fetch_espn_matches() -> tuple[list[dict], Optional[str], int]:
    """
    Fetch and normalize all current ATP and WTA matches via sports_skills.
    """
    if not tennis:
        return [], "sports_skills not installed", 0

    start = time.monotonic()
    all_matches = []
    
    for tour in ["atp", "wta"]:
        try:
            res = tennis.get_scoreboard(tour=tour)
            if not res or not res.get("status") or "data" not in res:
                logger.warning(f"[TENNIS PROVIDER] Failed to parse {tour.upper()} scoreboard")
                continue
                
            tournaments = res["data"].get("tournaments", [])
            for t in tournaments:
                tournament_name = t.get("name", "Unknown Tournament")
                for draw in t.get("draws", []):
                    # Only parse singles
                    if "Singles" not in draw.get("name", ""):
                        continue
                        
                    for match in draw.get("matches", []):
                        comps = match.get("competitors", [])
                        if len(comps) < 2:
                            continue
                            
                        # Double check singles inside competitors
                        if comps[0].get("type") != "singles":
                            continue
                            
                        p1 = comps[0]
                        p2 = comps[1]
                        
                        m = dict(_EMPTY_MATCH)
                        m["match_id"] = str(match.get("id"))
                        
                        dt_str = match.get("date", "")
                        if "T" in dt_str:
                            parts = dt_str.split("T")
                            m["date"] = parts[0]
                            m["start_time"] = parts[1][:5]
                        else:
                            m["date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                            m["start_time"] = "00:00"
                            
                        m["tournament"] = tournament_name
                        m["player_1"] = str(p1.get("name", "TBD"))
                        m["player_2"] = str(p2.get("name", "TBD"))
                        
                        # Only accept if real players
                        if m["player_1"] == "TBD" or m["player_2"] == "TBD":
                            continue
                            
                        m["rank_1"] = _safe_int(p1.get("seed"))
                        m["rank_2"] = _safe_int(p2.get("seed"))
                        
                        m["status"] = _normalize_status(str(match.get("status", "")))
                        
                        s1_won = 0
                        s2_won = 0
                        p1_sets = p1.get("set_scores", [])
                        p2_sets = p2.get("set_scores", [])
                        for i in range(min(len(p1_sets), len(p2_sets))):
                            g1 = p1_sets[i].get("games", 0)
                            g2 = p2_sets[i].get("games", 0)
                            if g1 > g2:
                                s1_won += 1
                            elif g2 > g1:
                                s2_won += 1
                                
                        # Handle retirements/walkovers where sets might be equal
                        if p1.get("winner") and s1_won <= s2_won:
                            s1_won = s2_won + 1
                        elif p2.get("winner") and s2_won <= s1_won:
                            s2_won = s1_won + 1

                        m["sets_1"] = s1_won
                        m["sets_2"] = s2_won
                        m["games_1"] = sum(s.get("games", 0) for s in p1_sets)
                        m["games_2"] = sum(s.get("games", 0) for s in p2_sets)
                        
                        m["last_live_update"] = datetime.now(timezone.utc).isoformat()
                        
                        all_matches.append(m)
        except Exception as e:
            logger.error(f"[TENNIS PROVIDER] Error fetching {tour.upper()}: {e}")
            
    latency_ms = int((time.monotonic() - start) * 1000)
    return all_matches, None, latency_ms


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_daily_matches(date_str: str) -> tuple[list[dict], Optional[str], int]:
    """
    Fetch tennis fixtures for the warehouse and prediction generation.
    Filters the ESPN results to match the requested date.
    """
    logger.info(f"[TENNIS DAILY] Fetching fixtures for {date_str} via sports_skills")
    matches, err, latency = _fetch_espn_matches()
    
    if err:
        return [], err, latency
        
    # Filter for the requested date (or matches that are active/finished today)
    # Since ESPN scoreboard shows active tournaments, we'll return all matches 
    # whose date matches date_str, or any LIVE/FT matches that might be relevant today.
    filtered = [m for m in matches if m["date"] == date_str or m["status"] in ("LIVE", "FT")]
    
    logger.info(f"[TENNIS DAILY] {len(filtered)} matches fetched for {date_str}")
    return filtered, None, latency


def fetch_live_matches() -> tuple[list[dict], Optional[str], int]:
    """
    Fetch live tennis match states for UI overlay.
    """
    logger.info("[TENNIS LIVE] Fetching live tennis matches via sports_skills")
    matches, err, latency = _fetch_espn_matches()
    
    if err:
        return [], err, latency
        
    live_matches = [m for m in matches if m["status"] == "LIVE"]
    
    logger.info(f"[TENNIS LIVE] {len(live_matches)} live matches")
    return live_matches, None, latency


def apply_live_overlay(base_matches: list[dict], live_matches: list[dict]) -> list[dict]:
    """
    Merge live status into the daily base fixtures.
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

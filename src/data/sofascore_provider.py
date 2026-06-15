import json
import logging
import urllib.request
import urllib.error
import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from curl_cffi import requests as cffi_requests
from src.config import RAPIDAPI_KEY, RAPIDAPI_HOST
from src.data.provider_health import is_circuit_open, record_provider_result

logger = logging.getLogger("football_predictor")

PROVIDER_NAME = "sofascore"

# ── Main Fixture Rules ────────────────────────────────────────────────────────

TIER_1_LEAGUES = {
    "Premier League", "LaLiga", "Serie A", "Bundesliga", "Ligue 1",
    "UEFA Champions League", "UEFA Europa League", "UEFA Europa Conference League",
    "Champions League", "Europa League", "Europa Conference League"
}

TIER_2_LEAGUES = {
    "Liga Portugal", "Primeira Liga", "Trendyol Süper Lig", "Süper Lig",
    "Eredivisie", "Pro League", "First Division A", "Major League Soccer", "MLS",
    "Brasileirão Série A", "Liga Profesional de Fútbol", "Saudi Pro League"
}

REJECT_KEYWORDS = [
    "U17", "U19", "U20", "U21", "U23", "Youth", "Reserve", "Women", 
    "Amateur", "Friendly", "Club Friendly Games"
]

def is_main_fixture(event: dict) -> bool:
    league_name = event.get("tournament", {}).get("name", "")
    
    # Fast reject
    for kw in REJECT_KEYWORDS:
        if kw.lower() in league_name.lower():
            return False
            
    # Explicit acceptance
    if league_name in TIER_1_LEAGUES or league_name in TIER_2_LEAGUES:
        return True
        
    return False

def compute_quality_score(event: dict, is_stale: bool = False, provider_error: str = None) -> int:
    score = 40
    league_name = event.get("tournament", {}).get("name", "")
    
    if league_name in TIER_1_LEAGUES:
        score = 95
    elif league_name in TIER_2_LEAGUES:
        score = 75
        
    # Example missing field penalties
    if not event.get("homeTeam") or not event.get("awayTeam"):
        score -= 20
        
    if is_stale or provider_error:
        score -= 15
        
    return max(0, min(100, score))


# ── Provider Logic ────────────────────────────────────────────────────────────

def _fetch_rapidapi(endpoint: str) -> dict:
    if not RAPIDAPI_KEY:
        return {"success": False, "error": "RapidAPI key missing", "latency": 0}
        
    url = f"https://{RAPIDAPI_HOST}{endpoint}"
    req = urllib.request.Request(url, headers={
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": RAPIDAPI_HOST,
        "Accept": "application/json"
    })
    
    start_time = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        latency = int((time.time() - start_time) * 1000)
        return {"success": True, "data": data, "latency": latency}
    except Exception as e:
        latency = int((time.time() - start_time) * 1000)
        return {"success": False, "error": f"RapidAPI HTTP {e}", "latency": latency}

def _fetch_direct(endpoint: str) -> dict:
    url = f"https://api.sofascore.com{endpoint}"
    start_time = time.time()
    try:
        res = cffi_requests.get(url, impersonate="chrome110", timeout=5)
        latency = int((time.time() - start_time) * 1000)
        if res.status_code == 200:
            return {"success": True, "data": res.json(), "latency": latency}
        else:
            return {"success": False, "error": f"Direct HTTP {res.status_code}", "latency": latency}
    except Exception as e:
        latency = int((time.time() - start_time) * 1000)
        return {"success": False, "error": f"Direct Exception: {e}", "latency": latency}

def _make_request(endpoint: str) -> dict:
    if is_circuit_open(PROVIDER_NAME):
        logger.warning(f"SofaScore circuit open, skipping {endpoint}")
        return {"success": False, "error": "Circuit Open", "latency": 0}
        
    # Try RapidAPI first
    res = _fetch_rapidapi(endpoint)
    if res.get("success"):
        record_provider_result(PROVIDER_NAME, "request", True, res["latency"])
        return res
        
    logger.warning(f"SofaScore RapidAPI failed: {res.get('error')}. Falling back to Direct curl_cffi...")
    
    # Fallback to Direct
    res_direct = _fetch_direct(endpoint)
    if res_direct.get("success"):
        record_provider_result(PROVIDER_NAME, "request", True, res_direct["latency"])
        return res_direct
        
    # Both failed
    logger.error(f"SofaScore completely failed. Direct Error: {res_direct.get('error')}")
    record_provider_result(PROVIDER_NAME, "request", False, res_direct["latency"], error_message=res_direct.get("error"))
    return res_direct

# ── Normalization ─────────────────────────────────────────────────────────────

def _normalize_status(code: int, type_str: str) -> str:
    # SofaScore status logic
    # type: notstarted, inprogress, finished, canceled
    type_str = type_str.lower()
    if type_str == "finished":
        return "FT"
    if type_str == "inprogress":
        if code == 31:
            return "HT"
        return "LIVE"
    if type_str == "canceled" or type_str == "postponed":
        return "CANCELLED"
    return "NS"

def normalize_event(event: dict, is_stale: bool = False, provider_error: str = None) -> dict:
    event_id = str(event.get("id", ""))
    tournament = event.get("tournament", {})
    home = event.get("homeTeam", {})
    away = event.get("awayTeam", {})
    status = event.get("status", {})
    score = event.get("homeScore", {}).get("current", 0)
    away_score = event.get("awayScore", {}).get("current", 0)
    
    status_str = _normalize_status(status.get("code", 0), status.get("type", "notstarted"))
    
    # Date handling
    ts = event.get("startTimestamp")
    if ts:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d")
        kickoff_str = dt.strftime("%H:%M")
    else:
        date_str = None
        kickoff_str = None

    elapsed = None
    if status_str in ("LIVE", "HT"):
        # Very rough elapsed logic if needed, but SofaScore rarely gives exact minutes nicely in scheduled-events
        elapsed = 45 if status_str == "HT" else 41  # mock elapsed for now
        
    is_main = is_main_fixture(event)
    quality = compute_quality_score(event, is_stale, provider_error)

    return {
        "provider": "sofascore",
        "event_id": event_id,
        "league": tournament.get("name", "Unknown"),
        "country": tournament.get("category", {}).get("name", "Unknown"),
        "date": date_str,
        "kickoff": kickoff_str,
        "home_team": home.get("name", "Unknown"),
        "away_team": away.get("name", "Unknown"),
        "status": status_str if not is_stale else "STALE",
        "elapsed": elapsed,
        "home_score": score,
        "away_score": away_score,
        "is_main_fixture": is_main,
        "data_quality_score": quality,
        "is_stale": is_stale,
        "provider_error": provider_error,
        "last_live_update": datetime.now(timezone.utc).isoformat()
    }

# ── Public API ────────────────────────────────────────────────────────────────

def fetch_daily_matches(date_str: str) -> tuple[List[dict], Optional[str]]:
    endpoint = f"/api/v1/sport/football/scheduled-events/{date_str}"
    res = _make_request(endpoint)
    
    if not res.get("success"):
        return [], res.get("error")
        
    events = res.get("data", {}).get("events", [])
    normalized = []
    for e in events:
        if is_main_fixture(e):
            normalized.append(normalize_event(e))
            
    return normalized, None

def fetch_live_matches() -> tuple[List[dict], Optional[str]]:
    endpoint = f"/api/v1/sport/football/events/live"
    res = _make_request(endpoint)
    
    if not res.get("success"):
        # We couldn't get live data, so return empty list and error to trigger STALE
        return [], res.get("error")
        
    events = res.get("data", {}).get("events", [])
    normalized = []
    for e in events:
        if is_main_fixture(e):
            normalized.append(normalize_event(e))
            
    return normalized, None

def fetch_match(event_id: str) -> tuple[Optional[dict], Optional[str]]:
    endpoint = f"/api/v1/event/{event_id}"
    res = _make_request(endpoint)
    if not res.get("success"):
        return None, res.get("error")
    return res.get("data", {}).get("event"), None

def fetch_standings(league_id: str) -> dict:
    return {} # Placeholder

def fetch_lineups(event_id: str) -> dict:
    return {} # Placeholder

import os
import json
import time
import logging
from datetime import datetime, timezone
import requests
from typing import Dict, List, Optional
from pathlib import Path
import sqlite3

logger = logging.getLogger("football_predictor")

# The unified endpoint config
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")
RAPIDAPI_HOST = os.getenv("RAPIDAPI_HOST", "sofascore.p.rapidapi.com")

def _log_provider_health(provider: str, endpoint: str, success: bool, latency_ms: int,
                         fixture_count: int, error_message: str = None):
    try:
        from src.db.database import get_db
        conn = get_db()
        conn.execute(
            """INSERT INTO provider_health_log 
               (provider, endpoint, success, latency_ms, fixture_count, error_message, live_updates)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (provider, endpoint, int(success), latency_ms, fixture_count, error_message)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"[Health Log] Failed to log provider health: {e}")

def normalize_live_payload(ev: dict) -> dict:
    """Normalize Sofascore event to strictly allowed merge fields."""
    # Match minute
    status_info = ev.get("status", {})
    code = status_info.get("code", 0)
    status_type = status_info.get("type", "notstarted")
    description = status_info.get("description", "")
    
    status_mapped = "NS"
    if status_type == "inprogress":
        if description == "Halftime":
            status_mapped = "HT"
        else:
            status_mapped = "LIVE"
    elif status_type == "finished":
        status_mapped = "FT"
    elif status_type in ["canceled", "postponed"]:
        status_mapped = "CANC"

    time_info = ev.get("time", {})
    elapsed = time_info.get("currentPeriodStartTimestamp")
    # For simplicity, if we have a current period, calculate roughly. But Sofascore gives 'played' or we just say LIVE.
    minute = 0
    if status_mapped == "LIVE" and elapsed:
        minute = (int(time.time()) - elapsed) // 60
        if description == "2nd half":
            minute += 45
    
    # In Sofascore, score is often in homeScore / awayScore
    home_score = ev.get("homeScore", {}).get("current", 0)
    away_score = ev.get("awayScore", {}).get("current", 0)
    
    return {
        "home_team": ev.get("homeTeam", {}).get("name", ""),
        "away_team": ev.get("awayTeam", {}).get("name", ""),
        "status": status_mapped,
        "elapsed": minute,
        "home_score": home_score,
        "away_score": away_score,
        "provider": "rapidapi_sofascore",
        "last_live_update": datetime.now(timezone.utc).isoformat(),
        "is_stale": False,
        "provider_error": None
    }

def fetch_live_scores(date_str: str) -> List[Dict]:
    """
    Fetch live scores using RapidAPI Sofascore.
    Only returns the strict allowed fields.
    """
    if not RAPIDAPI_KEY or RAPIDAPI_KEY == "dummy_key_please_replace":
        _log_provider_health("rapidapi_sofascore", "/scheduled-events", False, 0, 0, "No RapidAPI Key")
        return []

    url = f"https://{RAPIDAPI_HOST}/matches/v1/list-by-date"
    querystring = {"sport": "football", "date": date_str}
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST
    }

    start_time = time.time()
    try:
        # We will try the rapidapi list-by-date endpoint. 
        # If the API endpoint is different, this will fail gracefully into STALE.
        response = requests.get(url, headers=headers, params=querystring, timeout=10)
        latency = int((time.time() - start_time) * 1000)
        
        if response.status_code != 200:
            # Try direct API fallback if RapidAPI is not the right wrapper
            # But the user said: "Use ONLY RapidAPI Sofascore"
            _log_provider_health("rapidapi_sofascore", url, False, latency, 0, f"HTTP {response.status_code}")
            return []
            
        data = response.json()
        events = data.get("events", [])
        
        normalized = []
        for ev in events:
            normalized.append(normalize_live_payload(ev))
            
        _log_provider_health("rapidapi_sofascore", url, True, latency, len(normalized))
        return normalized
    except Exception as e:
        latency = int((time.time() - start_time) * 1000)
        _log_provider_health("rapidapi_sofascore", url, False, latency, 0, str(e))
        return []

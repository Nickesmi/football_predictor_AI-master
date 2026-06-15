"""
SofaScore Fetcher using RapidAPI wrapper to bypass Cloudflare.
"""

import json
import logging
import urllib.request
import urllib.error
import time
from typing import List, Dict, Any

from src.config import RAPIDAPI_KEY, RAPIDAPI_HOST
from src.data.provider_health import is_circuit_open, record_provider_result

logger = logging.getLogger("football_predictor")

PROVIDER_NAME = "sofascore"

def _make_request(endpoint: str) -> dict:
    if is_circuit_open(PROVIDER_NAME):
        logger.warning(f"SofaScore circuit open, skipping request to {endpoint}")
        return {}

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
    except urllib.error.HTTPError as e:
        latency = int((time.time() - start_time) * 1000)
        err_msg = f"HTTP {e.code}: {e.reason}"
        logger.error(f"SofaScore RapidAPI failed: {err_msg}")
        return {"success": False, "error": err_msg, "latency": latency}
    except Exception as e:
        latency = int((time.time() - start_time) * 1000)
        err_msg = str(e)
        logger.error(f"SofaScore RapidAPI failed: {err_msg}")
        return {"success": False, "error": err_msg, "latency": latency}

def fetch_fixtures_by_date(date_str: str) -> List[Dict[Any, Any]]:
    """Fetch scheduled events from SofaScore via RapidAPI. Safe for background execution."""
    endpoint = f"/api/v1/sport/football/scheduled-events/{date_str}"
    try:
        res = _make_request(endpoint)
        
        if not res.get("success"):
            if res: # Only log if not circuit breaker
                record_provider_result(PROVIDER_NAME, "fixtures", False, res.get("latency", 0), error_message=res.get("error"))
            return []
            
        data = res.get("data", {})
        events = data.get("events", [])
        record_provider_result(PROVIDER_NAME, "fixtures", True, res.get("latency", 0), fixture_count=len(events))
        return events
    except Exception as e:
        logger.error(f"Unhandled exception in background SofaScore fetch: {e}")
        return []

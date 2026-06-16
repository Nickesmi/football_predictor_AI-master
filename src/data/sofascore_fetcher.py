"""
SofaScore Fetcher using RapidAPI wrapper to bypass Cloudflare.
"""

import json
import logging
import urllib.request
import urllib.error
import time
from typing import List, Dict, Any

from curl_cffi import requests as cffi_requests

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


def _make_sofasport_request(date_str: str) -> dict:
    """Fetch football schedule through the sofasport RapidAPI wrapper."""
    if not RAPIDAPI_KEY:
        return {"success": False, "error": "RapidAPI key missing", "latency": 0}
    if is_circuit_open(PROVIDER_NAME):
        logger.warning(f"SofaScore circuit open, skipping sofasport request for {date_str}")
        return {}

    host = "sofasport.p.rapidapi.com"
    endpoint = f"/v1/events/schedule/date?date={date_str}&sport=football"
    url = f"https://{host}{endpoint}"
    req = urllib.request.Request(url, headers={
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": host,
        "Accept": "application/json",
    })

    start_time = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        payload = json.loads(resp.read())
        latency = int((time.time() - start_time) * 1000)
        events = payload.get("events") or payload.get("data") or []
        return {"success": True, "data": {"events": events}, "latency": latency}
    except urllib.error.HTTPError as e:
        latency = int((time.time() - start_time) * 1000)
        err_msg = f"HTTP {e.code}: {e.reason}"
        logger.error(f"SofaSport RapidAPI failed: {err_msg}")
        return {"success": False, "error": err_msg, "latency": latency}
    except Exception as e:
        latency = int((time.time() - start_time) * 1000)
        err_msg = str(e)
        logger.error(f"SofaSport RapidAPI failed: {err_msg}")
        return {"success": False, "error": err_msg, "latency": latency}


def _make_direct_request(endpoint: str) -> dict:
    """Fetch the official SofaScore endpoint directly with browser impersonation."""
    if is_circuit_open(PROVIDER_NAME):
        logger.warning(f"SofaScore circuit open, skipping direct request to {endpoint}")
        return {}

    url = f"https://api.sofascore.com{endpoint}"
    start_time = time.time()
    try:
        resp = cffi_requests.get(
            url,
            impersonate="chrome110",
            timeout=10,
            headers={
                "Accept": "application/json",
                "Referer": "https://www.sofascore.com/",
                "Origin": "https://www.sofascore.com",
            },
        )
        latency = int((time.time() - start_time) * 1000)
        if resp.status_code == 200:
            return {"success": True, "data": resp.json(), "latency": latency}
        err_msg = f"Direct HTTP {resp.status_code}"
        logger.error(f"SofaScore direct fetch failed: {err_msg}")
        return {"success": False, "error": err_msg, "latency": latency}
    except Exception as e:
        latency = int((time.time() - start_time) * 1000)
        err_msg = str(e)
        logger.error(f"SofaScore direct fetch failed: {err_msg}")
        return {"success": False, "error": err_msg, "latency": latency}

def fetch_fixtures_by_date(date_str: str) -> List[Dict[Any, Any]]:
    """Fetch scheduled events from SofaScore via RapidAPI. Safe for background execution."""
    endpoint = f"/api/v1/sport/football/scheduled-events/{date_str}"
    try:
        res = _make_request(endpoint)
        
        if not res.get("success"):
            if res:
                logger.warning(
                    "SofaScore RapidAPI fixtures failed: %s. Falling back to SofaSport wrapper.",
                    res.get("error"),
                )
            res = _make_sofasport_request(date_str)

        if not res.get("success"):
            if res:
                logger.warning(
                    "SofaSport RapidAPI fixtures failed: %s. Falling back to direct API.",
                    res.get("error"),
                )
            res = _make_direct_request(endpoint)
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

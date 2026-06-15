"""
Configuration module for Football Predictor AI.

Loads settings from environment variables and .env file.
Provides centralized configuration for the API client,
caching, and logging.
"""

import os
import json
import logging
import sys
from pathlib import Path
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Persistent User Directory (Desktop Mode)
# ---------------------------------------------------------------------------
USER_DATA_DIR = Path.home() / ".football_predictor"
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = USER_DATA_DIR / "settings.json"
LOG_DIR = USER_DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Settings Management
# ---------------------------------------------------------------------------
def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_settings(settings: dict):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4)

_settings = load_settings()

APIFOOTBALL_API_KEY: str = _settings.get("api_key") or os.getenv("APIFOOTBALL_API_KEY") or os.getenv("API_FOOTBALL_KEY", "")
APIFOOTBALL_HOST: str = _settings.get("api_host") or os.getenv("APIFOOTBALL_HOST", "v3.football.api-sports.io")
RAPIDAPI_KEY: str = os.getenv("RAPIDAPI_KEY", "")
RAPIDAPI_HOST: str = os.getenv("RAPIDAPI_HOST", "")

if not APIFOOTBALL_API_KEY:
    print("FATAL ERROR: APIFOOTBALL_API_KEY or API_FOOTBALL_KEY is missing from environment variables.", file=sys.stderr)
    sys.exit(1)

if not RAPIDAPI_KEY or not RAPIDAPI_HOST:
    print("FATAL ERROR: RAPIDAPI_KEY and RAPIDAPI_HOST must be provided in environment variables for SofaScore fallback.", file=sys.stderr)
    sys.exit(1)
API_RATE_LIMIT_PER_MINUTE: int = int(_settings.get("api_rate_limit") or os.getenv("API_RATE_LIMIT_PER_MINUTE", "10"))

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
if getattr(sys, 'frozen', False):
    CACHE_DIR = USER_DATA_DIR / ".cache"
else:
    CACHE_DIR = _PROJECT_ROOT / ".cache"

CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", str(60 * 60 * 6)))  # 6 h

# ---------------------------------------------------------------------------
# Logging (File + Console)
# ---------------------------------------------------------------------------
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

logger = logging.getLogger("football_predictor")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

formatter = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S")

# Console Handler
ch = logging.StreamHandler()
ch.setFormatter(formatter)
logger.addHandler(ch)

# File Handler (Crash Logs / App Logs)
try:
    fh = logging.FileHandler(LOG_DIR / "app.log")
    fh.setFormatter(formatter)
    logger.addHandler(fh)
except Exception:
    pass

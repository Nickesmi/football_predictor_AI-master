"""
Configuration module for Football Predictor AI.

Loads settings from environment variables and .env file.
Provides centralized configuration for the API client,
caching, and logging.
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from project root (two levels up from this file)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# API Configuration  –  api-football.com  (via RapidAPI or direct)
# ---------------------------------------------------------------------------
# We support TWO hosting options:
#   1. RapidAPI  (x-rapidapi-key header)
#   2. Direct    (x-apisports-key header, base URL v3.football.api-sports.io)
#
# Set APIFOOTBALL_HOST to switch between providers.
# ---------------------------------------------------------------------------

# Support both legacy and preferred env var names. Prefer `API_FOOTBALL_KEY`.
APIFOOTBALL_API_KEY: str = os.getenv("API_FOOTBALL_KEY") or os.getenv("APIFOOTBALL_API_KEY", "")
APIFOOTBALL_HOST: str = os.getenv(
    "APIFOOTBALL_HOST", "v3.football.api-sports.io"
)

# Rate-limit guard – free tier allows ~100 requests / day on api-sports.io
# and 100 requests / day on RapidAPI free plan.
API_RATE_LIMIT_PER_MINUTE: int = int(os.getenv("API_RATE_LIMIT_PER_MINUTE", "10"))

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
CACHE_DIR: Path = _PROJECT_ROOT / ".cache"
CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", str(60 * 60 * 6)))  # 6 h

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("football_predictor")

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
from datetime import datetime, timezone
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

# ---------------------------------------------------------------------------
# Security — no credential has a built-in default. Outside development the API
# refuses to start unless these are configured (see validate_security_config).
# ---------------------------------------------------------------------------
APP_ENV: str = os.getenv("APP_ENV", "development").strip().lower()
IS_DEVELOPMENT: bool = APP_ENV in ("development", "dev", "local", "test")
MIN_ADMIN_API_KEY_LENGTH = 32

# Empty means the admin API is disabled (every admin request is refused).
ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "").strip()


def _parse_origins(raw: str) -> list[str]:
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


_WEB_PORT = os.getenv("WEB_PORT", "5173").strip() or "5173"
_DEV_CORS_ORIGINS = sorted({
    f"http://{host}:{port}"
    for host in ("localhost", "127.0.0.1")
    for port in (_WEB_PORT, "5173")
})
CORS_ALLOW_ORIGINS: list[str] = (
    _parse_origins(os.getenv("CORS_ALLOW_ORIGINS", ""))
    or (_DEV_CORS_ORIGINS if IS_DEVELOPMENT else [])
)


# ---------------------------------------------------------------------------
# Frozen football baseline (docs/BASELINE_FREEZE_DESIGN.md). Unset = not frozen,
# behaviour unchanged. Set to a version created by scripts/freeze_baseline.py.
# ---------------------------------------------------------------------------
_BASELINE_VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
BASELINE_FREEZE: str | None = os.getenv("BASELINE_FREEZE", "").strip() or None
if BASELINE_FREEZE is not None:
    import re as _re
    if not _re.match(_BASELINE_VERSION_PATTERN, BASELINE_FREEZE):
        raise RuntimeError(f"Invalid BASELINE_FREEZE value {BASELINE_FREEZE!r}")
BASELINE_MANIFEST_DIR: Path = Path(
    os.getenv("BASELINE_MANIFEST_DIR", "") or (_PROJECT_ROOT / "models" / "baseline")
)


def validate_security_config() -> list[str]:
    """Return a list of security misconfigurations (empty when safe to run)."""
    problems = []
    if not IS_DEVELOPMENT:
        if len(ADMIN_API_KEY) < MIN_ADMIN_API_KEY_LENGTH:
            problems.append(
                f"ADMIN_API_KEY must be set to at least {MIN_ADMIN_API_KEY_LENGTH} characters when APP_ENV={APP_ENV}"
            )
        if not CORS_ALLOW_ORIGINS:
            problems.append(f"CORS_ALLOW_ORIGINS must list the allowed origins when APP_ENV={APP_ENV}")
    if "*" in CORS_ALLOW_ORIGINS:
        problems.append("CORS_ALLOW_ORIGINS must not contain '*' (credentials are allowed)")
    return problems

if not APIFOOTBALL_API_KEY:
    print(
        "WARNING: APIFOOTBALL_API_KEY or API_FOOTBALL_KEY is missing. "
        "Live API-Football calls will be disabled until a key is configured.",
        file=sys.stderr,
    )

if not RAPIDAPI_KEY or not RAPIDAPI_HOST:
    print(
        "WARNING: RAPIDAPI_KEY and RAPIDAPI_HOST are not fully configured. "
        "SofaScore fallback calls will be disabled until both values are configured.",
        file=sys.stderr,
    )
API_RATE_LIMIT_PER_MINUTE: int = int(_settings.get("api_rate_limit") or os.getenv("API_RATE_LIMIT_PER_MINUTE", "10"))

# ---------------------------------------------------------------------------
# Time handling — every kickoff/timestamp comparison in the system MUST use
# UTC-aware datetimes. Mixing naive datetime.now() (server-local time) with
# UTC timestamps from the DB/providers silently shifts match-timing decisions
# (e.g. "is this match already started?", "how stale are these odds?") by the
# server's UTC offset. Use these helpers instead of bare datetime.now()/utcnow().
# ---------------------------------------------------------------------------
def now_utc() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def parse_utc(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, assuming UTC when no offset is present."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

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

# ── Tracked leagues (SofaScore uniqueTournament IDs) ──────
# Includes top European club leagues + active international competitions
# that run during the European off-season (qualifiers, friendlies, etc.)
TOP_LEAGUES = {
    # ── European Club Leagues (Aug–May) ──
    17:  "Premier League",
    8:   "LaLiga",
    23:  "Serie A",
    35:  "Bundesliga",
    34:  "Ligue 1",
    37:  "Eredivisie",
    238: "Primeira Liga",
    244: "Scottish Premiership",
    180: "Turkish Süper Lig",
    155: "Russian Premier League",
    203: "Pro League (Belgium)",
    # ── UEFA Club Competitions ──
    7:   "Champions League",
    679: "Europa League",
    931: "UEFA Conference League",
    # ── National Team / International ──
    1:   "World Cup",
    16:  "Euro Championship",
    28:  "AFC Asian Cup Qual.",
    36:  "Copa America",
    44:  "FIFA World Cup",
    68:  "World Cup Qualification (Europe)",
    69:  "World Cup Qualification (CONMEBOL)",
    70:  "World Cup Qualification (Africa)",
    71:  "World Cup Qualification (Asia)",
    80:  "World Cup Qualification (CONCACAF)",
    851: "International Friendly Games",
    852: "International Friendly Games Women",
    854: "U21 Friendly Games",
    429: "U17 European Championship",
    132: "U21 European Championship",
    480: "UEFA Nations League",
    2084: "U23 Toulon Tournament",
    # ── Active Non-European Leagues ──
    196: "J1 League",
    402: "J2 League",
    325: "Brasileirão",
    390: "Brasileirão Série B",
    162: "MLS",
    18641: "MLS Next Pro",
    777: "K League",
    937: "Botola Pro",
    841: "Algerian Ligue 1",
    1024: "Copa Argentina",
    278: "Liga AUF Uruguaya",
    703: "Primera Nacional (Argentina)",
}

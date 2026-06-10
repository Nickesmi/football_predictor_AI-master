from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

LOGGER_NAME = "football_predictor"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _base_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))  # type: ignore[attr-defined]
    return _project_root()


def get_runtime_mode() -> str:
    value = (os.getenv("FOOTBALL_RUNTIME_MODE") or "").strip().lower()
    if value in {"desktop", "web", "dev"}:
        return value
    argv0 = Path(sys.argv[0]).name.lower() if sys.argv else ""
    argv_joined = " ".join(arg.lower() for arg in sys.argv)
    if "desktop.py" in argv_joined:
        return "desktop"
    if "uvicorn" in argv0 or "uvicorn" in argv_joined:
        return "web"
    if getattr(sys, "frozen", False):
        return "desktop"
    return "dev"


def get_app_data_dir() -> Path:
    app_dir = Path.home() / ".football_predictor"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_database_path() -> Path:
    explicit = os.getenv("FOOTBALL_PREDICTOR_DB_PATH")
    if explicit:
        return Path(explicit).expanduser()
    return get_app_data_dir() / "engine.db"


def load_environment() -> dict[str, str]:
    root = _project_root()
    load_dotenv(root / ".env", override=False)
    db_path = get_database_path()
    os.environ.setdefault("FOOTBALL_PREDICTOR_DB_PATH", str(db_path))
    return {
        "env_path": str(root / ".env"),
        "database_path": str(db_path),
    }


def load_settings() -> dict[str, Any]:
    settings_path = get_app_data_dir() / "settings.json"
    if settings_path.exists():
        try:
            return json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def setup_logging() -> logging.Logger:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        root_logger.setLevel(level)
    return logging.getLogger(LOGGER_NAME)


def get_frontend_dist_path() -> Path:
    return _base_path() / "frontend" / "dist"


def configure_cors(app: Any, allow_origins: list[str] | None = None) -> None:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def initialize_database() -> Any:
    from src.db.database import get_db

    return get_db()


def start_background_tasks() -> None:
    # Background tasks are currently initialized inside api.main startup handlers.
    # This helper is intentionally lightweight so desktop and web can share one call.
    return None

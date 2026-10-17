#!/usr/bin/env python3
"""
Quota-controlled local demo API (owner-authorised 2026-10-17). Never the production database.

    python scripts/demo_server.py --db <isolated copy> --workdir <demo dir> [--port 8001] [--cap 5]

* refuses data/engine.db; the production service hold is untouched;
* runs from --workdir, so the fixture cache (.cache/) and settings are the demo's own
  (cached fixture files from the repo's .cache are copied in once, never written back);
* no background tasks: no hourly fixture refresh, no nightly retrain, no live-score loop;
* API-Football is reached only when the interface asks for a date that is not cached,
  each date at most once, and at most --cap (<= 5) requests in total, retries included,
  counted persistently in <workdir>/demo_api_usage.db (a restart cannot reset it);
* prediction, identity and calibration settings are the repository defaults
  (PROBABILITY_INPUTS unset = legacy, BASELINE_FREEZE unset).
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = (PROJECT_ROOT / "data" / "engine.db").resolve()


def prepare(db: Path, workdir: Path, cap: int) -> Path:
    if db.resolve() == LIVE_DB:
        raise SystemExit("refusing: the demo never uses data/engine.db")
    if not db.exists():
        raise SystemExit(f"refusing: {db} does not exist")
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "home").mkdir(exist_ok=True)
    cache = workdir / ".cache"
    if not cache.exists():
        cache.mkdir()
        for f in (PROJECT_ROOT / ".cache").glob("fixtures-*.json"):
            shutil.copy2(f, cache / f.name)
    os.environ["FOOTBALL_PREDICTOR_DB_PATH"] = str(db.resolve())
    os.environ["HOME"] = str(workdir / "home")
    os.environ.setdefault("APP_ENV", "development")
    os.environ.pop("PROBABILITY_INPUTS", None)
    os.environ.pop("BASELINE_FREEZE", None)
    os.environ["RAPIDAPI_KEY"] = ""
    os.chdir(workdir)
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.ops import demo_quota

    demo_quota.install(workdir / "demo_api_usage.db", cap)

    import api.services.system_service as system_service

    async def no_background_tasks():
        from src.config import logger

        logger.info("[DEMO] background tasks disabled (no auto-refresh, no retrain, no live loop)")

    system_service.start_background_tasks = no_background_tasks

    import api.services.fixtures_service as fixtures_service

    original_fetch = fixtures_service._fetch_api_football_fixtures

    def fetch_once_per_date(date_str):
        if demo_quota.date_already_fetched(date_str):
            return []          # served from cache; never re-requested in the demo
        try:
            return original_fetch(date_str)
        finally:
            demo_quota.mark_date_fetched(date_str)

    fixtures_service._fetch_api_football_fixtures = fetch_once_per_date
    return workdir


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--workdir", required=True, type=Path)
    p.add_argument("--port", type=int, default=8001)
    p.add_argument("--cap", type=int, default=5)
    a = p.parse_args()
    prepare(a.db, a.workdir, a.cap)
    import uvicorn

    from api.main import app

    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())

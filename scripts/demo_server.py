#!/usr/bin/env python3
"""
Quota-controlled local demo API (owner-authorised 2026-10-17; explicit refresh 2026-10-23).
Never the production database.

    python scripts/demo_server.py --db <isolated copy> --workdir <demo dir> [--port 8001]
        [--cap 0..5] [--ledger <shared ledger> --ledger-budget N] [--provider-floor 30] [--shadow-root DIR]

* refuses data/engine.db; the production service hold is untouched;
* runs from --workdir, so the fixture cache (.cache/) and settings are the demo's own
  (cached fixture files from the repo's .cache are copied in once, never written back);
* no background tasks: no hourly fixture refresh, no nightly retrain, no live-score loop;
* page loads, date changes and open tabs only read the cache: API-Football is reached only
  through POST /api/demo/refresh-fixtures/<date> (the interface's 'Refresh fixtures' button),
  one request per refresh, for a date that is not cached yet (api/demo_mode.py);
* every request is counted before it is sent, in <workdir>/demo_api_usage.db (a restart cannot
  reset it): first the legacy lifetime --cap (<= 5, default 0), then owner grants made with
  scripts/demo_allowance.py (none by default, so nothing new can be sent until authorized);
* the provider's own remaining count reported today must stay above --provider-floor;
* if the shadow experiment's key file (<shadow-root>/etc/api_key.env) holds the same key, a grant
  request also needs --ledger (the shadow's shared ledger) and is reserved there as consumer "demo";
* prediction, identity and calibration settings are the repository defaults
  (PROBABILITY_INPUTS unset = legacy, BASELINE_FREEZE unset).
"""

import argparse
import os
import pwd
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = (PROJECT_ROOT / "data" / "engine.db").resolve()
DEFAULT_SHADOW_ROOT = Path(pwd.getpwuid(os.getuid()).pw_dir) / "FootballShadow"


def prepare(db: Path, workdir: Path, cap: int, *, ledger: Path | None = None, ledger_budget: int | None = None,
            provider_floor: int = 30, shadow_root: Path | None = DEFAULT_SHADOW_ROOT):
    if db.resolve() == LIVE_DB:
        raise SystemExit("refusing: the demo never uses data/engine.db")
    if not db.exists():
        raise SystemExit(f"refusing: {db} does not exist")
    if shadow_root is not None and db.resolve().is_relative_to(Path(shadow_root).resolve()):
        raise SystemExit("refusing: the demo never uses the shadow experiment's files")
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
    os.environ.pop("API_FOOTBALL_LEDGER", None)   # the shared ledger is used by the demo guard only, never globally
    os.environ["API_FOOTBALL_REQUIRE_LEDGER"] = "0"   # the demo has its own guard (src/ops/demo_quota)
    os.environ["RAPIDAPI_KEY"] = ""
    os.environ["FOOTBALL_PREDICTOR_CACHE_DIR"] = str(cache)
    os.chdir(workdir)
    sys.path.insert(0, str(PROJECT_ROOT))

    from src import config
    from src.ops import demo_quota

    demo_quota.install(workdir / "demo_api_usage.db", cap, explicit_only=True, provider_floor=provider_floor,
                       ledger=None if ledger is None else ledger.resolve(), ledger_budget=ledger_budget,
                       shadow_key_file=None if shadow_root is None else Path(shadow_root) / "etc" / "api_key.env",
                       api_key=config.APIFOOTBALL_API_KEY)

    import api.services.system_service as system_service

    async def no_background_tasks():
        from src.config import logger

        logger.info("[DEMO] background tasks disabled (no auto-refresh, no retrain, no live loop)")

    system_service.start_background_tasks = no_background_tasks

    from api import demo_mode
    from api.main import app

    demo_mode.install(app)
    return workdir


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--workdir", required=True, type=Path)
    p.add_argument("--port", type=int, default=8001)
    p.add_argument("--cap", type=int, default=0, help="legacy lifetime allowance (0..5); new requests need a grant")
    p.add_argument("--ledger", type=Path, help="shared API-Football ledger; grant requests are reserved there too")
    p.add_argument("--ledger-budget", type=int, help="all-consumer daily ceiling in the shared ledger (1..70)")
    p.add_argument("--provider-floor", type=int, default=30,
                   help="refuse when the provider reports this many or fewer requests left today")
    p.add_argument("--shadow-root", type=Path, default=DEFAULT_SHADOW_ROOT,
                   help="shadow experiment root, to detect a shared API key (digest comparison only)")
    a = p.parse_args()
    if a.ledger and not a.ledger_budget:
        p.error("--ledger needs --ledger-budget")
    prepare(a.db, a.workdir, a.cap, ledger=a.ledger, ledger_budget=a.ledger_budget, provider_floor=a.provider_floor,
            shadow_root=a.shadow_root)
    import uvicorn

    from api.main import app

    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())

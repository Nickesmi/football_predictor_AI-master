"""Service hold: refuse to open the production database while an operator hold is in place.

During M3 the football app was stopped deliberately (data remediation and a
controlled cutover are pending). Anything that re-runs the old launch command
(an IDE agent, a stale terminal, a worker) would otherwise reopen
data/engine.db, migrate its schema and resume the nightly refit.

The hold is a JSON file next to the database (data/SERVICE_HOLD.json):

    {"hold_id": "...", "reason": "...", "created_at": "...", "created_by": "..."}

While it exists, get_db() refuses the production database unless the process
environment sets FOOTBALL_SERVICE_HOLD_RELEASE to the same hold_id, which only
a deliberate, approved start does. Other database paths (tests, isolated
copies via FOOTBALL_PREDICTOR_DB_PATH) are never affected.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HOLD_FILENAME = "SERVICE_HOLD.json"
RELEASE_ENV = "FOOTBALL_SERVICE_HOLD_RELEASE"
PRODUCTION_DB = (Path(__file__).resolve().parents[2] / "data" / "engine.db").resolve()


class ServiceOnHold(RuntimeError):
    """The production database is under an operator hold."""


def hold_path(db_path: Path) -> Path:
    return Path(db_path).resolve().parent / HOLD_FILENAME


def read_hold(db_path: Path) -> dict | None:
    path = hold_path(db_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        # An unreadable hold is still a hold.
        return {"hold_id": None, "reason": f"unreadable hold file {path}"}
    return data if isinstance(data, dict) else {"hold_id": None, "reason": "malformed hold file"}


def check(db_path: Path) -> None:
    """Raise ServiceOnHold if db_path is the production DB, a hold exists and it is not released."""
    if Path(db_path).resolve() != PRODUCTION_DB:
        return
    hold = read_hold(db_path)
    if hold is None:
        return
    release = os.getenv(RELEASE_ENV, "")
    if hold.get("hold_id") and release == hold["hold_id"]:
        return
    raise ServiceOnHold(
        f"production database is on hold ({hold.get('reason', 'no reason given')}); "
        f"see docs/M3_CUTOVER_RUNBOOK.md. Set {RELEASE_ENV} to the hold_id only for an approved start."
    )

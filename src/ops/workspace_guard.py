"""Keep development workflows out of the production repository's shared .cache (owner request 2026-10-17).

The production app (data/engine.db) legitimately uses <repo>/.cache. Anything else (demo,
smoke test, rehearsal, shadow experiment) must run from its own working directory and point
FOOTBALL_PREDICTOR_CACHE_DIR at its own cache: several writers use the relative path ".cache"
(fixture caches, the nightly retrain lock) and src/config.CACHE_DIR is absolute.

In M3 an isolated demo and a smoke test were started from the repository directory and wrote
fixture caches and a retrain lock into the shared .cache.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SHARED_CACHE = (PROJECT_ROOT / ".cache").resolve()
PRODUCTION_DB = (PROJECT_ROOT / "data" / "engine.db").resolve()


class WorkspaceNotIsolated(RuntimeError):
    """A non-production workflow would write into the shared repository cache."""


def check(db_path, cache_dir, cwd=None, app_env=None) -> None:
    """Raise unless the production DB is used, or both the cwd and the cache are outside the shared cache.

    APP_ENV=test is exempt here: the test suite isolates CACHE_DIR in conftest and asserts at the end
    of the session that the shared cache did not change.
    """
    if Path(db_path).resolve() == PRODUCTION_DB:
        return
    if (app_env or os.getenv("APP_ENV", "")).strip().lower() == "test":
        return
    cwd = Path(cwd or os.getcwd()).resolve()
    problems = []
    if cwd == PROJECT_ROOT:
        problems.append(f"working directory is the repository ({cwd}); relative '.cache' paths would be shared")
    if Path(cache_dir).resolve() == SHARED_CACHE:
        problems.append("FOOTBALL_PREDICTOR_CACHE_DIR is not set; config.CACHE_DIR is the shared repository cache")
    if problems:
        raise WorkspaceNotIsolated("non-production database with a shared cache: " + "; ".join(problems)
                                   + ". Run from an isolated directory with FOOTBALL_PREDICTOR_CACHE_DIR set "
                                   "(see scripts/demo_server.py or src/ops/workspace_guard.isolate).")


def isolate(workdir: Path) -> Path:
    """Make the current process isolated: own working directory and own cache. Call before importing src.config."""
    workdir = Path(workdir).resolve()
    if workdir == PROJECT_ROOT or SHARED_CACHE in (workdir / ".cache").resolve().parents or \
            (workdir / ".cache").resolve() == SHARED_CACHE:
        raise WorkspaceNotIsolated(f"{workdir} is not outside the repository cache")
    (workdir / ".cache").mkdir(parents=True, exist_ok=True)
    os.environ["FOOTBALL_PREDICTOR_CACHE_DIR"] = str(workdir / ".cache")
    os.chdir(workdir)
    return workdir

"""Development workflows must not write into the production repository's shared .cache (2026-10-17)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.ops import workspace_guard as wg

ROOT = Path(__file__).resolve().parent.parent


def test_production_database_may_use_the_shared_cache():
    wg.check(wg.PRODUCTION_DB, wg.SHARED_CACHE, cwd=ROOT, app_env="production")


@pytest.mark.parametrize("cwd, cache, problem", [
    (ROOT, None, "working directory is the repository"),
    (None, "shared", "FOOTBALL_PREDICTOR_CACHE_DIR is not set"),
])
def test_copies_with_shared_paths_are_refused(tmp_path, cwd, cache, problem):
    with pytest.raises(wg.WorkspaceNotIsolated, match=problem):
        wg.check(tmp_path / "copy.db", wg.SHARED_CACHE if cache == "shared" else tmp_path / ".cache",
                 cwd=cwd or tmp_path, app_env="development")


def test_isolated_copy_passes(tmp_path):
    wg.check(tmp_path / "copy.db", tmp_path / ".cache", cwd=tmp_path, app_env="development")


def test_isolate_refuses_the_repository(tmp_path):
    with pytest.raises(wg.WorkspaceNotIsolated):
        wg.isolate(ROOT)


def test_config_honours_the_cache_override():
    from src import config

    assert Path(config.CACHE_DIR).resolve() != wg.SHARED_CACHE        # conftest isolates the suite
    assert Path(config.CACHE_DIR) == Path(os.environ["FOOTBALL_PREDICTOR_CACHE_DIR"])


def _start_api(env, cwd):
    return subprocess.run([sys.executable, "-c", "import api.main"], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=120)


def test_api_refuses_a_copy_started_from_the_repository(tmp_path):
    """The M3 smoke-test scenario: uvicorn api.main from the repo with a DB copy."""
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": str(ROOT), "APP_ENV": "development",
           "FOOTBALL_PREDICTOR_DB_PATH": str(tmp_path / "copy.db")}
    r = _start_api(env, ROOT)
    assert r.returncode != 0 and "WorkspaceNotIsolated" in r.stderr


def test_api_starts_from_an_isolated_workspace(tmp_path):
    work = tmp_path / "w"
    (work / ".cache").mkdir(parents=True)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": str(ROOT), "APP_ENV": "development",
           "FOOTBALL_PREDICTOR_DB_PATH": str(tmp_path / "copy.db"),
           "FOOTBALL_PREDICTOR_CACHE_DIR": str(work / ".cache")}
    r = _start_api(env, work)
    assert r.returncode == 0, r.stderr

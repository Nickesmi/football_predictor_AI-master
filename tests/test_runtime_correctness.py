"""Regression tests for runtime breakages found in M1.1 (missing imports / stale references)."""
import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import config
from tests.fakes import providers

ROOT = Path(__file__).resolve().parent.parent
ADMIN_KEY = "a" * 40


def test_competition_tracking_persists_fixture_leagues():
    import api.services.fixtures_service as fs
    from src.db.database import get_db

    fixtures = [fs._api_football_to_fixture(raw, "2026-07-04") for raw in providers.api_football_fixtures("2026-07-04")]
    fs._track_competitions_from_fixtures(fixtures)

    rows = get_db().execute("SELECT id, name, country FROM competitions WHERE id IN ('39', '135', '78') ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [
        ("135", "Serie A", "Italy"), ("39", "Premier League", "England"), ("78", "Bundesliga", "Germany"),
    ]


def test_prewarm_computes_cached_fixtures_for_that_date_only(monkeypatch):
    import api.services.fixtures_service as fs
    from api.services.system_service import prewarm_predictions_for_date

    # future date: past-date caches holding unfinished matches are deliberately treated as expired
    fixtures = [fs._api_football_to_fixture(raw, "2030-07-04") for raw in providers.api_football_fixtures("2030-07-04")]
    other_day = dict(fixtures[0], id="other-day", date="2030-07-05")
    fs._write_fixture_cache("2030-07-04", fixtures + [other_day])
    monkeypatch.setattr(fs, "_PREDICTION_STATUS", {})

    assert prewarm_predictions_for_date("2030-07-04") == 3
    assert {fid: s["status"] for fid, s in fs._PREDICTION_STATUS.items()} == {
        "9000000": "ready", "9000001": "ready", "9000002": "ready",
    }


def test_prewarm_with_no_cache_does_nothing(monkeypatch):
    import api.services.fixtures_service as fs
    from api.services.system_service import prewarm_predictions_for_date

    monkeypatch.setattr(fs, "_PREDICTION_STATUS", {})
    assert prewarm_predictions_for_date("2030-01-01") == 0
    assert fs._PREDICTION_STATUS == {}


@pytest.mark.parametrize("path", ["/api/debug/date-trace", "/api/debug/date-validation", "/api/debug/logo-audit"])
def test_previously_broken_admin_endpoints_now_answer(path, monkeypatch):
    from api.main import app

    providers.install(monkeypatch)
    monkeypatch.setattr(config, "ADMIN_API_KEY", ADMIN_KEY)
    response = TestClient(app).get(path, params={"date": "2026-07-04"}, headers={"X-API-Key": ADMIN_KEY})
    assert response.status_code == 200, response.text[:300]


def test_audit_engine_uses_the_real_league_name_map():
    from api.services.match_analysis_service import LEAGUE_NAME_MAP

    source = (ROOT / "src" / "engine" / "audit_engine.py").read_text()
    assert "from api.main import LEAGUE_NAME_MAP" not in source
    assert LEAGUE_NAME_MAP["La Liga"] == "LaLiga"
    assert len(LEAGUE_NAME_MAP) == 51


# ── Static guard: undefined names and unresolvable project imports ──────────
# Known, documented exceptions (KNOWN_ISSUES.md K27 and orphan files); anything else fails.
ALLOWED_UNDEFINED = {
    ("api/backtest_snippet.py", "app"),
    ("api/backtest_snippet.py", "_compute_match_analysis"),
    ("src/prediction_service/providers/live_provider.py", "_Response"),  # string annotation only
}
ALLOWED_MISSING_MODULES = {("src/engine/classic_prediction.py", "src.db.daily_repo")}
SCANNED = ["api", "src", "scripts"]


def _python_files():
    for top in SCANNED:
        yield from sorted((ROOT / top).rglob("*.py"))


def test_no_new_undefined_names():
    pyflakes_api = pytest.importorskip("pyflakes.api")
    from pyflakes import messages

    found = set()

    class Collector:
        def flake(self, message):
            if isinstance(message, messages.UndefinedName):
                rel = str(Path(message.filename).resolve().relative_to(ROOT))
                found.add((rel, message.message_args[0]))

        def unexpectedError(self, filename, msg):
            found.add((filename, f"unexpected: {msg}"))

        def syntaxError(self, filename, msg, *args):
            found.add((filename, f"syntax: {msg}"))

    for path in _python_files():
        pyflakes_api.checkPath(str(path), Collector())
    assert found - ALLOWED_UNDEFINED == set()


def test_no_new_unresolvable_project_imports():
    missing = set()
    for path in _python_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                top = node.module.split(".")[0]
                if top in ("api", "src"):
                    base = ROOT / node.module.replace(".", "/")
                    if not (base.with_suffix(".py").exists() or base.is_dir()):
                        missing.add((str(path.relative_to(ROOT)), node.module))
    assert missing - ALLOWED_MISSING_MODULES == set()


def test_no_imports_of_names_removed_from_api_main():
    exported = {"app", "_compute_match_analysis", "lifespan"}
    stale = []
    for path in _python_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module == "api.main":
                stale += [f"{path.relative_to(ROOT)}: {a.name}" for a in node.names if a.name not in exported]
    assert stale == []

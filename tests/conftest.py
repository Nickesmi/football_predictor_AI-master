"""conftest.py — shared fixtures and safety rails for the test suite.

Everything above the imports runs before any `src`/`api` module is imported,
so module-level config (DB path, HOME-based user dir, .env keys) can never
resolve to production resources:

* FOOTBALL_PREDICTOR_DB_PATH -> a throwaway SQLite file (never data/engine.db)
* HOME -> a temp dir (config writes logs/settings under ~/.football_predictor)
* provider keys -> empty (load_dotenv never overrides already-set variables)

Network access is blocked for every test. A test that attempts a non-loopback
connection fails, even if production code swallows the error. Tests that
genuinely need the network must be marked `@pytest.mark.external` and only
run when RUN_EXTERNAL_TESTS=1.
"""
import os
import socket
import sqlite3
import tempfile
from pathlib import Path

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="football-predictor-tests-"))
os.environ["HOME"] = str(_TEST_ROOT)
os.environ["FOOTBALL_PREDICTOR_DB_PATH"] = str(_TEST_ROOT / "engine.db")
os.environ["FOOTBALL_PREDICTOR_SNAPSHOT_DB_PATH"] = str(_TEST_ROOT / "predictions.db")
for _key in (
    "API_FOOTBALL_KEY", "APIFOOTBALL_API_KEY", "RAPIDAPI_KEY", "ODDS_API_KEY",
    "FOOTBALL_DATA_API_KEY", "ADMIN_API_KEY",
):
    os.environ[_key] = ""
os.environ["APP_ENV"] = "test"
for _key in ("CORS_ALLOW_ORIGINS", "WEB_PORT"):
    os.environ.pop(_key, None)

import pytest  # noqa: E402

from src.db import database  # noqa: E402
from src.db.database import init_db  # noqa: E402

_LIVE_DB = (Path(__file__).resolve().parent.parent / "data" / "engine.db").resolve()


class NetworkBlocked(OSError):
    """Raised when a test tries to reach the network."""


def _is_local(address) -> bool:
    host = address[0] if isinstance(address, tuple) else address
    return host in ("127.0.0.1", "::1", "localhost") or str(host).startswith("127.")


def pytest_configure(config):
    config.addinivalue_line("markers", "external: test talks to real external services (needs RUN_EXTERNAL_TESTS=1)")
    assert Path(database.DB_PATH).resolve() != _LIVE_DB, "tests must never use the production database"


def pytest_collection_modifyitems(config, items):
    if os.getenv("RUN_EXTERNAL_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="external integration test; set RUN_EXTERNAL_TESTS=1 to run")
    for item in items:
        if "external" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _block_network(request, monkeypatch):
    if "external" in request.keywords and os.getenv("RUN_EXTERNAL_TESTS") == "1":
        yield
        return

    attempts: list[str] = []

    def _deny(target):
        attempts.append(str(target))
        raise NetworkBlocked(f"network access blocked in tests: {target}")

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(sock, address):
        if sock.family == getattr(socket, "AF_UNIX", None) or _is_local(address):
            return real_connect(sock, address)
        _deny(address)

    def guarded_connect_ex(sock, address):
        if sock.family == getattr(socket, "AF_UNIX", None) or _is_local(address):
            return real_connect_ex(sock, address)
        _deny(address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host is None or _is_local((host,)):
            return real_getaddrinfo(host, *args, **kwargs)
        _deny(host)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    try:
        import curl_cffi.curl as _curl
        monkeypatch.setattr(_curl.Curl, "perform", lambda self, *a, **k: _deny("curl_cffi request"))
    except ImportError:
        pass

    yield

    if attempts:
        pytest.fail(f"test attempted {len(attempts)} network call(s); mock the provider: {attempts[:3]}")


@pytest.fixture(autouse=True)
def _isolate_file_side_effects(monkeypatch, tmp_path):
    """Keep caches and research DBs written during tests out of the repository."""
    import api.services.fixtures_service as fixtures_service
    import src.prediction_service.snapshot_db as snapshot_db

    monkeypatch.setattr(fixtures_service, "_FIXTURE_CACHE_DIR", tmp_path / "fixture-cache")
    monkeypatch.setattr(snapshot_db, "_DB_PATH", tmp_path / "predictions.db")


@pytest.fixture
def engine_db():
    """In-memory SQLite DB with the full engine schema initialized."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    yield conn
    conn.close()

"""Admin authentication, CORS and startup security checks."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api.dependencies import verify_admin_key
from src import config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STRONG_KEY = "s" * config.MIN_ADMIN_API_KEY_LENGTH


@pytest.fixture
def admin_client():
    app = FastAPI()

    @app.get("/admin-only", dependencies=[Depends(verify_admin_key)])
    def admin_only():
        return {"ok": True}

    return TestClient(app)


def test_no_hardcoded_admin_key_default():
    source = (PROJECT_ROOT / "src" / "config.py").read_text()
    assert "dev-admin-secret" not in source
    assert config.ADMIN_API_KEY == ""  # conftest sets ADMIN_API_KEY="" for the test process


def test_admin_api_disabled_when_key_unconfigured(admin_client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_API_KEY", "")
    for headers in ({}, {"X-API-Key": ""}, {"X-API-Key": "dev-admin-secret"}):
        response = admin_client.get("/admin-only", headers=headers)
        assert response.status_code == 503
        assert response.json() == {"detail": "Admin API disabled: ADMIN_API_KEY is not configured"}


def test_admin_rejects_missing_or_wrong_key(admin_client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_API_KEY", STRONG_KEY)
    assert admin_client.get("/admin-only").status_code == 401
    assert admin_client.get("/admin-only", headers={"X-API-Key": STRONG_KEY[:-1]}).status_code == 401
    assert admin_client.get("/admin-only", headers={"X-API-Key": STRONG_KEY + "x"}).status_code == 401


def test_admin_accepts_exact_key(admin_client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_API_KEY", STRONG_KEY)
    response = admin_client.get("/admin-only", headers={"X-API-Key": STRONG_KEY})
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_development_defaults_allow_only_local_origins():
    assert config.IS_DEVELOPMENT is True
    assert config.CORS_ALLOW_ORIGINS == ["http://127.0.0.1:5173", "http://localhost:5173"]
    assert config.validate_security_config() == []


@pytest.mark.parametrize("key, origins, expected", [
    ("", ["https://app.example.com"], ["ADMIN_API_KEY must be set to at least 32 characters when APP_ENV=production"]),
    ("short", ["https://app.example.com"], ["ADMIN_API_KEY must be set to at least 32 characters when APP_ENV=production"]),
    (STRONG_KEY, [], ["CORS_ALLOW_ORIGINS must list the allowed origins when APP_ENV=production"]),
    (STRONG_KEY, ["*"], ["CORS_ALLOW_ORIGINS must not contain '*' (credentials are allowed)"]),
    (STRONG_KEY, ["https://app.example.com"], []),
])
def test_production_security_validation(monkeypatch, key, origins, expected):
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "IS_DEVELOPMENT", False)
    monkeypatch.setattr(config, "ADMIN_API_KEY", key)
    monkeypatch.setattr(config, "CORS_ALLOW_ORIGINS", origins)
    assert config.validate_security_config() == expected


def _import_api_main(extra_env: dict) -> subprocess.CompletedProcess:
    # Isolated working directory: the test DB is a non-production DB, so api.main refuses the repo cwd.
    import tempfile

    workdir = tempfile.mkdtemp(prefix="api-main-")
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT), **extra_env}
    return subprocess.run(
        [sys.executable, "-c", "import api.main"],
        cwd=workdir, env=env, capture_output=True, text=True, timeout=120,
    )


def test_api_refuses_to_start_in_production_without_secure_config():
    proc = _import_api_main({"APP_ENV": "production", "ADMIN_API_KEY": "", "CORS_ALLOW_ORIGINS": ""})
    assert proc.returncode != 0
    assert "Refusing to start (APP_ENV=production)" in proc.stderr
    assert "ADMIN_API_KEY must be set" in proc.stderr
    assert "CORS_ALLOW_ORIGINS must list" in proc.stderr


def test_api_starts_in_production_with_secure_config():
    proc = _import_api_main({
        "APP_ENV": "production", "ADMIN_API_KEY": STRONG_KEY,
        "CORS_ALLOW_ORIGINS": "https://app.example.com",
    })
    assert proc.returncode == 0, proc.stderr


def test_cors_allows_configured_origin_and_rejects_others():
    from api.main import app

    client = TestClient(app)
    allowed = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"

    denied = client.get("/api/health", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in denied.headers

    preflight = client.options("/api/health", headers={
        "Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET",
    })
    assert preflight.status_code == 400

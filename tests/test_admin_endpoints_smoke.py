"""Every admin GET endpoint must answer without a server error (offline, isolated DB)."""
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api.dependencies import verify_admin_key
from api.main import app
from src import config

ADMIN_KEY = "a" * 40
QUERY = {"date": "2026-07-04", "date_str": "2026-07-04", "team": "Arsenal", "team_name": "Arsenal", "limit": 5}


def _admin_get_routes():
    routes = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or "GET" not in route.methods:
            continue
        if any(dep.call is verify_admin_key for dep in route.dependant.dependencies):
            routes.append(route.path.replace("{date_str}", "2026-07-04"))
    return sorted(routes)


ADMIN_ROUTES = _admin_get_routes()


def test_admin_routes_discovered():
    assert len(ADMIN_ROUTES) == 24


@pytest.mark.parametrize("path", ADMIN_ROUTES)
def test_admin_get_endpoint_has_no_server_error(path, monkeypatch):
    from tests.fakes import providers
    providers.install(monkeypatch)
    monkeypatch.setattr(config, "ADMIN_API_KEY", ADMIN_KEY)

    response = TestClient(app).get(path, params=QUERY, headers={"X-API-Key": ADMIN_KEY})
    assert response.status_code < 500, f"{path} -> {response.status_code}: {response.text[:300]}"

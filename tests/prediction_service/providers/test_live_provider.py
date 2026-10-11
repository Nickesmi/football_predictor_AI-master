"""
Regression tests for LiveProvider (Phase 4 §9/§34).

These are explicitly labeled INTEGRATION SIMULATIONS: this environment
cannot reach any real football data provider (confirmed in AUDIT_REPORT.md
Phase 1 — the network policy blocks api.football-data.org and
api.sofascore.com). Every test here injects a fake `transport` callable
instead of making a real HTTP request, simulating success, auth failure,
rate limiting, timeouts, and malformed responses — proving the CONTRACT
is correct without pretending to test real connectivity.
"""

from __future__ import annotations

import pytest

from src.prediction_service.data_contract import ProviderHealthStatus
from src.prediction_service.providers.base import ProviderError
from src.prediction_service.providers.live_provider import LiveProvider


class _FakeResponse:
    def __init__(self, status_code: int, payload=None, raise_on_json=False):
        self.status_code = status_code
        self._payload = payload
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            raise ValueError("not valid json")
        return self._payload


def _configured_env(monkeypatch):
    monkeypatch.setenv("FOOTBALL_DATA_PROVIDER", "test-provider")
    monkeypatch.setenv("FOOTBALL_DATA_API_KEY", "test-key")
    monkeypatch.setenv("FOOTBALL_DATA_BASE_URL", "https://example.invalid/api")


def test_unconfigured_provider_reports_unavailable_without_attempting_network(monkeypatch):
    monkeypatch.delenv("FOOTBALL_DATA_PROVIDER", raising=False)
    monkeypatch.delenv("FOOTBALL_DATA_API_KEY", raising=False)
    monkeypatch.delenv("FOOTBALL_DATA_BASE_URL", raising=False)

    def _should_never_be_called(*a, **kw):
        raise AssertionError("transport must not be called when unconfigured")

    provider = LiveProvider(transport=_should_never_be_called)
    health = provider.health_check()
    assert health.status == ProviderHealthStatus.UNAVAILABLE
    assert "not configured" in health.detail


def test_successful_health_check_reports_available(monkeypatch):
    _configured_env(monkeypatch)
    provider = LiveProvider(transport=lambda url, headers, params: _FakeResponse(200, {}))
    health = provider.health_check()
    assert health.status == ProviderHealthStatus.AVAILABLE
    assert health.latency_ms is not None


def test_auth_failure_is_reported_distinctly_from_network_failure(monkeypatch):
    _configured_env(monkeypatch)
    provider = LiveProvider(transport=lambda url, headers, params: _FakeResponse(401))
    health = provider.health_check()
    assert health.status == ProviderHealthStatus.AUTH_FAILURE


def test_rate_limit_is_reported_distinctly(monkeypatch):
    _configured_env(monkeypatch)
    provider = LiveProvider(transport=lambda url, headers, params: _FakeResponse(429))
    health = provider.health_check()
    assert health.status == ProviderHealthStatus.RATE_LIMITED


def test_server_error_is_reported_as_degraded_not_unavailable(monkeypatch):
    _configured_env(monkeypatch)
    provider = LiveProvider(transport=lambda url, headers, params: _FakeResponse(503))
    health = provider.health_check()
    assert health.status == ProviderHealthStatus.DEGRADED


def test_timeout_is_reported_as_network_failure_not_zero_matches(monkeypatch):
    _configured_env(monkeypatch)

    def _timeout(*a, **kw):
        raise TimeoutError("simulated timeout")

    provider = LiveProvider(transport=_timeout)
    health = provider.health_check()
    assert health.status == ProviderHealthStatus.NETWORK_FAILURE


def test_fetch_fixtures_raises_provider_error_on_malformed_response(monkeypatch):
    _configured_env(monkeypatch)
    provider = LiveProvider(transport=lambda url, headers, params: _FakeResponse(200, {"not_fixtures_key": []}))
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_fixtures(as_of="2025-01-01T00:00:00Z")
    assert exc_info.value.status == ProviderHealthStatus.INVALID_RESPONSE


def test_fetch_fixtures_raises_provider_error_on_invalid_json(monkeypatch):
    _configured_env(monkeypatch)
    provider = LiveProvider(transport=lambda url, headers, params: _FakeResponse(200, raise_on_json=True))
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_fixtures(as_of="2025-01-01T00:00:00Z")
    assert exc_info.value.status == ProviderHealthStatus.INVALID_RESPONSE


def test_fetch_fixtures_succeeds_and_tags_data_mode_live(monkeypatch):
    _configured_env(monkeypatch)
    payload = {"fixtures": [{
        "fixture_id": "f1", "competition": "Test League", "season": "2025-26",
        "home_team": "A", "away_team": "B", "scheduled_kickoff": "2025-01-01T15:00:00Z",
    }]}
    provider = LiveProvider(transport=lambda url, headers, params: _FakeResponse(200, payload))
    fixtures = provider.fetch_fixtures(as_of="2025-01-01T00:00:00Z")
    assert len(fixtures) == 1
    assert fixtures[0].data_mode.value == "LIVE"
    assert fixtures[0].home_goals is None  # a "fixture" listing must never carry a result


def test_fetch_odds_returns_unavailable_rather_than_raising_on_provider_error(monkeypatch):
    _configured_env(monkeypatch)
    provider = LiveProvider(transport=lambda url, headers, params: _FakeResponse(500))
    odds = provider.fetch_odds("f1", as_of="2025-01-01T00:00:00Z")
    assert odds.status.value == "UNAVAILABLE"
    assert odds.odds is None


def test_fetch_lineup_returns_unknown_rather_than_raising_on_provider_error(monkeypatch):
    _configured_env(monkeypatch)
    provider = LiveProvider(transport=lambda url, headers, params: _FakeResponse(500))
    lineup = provider.fetch_lineup("f1", as_of="2025-01-01T00:00:00Z")
    assert lineup.status.value == "UNKNOWN"


def test_never_hardcodes_credentials_reads_only_from_environment(monkeypatch):
    monkeypatch.setenv("FOOTBALL_DATA_PROVIDER", "provider-x")
    monkeypatch.setenv("FOOTBALL_DATA_API_KEY", "super-secret-value")
    monkeypatch.setenv("FOOTBALL_DATA_BASE_URL", "https://example.invalid")
    provider = LiveProvider()
    assert provider.api_key == "super-secret-value"

    import inspect
    from src.prediction_service.providers import live_provider as mod
    source = inspect.getsource(mod)
    assert "super-secret-value" not in source
    assert '"sk-' not in source and "api_key = \"" not in source

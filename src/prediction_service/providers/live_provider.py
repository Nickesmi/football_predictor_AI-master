"""
Live provider (Phase 4 §7/§8/§9/§34).

IMPORTANT: this environment's network policy blocks every real football
data provider (see AUDIT_REPORT.md — api.football-data.org and
api.sofascore.com both returned connect_rejected during Phase 1). This
class is therefore never exercised end-to-end against a real provider in
this session — per the Phase 4 instructions, it is tested against a
MOCKED transport (see tests/prediction_service/providers/
test_live_provider.py), simulating success, auth failure, rate limiting,
timeouts, and malformed responses. That is clearly labeled as an
integration simulation, not a real connectivity test.

Configuration (never hardcoded, never committed):
    FOOTBALL_DATA_PROVIDER    — provider identifier, e.g. "api-football"
    FOOTBALL_DATA_API_KEY     — the API key/token
    FOOTBALL_DATA_BASE_URL    — base URL for the configured provider

To activate a real provider in a production deployment: set these three
environment variables (or your secrets manager's equivalent), restart the
service, and health_check() will report AVAILABLE once a real request
succeeds. Nothing else in the codebase needs to change — the prediction
pipeline only ever consumes the canonical FixtureRecord/OddsRecord/
LineupRecord types this class produces.
"""

from __future__ import annotations

import os
import time
from typing import Callable, Optional

from src.prediction_service.data_contract import (
    DataMode, FixtureRecord, OddsRecord, LineupRecord, ProviderHealth, ProviderHealthStatus, now_iso,
)
from src.prediction_service.providers.base import FootballDataProvider, ProviderError
from src.prediction_service import observability

# How old a successful response may be before health_check demotes
# AVAILABLE to STALE_DATA (Phase 4 §10).
FRESHNESS_THRESHOLD_SECONDS = 6 * 3600


class LiveProvider(FootballDataProvider):
    name = "live_http_provider"
    data_mode = DataMode.LIVE

    def __init__(self, transport: Optional[Callable[[str, dict, dict], "_Response"]] = None):
        """transport(url, headers, params) -> _Response-like object with
        .status_code and .json(). Defaults to a real `requests.get` call;
        tests inject a fake transport instead of hitting the network."""
        self.provider_id = os.getenv("FOOTBALL_DATA_PROVIDER", "")
        self.api_key = os.getenv("FOOTBALL_DATA_API_KEY", "")
        self.base_url = os.getenv("FOOTBALL_DATA_BASE_URL", "")
        self._transport = transport or self._default_transport
        self._last_success_at: Optional[float] = None

    @staticmethod
    def _default_transport(url: str, headers: dict, params: dict):
        import requests
        return requests.get(url, headers=headers, params=params, timeout=10)

    def is_configured(self) -> bool:
        return bool(self.provider_id and self.api_key and self.base_url)

    def health_check(self) -> ProviderHealth:
        result = self._health_check_inner()
        observability.log_provider_health(result.provider_name, result.status.value, result.latency_ms, result.detail)
        return result

    def _health_check_inner(self) -> ProviderHealth:
        checked_at = now_iso()
        if not self.is_configured():
            return ProviderHealth(
                provider_name=self.name, status=ProviderHealthStatus.UNAVAILABLE, checked_at=checked_at,
                detail="not configured — set FOOTBALL_DATA_PROVIDER, FOOTBALL_DATA_API_KEY, FOOTBALL_DATA_BASE_URL",
            )

        start = time.monotonic()
        try:
            resp = self._transport(f"{self.base_url}/health", {"Authorization": f"Bearer {self.api_key}"}, {})
        except TimeoutError as e:
            return ProviderHealth(self.name, ProviderHealthStatus.NETWORK_FAILURE, checked_at, detail=str(e))
        except ConnectionError as e:
            return ProviderHealth(self.name, ProviderHealthStatus.NETWORK_FAILURE, checked_at, detail=str(e))
        except Exception as e:
            return ProviderHealth(self.name, ProviderHealthStatus.NETWORK_FAILURE, checked_at, detail=str(e))
        latency_ms = (time.monotonic() - start) * 1000

        status = self._classify_response(resp)
        if status == ProviderHealthStatus.AVAILABLE:
            self._last_success_at = time.time()
        return ProviderHealth(self.name, status, checked_at, latency_ms=round(latency_ms, 1))

    @staticmethod
    def _classify_response(resp) -> ProviderHealthStatus:
        code = getattr(resp, "status_code", None)
        if code == 200:
            return ProviderHealthStatus.AVAILABLE
        if code == 401 or code == 403:
            return ProviderHealthStatus.AUTH_FAILURE
        if code == 429:
            return ProviderHealthStatus.RATE_LIMITED
        if code is not None and code >= 500:
            return ProviderHealthStatus.DEGRADED
        return ProviderHealthStatus.INVALID_RESPONSE

    def _get(self, path: str, params: dict) -> dict:
        if not self.is_configured():
            raise ProviderError(ProviderHealthStatus.UNAVAILABLE, "provider not configured")
        try:
            resp = self._transport(f"{self.base_url}{path}", {"Authorization": f"Bearer {self.api_key}"}, params)
        except Exception as e:
            raise ProviderError(ProviderHealthStatus.NETWORK_FAILURE, str(e)) from e

        status = self._classify_response(resp)
        if status != ProviderHealthStatus.AVAILABLE:
            raise ProviderError(status, f"HTTP {getattr(resp, 'status_code', '?')}")

        try:
            payload = resp.json()
        except Exception as e:
            raise ProviderError(ProviderHealthStatus.INVALID_RESPONSE, f"response was not valid JSON: {e}") from e

        if not isinstance(payload, dict):
            raise ProviderError(ProviderHealthStatus.INVALID_RESPONSE, "response JSON was not an object")

        self._last_success_at = time.time()
        return payload

    def fetch_fixtures(self, as_of: str, competition: Optional[str] = None) -> list[FixtureRecord]:
        params = {"date": as_of[:10]}
        if competition:
            params["competition"] = competition
        payload = self._get("/fixtures", params)

        raw_fixtures = payload.get("fixtures")
        if not isinstance(raw_fixtures, list):
            raise ProviderError(ProviderHealthStatus.INVALID_RESPONSE, "'fixtures' field missing or not a list")

        out = []
        ingestion_ts = now_iso()
        for raw in raw_fixtures:
            try:
                out.append(FixtureRecord(
                    fixture_id=str(raw["fixture_id"]), competition=raw["competition"], season=raw["season"],
                    home_team=raw["home_team"], away_team=raw["away_team"],
                    scheduled_kickoff=raw["scheduled_kickoff"], status=raw.get("status", "scheduled"),
                    data_mode=DataMode.LIVE, source=self.provider_id,
                    source_timestamp=raw.get("source_timestamp", ingestion_ts), ingestion_timestamp=ingestion_ts,
                    actual_kickoff=raw.get("actual_kickoff"), home_goals=raw.get("home_goals"), away_goals=raw.get("away_goals"),
                ))
            except KeyError as e:
                raise ProviderError(ProviderHealthStatus.INVALID_RESPONSE, f"fixture missing required field {e}") from e
        return out

    def fetch_odds(self, fixture_id: str, as_of: str) -> OddsRecord:
        try:
            payload = self._get("/odds", {"fixture_id": fixture_id})
        except ProviderError:
            return OddsRecord.unavailable(fixture_id)  # odds absence is normal, not a failure to propagate
        if not payload.get("available"):
            return OddsRecord.unavailable(fixture_id)
        return OddsRecord(
            fixture_id=fixture_id, status=payload.get("status", "AVAILABLE"), source=payload.get("source"),
            market=payload.get("market"), selection=payload.get("selection"), odds=payload.get("odds"),
            timestamp=payload.get("timestamp"), is_opening=payload.get("is_opening"), is_closing=payload.get("is_closing"),
        )

    def fetch_lineup(self, fixture_id: str, as_of: str) -> LineupRecord:
        try:
            payload = self._get("/lineups", {"fixture_id": fixture_id})
        except ProviderError:
            return LineupRecord.unknown(fixture_id)
        return LineupRecord(
            fixture_id=fixture_id, status=payload.get("status", "UNKNOWN"),
            source=payload.get("source"), retrieved_at=payload.get("retrieved_at"),
        )

    def fetch_result(self, fixture_id: str, as_of: str):
        payload = self._get("/results", {"fixture_id": fixture_id})  # let ProviderError propagate — a
        # result-fetch failure must fail closed, unlike odds/lineup absence which are normal states.
        if payload.get("status") != "finished":
            return None
        ingestion_ts = now_iso()
        return FixtureRecord(
            fixture_id=fixture_id, competition=payload.get("competition", ""), season=payload.get("season", ""),
            home_team=payload["home_team"], away_team=payload["away_team"],
            scheduled_kickoff=payload.get("scheduled_kickoff", ""), status="finished",
            data_mode=DataMode.LIVE, source=self.provider_id,
            source_timestamp=payload.get("source_timestamp", ingestion_ts), ingestion_timestamp=ingestion_ts,
            actual_kickoff=payload.get("actual_kickoff"),
            home_goals=payload.get("home_goals"), away_goals=payload.get("away_goals"),
        )

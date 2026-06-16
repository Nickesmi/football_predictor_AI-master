"""
Low-level HTTP client for API-Football v3.

The higher-level parsing code lives in `api_football_fetcher.py`; this class
keeps network, caching, and API-Football authentication concerns in one place.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import (
    APIFOOTBALL_API_KEY,
    APIFOOTBALL_HOST,
    API_RATE_LIMIT_PER_MINUTE,
    CACHE_DIR,
    CACHE_TTL_SECONDS,
    logger,
)


class APIFootballClient:
    """Cached HTTP client for the API-Football v3 REST API."""

    BASE_URL = "https://{host}/v3"

    def __init__(
        self,
        api_key: Optional[str] = None,
        host: Optional[str] = None,
        cache_dir: Optional[Path] = None,
        cache_ttl: int = CACHE_TTL_SECONDS,
        rate_limit: int = API_RATE_LIMIT_PER_MINUTE,
    ):
        self._api_key = api_key or APIFOOTBALL_API_KEY
        self._host = host or APIFOOTBALL_HOST
        self._base_url = self.BASE_URL.format(host=self._host)
        self._cache_dir = Path(cache_dir) if cache_dir is not None else CACHE_DIR
        self._cache_ttl = cache_ttl
        self._min_interval = 60.0 / max(rate_limit, 1)
        self._last_request_ts = 0.0

        self._cache_dir.mkdir(parents=True, exist_ok=True)

        self._session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=1.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retries)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def get(self, endpoint: str, **params: Any) -> dict:
        """Fetch a JSON response, returning cached data when available."""
        cache_key = self._make_cache_key(endpoint, params)
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached

        try:
            from src.data.provider_health import is_circuit_open, record_provider_result
        except Exception:
            is_circuit_open = lambda _provider: False
            record_provider_result = lambda *_args, **_kwargs: None

        if is_circuit_open("api-football"):
            raise RuntimeError("Circuit breaker open for api-football")

        self._throttle()
        url = f"{self._base_url}/{endpoint}"
        headers = self._build_headers()

        start_time = time.time()
        try:
            response = self._session.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            self._validate_response(data, endpoint, params)
            latency = int((time.time() - start_time) * 1000)
            record_provider_result(
                "api-football",
                endpoint,
                True,
                latency,
                fixture_count=data.get("results", 0),
            )
            self._write_cache(cache_key, data)
            return data
        except Exception as exc:
            latency = int((time.time() - start_time) * 1000)
            record_provider_result(
                "api-football",
                endpoint,
                False,
                latency,
                error_message=str(exc),
            )
            raise

    def _build_headers(self) -> dict[str, str]:
        if "rapidapi" in self._host.lower():
            return {
                "x-rapidapi-key": self._api_key,
                "x-rapidapi-host": self._host,
            }
        return {"x-apisports-key": self._api_key}

    def _throttle(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_ts
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_ts = time.monotonic()

    @staticmethod
    def _validate_response(data: dict, endpoint: str, params: dict) -> None:
        errors = data.get("errors")
        if errors:
            raise ValueError(f"API error on /{endpoint} {params}: {errors}")
        if data.get("results", 0) == 0:
            logger.debug("API returned 0 results for /%s %s", endpoint, params)

    def _make_cache_key(self, endpoint: str, params: dict) -> str:
        raw = json.dumps({"endpoint": endpoint, **params}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    def _read_cache(self, key: str) -> Optional[dict]:
        path = self._cache_path(key)
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self._cache_ttl:
            path.unlink(missing_ok=True)
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            path.unlink(missing_ok=True)
            return None

    def _write_cache(self, key: str, data: dict) -> None:
        path = self._cache_path(key)
        try:
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            logger.warning("Cache write failed for %s: %s", key[:12], exc)

    def clear_cache(self) -> int:
        count = 0
        for cache_file in self._cache_dir.glob("*.json"):
            cache_file.unlink(missing_ok=True)
            count += 1
        return count

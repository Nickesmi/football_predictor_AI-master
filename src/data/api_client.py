"""
Low-level HTTP client for the API-Football v3 REST API.

Handles:
  - Authentication (x-apisports-key or x-rapidapi-key)
  - Rate limiting (token-bucket style)
  - Transparent disk caching (JSON files in .cache/)
  - Retry with exponential backoff on transient errors
  - Structured logging of every request

This module is intentionally thin – it only fetches raw JSON.
The parsing/mapping layer lives in `api_football_fetcher.py`.
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
    """
    Thread-safe, cached HTTP client for API-Football v3.

    Usage::

        client = APIFootballClient()
        data = client.get("fixtures", season=2024, league=39, team=33)
    """

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
        self._base_url = self._resolve_base_url(self._host)
        self._cache_dir = cache_dir or CACHE_DIR
        self._cache_ttl = cache_ttl
        self._min_interval = 60.0 / max(rate_limit, 1)
        self._last_request_ts: float = 0.0

        if not self._api_key:
            logger.warning(
                "APIFOOTBALL_API_KEY is not set. API calls will fail. "
                "Set it in your .env file or environment."
            )

        # Prepare cache directory
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        # Build a requests session with retry logic
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

    @staticmethod
    def _resolve_base_url(host: str) -> str:
        """Build the API root URL for direct api-sports.io vs RapidAPI hosts."""
        if "rapidapi" in host.lower():
            return f"https://{host}/v3"
        return f"https://{host}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, endpoint: str, **params: Any) -> dict:
        """
        Execute a GET request against the given endpoint.

        Args:
            endpoint: API endpoint name (e.g. "fixtures", "leagues", "teams").
            **params: Query-string parameters forwarded to the API.

        Returns:
            The full JSON response as a Python dict.

        Raises:
            requests.HTTPError: On non-2xx status after retries.
            ValueError: If the API returns an error payload.
        """
        cache_key = self._make_cache_key(endpoint, params)
        cached = self._read_cache(cache_key)
        if cached is not None:
            logger.debug("Cache HIT for %s %s", endpoint, params)
            return cached

        logger.info("Cache MISS – calling API: %s %s", endpoint, params)
        self._throttle()

        url = f"{self._base_url}/{endpoint}"
        headers = self._build_headers()

        response = self._session.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()

        data = response.json()
        self._validate_response(data, endpoint, params)
        self._write_cache(cache_key, data)

        return data

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        """Build auth headers depending on the host provider."""
        if "rapidapi" in self._host.lower():
            return {
                "x-rapidapi-key": self._api_key,
                "x-rapidapi-host": self._host,
            }
        # Direct api-sports.io
        return {"x-apisports-key": self._api_key}

    def _throttle(self) -> None:
        """Simple rate-limiter: ensure minimum interval between requests."""
        now = time.monotonic()
        elapsed = now - self._last_request_ts
        if elapsed < self._min_interval:
            wait = self._min_interval - elapsed
            logger.debug("Rate-limit: sleeping %.2fs", wait)
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    @staticmethod
    def _validate_response(data: dict, endpoint: str, params: dict) -> None:
        """Check the API response for known error patterns."""
        errors = data.get("errors")
        if errors:
            # errors can be a dict or a list depending on the API
            msg = f"API error on /{endpoint} {params}: {errors}"
            logger.error(msg)
            raise ValueError(msg)

        results = data.get("results", 0)
        if results == 0:
            logger.warning(
                "API returned 0 results for /%s %s – "
                "this may be expected (e.g. no fixtures yet).",
                endpoint,
                params,
            )

    # ------------------------------------------------------------------
    # Disk cache
    # ------------------------------------------------------------------

    def _make_cache_key(self, endpoint: str, params: dict) -> str:
        """Deterministic hash for the request."""
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
            logger.debug("Cache EXPIRED for key %s (age=%.0fs)", key[:12], age)
            path.unlink(missing_ok=True)
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Cache read failed for %s: %s", key[:12], exc)
            path.unlink(missing_ok=True)
            return None

    def _write_cache(self, key: str, data: dict) -> None:
        path = self._cache_path(key)
        try:
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            logger.warning("Cache write failed for %s: %s", key[:12], exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def clear_cache(self) -> int:
        """Delete all cached responses. Returns number of files removed."""
        count = 0
        for f in self._cache_dir.glob("*.json"):
            f.unlink(missing_ok=True)
            count += 1
        logger.info("Cleared %d cached responses.", count)
        return count

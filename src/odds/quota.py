"""Request budget guard for paid odds providers.

No request is sent unless the operator has granted a daily budget:
ODDS_REQUEST_BUDGET_<PROVIDER> (for example ODDS_REQUEST_BUDGET_THE_ODDS_API),
falling back to ODDS_REQUEST_BUDGET. Both default to 0, which means no
external odds requests at all. Every attempt, refusal and failure is written to
provider_quota_log with the provider's own remaining-quota headers. Logged
endpoints never contain credentials.

ODDS_MIN_REMAINING (default 20) also refuses a request when the provider last
reported fewer remaining requests than that, so odds collection cannot drain a
quota the fixture feeds share (API-Football's daily limit is shared).
"""
from __future__ import annotations

import json
import os
import sqlite3
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Callable

from src.odds import store

Transport = Callable[[str, dict, float], tuple[int, dict, bytes]]


class BudgetExceeded(RuntimeError):
    """The request would exceed the operator-approved budget; nothing was sent."""


class ProviderUnavailable(RuntimeError):
    """The provider failed or answered with an error; no quotes were produced."""


def daily_budget(provider: str) -> int:
    value = os.getenv(f"ODDS_REQUEST_BUDGET_{provider.upper()}") or os.getenv("ODDS_REQUEST_BUDGET") or "0"
    try:
        return max(0, int(value))
    except ValueError:
        return 0


def min_remaining() -> int:
    try:
        return max(0, int(os.getenv("ODDS_MIN_REMAINING", "20")))
    except ValueError:
        return 20


def _last_reported_remaining(conn: sqlite3.Connection, provider: str) -> int | None:
    row = conn.execute(
        """SELECT requests_remaining FROM provider_quota_log
           WHERE provider = ? AND status = 'ok' AND requests_remaining IS NOT NULL
           ORDER BY at DESC, id DESC LIMIT 1""",
        (provider,),
    ).fetchone()
    try:
        return int(float(row[0])) if row else None
    except (TypeError, ValueError):
        return None


def _urllib_transport(url: str, headers: dict, timeout: float) -> tuple[int, dict, bytes]:
    import certifi

    context = ssl.create_default_context(cafile=certifi.where())
    request = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, exc.read() or b""


def fetch_json(
    conn: sqlite3.Connection,
    provider: str,
    endpoint: str,
    url: str,
    headers: dict,
    cost: int,
    remaining_header: str,
    used_header: str | None = None,
    cost_header: str | None = None,
    transport: Transport | None = None,
    now: datetime | None = None,
):
    """Send one budgeted GET. `endpoint` is the credential-free label that gets logged."""
    moment = now or datetime.now(timezone.utc)
    used = store.requests_used_today(conn, provider, moment)
    budget = daily_budget(provider)
    remaining = _last_reported_remaining(conn, provider)
    refusal = None
    if used + cost > budget:
        refusal = f"budget: {used} used + {cost} requested > {budget} approved today"
    elif remaining is not None and remaining - cost < min_remaining():
        refusal = f"provider reports {remaining} remaining; keeping at least {min_remaining()}"
    if refusal:
        store.log_request(conn, provider, endpoint, cost, "refused", moment, detail=refusal)
        conn.commit()
        raise BudgetExceeded(refusal)

    try:
        status, response_headers, body = (transport or _urllib_transport)(url, headers, 20.0)
    except Exception as exc:  # network failure: nothing usable was received
        store.log_request(conn, provider, endpoint, cost, "error", moment, detail=type(exc).__name__)
        conn.commit()
        raise ProviderUnavailable(f"{provider} request failed: {type(exc).__name__}") from None

    actual_cost = cost
    if cost_header and response_headers.get(cost_header, "").strip().isdigit():
        actual_cost = int(response_headers[cost_header])
    remaining_value = response_headers.get(remaining_header)
    used_value = response_headers.get(used_header) if used_header else None
    if status != 200:
        store.log_request(conn, provider, endpoint, actual_cost, "error", moment, status,
                          remaining_value, used_value, detail=body[:200].decode("utf-8", "replace"))
        conn.commit()
        raise ProviderUnavailable(f"{provider} answered HTTP {status}")
    try:
        data = json.loads(body)
    except ValueError:
        store.log_request(conn, provider, endpoint, actual_cost, "error", moment, status,
                          remaining_value, used_value, detail="invalid JSON")
        conn.commit()
        raise ProviderUnavailable(f"{provider} returned invalid JSON") from None
    store.log_request(conn, provider, endpoint, actual_cost, "ok", moment, status, remaining_value, used_value)
    conn.commit()
    return data

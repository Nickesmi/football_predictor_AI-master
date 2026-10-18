"""Process-wide API-Football guard bound to the shared ledger (M3.4.1).

install(consumer) makes every request to *.api-sports.io from this process (urllib,
requests/urllib3 — including their internal retries — all go through http.client)
reserve one unit in the shared ledger (src/odds/ledger.py) BEFORE it is sent. Over the
daily budget, or with a missing/unwritable ledger, the request is refused (fail closed).
DNS lookups of api-sports.io from code paths that bypass http.client are refused.
quota.fetch_json reserves in the ledger itself and pre-approves its own request, so it is
counted exactly once. The media.api-sports.io logo CDN is not an API request.

With API_FOOTBALL_REQUIRE_LEDGER=1 (set for the whole shadow host) the shadow cycle, the
settlement tool and the API app install this guard and refuse to start without a ledger.
"""
from __future__ import annotations

import http.client
import os
import socket
import threading

_local = threading.local()
_state: dict = {}


class LedgerUnavailable(RuntimeError):
    """No usable shared ledger: API-Football requests are refused."""


class QuotaRefused(RuntimeError):
    """The shared daily budget is used up; the request was not sent."""


def required() -> bool:
    return os.getenv("API_FOOTBALL_REQUIRE_LEDGER", "").strip() == "1"


def _is_api(host) -> bool:
    h = (host or "").lower().rstrip(".") if isinstance(host, str) else ""
    return h.endswith("api-sports.io") and not h.startswith("media.")


def verify_ledger_writable() -> None:
    from src.odds import ledger

    p = ledger.path()
    if p is None:
        raise LedgerUnavailable("API_FOOTBALL_LEDGER is not set")
    try:
        c = ledger._conn(p)
        c.execute("BEGIN IMMEDIATE")
        c.rollback()
        c.close()
    except Exception as exc:  # unwritable path, read-only file system, corrupt file ...
        raise LedgerUnavailable(f"shared ledger {p} is not writable: {type(exc).__name__}: {exc}") from None


def preapprove() -> None:
    """Called by quota.fetch_json after it reserved this request in the ledger itself."""
    _local.preapproved = True


def installed() -> bool:
    return bool(_state.get("installed"))


def install(consumer: str) -> None:
    from src.odds import ledger
    from src.odds.quota import daily_budget

    verify_ledger_writable()
    _state["consumer"] = consumer
    if _state.get("installed"):
        return
    _state["installed"] = True
    original_putrequest = http.client.HTTPConnection.putrequest
    original_getaddrinfo = socket.getaddrinfo

    def putrequest(self, method, url, *args, **kwargs):
        if _is_api(self.host):
            if getattr(_local, "preapproved", False):
                _local.preapproved = False
            else:
                try:
                    ok, used = ledger.reserve("api_football", str(url).split("?")[0], 1,
                                              daily_budget("api_football"), _state["consumer"])
                except Exception as exc:
                    raise LedgerUnavailable(f"shared ledger unavailable: {type(exc).__name__}") from None
                if not ok:
                    raise QuotaRefused(f"shared daily API-Football budget used up ({used})")
            _local.permit = True
        return original_putrequest(self, method, url, *args, **kwargs)

    def getaddrinfo(host, *args, **kwargs):
        if _is_api(host):
            if not getattr(_local, "permit", False):
                raise socket.gaierror(f"api guard: {host} may only be reached through a counted request")
            _local.permit = False
        return original_getaddrinfo(host, *args, **kwargs)

    http.client.HTTPConnection.putrequest = putrequest
    socket.getaddrinfo = getaddrinfo

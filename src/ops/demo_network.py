"""Outbound network guard for the local demo: nothing external is contacted automatically.

install() (after src/ops/demo_quota.install) makes every external host unreachable from the demo
process unless a request is explicitly permitted:
  * API-Football (*.api-sports.io): left to demo_quota (counted, explicit refresh only);
  * logo images: GET paths ending in /image on SofaScore image endpoints, and media.api-sports.io;
  * a host named in `explicit(host)`, only inside that block on that thread (football-data.org refresh);
  * loopback is always allowed.
Everything else (SofaScore data, odds providers, LLM calls, scrapers) is refused before any
connection: DNS for unpermitted external names fails (covers http.client, urllib3, requests, httpx),
and curl_cffi, which resolves names itself, is refused at Session.request. Refusals are counted in
memory and reported by /api/demo/status.
"""
from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import threading
from contextlib import contextmanager
from urllib.parse import urlsplit

IMAGE_HOSTS = {"api.sofascore.com", "api.sofascore.app", "img.sofascore.com"}
IMAGE_PATH = re.compile(r"/image/?$")
_local = threading.local()
_blocked: dict[str, int] = {}
_installed: list = []


class DemoNetworkBlocked(ConnectionError):
    """The demo does not contact this external host automatically."""


def _loopback(host: str) -> bool:
    h = (host or "").strip("[]").lower().rstrip(".")
    if h in ("localhost", "testserver"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _api_football(host: str) -> bool:
    return (host or "").lower().rstrip(".").endswith("api-sports.io")


@contextmanager
def explicit(host: str):
    """Admit one external host for the duration of an explicit, already-counted request."""
    previous = getattr(_local, "explicit", None)
    _local.explicit = host.lower()
    try:
        yield
    finally:
        _local.explicit = previous


def allowed(host: str, path: str) -> bool:
    h = (host or "").lower().rstrip(".")
    if _loopback(h) or h == "media.api-sports.io" or h == getattr(_local, "explicit", None):
        return True
    return h in IMAGE_HOSTS and bool(IMAGE_PATH.search(urlsplit(path).path if "://" in path else path.split("?")[0]))


def _block(host: str) -> None:
    _blocked[host] = _blocked.get(host, 0) + 1
    raise DemoNetworkBlocked(f"demo: no automatic request to {host}")


def blocked_counts() -> dict[str, int]:
    return dict(_blocked)


def install() -> None:
    if _installed:
        return
    _installed.append(True)
    inner_putrequest = http.client.HTTPConnection.putrequest
    inner_getaddrinfo = socket.getaddrinfo

    def putrequest(self, method, url, *args, **kwargs):
        if not _api_football(self.host) and not _loopback(self.host):
            if not allowed(self.host, url):
                _block(self.host)
            _local.permit = self.host
        return inner_putrequest(self, method, url, *args, **kwargs)

    def getaddrinfo(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if isinstance(name, str) and not _loopback(name) and not _api_football(name):
            if getattr(_local, "permit", None) != name:
                _block(name)
            _local.permit = None
        return inner_getaddrinfo(host, *args, **kwargs)   # api-sports: demo_quota's own guard decides

    http.client.HTTPConnection.putrequest = putrequest
    socket.getaddrinfo = getaddrinfo
    try:
        from curl_cffi import requests as cffi
    except ImportError:
        return
    original = cffi.Session.request

    def session_request(self, method, url, *args, **kwargs):
        parts = urlsplit(url)
        if not allowed(parts.hostname or "", parts.path):
            _block(parts.hostname or url)
        return original(self, method, url, *args, **kwargs)

    cffi.Session.request = session_request

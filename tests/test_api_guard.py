"""M3.4.1: the shared ledger is the single global API-Football quota; a missing or unwritable ledger fails closed."""
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _env(tmp_path, **extra):
    base = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": str(ROOT), "APP_ENV": "development",
            "API_FOOTBALL_KEY": "", "APIFOOTBALL_API_KEY": ""}
    base.update({k: str(v) for k, v in extra.items()})
    return base


def _py(code, tmp_path, **env):
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp_path,
                          env=_env(tmp_path, **env), timeout=120)


def _shadow(tmp_path, *args, **env):
    db = tmp_path / "shadow.db"
    if not db.exists():
        sqlite3.connect(db).close()
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "shadow_cycle.py"), "--db", str(db), *args],
                          capture_output=True, text=True, cwd=tmp_path, env=_env(tmp_path, **env), timeout=180)


def test_shadow_deployment_without_ledger_fails_closed(tmp_path):
    r = _shadow(tmp_path, "--budget", "70")
    assert r.returncode == 2 and "fail closed" in r.stderr and "API_FOOTBALL_LEDGER is not set" in r.stderr


def test_shadow_deployment_with_unusable_ledger_path_fails_closed(tmp_path):
    """A ledger whose parent is not a directory is unusable for every user, root included."""
    bad = tmp_path / "not_a_dir"
    bad.write_text("x")
    r = _shadow(tmp_path, "--budget", "70", "--ledger", str(bad / "ledger.db"))
    assert r.returncode == 2 and "fail closed" in r.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions (the shadow host runs as a non-root user)")
def test_shadow_deployment_with_unwritable_ledger_fails_closed(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)
    try:
        r = _shadow(tmp_path, "--budget", "70", "--ledger", str(ro / "ledger.db"))
    finally:
        os.chmod(ro, stat.S_IRWXU)
    assert r.returncode == 2 and "not writable" in r.stderr


def test_require_flag_refuses_even_with_budget_zero(tmp_path):
    r = _shadow(tmp_path, "--budget", "0", API_FOOTBALL_REQUIRE_LEDGER=1)
    assert r.returncode == 2 and "fail closed" in r.stderr


GUARD = """
import http.client, os, sys
from src.ops import api_guard
from src.odds import ledger
api_guard.install("test")
ok = refused = 0
for i in range({n}):
    c = http.client.HTTPSConnection("v3.football.api-sports.io")
    try:
        c.putrequest("GET", f"/fixtures?i={{i}}")   # counted before sending; no connection is opened
        ok += 1
    except api_guard.QuotaRefused:
        refused += 1
print(ok, refused, ledger.used_today("api_football"))
"""


def test_direct_http_requests_and_retries_are_counted_in_the_ledger(tmp_path):
    led = tmp_path / "ledger.db"
    r = _py(GUARD.format(n=5), tmp_path, API_FOOTBALL_LEDGER=led, ODDS_REQUEST_BUDGET_API_FOOTBALL=3)
    assert r.stdout.split() == ["3", "2", "3"], r.stderr
    r2 = _py(GUARD.format(n=2), tmp_path, API_FOOTBALL_LEDGER=led, ODDS_REQUEST_BUDGET_API_FOOTBALL=3)
    assert r2.stdout.split() == ["0", "2", "3"], r2.stderr       # restart: the allowance is not reset


def test_requests_library_paths_are_blocked_not_leaked(tmp_path):
    """urllib3 (requests) opens HTTPS connections before putrequest: the DNS guard refuses them, so these
    consumers (results statistics, debug status, the old api_client) are disabled under the guard."""
    code = """
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from src.ops import api_guard
from src.odds import ledger
api_guard.install("test")
s = requests.Session()
s.mount("https://", HTTPAdapter(max_retries=Retry(total=4, connect=4, backoff_factor=0)))
try:
    s.get("https://v3.football.api-sports.io/status", timeout=2)
    print("SENT")
except requests.exceptions.ConnectionError as e:
    print("BLOCKED", "may only be reached through a counted request" in str(e))
print(ledger.used_today("api_football"))
"""
    r = _py(code, tmp_path, API_FOOTBALL_LEDGER=tmp_path / "l.db", ODDS_REQUEST_BUDGET_API_FOOTBALL=2)
    assert r.stdout.split() == ["BLOCKED", "True", "0"], r.stdout + r.stderr


def test_fetch_json_counts_once_with_the_process_guard(tmp_path):
    code = """
from src.ops import api_guard
from src.odds import ledger, quota
import sqlite3
from src.db.database import init_db
api_guard.install("test")
conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row; init_db(conn)
import http.client
def transport(url, headers, timeout):
    c = http.client.HTTPSConnection("v3.football.api-sports.io")
    c.putrequest("GET", "/status")             # what urllib does inside the real transport
    return 200, {}, b'{"errors": [], "response": []}'
quota.fetch_json(conn, "api_football", "/status", "https://v3.football.api-sports.io/status", {}, cost=1,
                 remaining_header="x", transport=transport)
print(ledger.used_today("api_football"))
"""
    r = _py(code, tmp_path, API_FOOTBALL_LEDGER=tmp_path / "l.db", ODDS_REQUEST_BUDGET_API_FOOTBALL=5)
    assert r.stdout.strip() == "1", r.stdout + r.stderr


def test_fetch_json_refuses_without_ledger_when_required(tmp_path):
    code = """
import sqlite3
from src.db.database import init_db
from src.odds import quota
conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row; init_db(conn)
try:
    quota.fetch_json(conn, "api_football", "/x", "https://h/x", {}, cost=1, remaining_header="x",
                     transport=lambda u, h, t: (200, {}, b"{}"))
    print("SENT")
except quota.BudgetExceeded as e:
    print("REFUSED", "fail closed" in str(e))
"""
    r = _py(code, tmp_path, API_FOOTBALL_REQUIRE_LEDGER=1, ODDS_REQUEST_BUDGET_API_FOOTBALL=5)
    assert r.stdout.split() == ["REFUSED", "True"], r.stderr


def test_unwritable_ledger_makes_fetch_json_fail_closed(tmp_path):
    bad = tmp_path / "not_a_dir"
    bad.write_text("x")
    code = """
import sqlite3
from src.db.database import init_db
from src.odds import quota
conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row; init_db(conn)
try:
    quota.fetch_json(conn, "api_football", "/x", "https://h/x", {}, cost=1, remaining_header="x",
                     transport=lambda u, h, t: (200, {}, b"{}"))
    print("SENT")
except quota.BudgetExceeded as e:
    print("REFUSED")
"""
    r = _py(code, tmp_path, API_FOOTBALL_LEDGER=bad / "ledger.db", ODDS_REQUEST_BUDGET_API_FOOTBALL=5)
    assert r.stdout.strip() == "REFUSED", r.stdout + r.stderr


def test_uncounted_dns_is_blocked(tmp_path):
    code = """
import socket
from src.ops import api_guard
api_guard.install("test")
try:
    socket.getaddrinfo("v3.football.api-sports.io", 443); print("LEAKED")
except socket.gaierror:
    print("BLOCKED")
"""
    r = _py(code, tmp_path, API_FOOTBALL_LEDGER=tmp_path / "l.db", ODDS_REQUEST_BUDGET_API_FOOTBALL=5)
    assert r.stdout.strip() == "BLOCKED", r.stderr


def test_api_app_refuses_to_start_without_ledger_on_the_shadow_host(tmp_path):
    work = tmp_path / "w"
    (work / ".cache").mkdir(parents=True)
    env = dict(FOOTBALL_PREDICTOR_DB_PATH=tmp_path / "copy.db", FOOTBALL_PREDICTOR_CACHE_DIR=work / ".cache",
               API_FOOTBALL_REQUIRE_LEDGER=1)
    r = subprocess.run([sys.executable, "-c", "import api.main"], cwd=work, env=_env(tmp_path, **env),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode != 0 and "LedgerUnavailable" in r.stderr
    r = subprocess.run([sys.executable, "-c", "import api.main"], cwd=work,
                       env=_env(tmp_path, API_FOOTBALL_LEDGER=tmp_path / "l.db", **env),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr

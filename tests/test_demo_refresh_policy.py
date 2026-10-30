"""Demo refresh policy (2026-10-23): cached fixtures by default, API-Football only on an explicit,
owner-authorized refresh. Every scenario runs in its own subprocess (the guard patches http.client
globally) against an isolated database and work directory, with mocked HTTPS responses parsed by
the real http.client; any real network connection raises.
"""
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
KEY = "demo-test-key-not-real"
DATE = "2030-01-05"          # future dates: the fixture service makes no SofaScore call for them
DATE2 = "2030-01-06"
DATE3 = "2030-01-07"

PRELUDE = r"""
import http.client, io, json, os, socket, sys, threading, time
from pathlib import Path
SCRIPT = json.loads(os.environ.get("FAKE_RESPONSES", "[]"))
CALLS, URLS, CFFI = [], [], []
_lock = threading.Lock()

def _no_network(*a, **k):
    raise AssertionError("a real network connection was attempted")

socket.socket.connect = _no_network          # DNS guards run first; no socket ever connects
socket.socket.connect_ex = _no_network
_inner_put = http.client.HTTPConnection.putrequest
def _record_put(self, method, url, *a, **k):
    URLS.append(self.host + url)
    return _inner_put(self, method, url, *a, **k)
http.client.HTTPConnection.putrequest = _record_put
from curl_cffi import requests as _cffi
_cffi.Session.request = lambda self, method, url, *a, **k: CFFI.append(url) or (_ for _ in ()).throw(AssertionError("curl_cffi reached"))
http.client.HTTPConnection.connect = lambda self: None
http.client.HTTPSConnection.connect = lambda self: None
http.client.HTTPConnection.send = lambda self, data: None

class _Sock:
    def __init__(self, raw): self.raw = raw
    def makefile(self, mode): return io.BytesIO(self.raw)

def _fake_getresponse(self, *a, **k):
    with _lock:
        CALLS.append(self.host)
        spec = SCRIPT[min(len(CALLS), len(SCRIPT)) - 1] if SCRIPT else {"status": 500}
    time.sleep(spec.get("delay", 0))
    if spec.get("raise"):
        raise OSError("Network is unreachable")
    body = json.dumps(spec.get("json", {"errors": [], "response": spec.get("fixtures", [])})).encode()
    head = f"HTTP/1.1 {spec['status']} X\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
    if spec.get("remaining") is not None:
        head += f"x-ratelimit-requests-remaining: {spec['remaining']}\r\n"
    r = http.client.HTTPResponse(_Sock(head.encode() + b"\r\n" + body), method="GET")
    r.begin()
    return r

http.client.HTTPConnection.getresponse = _fake_getresponse

import importlib.util
ROOT = Path(os.environ["DEMO_ROOT"])
spec = importlib.util.spec_from_file_location("demo_server", ROOT / "scripts" / "demo_server.py")
demo_server = importlib.util.module_from_spec(spec); spec.loader.exec_module(demo_server)
ARGS = json.loads(os.environ["DEMO_ARGS"])
demo_server.prepare(Path(ARGS["db"]), Path(ARGS["workdir"]), ARGS.get("cap", 0),
                    ledger=Path(ARGS["ledger"]) if ARGS.get("ledger") else None,
                    ledger_budget=ARGS.get("ledger_budget"), provider_floor=ARGS.get("floor", 30),
                    shadow_root=Path(ARGS["shadow_root"]),
                    fixture_provider=ARGS.get("fixture_provider", "api_football"))
from fastapi.testclient import TestClient
from api.main import app
from src.ops import demo_quota
client = TestClient(app)
OUT = {}
def done():
    OUT["calls"] = len(CALLS)
    OUT["urls"] = [u for u in URLS if "api-sports" in u]
    OUT["cffi"] = CFFI
    print("RESULT " + json.dumps(OUT, default=str))
"""


def _fixture(fid, date=DATE, hour=12):
    return {"fixture": {"id": fid, "date": f"{date}T{hour:02d}:00:00+00:00", "status": {"short": "NS"}},
            "league": {"id": 39, "name": "Premier League", "country": "England", "logo": "", "season": 2029},
            "teams": {"home": {"id": 40, "name": "Liverpool", "logo": ""},
                      "away": {"id": 57, "name": "Ipswich Town", "logo": ""}},
            "goals": {"home": None, "away": None}, "score": {}}


def _ok(date, remaining="95", n=2):
    return {"status": 200, "remaining": remaining, "fixtures": [_fixture(9000 + i, date) for i in range(n)]}


@pytest.fixture
def env(tmp_path):
    from src.db.database import init_db

    db = tmp_path / "copy.db"
    conn = sqlite3.connect(db)
    init_db(conn)
    conn.close()
    shadow = tmp_path / "FootballShadow"
    (shadow / "etc").mkdir(parents=True)
    (shadow / "etc" / "api_key.env").write_text("API_FOOTBALL_KEY=\n")
    return {"tmp": tmp_path, "db": db, "work": tmp_path / "w", "shadow": shadow}


def _run(env, body, responses=(), **args):
    a = {"db": str(env["db"]), "workdir": str(env["work"]), "shadow_root": str(env["shadow"]), **args}
    r = subprocess.run([sys.executable, "-c", PRELUDE + body + "\ndone()\n"], cwd=ROOT, timeout=180,
                       capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(env["tmp"]), "PYTHONPATH": str(ROOT),
                            "DEMO_ROOT": str(ROOT), "DEMO_ARGS": json.dumps(a), "FAKE_RESPONSES": json.dumps(list(responses)),
                            "APIFOOTBALL_API_KEY": KEY, "API_FOOTBALL_KEY": KEY,
                            "FOOTBALL_DATA_TOKEN": args.get("fd_token", "fd-test-token-not-real")})
    lines = [line for line in r.stdout.splitlines() if line.startswith("RESULT ")]
    assert r.returncode == 0 and lines, r.stdout[-3000:] + r.stderr[-3000:]
    return json.loads(lines[-1][7:])


def _usage(env):
    return sqlite3.connect(env["work"] / "demo_api_usage.db")


def _seed_exhausted_legacy(env, refused=1):
    """The original demo's state: 5 of 5 sent, then refusals."""
    env["work"].mkdir(parents=True, exist_ok=True)
    conn = _usage(env)
    conn.execute("CREATE TABLE demo_api_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, "
                 "host TEXT NOT NULL, path TEXT NOT NULL, allowed INTEGER NOT NULL)")
    conn.execute("CREATE TABLE demo_fetched_dates (date TEXT PRIMARY KEY, at TEXT NOT NULL)")
    rows = [(f"2026-10-{17 + i}T20:30:00+00:00", "v3.football.api-sports.io", f"/fixtures?date=2026-10-{18 + i}", 1)
            for i in range(5)] + [("2026-10-22T20:30:36+00:00", "v3.football.api-sports.io",
                                   "/fixtures?date=2026-10-23", 0)] * refused
    conn.executemany("INSERT INTO demo_api_usage (at, host, path, allowed) VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def _legacy_digest(env):
    rows = _usage(env).execute("SELECT * FROM demo_api_usage ORDER BY id").fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def _cache(env, date, n=3):
    cache = env["work"] / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    fixtures = [{"id": f"c{i}", "date": date, "time": "15:30", "provider": "api_football", "source": "api_football",
                 "status": "NS", "home_team": {"name": "Liverpool"}, "away_team": {"name": "Ipswich Town"},
                 "league": {"name": "Premier League"}} for i in range(n)]
    (cache / f"fixtures-{date}.json").write_text(json.dumps(fixtures))


def _grant(env, total, daily, days=None):
    args = [sys.executable, str(ROOT / "scripts" / "demo_allowance.py"), "--usage-db",
            str(env["work"] / "demo_api_usage.db"), "grant", "--total", str(total), "--daily", str(daily),
            "--note", "test grant", "--i-authorize-api-football-requests"]
    if days:
        args += ["--days", str(days)]
    r = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["granted"]


# ── Exhausted allowance, cache, page loads ───────────────────────────────────

def test_exhausted_allowance_still_serves_cached_fixtures_and_sends_nothing(env):
    _seed_exhausted_legacy(env)
    _cache(env, DATE)
    before = _legacy_digest(env)
    out = _run(env, f"""
r = client.get("/api/fixtures/{DATE}").json()
OUT["n"] = len(r["fixtures"]); OUT["demo"] = r["demo"]
s = client.get("/api/demo/status").json(); OUT["status"] = s
p = client.post("/api/demo/refresh-fixtures/{DATE2}"); OUT["post"] = [p.status_code, p.json()]
""", cap=5)
    assert out["n"] == 3 and out["demo"]["state"] == "cached" and out["demo"]["live"] is False
    assert out["demo"]["last_updated_at"] is None and out["demo"]["data_source"] == "cache"   # no recorded fetch
    assert out["status"]["legacy"] == {"cap": 5, "used": 5, "refused": 1, "remaining": 0}
    assert out["status"]["can_refresh"] is False and out["status"]["remaining_total"] == 0
    assert any("no owner-authorized allowance" in r for r in out["status"]["blocked_reasons"])
    assert out["post"][0] == 403 and out["post"][1]["detail"]["error"] == "no_allowance"
    assert out["calls"] == 0
    assert _legacy_digest(env) == before                    # the 5/5 record is untouched


def test_last_update_comes_from_a_recorded_successful_fetch_never_from_file_times(env):
    _seed_exhausted_legacy(env)
    conn = _usage(env)
    conn.execute("INSERT INTO demo_api_usage (at, host, path, allowed) VALUES (?, ?, ?, 1)",
                 ("2026-10-21T20:30:46+00:00", "v3.football.api-sports.io", f"/fixtures?date={DATE}&timezone=Asia/Tehran"))
    conn.executemany("INSERT INTO demo_fetched_dates VALUES (?, ?)",
                     [(DATE, "2026-10-21T20:30:46+00:00"), (DATE2, "2026-10-22T20:30:36+00:00")])   # DATE2: refused
    conn.commit()
    conn.close()
    _cache(env, DATE)
    _cache(env, DATE2)
    out = _run(env, f"""
OUT["a"] = client.get("/api/fixtures/{DATE}").json()["demo"]["last_updated_at"]
OUT["b"] = client.get("/api/fixtures/{DATE2}").json()["demo"]["last_updated_at"]
""", cap=5)
    assert out["a"] == "2026-10-21T20:30:46+00:00" and out["b"] is None and out["calls"] == 0


def test_missing_cache_shows_a_message_and_sends_nothing(env):
    out = _run(env, f"""
r = client.get("/api/fixtures/{DATE3}").json()
OUT["r"] = r
""")
    assert out["r"]["fixtures"] == [] and "No cached fixtures" in out["r"]["message"]
    assert "unavailable" not in out["r"]["message"] and out["r"]["demo"]["state"] == "quota_exhausted"
    assert out["calls"] == 0


def test_repeated_reloads_force_refresh_and_tab_returns_never_send(env):
    env["work"].mkdir(parents=True, exist_ok=True)
    sqlite3.connect(env["work"] / "demo_api_usage.db").close()
    _grant(env, 5, 5)
    _cache(env, DATE)
    out = _run(env, f"""
for i in range(10):
    client.get("/api/fixtures/{DATE}")
    client.get("/api/fixtures/{DATE3}?force_refresh=true&t=" + str(i))
    client.get("/api/demo/status")
    client.get("/api/demo/fixture-cache/{DATE3}")
OUT["status"] = client.get("/api/demo/status").json()
""", [_ok(DATE3)])
    assert out["calls"] == 0 and out["status"]["remaining_total"] == 5
    assert _usage(env).execute("SELECT COUNT(*) FROM demo_requests").fetchone()[0] == 0


def test_any_other_code_path_is_refused_even_with_an_allowance(env):
    env["work"].mkdir(parents=True, exist_ok=True)
    sqlite3.connect(env["work"] / "demo_api_usage.db").close()
    _grant(env, 5, 5)
    out = _run(env, """
import urllib.request
try:
    urllib.request.urlopen("https://v3.football.api-sports.io/odds?fixture=1", timeout=5)
    OUT["sent"] = True
except Exception as e:
    OUT["error"] = type(e).__name__ + ": " + str(e)
try:
    socket.getaddrinfo("v3.football.api-sports.io", 443)
    OUT["dns"] = "leaked"
except socket.gaierror:
    OUT["dns"] = "blocked"
OUT["status"] = demo_quota.status()
""", [_ok(DATE)])
    assert out["calls"] == 0 and "sent" not in out and "explicit" in out["error"] and out["dns"] == "blocked"
    assert out["status"]["remaining_total"] == 5
    assert _usage(env).execute("SELECT reason FROM demo_requests").fetchall() == [("not an explicit user action",)]


# ── Authorized refresh ───────────────────────────────────────────────────────

def test_authorized_refresh_sends_once_caches_and_never_repeats_a_cached_date(env):
    _seed_exhausted_legacy(env)
    before = _legacy_digest(env)
    gid = _grant(env, 3, 2)
    out = _run(env, f"""
OUT["pre"] = client.get("/api/demo/status").json()
p = client.post("/api/demo/refresh-fixtures/{DATE}"); OUT["post"] = [p.status_code, p.json()]
OUT["get"] = client.get("/api/fixtures/{DATE}").json()
again = client.post("/api/demo/refresh-fixtures/{DATE}"); OUT["again"] = [again.status_code, again.json()]
""", [_ok(DATE, remaining="91")], cap=5)
    assert out["pre"]["can_refresh"] is True and out["pre"]["remaining_total"] == 3 and out["pre"]["cost_per_refresh"] == 1
    assert out["post"][0] == 200 and out["post"][1]["state"] == "fresh" and out["post"][1]["cache"]["cached_fixtures"] == 2
    assert out["post"][1]["quota"]["remaining_total"] == 2 and out["post"][1]["quota"]["remaining_today"] == 1
    assert out["post"][1]["quota"]["provider_remaining_today"] == 91
    assert len(out["get"]["fixtures"]) == 2 and out["get"]["demo"]["state"] == "cached"
    assert out["get"]["demo"]["last_updated_at"] == out["post"][1]["cache"]["last_updated_at"] is not None
    assert out["again"][0] == 409 and out["again"][1]["detail"]["error"] == "already_fetched"
    assert out["calls"] == 1
    rows = _usage(env).execute("SELECT grant_id, allowed, http_status, provider_remaining, outcome FROM demo_requests"
                               ).fetchall()
    assert rows == [(gid, 1, 200, 91, "ok")]
    assert _legacy_digest(env) == before


def test_daily_limit_resets_at_utc_midnight_and_total_holds(env):
    env["work"].mkdir(parents=True, exist_ok=True)
    sqlite3.connect(env["work"] / "demo_api_usage.db").close()
    _grant(env, 2, 1)
    out = _run(env, f"""
from datetime import datetime, timezone
t = [datetime(2026, 10, 23, 23, 59, 30, tzinfo=timezone.utc)]
demo_quota._state["clock"] = lambda: t[0]
OUT["a"] = client.post("/api/demo/refresh-fixtures/{DATE}").status_code
b = client.post("/api/demo/refresh-fixtures/{DATE2}"); OUT["b"] = [b.status_code, b.json()["detail"].get("reasons")]
t[0] = datetime(2026, 10, 24, 0, 0, 5, tzinfo=timezone.utc)
OUT["c"] = client.post("/api/demo/refresh-fixtures/{DATE2}").status_code
d = client.post("/api/demo/refresh-fixtures/{DATE3}"); OUT["d"] = [d.status_code, d.json()["detail"].get("reasons")]
""", [_ok(DATE), _ok(DATE2)])
    assert out["a"] == 200 and out["b"][0] == 403 and "daily limit 1 reached" in out["b"][1][0]
    assert out["c"] == 200                                   # new UTC day
    assert out["d"][0] == 403 and "authorized allowance used up" in out["d"][1][0]
    assert out["calls"] == 2


def test_usage_persists_across_restarts(env):
    env["work"].mkdir(parents=True, exist_ok=True)
    sqlite3.connect(env["work"] / "demo_api_usage.db").close()
    _grant(env, 2, 2)
    first = _run(env, f'OUT["s"] = client.post("/api/demo/refresh-fixtures/{DATE}").status_code', [_ok(DATE)])
    second = _run(env, f"""
OUT["before"] = client.get("/api/demo/status").json()["remaining_total"]
OUT["s"] = client.post("/api/demo/refresh-fixtures/{DATE2}").status_code
OUT["after"] = client.get("/api/demo/status").json()["remaining_total"]
OUT["third"] = client.post("/api/demo/refresh-fixtures/{DATE3}").status_code
""", [_ok(DATE2)])
    assert first["s"] == 200 and second["before"] == 1 and second["s"] == 200 and second["after"] == 0
    assert second["third"] == 403 and first["calls"] + second["calls"] == 2


# ── Concurrency ──────────────────────────────────────────────────────────────

def test_concurrent_refreshes_of_one_date_send_once(env):
    env["work"].mkdir(parents=True, exist_ok=True)
    sqlite3.connect(env["work"] / "demo_api_usage.db").close()
    _grant(env, 5, 5)
    out = _run(env, f"""
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(6) as ex:
    codes = list(ex.map(lambda _: client.post("/api/demo/refresh-fixtures/{DATE}").status_code, range(6)))
OUT["codes"] = sorted(codes)
OUT["left"] = client.get("/api/demo/status").json()["remaining_total"]
""", [{**_ok(DATE), "delay": 0.5}])
    assert out["codes"].count(200) == 1 and set(out["codes"]) <= {200, 409}
    assert out["calls"] == 1 and out["left"] == 4


GRAB = r"""
import sys, json
from pathlib import Path
from src.ops import demo_quota
demo_quota.install(Path(sys.argv[1]), 0)
ok = sum(demo_quota._reserve("v3.football.api-sports.io", f"/fixtures?p={i}") for i in range(int(sys.argv[2])))
print(ok)
"""


def test_several_processes_cannot_exceed_one_allowance(env):
    env["work"].mkdir(parents=True, exist_ok=True)
    usage = env["work"] / "demo_api_usage.db"
    sqlite3.connect(usage).close()
    _grant(env, 3, 3)
    procs = [subprocess.Popen([sys.executable, "-c", GRAB, str(usage), "4"], cwd=ROOT, stdout=subprocess.PIPE,
                              text=True, env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT)}) for _ in range(4)]
    total = sum(int(p.communicate(timeout=120)[0].strip()) for p in procs)
    assert total == 3
    assert _usage(env).execute("SELECT COUNT(*) FROM demo_requests WHERE allowed = 1").fetchone()[0] == 3


# ── Failures ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("response, outcome, status, code, state", [
    ({"status": 500}, "http_error", 500, 502, "provider_error"),
    ({"status": 429, "remaining": "0"}, "http_error", 429, 429, "quota_exhausted"),
    ({"raise": True}, "error", None, 502, "provider_error"),
])
def test_failures_are_counted_not_cached_and_retryable_only_with_allowance(env, response, outcome, status, code, state):
    env["work"].mkdir(parents=True, exist_ok=True)
    sqlite3.connect(env["work"] / "demo_api_usage.db").close()
    _grant(env, 3, 3)
    out = _run(env, f"""
p = client.post("/api/demo/refresh-fixtures/{DATE}"); OUT["p"] = [p.status_code, p.json()]
OUT["get"] = client.get("/api/fixtures/{DATE}").json()
OUT["status"] = client.get("/api/demo/status").json()
""", [response])
    assert out["p"][0] == code and out["p"][1]["detail"]["ok"] is False and out["p"][1]["detail"]["error"]
    assert out["p"][1]["detail"]["state"] == state and out["get"]["demo"]["state"] == (
        "quota_exhausted" if status == 429 else "provider_error")             # the failure is shown, not hidden
    assert out["get"]["fixtures"] == [] and out["calls"] == 1
    assert out["status"]["remaining_total"] == 2                       # the failed request was counted
    rows = _usage(env).execute("SELECT allowed, http_status, outcome FROM demo_requests").fetchall()
    assert rows == [(1, status, outcome)]
    assert _usage(env).execute("SELECT COUNT(*) FROM demo_fetched_dates").fetchone()[0] == 0   # not marked fetched
    if status == 429:                                                  # provider says 0 left: blocked by the floor
        assert out["status"]["can_refresh"] is False
        assert any("provider reports 0" in r for r in out["status"]["blocked_reasons"])


def test_one_refresh_can_never_send_more_than_one_request(env):
    env["work"].mkdir(parents=True, exist_ok=True)
    sqlite3.connect(env["work"] / "demo_api_usage.db").close()
    _grant(env, 5, 5)
    out = _run(env, """
import urllib.request
res = []
with demo_quota.explicit(max_requests=1):
    for i in range(3):
        try:
            urllib.request.urlopen(f"https://v3.football.api-sports.io/fixtures?date=2030-01-0{i + 5}", timeout=5)
            res.append("sent")
        except Exception as e:
            res.append(type(e).__name__)
OUT["res"] = res
""", [_ok(DATE)] * 3)
    assert out["res"] == ["sent", "DemoQuotaExceeded", "DemoQuotaExceeded"] and out["calls"] == 1


# ── Shared quota ─────────────────────────────────────────────────────────────

def test_same_key_as_the_shadow_requires_the_shared_ledger(env):
    env["work"].mkdir(parents=True, exist_ok=True)
    sqlite3.connect(env["work"] / "demo_api_usage.db").close()
    _grant(env, 5, 5)
    (env["shadow"] / "etc" / "api_key.env").write_text(f"API_FOOTBALL_KEY={KEY}\n")
    out = _run(env, f"""
s = client.get("/api/demo/status").json(); OUT["s"] = s
OUT["p"] = client.post("/api/demo/refresh-fixtures/{DATE}").status_code
""", [_ok(DATE)])
    assert out["s"]["same_key_as_shadow"] is True and out["s"]["can_refresh"] is False
    assert any("shared ledger is required" in r for r in out["s"]["blocked_reasons"])
    assert out["p"] == 403 and out["calls"] == 0
    (env["shadow"] / "etc" / "api_key.env").write_text("API_FOOTBALL_KEY=a-different-key\n")
    other = _run(env, 'OUT["s"] = client.get("/api/demo/status").json()')
    assert other["s"]["same_key_as_shadow"] is False and other["s"]["can_refresh"] is True


@pytest.mark.parametrize("used_by_shadow, expect", [(70, 429), (69, 200)])
def test_shared_ledger_daily_total_of_all_consumers_holds(env, used_by_shadow, expect):
    from datetime import datetime, timezone

    env["work"].mkdir(parents=True, exist_ok=True)
    sqlite3.connect(env["work"] / "demo_api_usage.db").close()
    _grant(env, 5, 5)
    (env["shadow"] / "etc" / "api_key.env").write_text(f"API_FOOTBALL_KEY={KEY}\n")
    led = env["tmp"] / "api_football_ledger.db"
    import os

    os.environ["API_FOOTBALL_LEDGER"] = str(led)
    try:
        from src.odds import ledger

        now = datetime.now(timezone.utc)
        for i in range(used_by_shadow):
            ledger.reserve("api_football", "/odds", 1, 70, "shadow_cycle", now)
    finally:
        os.environ.pop("API_FOOTBALL_LEDGER")
    out = _run(env, f'OUT["p"] = client.post("/api/demo/refresh-fixtures/{DATE}").status_code', [_ok(DATE)],
               ledger=str(led), ledger_budget=70)
    assert out["p"] == expect
    reserved = dict(sqlite3.connect(led).execute(
        "SELECT consumer, SUM(cost) FROM ledger WHERE status = 'reserved' GROUP BY 1").fetchall())
    assert sum(reserved.values()) <= 70                          # never above the shadow's ceiling
    if expect == 429:
        assert out["calls"] == 0 and "demo" not in reserved
        assert "shared ledger refused" in _usage(env).execute("SELECT reason FROM demo_requests").fetchone()[0]
    else:
        assert out["calls"] == 1 and reserved == {"shadow_cycle": 69, "demo": 1}


def test_provider_floor_blocks_before_sending(env):
    env["work"].mkdir(parents=True, exist_ok=True)
    sqlite3.connect(env["work"] / "demo_api_usage.db").close()
    _grant(env, 5, 5)
    out = _run(env, f"""
OUT["a"] = client.post("/api/demo/refresh-fixtures/{DATE}").status_code
OUT["s"] = client.get("/api/demo/status").json()
OUT["b"] = client.post("/api/demo/refresh-fixtures/{DATE2}").status_code
""", [_ok(DATE, remaining="30")])
    assert out["a"] == 200 and out["s"]["provider_remaining_today"] == 30 and out["s"]["can_refresh"] is False
    assert out["b"] == 403 and out["calls"] == 1


# ── Owner control ────────────────────────────────────────────────────────────

def test_grants_need_explicit_confirmation_limits_and_can_be_revoked(env):
    _seed_exhausted_legacy(env)
    before = _legacy_digest(env)
    usage = str(env["work"] / "demo_api_usage.db")
    cli = [sys.executable, str(ROOT / "scripts" / "demo_allowance.py"), "--usage-db", usage]

    def run(*a):
        return subprocess.run(cli + list(a), capture_output=True, text=True, cwd=ROOT, timeout=60)

    assert "i-authorize" in run("grant", "--total", "5", "--daily", "2", "--note", "x").stderr
    assert run("grant", "--total", "500", "--daily", "2", "--note", "x",
               "--i-authorize-api-football-requests").returncode == 2
    assert run("grant", "--total", "5", "--daily", "50", "--note", "x",
               "--i-authorize-api-football-requests").returncode == 2
    assert run("grant", "--total", "5", "--daily", "2", "--note", " ",
               "--i-authorize-api-football-requests").returncode == 2
    status = json.loads(run("status").stdout)
    assert status["remaining_total"] == 0 and status["legacy"]["used"] == 5        # default: nothing new
    ok = run("grant", "--total", "5", "--daily", "2", "--days", "14", "--note", "owner test",
             "--i-authorize-api-football-requests")
    gid = json.loads(ok.stdout)["granted"]
    assert json.loads(run("status").stdout)["remaining_total"] == 5
    assert json.loads(run("revoke", "--id", str(gid)).stdout) == {"revoked": gid}
    assert json.loads(run("status").stdout)["remaining_total"] == 0
    rows = _usage(env).execute("SELECT total, daily_limit, note, revoked_at IS NOT NULL FROM demo_allowance_grants"
                               ).fetchall()
    assert rows == [(5, 2, "owner test", 1)]                                      # kept, marked revoked
    assert _legacy_digest(env) == before
    assert subprocess.run(cli[:2] + ["--usage-db", str(env["tmp"] / "missing.db"), "status"], capture_output=True,
                          text=True, cwd=ROOT).returncode == 2


def test_demo_refuses_the_shadow_experiments_database(env):
    shadow_db = env["shadow"] / "run" / "engine_shadow.db"
    shadow_db.parent.mkdir(parents=True)
    sqlite3.connect(shadow_db).close()
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "demo_server.py"), "--db", str(shadow_db), "--workdir",
                        str(env["work"]), "--shadow-root", str(env["shadow"])], capture_output=True, text=True,
                       timeout=60, cwd=ROOT)
    assert r.returncode != 0 and "shadow experiment" in r.stderr

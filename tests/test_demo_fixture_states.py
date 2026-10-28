"""Regression tests (2026-10-23): fixture states, the October 23 failure, Europe/Istanbul dates with UTC
kick-offs, repeated page loads across midnight, and the demo's outbound network guard.
Harness, mocked API-Football and isolation: tests/test_demo_refresh_policy.py."""
import json
import sqlite3
from datetime import datetime, timezone

from api import fixture_time as ft
from tests.test_demo_refresh_policy import _grant, _run, _seed_exhausted_legacy, _usage, env  # noqa: F401

D = "2030-01-05"


def _store(env, storage_date, fixtures):
    cache = env["work"] / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"fixtures-{storage_date}.json").write_text(json.dumps(fixtures))


def _legacy(storage_date, times, prefix):
    """Fixtures as the old code stored them: local date/time strings, no UTC."""
    return [{"id": f"{prefix}{i}", "date": storage_date, "time": t, "provider": "api_football",
             "source": "api_football", "status": "NS", "home_team": {"name": f"H{i}"}, "away_team": {"name": f"A{i}"},
             "league": {"name": "L"}} for i, t in enumerate(times)]


TEHRAN_DAY = [f"{h:02d}:30" for h in range(1, 23)] + ["00:15", "23:45"]          # 24 fixtures, decisive ':30'
ISTANBUL_DAY = [f"{h:02d}:00" for h in range(1, 23)] + ["23:30", "00:10"]        # 24 fixtures, decisive ':00'


# ── Time conversion (pure) ───────────────────────────────────────────────────

def test_today_is_explicitly_istanbul_not_tehran():
    t = datetime(2026, 10, 22, 20, 30, 36, tzinfo=timezone.utc)                    # the October 23 moment
    assert ft.today("Europe/Istanbul", t) == "2026-10-22"                           # 23:30 in Istanbul
    assert ft.today("Asia/Tehran", t) == "2026-10-23"                               # 00:00 in Tehran: the old switch
    assert ft.today("Europe/Istanbul", datetime(2026, 10, 22, 21, 0, tzinfo=timezone.utc)) == "2026-10-23"
    assert ft.day_window("2026-10-23", "Europe/Istanbul") == (
        datetime(2026, 10, 22, 21, 0, tzinfo=timezone.utc), datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc))


def test_basis_is_inferred_only_when_decisive():
    assert ft.infer_basis(_legacy(D, TEHRAN_DAY, "t"), D) == "Asia/Tehran"
    assert ft.infer_basis(_legacy(D, ISTANBUL_DAY, "i"), D) == "Europe/Istanbul"
    assert ft.infer_basis(_legacy(D, ["15:30"] * 5, "s"), D) is None                 # too few
    assert ft.infer_basis(_legacy(D, ["15:30", "15:00"] * 15, "m"), D) is None       # no 2:1 majority


def test_day_view_moves_kickoffs_across_midnight_by_their_utc_instant():
    sources = {
        "2030-01-04": [],
        D: _legacy(D, TEHRAN_DAY, "t"),
        "2030-01-06": _legacy("2030-01-06", TEHRAN_DAY, "n") + [
            {"id": "new1", "date": "2030-01-06", "time": "00:40", "provider": "api_football",
             "kickoff_utc": "2030-01-05T21:10:00Z"}],
    }
    view = {f["id"]: f for f in ft.day_view(D, "Europe/Istanbul", sources)}
    assert "t22" not in view                       # Tehran 01-05 00:15 = 01-04 20:45 UTC = Istanbul 01-04 23:45
    assert view["t23"]["time"] == "23:15" and view["t23"]["kickoff_utc"] == "2030-01-05T20:15:00Z"  # Tehran 23:45
    assert view["n22"]["date"] == D and view["n22"]["time"] == "23:45"   # Tehran 01-06 00:15 -> Istanbul 01-05 23:45
    assert view["t0"]["time"] == "01:00" and view["t0"]["time_basis"] == "inferred:Asia/Tehran"
    assert "new1" not in view                      # 21:10 UTC = Istanbul 01-06 00:10: next day
    nxt = {f["id"]: f for f in ft.day_view("2030-01-06", "Europe/Istanbul", sources)}
    assert nxt["new1"]["time"] == "00:10" and nxt["new1"]["time_basis"] == "utc"
    assert all(f["display_timezone"] == "Europe/Istanbul" for f in view.values())


def test_istanbul_basis_files_keep_their_times_and_unknown_basis_is_never_moved():
    view = {f["id"]: f for f in ft.day_view(D, "Europe/Istanbul", {D: _legacy(D, ISTANBUL_DAY, "i")})}
    assert view["i22"]["time"] == "23:30" and view["i23"]["time"] == "00:10" and len(view) == 24
    few = ft.day_view(D, "Europe/Istanbul", {D: _legacy(D, ["00:15", "15:30", "23:45"], "u"),
                                             "2030-01-06": _legacy("2030-01-06", ["00:05"], "x")})
    assert [(f["id"], f["time"], f["time_basis"]) for f in few] == [
        ("u0", "00:15", "unknown"), ("u1", "15:30", "unknown"), ("u2", "23:45", "unknown")]   # shown as stored


# ── The October 23 failure ───────────────────────────────────────────────────

def _seed_october(env):
    _seed_exhausted_legacy(env)                                   # 5 sent (10-18..10-22) + row 6 refused (10-23)
    conn = _usage(env)
    conn.executemany("INSERT INTO demo_fetched_dates VALUES (?, ?)",
                     [(f"2026-10-{d}", f"2026-10-{d - 1}T20:30:00+00:00") for d in range(18, 24)])   # incl. bogus 10-23
    conn.commit()
    conn.close()
    _store(env, "2026-10-21", _legacy("2026-10-21", TEHRAN_DAY, "a"))
    _store(env, "2026-10-22", _legacy("2026-10-22", TEHRAN_DAY, "b"))


CLOCK = """
from datetime import datetime, timezone
t = [datetime(2026, 10, 22, 20, 30, 36, tzinfo=timezone.utc)]
demo_quota._state["clock"] = lambda: t[0]
"""


def test_october_23_failure_cached_days_stay_and_the_refused_day_is_not_fetched(env):
    _seed_october(env)
    out = _run(env, CLOCK + """
OUT["status"] = client.get("/api/demo/status").json()
r22 = client.get("/api/fixtures/2026-10-22").json(); OUT["r22"] = [len(r22["fixtures"]), r22["demo"]["state"]]
r23 = client.get("/api/fixtures/2026-10-23").json(); OUT["r23"] = [len(r23["fixtures"]), r23["demo"]["state"], r23.get("message")]
OUT["cache23"] = client.get("/api/demo/fixture-cache/2026-10-23").json()
p = client.post("/api/demo/refresh-fixtures/2026-10-23"); OUT["p"] = [p.status_code, p.json()["detail"]["state"]]
""", cap=5)
    assert out["status"]["today"] == "2026-10-22" and out["status"]["display_timezone"] == "Europe/Istanbul"
    assert out["r22"] == [23, "cached"]       # served, not replaced by an error (Tehran 00:15 = Istanbul 10-21 23:45)
    assert out["r23"][:2] == [0, "quota_exhausted"] and "Cached dates stay available" in out["r23"][2]
    assert "unavailable" not in out["r23"][2]
    assert out["cache23"]["fetched"] is False                 # the refused attempt never counts as a fetch
    assert out["p"] == [403, "quota_exhausted"] and out["calls"] == 0 and out["urls"] == []
    assert _usage(env).execute("SELECT COUNT(*) FROM demo_api_usage").fetchone()[0] == 6   # record intact


def test_october_23_with_an_owner_grant_fetches_the_istanbul_day_once(env):
    _seed_october(env)
    gid = _grant(env, 2, 2)
    fixtures = [{"fixture": {"id": 500 + i, "date": f"2026-10-23T{h:02d}:00:00+00:00", "status": {"short": "NS"}},
                 "league": {"id": 39, "name": "Premier League", "country": "England", "logo": "", "season": 2026},
                 "teams": {"home": {"id": 1, "name": "A", "logo": ""}, "away": {"id": 2, "name": "B", "logo": ""}},
                 "goals": {"home": None, "away": None}, "score": {}} for i, h in enumerate((11, 20))]
    out = _run(env, CLOCK + """
p = client.post("/api/demo/refresh-fixtures/2026-10-23"); OUT["p"] = [p.status_code, p.json()["state"]]
r = client.get("/api/fixtures/2026-10-23").json()
OUT["r"] = [(f["id"], f["time"], f["kickoff_utc"], f["time_basis"]) for f in r["fixtures"]]
OUT["state"] = r["demo"]["state"]
""", [{"status": 200, "remaining": "90", "fixtures": fixtures}], cap=5)
    assert out["p"] == [200, "fresh"] and out["calls"] == 1
    assert out["urls"] == ["v3.football.api-sports.io/fixtures?date=2026-10-23&timezone=Europe/Istanbul"]
    assert out["r"] == [["500", "14:00", "2026-10-23T11:00:00Z", "utc"], ["501", "23:00", "2026-10-23T20:00:00Z", "utc"]]
    assert out["state"] == "cached"
    assert _usage(env).execute("SELECT grant_id, outcome FROM demo_requests").fetchall() == [(gid, "ok")]
    assert _usage(env).execute("SELECT COUNT(*) FROM demo_api_usage").fetchone()[0] == 6


# ── Page loads across midnight ───────────────────────────────────────────────

def test_repeated_page_loads_across_istanbul_and_tehran_midnight_never_send(env):
    _seed_october(env)
    _grant(env, 5, 5)                                         # even with an allowance available
    out = _run(env, """
from datetime import datetime, timedelta, timezone
t = [datetime(2026, 10, 22, 20, 0, tzinfo=timezone.utc)]
demo_quota._state["clock"] = lambda: t[0]
days = []
while t[0] <= datetime(2026, 10, 22, 22, 0, tzinfo=timezone.utc):
    today = client.get("/api/demo/status").json()["today"]
    days.append(today)
    for _ in range(3):
        client.get(f"/api/fixtures/{today}")
        client.get(f"/api/fixtures/{today}?force_refresh=true")
    t[0] += timedelta(minutes=10)
OUT["days"] = sorted(set(days)); OUT["first_23"] = days.index("2026-10-23")
""")
    assert out["days"] == ["2026-10-22", "2026-10-23"] and out["first_23"] == 6   # switches at 21:00 UTC, not 20:30
    assert out["calls"] == 0 and out["urls"] == []
    assert _usage(env).execute("SELECT COUNT(*) FROM demo_requests").fetchone()[0] == 0


# ── States ───────────────────────────────────────────────────────────────────

def test_no_fixtures_scheduled_is_a_successful_fetch_and_is_not_requested_again(env):
    env["work"].mkdir(parents=True, exist_ok=True)
    sqlite3.connect(env["work"] / "demo_api_usage.db").close()
    _grant(env, 3, 3)
    out = _run(env, f"""
p = client.post("/api/demo/refresh-fixtures/{D}"); OUT["p"] = [p.status_code, p.json()["state"]]
r = client.get("/api/fixtures/{D}").json(); OUT["r"] = [r["fixtures"], r["demo"]["state"], r["message"]]
again = client.post("/api/demo/refresh-fixtures/{D}"); OUT["again"] = [again.status_code, again.json()["detail"]["state"]]
""", [{"status": 200, "remaining": "90", "fixtures": []}])
    assert out["p"] == [200, "no_fixtures"] and out["r"][:2] == [[], "no_fixtures"]
    assert "No fixtures are scheduled" in out["r"][2] and out["again"] == [409, "no_fixtures"] and out["calls"] == 1


def test_provider_request_limit_in_the_body_is_quota_exhausted_not_a_fetch(env):
    env["work"].mkdir(parents=True, exist_ok=True)
    sqlite3.connect(env["work"] / "demo_api_usage.db").close()
    _grant(env, 3, 3)
    body = {"errors": {"requests": "You have reached the request limit for the day"}, "response": []}
    out = _run(env, f"""
p = client.post("/api/demo/refresh-fixtures/{D}"); OUT["p"] = [p.status_code, p.json()["detail"]["state"]]
OUT["cache"] = client.get("/api/demo/fixture-cache/{D}").json()
""", [{"status": 200, "remaining": "0", "json": body}])
    assert out["p"] == [429, "quota_exhausted"]
    assert out["cache"]["fetched"] is False and out["cache"]["state"] == "quota_exhausted"
    assert _usage(env).execute("SELECT COUNT(*) FROM demo_fetched_dates").fetchone()[0] == 0


def test_a_failed_refresh_never_replaces_cached_fixtures(env):
    env["work"].mkdir(parents=True, exist_ok=True)
    sqlite3.connect(env["work"] / "demo_api_usage.db").close()
    _grant(env, 3, 3)
    _store(env, D, _legacy(D, ISTANBUL_DAY, "i"))
    out = _run(env, f"""
before = client.get("/api/fixtures/{D}").json()["fixtures"]
p = client.post("/api/demo/refresh-fixtures/2030-01-06"); OUT["p"] = p.status_code
same = client.post("/api/demo/refresh-fixtures/{D}"); OUT["same"] = same.status_code
after = client.get("/api/fixtures/{D}").json()
OUT["equal"] = [(f["id"], f["time"]) for f in before] == [(f["id"], f["time"]) for f in after["fixtures"]]
OUT["n"] = len(after["fixtures"]); OUT["state"] = after["demo"]["state"]
""", [{"status": 503}])
    assert out["p"] == 502 and out["same"] == 409 and out["equal"] and out["n"] == 24 and out["state"] == "cached"
    assert out["calls"] == 1


# ── Network guard ────────────────────────────────────────────────────────────

def test_no_automatic_external_request_leaves_the_demo(env):
    out = _run(env, """
import urllib.request
from src.ops import demo_network
res = {}
for name, url in [("sofascore_data", "https://api.sofascore.com/api/v1/sport/football/scheduled-events/2030-01-05"),
                  ("odds_api", "https://api.the-odds-api.com/v4/sports"), ("openai", "https://api.openai.com/v1/models")]:
    try:
        urllib.request.urlopen(url, timeout=5); res[name] = "sent"
    except Exception as e:
        res[name] = type(e).__name__
try:
    import httpx
    httpx.get("https://example.org/", timeout=5); res["httpx"] = "sent"
except Exception as e:
    res["httpx"] = type(e).__name__
from curl_cffi import requests as cffi
try:
    cffi.get("https://api.sofascore.com/api/v1/event/1/statistics"); res["curl_cffi"] = "sent"
except Exception as e:
    res["curl_cffi"] = type(e).__name__
OUT["res"] = res
OUT["allowed_image"] = demo_network.allowed("api.sofascore.com", "/api/v1/team/42/image")
OUT["allowed_data"] = demo_network.allowed("api.sofascore.com", "/api/v1/team/42")
OUT["blocked"] = client.get("/api/demo/status").json()["external_requests_blocked"]
""")
    assert set(out["res"].values()) <= {"URLError", "DemoNetworkBlocked", "ConnectError"}, out["res"]
    assert "sent" not in out["res"].values() and out["cffi"] == []          # curl_cffi never reached its transport
    assert out["allowed_image"] is True and out["allowed_data"] is False
    assert {"api.sofascore.com", "api.the-odds-api.com", "api.openai.com", "example.org"} <= set(out["blocked"])


def test_late_evening_istanbul_kickoffs_survive_page_loads(env):
    """2026-10-28: kick-offs at 23:30-23:59 Istanbul (00:00-00:29 Tehran) were dropped from the day's cache by
    the page-load rewrite. They must stay, at their Istanbul time, through any number of reloads."""
    env["work"].mkdir(parents=True, exist_ok=True)
    sqlite3.connect(env["work"] / "demo_api_usage.db").close()
    _grant(env, 3, 3)
    fx = [{"fixture": {"id": 700 + i, "date": utc, "status": {"short": "NS"}},
           "league": {"id": 39, "name": "L", "country": "C", "logo": "", "season": 2030},
           "teams": {"home": {"id": 1, "name": f"H{i}", "logo": ""}, "away": {"id": 2, "name": f"A{i}", "logo": ""}},
           "goals": {"home": None, "away": None}, "score": {}}
          for i, utc in enumerate(["2030-01-05T09:00:00+00:00", "2030-01-05T20:10:00+00:00",
                                   "2030-01-05T20:45:00+00:00", "2030-01-05T20:59:00+00:00"])]
    out = _run(env, f"""
OUT["p"] = client.post("/api/demo/refresh-fixtures/{D}").status_code
for _ in range(3):
    r = client.get("/api/fixtures/{D}").json()
OUT["times"] = [f["time"] for f in r["fixtures"]]
OUT["next"] = [f["time"] for f in client.get("/api/fixtures/2030-01-06").json()["fixtures"]]
import json
OUT["stored"] = sorted(f["time"] for f in json.load(open(r"{env['work']}/.cache/fixtures-{D}.json")))
""", [{"status": 200, "remaining": "90", "fixtures": fx}])
    assert out["p"] == 200 and out["calls"] == 1
    assert out["times"] == ["12:00", "23:10", "23:45", "23:59"]       # all four, at Istanbul time
    assert out["stored"] == ["12:00", "23:10", "23:45", "23:59"]      # still in the cache after reloads
    assert out["next"] == []                                          # none leaks into the next day

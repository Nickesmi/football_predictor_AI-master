"""Demo fixtures from football-data.org (2026-10-31): explicit refresh only, own cache and limits, Istanbul days
from UTC kick-offs, API-Football days kept and never requested. Harness and mocked HTTPS (no network):
tests/test_demo_refresh_policy.py."""
import json
from datetime import datetime, timedelta, timezone

from tests.test_demo_fixture_states import _store
from tests.test_demo_refresh_policy import _fixture, env  # noqa: F401
from tests.test_demo_refresh_policy import _run as _run_demo

D = "2030-01-05"
FD = {"fixture_provider": "football_data"}


def _run(env, body, responses=(), **args):
    return _run_demo(env, body, responses, **{**FD, **args})


def _match(mid, utc, status="TIMED", score=(None, None), code="PL"):
    return {"id": mid, "utcDate": utc, "status": status, "matchday": 10, "stage": "REGULAR_SEASON",
            "lastUpdated": "2030-01-04T10:00:00Z", "area": {"name": "England"},
            "competition": {"id": 2021, "code": code, "name": "Premier League", "emblem": "https://crests/pl.png"},
            "season": {"startDate": "2029-08-10"},
            "homeTeam": {"id": 64, "name": "Liverpool FC", "shortName": "Liverpool", "crest": "https://crests/64.png"},
            "awayTeam": {"id": 57, "name": "Arsenal FC", "shortName": "Arsenal", "crest": "https://crests/57.png"},
            "score": {"fullTime": {"home": score[0], "away": score[1]}, "halfTime": {"home": None, "away": None}}}


DAY = [_match(1, "2030-01-04T20:30:00Z"),          # 23:30 Istanbul, the day before: not in D
       _match(2, "2030-01-04T21:15:00Z"),          # 00:15 Istanbul on D
       _match(3, "2030-01-05T12:00:00Z", "FINISHED", (2, 1)),
       _match(4, "2030-01-05T20:45:00Z"),          # 23:45 Istanbul on D
       _match(5, "2030-01-05T21:00:00Z")]          # 00:00 Istanbul on D+1: not in D


def _ok(matches=DAY):
    return {"status": 200, "json": {"matches": matches}}


BODY = f"""
OUT["p"] = client.post("/api/demo/refresh-fixtures/{D}").status_code
r = client.get("/api/fixtures/{D}").json()
OUT["fx"] = r["fixtures"]
OUT["state"] = r["demo"]["state"]
OUT["again"] = client.post("/api/demo/refresh-fixtures/{D}").status_code
OUT["fd_urls"] = [u for u in URLS if "football-data" in u]
OUT["status"] = client.get("/api/demo/status").json()
"""


def test_explicit_refresh_stores_the_istanbul_day_with_utc_kickoffs(env):
    out = _run(env, BODY, [_ok()])
    assert out["p"] == 200 and out["calls"] == 1 and out["urls"] == []          # no API-Football request
    assert out["fd_urls"] == ["api.football-data.org/v4/matches?dateFrom=2030-01-04&dateTo=2030-01-06"]
    assert [(f["id"], f["time"]) for f in out["fx"]] == [("fd-2", "00:15"), ("fd-3", "15:00"), ("fd-4", "23:45")]
    f = out["fx"][1]
    assert f["kickoff_utc"] == "2030-01-05T12:00:00Z" and f["date"] == D and f["display_timezone"] == "Europe/Istanbul"
    assert (f["status"], f["home_goals"], f["away_goals"], f["provider"]) == ("FT", 2, 1, "football_data")
    assert f["home_team"] == {"id": "fd-64", "name": "Liverpool FC", "short_name": "Liverpool",
                              "logo": "https://crests/64.png"}
    assert f["league"]["name"] == "Premier League" and f["league"]["id"] == "fd-2021"
    assert out["fx"][0]["home_goals"] is None and out["state"] == "cached"
    assert out["again"] == 409 and out["status"]["provider"] == "football_data"   # cached < 15 min: nothing sent
    assert out["status"]["used_today"] == 1 and out["status"]["remaining_today"] == 39


def test_page_loads_never_send(env):
    out = _run(env, f"""
for d in ["{D}", "2030-01-06", "2030-01-04"]:
    r = client.get("/api/fixtures/" + d).json()
OUT["state"] = r["demo"]["state"]
OUT["msg"] = r["message"]
""")
    assert out["calls"] == 0 and out["state"] == "not_cached" and "football-data.org" in out["msg"]


def test_an_empty_day_is_no_fixtures_and_a_failure_is_a_provider_error(env):
    out = _run(env, f"""
from src.ops import football_data
football_data.MIN_INTERVAL = 0
OUT["empty"] = client.post("/api/demo/refresh-fixtures/{D}").status_code
OUT["s1"] = client.get("/api/demo/fixture-cache/{D}").json()["state"]
OUT["fail"] = client.post("/api/demo/refresh-fixtures/2030-01-06").status_code
c = client.get("/api/demo/fixture-cache/2030-01-06").json()
OUT["s2"], OUT["err"] = c["state"], c["last_attempt"]["error"]
""", [_ok([]), {"status": 500, "json": {"message": "Internal error"}}])
    assert out["empty"] == 200 and out["s1"] == "no_fixtures"
    assert out["fail"] == 502 and out["s2"] == "provider_error" and out["err"] == "Internal error"


def test_a_failed_or_empty_refresh_never_replaces_cached_fixtures(env):
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    fx = [{"id": "fd-9", "kickoff_utc": "2030-01-05T12:00:00Z", "time": "15:00", "date": D, "provider": "football_data",
           "home_team": {"name": "H"}, "away_team": {"name": "A"}, "league": {"name": "L"}, "status": "NS"}]
    cache = env["work"] / ".cache"
    cache.mkdir(parents=True)
    (cache / f"fd-fixtures-{D}.json").write_text(json.dumps({"fetched_at": old, "fixtures": fx}))
    out = _run(env, f"""
from src.ops import football_data
football_data.MIN_INTERVAL = 0
OUT["codes"] = [client.post("/api/demo/refresh-fixtures/{D}").status_code for _ in range(2)]
OUT["ids"] = [f["id"] for f in client.get("/api/fixtures/{D}").json()["fixtures"]]
""", [{"status": 503, "json": {"message": "down"}}, _ok([])])
    assert out["codes"] == [502, 502] and out["calls"] == 2 and out["ids"] == ["fd-9"]


def test_rate_limit_daily_cap_provider_429_and_missing_token_send_nothing_extra(env):
    out = _run(env, f"""
OUT["a"] = client.post("/api/demo/refresh-fixtures/{D}").status_code
r = client.post("/api/demo/refresh-fixtures/2030-01-06")
OUT["b"], OUT["b_msg"] = r.status_code, r.json()["detail"]["message"]
from src.ops import football_data
football_data.MIN_INTERVAL = 0
OUT["c"] = client.post("/api/demo/refresh-fixtures/2030-01-07").status_code
OUT["c_state"] = client.get("/api/demo/fixture-cache/2030-01-07").json()["state"]
football_data.DAILY_CAP = 2
r = client.post("/api/demo/refresh-fixtures/2030-01-08")
OUT["d"], OUT["d_msg"] = r.status_code, r.json()["detail"]["message"]
""", [_ok(), {"status": 429, "json": {"message": "You reached your request limit."}}])
    assert out["a"] == 200 and out["b"] == 429 and "rate limit" in out["b_msg"]
    assert out["c"] == 429 and out["c_state"] == "quota_exhausted" and out["calls"] == 2
    assert out["d"] == 403 and "daily football-data.org limit of 2" in out["d_msg"]
    none = _run(env, f"""OUT["p"] = client.post("/api/demo/refresh-fixtures/2030-02-01").status_code""", fd_token="")
    assert none["p"] == 403 and none["calls"] == 0


def test_days_fetched_from_api_football_keep_their_cache_and_are_never_requested(env):
    raw = _fixture(9001, D)
    _store(env, D, [{"id": "9001", "date": D, "time": "15:00", "provider": "api_football", "source": "api_football",
                     "kickoff_utc": "2030-01-05T12:00:00Z", "status": "NS", "home_team": {"name": "Liverpool"},
                     "away_team": {"name": "Ipswich Town"}, "league": {"name": raw["league"]["name"]}}])
    out = _run(env, f"""
OUT["p"] = client.post("/api/demo/refresh-fixtures/{D}").status_code
OUT["fx"] = [(f["id"], f["provider"]) for f in client.get("/api/fixtures/{D}").json()["fixtures"]]
""", [_ok()])
    assert out["p"] == 409 and out["calls"] == 0 and out["fx"] == [["9001", "api_football"]]

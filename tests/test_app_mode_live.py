"""APP_MODE=live in the demo launcher (2026-11-05): automatic fetching of yesterday/today/tomorrow within a daily
request budget; demo mode unchanged. Subprocess scenarios reuse the demo refresh harness (mocked HTTPS parsed by
the real http.client; any real socket connection raises)."""
import json
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from src.ops import app_mode
from tests.test_demo_refresh_policy import KEY, PRELUDE, ROOT, _ok, _usage, env  # noqa: F401  (env is a fixture)

TODAY, YESTERDAY, TOMORROW = "2030-01-05", "2030-01-04", "2030-01-06"
CLOCK = "from datetime import datetime, timezone\ndemo_quota._state['clock'] = lambda: datetime(2030, 1, 5, 12, 0, tzinfo=timezone.utc)\n"


def _run_live(env, body, responses=(), extra_env=None, **args):
    a = {"db": str(env["db"]), "workdir": str(env["work"]), "shadow_root": str(env["shadow"]), **args}
    e = {"PATH": "/usr/bin:/bin", "HOME": str(env["tmp"]), "PYTHONPATH": str(ROOT), "DEMO_ROOT": str(ROOT),
         "DEMO_ARGS": json.dumps(a), "FAKE_RESPONSES": json.dumps(list(responses)),
         "APIFOOTBALL_API_KEY": KEY, "API_FOOTBALL_KEY": KEY, "APP_MODE": "live", **(extra_env or {})}
    r = subprocess.run([sys.executable, "-c", PRELUDE + CLOCK + body + "\ndone()\n"], cwd=ROOT, timeout=180,
                       capture_output=True, text=True, env=e)
    lines = [line for line in r.stdout.splitlines() if line.startswith("RESULT ")]
    return r, (json.loads(lines[-1][7:]) if lines else None)


def _ok_live(env, body, responses=(), **kw):
    r, out = _run_live(env, body, responses, **kw)
    assert r.returncode == 0 and out is not None, r.stdout[-3000:] + r.stderr[-3000:]
    return out


# ── settings and policy (no process) ─────────────────────────────────────────

def test_settings_default_to_demo_and_validate_live_values():
    assert app_mode.settings({}).mode == "demo" and app_mode.settings({}).daily_budget is None
    s = app_mode.settings({"APP_MODE": "live"})
    assert s.live and s.daily_budget == 30 and s.today_minutes == 120
    assert app_mode.settings({"APP_MODE": "LIVE", "API_FOOTBALL_DAILY_BUDGET": "80"}).daily_budget == 80
    for bad in ({"APP_MODE": "production"}, {"APP_MODE": "live", "API_FOOTBALL_DAILY_BUDGET": "0"},
                {"APP_MODE": "live", "API_FOOTBALL_DAILY_BUDGET": "8000"},
                {"APP_MODE": "live", "API_FOOTBALL_DAILY_BUDGET": "lots"}):
        with pytest.raises(ValueError):
            app_mode.settings(bad)


def test_refresh_policy_window_ttl_and_yesterday_completion():
    s = app_mode.settings({"APP_MODE": "live"})
    now = datetime(2030, 1, 5, 12, 0, tzinfo=timezone.utc)
    due = lambda d, upd, fx=None: app_mode.refresh_due(s, d, TODAY, upd, fx, now)[0]   # noqa: E731
    assert due(TODAY, None) and due(TOMORROW, None) and due(YESTERDAY, None)
    assert not due("2030-01-03", None) and not due("2030-01-07", None)          # free-plan window only
    assert not due(TODAY, "2030-01-05T11:00:00+00:00") and due(TODAY, "2030-01-05T09:59:00+00:00")
    assert not due(TOMORROW, "2030-01-05T07:00:00+00:00")                       # every 6 h
    assert due(YESTERDAY, "2030-01-05T09:00:00+00:00", [{"status": "FT"}, {"status": "2H"}])
    assert not due(YESTERDAY, "2030-01-05T09:00:00+00:00", [{"status": "FT"}, {"status": "PST"}])
    assert not app_mode.refresh_due(app_mode.settings({}), TODAY, TODAY, None, None, now)[0]


# ── live launcher scenarios ──────────────────────────────────────────────────

def test_page_load_fetches_today_once_without_any_grant(env):
    out = _ok_live(env, f"""
r = client.get("/api/fixtures/{TODAY}").json(); OUT["first"] = [len(r["fixtures"]), r["demo"]["state"], r["demo"]["live"]]
r = client.get("/api/fixtures/{TODAY}").json(); OUT["second"] = len(r["fixtures"])
OUT["status"] = client.get("/api/demo/status").json()
""", [_ok(TODAY)])
    assert out["first"] == [2, "cached", True] and out["second"] == 2
    assert out["calls"] == 1                                    # the second load is inside the 120-min window
    st = out["status"]
    assert st["mode"] == "live" and st["daily_limit"] == 30 and st["used_today"] == 1 and st["remaining_today"] == 29
    assert st["can_refresh"] is True and st["grants"] == []


def test_budget_stops_cleanly_and_is_recorded(env):
    out = _ok_live(env, """
from api import demo_mode
OUT["tick"] = demo_mode.live_tick()
OUT["status"] = client.get("/api/demo/status").json()
""", [_ok(YESTERDAY), _ok(TODAY), _ok(TOMORROW)], extra_env={"API_FOOTBALL_DAILY_BUDGET": "2"})
    assert out["calls"] == 2
    assert [t["state"] for t in out["tick"]] == ["fresh", "fresh", "quota_exhausted"]
    assert out["status"]["can_refresh"] is False
    assert out["status"]["blocked_reasons"][0] == "daily budget 2 reached (resets 00:00 UTC)"
    sent = _usage(env).execute("SELECT COUNT(*) FROM demo_requests WHERE allowed = 1").fetchone()[0]
    assert sent == 2


def test_manual_refresh_is_refused_right_after_an_update_and_outside_the_window(env):
    out = _ok_live(env, f"""
client.get("/api/fixtures/{TODAY}")
p = client.post("/api/demo/refresh-fixtures/{TODAY}"); OUT["manual"] = [p.status_code, p.json()["detail"]["error"]]
client.get("/api/fixtures/2030-01-09"); OUT["far"] = True
""", [_ok(TODAY)])
    assert out["manual"] == [409, "recently_updated"] and out["calls"] == 1


def test_provider_floor_still_applies_in_live_mode(env):
    out = _ok_live(env, f"""
client.get("/api/fixtures/{TODAY}")
client.get("/api/fixtures/{TOMORROW}")
OUT["status"] = client.get("/api/demo/status").json()
""", [_ok(TODAY, remaining="25"), _ok(TOMORROW)])
    assert out["calls"] == 1
    assert "provider reports 25 requests left today (floor 30)" in out["status"]["blocked_reasons"]


def test_live_mode_refuses_football_data_and_unknown_modes(env):
    r, _ = _run_live(env, "", fixture_provider="football_data")
    assert r.returncode != 0 and "APP_MODE=live needs --fixture-provider api_football" in r.stderr
    r, _ = _run_live(env, "", extra_env={"APP_MODE": "production"})
    assert r.returncode != 0 and "APP_MODE must be 'demo' or 'live'" in r.stderr


def test_demo_mode_stays_explicit_only(env):
    r, out = _run_live(env, f"""
r = client.get("/api/fixtures/{TODAY}").json(); OUT["state"] = r["demo"]["state"]; OUT["live"] = r["demo"]["live"]
OUT["mode"] = client.get("/api/demo/status").json()["mode"]
""", [_ok(TODAY)], extra_env={"APP_MODE": "demo"})
    assert r.returncode == 0 and out["calls"] == 0 and out["mode"] == "demo" and out["live"] is False

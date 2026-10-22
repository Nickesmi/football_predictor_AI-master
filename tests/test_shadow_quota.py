"""M3.5 quota behaviour on the Mac host: UTC midnight reset, shared-ledger persistence across processes,
daily and per-cycle caps, failed requests counted, and the demo's separate five-request lifetime cap.

Everything runs offline against a fake API-Football; nothing leaves the machine.
"""
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.odds import ledger, store
from src.shadow.cycle import PROFILES
from tests.test_shadow_mac_recovery import Schedule, _cycles
from tests.test_shadow_validation import T0, _run, _seed_history, shadow_db  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
MEDIUM = PROFILES["medium"]


def _sent(conn):
    return conn.execute("SELECT COUNT(*) FROM provider_quota_log WHERE provider = 'api_football' "
                        "AND status IN ('ok', 'error')").fetchone()[0]


def test_shared_ledger_resets_at_utc_midnight_and_never_before(tmp_path, monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_LEDGER", str(tmp_path / "ledger.db"))
    late = datetime(2030, 1, 5, 23, 59, 0, tzinfo=timezone.utc)
    for i in range(70):
        assert ledger.reserve("api_football", f"/r{i}", 1, 70, "shadow_cycle", late)[0]
    assert ledger.reserve("api_football", "/over", 1, 70, "shadow_cycle", late + timedelta(seconds=59))[0] is False
    # 02:59 local in Istanbul is still the same UTC day: no reset at local midnight.
    assert ledger.used_today("api_football", late + timedelta(seconds=59)) == 70
    midnight = datetime(2030, 1, 6, 0, 0, 0, tzinfo=timezone.utc)
    assert ledger.reserve("api_football", "/new-day", 1, 70, "shadow_cycle", midnight) == (True, 0)
    assert ledger.used_today("api_football", late) == 70          # yesterday's record is kept, not rewritten


def test_yesterdays_low_provider_remaining_does_not_deadlock_todays_cycle(shadow_db, tmp_path, monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_LEDGER", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "70")
    _seed_history(shadow_db)
    today = datetime(2030, 1, 6, 0, 30, tzinfo=timezone.utc)
    store.log_request(shadow_db, "api_football", "/x", 1, "ok", today - timedelta(hours=1), 200, "3", None)
    p = Schedule({9801: today + timedelta(minutes=125)})
    p.now = today
    r = _run(shadow_db, p, today, cfg=MEDIUM)
    assert r["discovery_requests"] >= 1 and any("/odds" in c for c in p.calls)   # collection resumes after 00:00 UTC
    assert not any("remaining" in e for e in r["errors"])


def test_todays_low_provider_remaining_still_stops_the_cycle(shadow_db, tmp_path, monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_LEDGER", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "70")
    _seed_history(shadow_db)
    today = datetime(2030, 1, 6, 9, 0, tzinfo=timezone.utc)
    store.log_request(shadow_db, "api_football", "/x", 1, "ok", today - timedelta(minutes=5), 200, "3", None)
    p = Schedule({9802: today + timedelta(minutes=125)})
    p.now = today
    r = _run(shadow_db, p, today, cfg=MEDIUM)
    assert p.calls == [] and r["errors"]                           # nothing sent while the provider is low


def test_per_cycle_cap_of_20_holds_with_more_fixtures_due(shadow_db, tmp_path, monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_LEDGER", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "70")
    _seed_history(shadow_db)
    k = T0 + timedelta(hours=3)
    p = Schedule({9900 + i: k for i in range(30)})                 # 30 fixtures due in the same window
    _cycles(shadow_db, p, T0, T0)
    before = len(p.calls)
    r = _cycles(shadow_db, p, k - timedelta(minutes=130), k - timedelta(minutes=130))[0]
    assert r["requests_sent"] == MEDIUM.max_requests_per_cycle == 20
    assert len(p.calls) - before == 20
    assert any("request cap 20 reached" in e for e in r["errors"])
    assert ledger.used_today("api_football", T0) == _sent(shadow_db) <= 70


def test_failed_requests_are_counted_in_the_ledger_and_the_log(shadow_db, tmp_path, monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_LEDGER", str(tmp_path / "ledger.db"))
    _seed_history(shadow_db)
    k = T0 + timedelta(hours=3)
    p = Schedule({9851: k})
    _cycles(shadow_db, p, T0, T0)
    real = Schedule.__call__

    def server_error(self, url, headers, timeout):
        if "/odds" in url:
            self.calls.append(url)
            return 500, {}, b"Internal Server Error"
        return real(self, url, headers, timeout)

    monkeypatch.setattr(Schedule, "__call__", server_error)
    _cycles(shadow_db, p, k - timedelta(minutes=130), k - timedelta(minutes=100))
    failed = shadow_db.execute("SELECT COUNT(*) FROM provider_quota_log WHERE status = 'error' "
                               "AND http_status = 500").fetchone()[0]
    assert failed == MEDIUM.max_attempts_per_window                # bounded retries, each one counted
    assert ledger.used_today("api_football", T0) == _sent(shadow_db)


RESERVE = r"""
import sys
from datetime import datetime, timezone
from src.odds import ledger
now = datetime(2030, 1, 5, 12, 0, tzinfo=timezone.utc)
ok = sum(ledger.reserve("api_football", "/p", 1, 70, sys.argv[1], now)[0] for _ in range(int(sys.argv[2])))
print(ok, ledger.used_today("api_football", now))
"""


def test_restarting_the_process_never_resets_usage(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT), "API_FOOTBALL_LEDGER": str(tmp_path / "ledger.db")}

    def run(consumer, n):
        r = subprocess.run([sys.executable, "-c", RESERVE, consumer, str(n)], env=env, cwd=tmp_path,
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        return [int(x) for x in r.stdout.split()]

    assert run("shadow_cycle", 40) == [40, 40]
    assert run("shadow_cycle", 40) == [30, 70]                   # a new process sees the earlier 40
    assert run("shadow_settle", 5) == [0, 70]                    # other consumers share the same ceiling
    rows = dict(sqlite3.connect(tmp_path / "ledger.db").execute(
        "SELECT status, COUNT(*) FROM ledger GROUP BY 1").fetchall())
    assert rows == {"reserved": 70, "refused": 15}


DEMO = r"""
import http.client, sys
from pathlib import Path
from src.ops import demo_quota
demo_quota.install(Path(sys.argv[1]), 5)
ok = refused = 0
for i in range(3):
    try:
        http.client.HTTPSConnection("v3.football.api-sports.io").putrequest("GET", f"/fixtures?i={i}")
        ok += 1
    except demo_quota.DemoQuotaExceeded:
        refused += 1
print(ok, refused, demo_quota.used())
"""


def test_demo_keeps_its_five_request_lifetime_cap_separate_from_the_shadow_ledger(tmp_path):
    # The running demo has no API_FOOTBALL_LEDGER / ODDS_* variables (checked on the live process), so its
    # own usage file is its only counter: five sends in its lifetime, across restarts.
    usage, shadow_ledger = tmp_path / "demo_api_usage.db", tmp_path / "api_football_ledger.db"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("API_FOOTBALL", "ODDS_"))}
    env["PYTHONPATH"] = str(ROOT)
    outs = []
    for _ in range(2):                                             # 3 + 3 attempts across a restart
        r = subprocess.run([sys.executable, "-c", DEMO, str(usage)], env=env, cwd=tmp_path,
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        outs.append(r.stdout.split())
    assert outs == [["3", "0", "3"], ["2", "1", "5"]]              # never more than 5 in its lifetime
    assert not shadow_ledger.exists()                              # the demo never writes the shadow ledger

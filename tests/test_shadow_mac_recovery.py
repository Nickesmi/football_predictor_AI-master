"""M3.5 Mac host: offline recovery tests. Sleep gaps, wake, lost connectivity, HTTP 429, quota exhaustion,
duplicate launches, abrupt termination, monitoring, the experiment caffeinate and the launchd templates.

Every scenario runs the real shadow cycle on 30-minute slots with a fake API-Football. Nothing leaves the
machine. The rule checked throughout: a missed decision window is recorded (missed slots, no observation)
and is never back-filled as a forward observation.
"""
import json
import os
import platform
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

from src.odds import ledger
from src.odds.model import iso_utc
from src.shadow import cycle
from src.shadow import manifest as sm
from src.shadow.cycle import PROFILES
from tests.test_shadow_validation import T0, FakeProvider, _run, _seed_history, shadow_db  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
MEDIUM = PROFILES["medium"]
SLOT = timedelta(minutes=30)


def _fx(fid, kickoff):
    return {"fixture": {"id": fid, "date": iso_utc(kickoff), "status": {"short": "NS"}},
            "league": {"id": 39, "name": "Premier League", "season": 2029},
            "teams": {"home": {"name": "Liverpool", "id": 40}, "away": {"name": "Ipswich Town", "id": 57}},
            "score": {"fulltime": {"home": None, "away": None}}}


def _odds(fid, kickoff, update):
    return {"fixture": {"id": fid, "date": iso_utc(kickoff)}, "update": iso_utc(update),
            "bookmakers": [{"name": "Bet365", "bets": [{"name": "Match Winner", "values": [
                {"value": "Home", "odd": "1.50"}, {"value": "Draw", "odd": "4.50"}, {"value": "Away", "odd": "7.00"}]}]}]}


class Schedule(FakeProvider):
    """Fixtures at given kickoffs; odds stamped 10 min before each request. Switchable failures."""

    def __init__(self, kickoffs: dict):
        super().__init__()
        self.kickoffs = kickoffs
        self.fixtures = [_fx(fid, k) for fid, k in kickoffs.items()]
        self.mode = "ok"

    def __call__(self, url, headers, timeout):
        if self.mode == "offline":
            self.calls.append(url)
            raise OSError("Network is unreachable")
        if self.mode == "429" and "/odds" in url:
            self.calls.append(url)
            return 429, {}, b"Too Many Requests"
        self.odds = [_odds(fid, k, self.now - timedelta(minutes=10)) for fid, k in self.kickoffs.items()]
        return super().__call__(url, headers, timeout)


def _cycles(conn, provider, start, end):
    reports, t = [], start
    while t <= end:
        provider.now = t
        reports.append(_run(conn, provider, t, cfg=MEDIUM))
        t += SLOT
    return reports


def _obs(conn):
    return {r["fixture_id"]: dict(r) for r in conn.execute("SELECT * FROM shadow_observations")}


def _no_backfill(conn):
    f = sm.forward_evidence_check(conn)
    assert f["decided_at_or_after_kickoff"] == 0 and f["without_cycle"] == 0 and f["counted_toward_baseline"] == 0
    assert f["forward"] == f["observations"]
    return f


@pytest.mark.parametrize("hours", [2, 8])
def test_sleep_interruption_records_missed_slots_and_never_backfills(shadow_db, hours):
    _seed_history(shadow_db)
    sleep_at = T0 + SLOT                                  # last cycle before the Mac sleeps
    wake = sleep_at + timedelta(hours=hours)
    kickoffs = {
        9001: sleep_at + timedelta(minutes=120),          # window just open at the last cycle: observed before sleep
        9002: wake + timedelta(minutes=30),               # window opened during sleep: observed late, on wake
        9003: wake + timedelta(hours=4),                  # well after wake: observed at the normal lead time
    }
    if hours == 8:
        kickoffs[9004] = sleep_at + timedelta(hours=4)    # whole window inside the sleep: lost
    p = Schedule(kickoffs)
    _cycles(shadow_db, p, T0, sleep_at)
    wake_report = _cycles(shadow_db, p, wake, wake)[0]
    assert wake_report["gap_minutes"] == hours * 60
    assert wake_report["missed_slots"] == hours * 2 - 1    # slots that should have run while asleep
    _cycles(shadow_db, p, wake + SLOT, wake + timedelta(hours=5))
    obs = _obs(shadow_db)
    assert obs["9001"]["decided_at"] == iso_utc(sleep_at)
    assert obs["9002"]["decided_at"] == iso_utc(wake)          # genuine decision time = the wake cycle
    assert obs["9002"]["minutes_to_kickoff"] == 30             # shorter lead than the nominal 120: recorded as is
    assert 90 < obs["9003"]["minutes_to_kickoff"] <= 120
    if hours == 8:
        assert "9004" not in obs                               # lost; never back-filled after wake
    _no_backfill(shadow_db)
    missed = shadow_db.execute("SELECT SUM(missed_slots) FROM shadow_cycles").fetchone()[0]
    assert missed == hours * 2 - 1


def test_wake_after_a_sleep_spanning_kickoff_never_creates_a_late_observation(shadow_db):
    _seed_history(shadow_db)
    k = T0 + timedelta(hours=2)
    p = Schedule({9101: k})
    _cycles(shadow_db, p, T0 - timedelta(hours=1), T0 - timedelta(hours=1))      # discovered, then asleep
    for t in (k + timedelta(minutes=5), k + timedelta(hours=3)):                  # wake after kickoff
        p.now = t
        r = _run(shadow_db, p, t, cfg=MEDIUM)
        assert r["observations"] == 0
    assert _obs(shadow_db) == {}
    _no_backfill(shadow_db)


def test_lost_connectivity_is_recorded_counted_bounded_and_recovers(shadow_db, tmp_path, monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_LEDGER", str(tmp_path / "ledger.db"))
    _seed_history(shadow_db)
    k = T0 + timedelta(hours=3)
    p = Schedule({9201: k})
    _cycles(shadow_db, p, T0, T0)                                     # discovery while online
    p.mode = "offline"
    reports = _cycles(shadow_db, p, k - timedelta(minutes=135), k - timedelta(minutes=75))
    errors = shadow_db.execute("SELECT COUNT(*) FROM provider_quota_log WHERE status = 'error' "
                               "AND http_status IS NULL").fetchone()[0]
    assert errors == MEDIUM.max_attempts_per_window                # 2 attempts per window, then no more sends
    assert any("retry_limit_reached" in r for r in reports)
    sent = shadow_db.execute("SELECT COUNT(*) FROM provider_quota_log WHERE status IN ('ok', 'error')").fetchone()[0]
    assert ledger.used_today("api_football", T0) == sent and errors > 0   # failed sends are counted
    assert all(r.get("finished_at") for r in reports)              # every cycle finished cleanly
    p.mode = "ok"
    _cycles(shadow_db, p, k - timedelta(minutes=60), k - timedelta(minutes=30))
    obs = _obs(shadow_db)["9201"]                                  # observed at the deferral deadline, no odds
    assert obs["decided_at"] == iso_utc(k - timedelta(minutes=60)) and json.loads(obs["candidates_json"]) == []
    _no_backfill(shadow_db)


def test_http_429_stops_the_cycle_counts_and_the_next_slot_recovers(shadow_db, tmp_path, monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_LEDGER", str(tmp_path / "ledger.db"))
    _seed_history(shadow_db)
    k = T0 + timedelta(hours=3)
    p = Schedule({9301: k, 9302: k + timedelta(minutes=5)})
    _cycles(shadow_db, p, T0, T0)
    p.mode = "429"
    r = _cycles(shadow_db, p, k - timedelta(minutes=130), k - timedelta(minutes=130))[0]
    assert r.get("throttled") is True
    assert len([c for c in p.calls if "/odds" in c]) == 1          # stopped after the first 429
    sent = shadow_db.execute("SELECT COUNT(*) FROM provider_quota_log WHERE status IN ('ok', 'error')").fetchone()[0]
    assert ledger.used_today("api_football", T0) == sent           # discovery and the 429, all counted
    assert shadow_db.execute("SELECT COUNT(*) FROM provider_quota_log WHERE http_status = 429").fetchone()[0] == 1
    p.mode = "ok"
    _cycles(shadow_db, p, k - timedelta(minutes=100), k - timedelta(minutes=70))
    obs = _obs(shadow_db)
    assert set(obs) == {"9301", "9302"} and all(json.loads(o["candidates_json"]) for o in obs.values())
    _no_backfill(shadow_db)


def test_quota_exhaustion_sends_nothing_and_still_observes_honestly(shadow_db, tmp_path, monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_LEDGER", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "70")
    _seed_history(shadow_db)
    k = T0 + timedelta(hours=3)
    p = Schedule({9401: k})
    _cycles(shadow_db, p, T0, T0)
    for _ in range(69):                                              # other consumers used the rest of today
        ledger.reserve("api_football", "/other", 1, 70, "shadow_settle", T0)
    sent_before = len(p.calls)
    reports = _cycles(shadow_db, p, k - timedelta(minutes=130), k - timedelta(minutes=40))
    assert len(p.calls) == sent_before                              # nothing reached the provider
    assert ledger.used_today("api_football", T0) == 70              # never above the ceiling
    assert any("refused" in e for r in reports for e in r["errors"])
    obs = _obs(shadow_db)["9401"]
    assert obs["quote_status"] != "fresh" and json.loads(obs["candidates_json"]) == []
    _no_backfill(shadow_db)


def test_duplicate_launch_inside_the_lease_is_skipped_without_requests(shadow_db):
    _seed_history(shadow_db)
    p = Schedule({9501: T0 + timedelta(hours=3)})
    cycle._ensure(shadow_db)
    shadow_db.execute("INSERT INTO shadow_cycles (cycle_id, started_at, discovery_ran, status) "
                      "VALUES ('wedged', ?, 0, 'running')", (iso_utc(T0 - timedelta(minutes=2)),))
    p.now = T0
    r = _run(shadow_db, p, T0, cfg=MEDIUM)
    assert r["skipped"].startswith("cycle wedged") and p.calls == []


def test_two_simultaneous_processes_one_runs_one_skips(tmp_path):
    db = tmp_path / "shadow.db"
    sqlite3.connect(db).close()
    code = ("import fcntl, sys, time; f = open(sys.argv[1], 'w'); fcntl.flock(f, fcntl.LOCK_EX); "
            "print('held', flush=True); time.sleep(20)")
    holder = subprocess.Popen([sys.executable, "-c", code, str(tmp_path / "shadow_cycle.lock")],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "shadow_cycle.py"), "--db", str(db),
                            "--budget", "0"], capture_output=True, text=True, cwd=tmp_path, timeout=120,
                           env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": str(ROOT)})
    finally:
        holder.kill()
        holder.wait()
    assert r.returncode == 0 and "another shadow cycle" in r.stdout


KILLED_CYCLE = r"""
import os, signal, sqlite3, sys
sys.path.insert(0, sys.argv[2])
from datetime import timedelta
from tests.test_shadow_mac_recovery import Schedule, MEDIUM
from tests.test_shadow_validation import T0
from src.shadow import cycle
cycle._bilqe = lambda m: {"skipped": "test"}
conn = sqlite3.connect(sys.argv[1]); conn.row_factory = sqlite3.Row
p = Schedule({9601: T0 + timedelta(minutes=100)})
p.now = T0
real = p.__call__
def die_on_odds(url, headers, timeout):
    if "/odds" in url:
        os.kill(os.getpid(), signal.SIGKILL)      # power loss / force quit in the middle of a cycle
    return real(url, headers, timeout)
cycle.run_cycle(conn, "key", "h", MEDIUM, now=T0, transport=die_on_odds, sleep=lambda s: None)
"""


def test_abrupt_termination_leaves_a_consistent_db_and_the_next_cycle_recovers(tmp_path, monkeypatch):
    from src.db.database import init_db
    from tests.test_shadow_validation import _seed_history as seed

    db = tmp_path / "engine_shadow.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    cycle._ensure(conn)
    seed(conn)
    conn.close()
    env = {**os.environ, "PYTHONPATH": str(ROOT), "ODDS_REQUEST_BUDGET_API_FOOTBALL": "50",
           "FOOTBALL_PREDICTOR_DB_PATH": str(db)}
    env.pop("API_FOOTBALL_LEDGER", None)
    r = subprocess.run([sys.executable, "-c", KILLED_CYCLE, str(db), str(ROOT)], cwd=tmp_path, env=env,
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == -signal.SIGKILL, r.stderr[-1500:]
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    wedged = conn.execute("SELECT cycle_id, status, finished_at FROM shadow_cycles").fetchall()
    assert len(wedged) == 1 and wedged[0]["status"] == "running" and wedged[0]["finished_at"] is None
    assert conn.execute("SELECT COUNT(*) FROM shadow_fixtures").fetchone()[0] >= 1    # discovery was committed
    monkeypatch.setattr(cycle, "_bilqe", lambda m: {"skipped": "test"})
    monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "50")
    p = Schedule({9601: T0 + timedelta(minutes=100)})
    p.now = T0 + timedelta(minutes=5)
    assert _run(conn, p, p.now, cfg=MEDIUM)["skipped"].startswith("cycle")           # inside the 10-min lease
    p.now = T0 + SLOT
    after = _run(conn, p, p.now, cfg=MEDIUM)                                        # next slot: recovers
    assert "skipped" not in after and after["observations"] == 1
    obs = _obs(conn)["9601"]
    assert obs["decided_at"] == iso_utc(T0 + SLOT)
    _no_backfill(conn)


def test_monitor_reports_staleness_missed_slots_network_quota_and_integrity(shadow_db, tmp_path, monkeypatch):
    from datetime import timezone  # noqa: F401
    sys.path.insert(0, str(ROOT / "scripts"))
    import shadow_monitor

    monkeypatch.setenv("API_FOOTBALL_LEDGER", str(tmp_path / "run" / "api_football_ledger.db"))
    _seed_history(shadow_db)
    p = Schedule({9701: T0 + timedelta(hours=4, minutes=30)})   # odds due during the offline cycles
    _cycles(shadow_db, p, T0, T0 + SLOT)
    p.mode = "offline"
    _cycles(shadow_db, p, T0 + timedelta(hours=3), T0 + timedelta(hours=3, minutes=30))  # after a 2.5 h sleep
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    out = sqlite3.connect(run / "engine_shadow.db")
    shadow_db.backup(out)
    out.close()
    doc = shadow_monitor.build(run, None, T0 + timedelta(hours=3, minutes=40), 75, 70, 20, 1)
    assert doc["cycles"]["missed_slots_24h"] == 4 and doc["integrity"]["quick_check"] == "ok"
    assert doc["network_24h"].get("connection", 0) >= 1 and doc["quota"]["ledger_used_today"] >= 1
    assert any("network/provider failures" in a for a in doc["alerts"])
    stale = shadow_monitor.build(run, None, T0 + timedelta(hours=6), 75, 70, 20, 99)
    assert any(a.startswith("stale") for a in stale["alerts"])
    assert stale["integrity"]["forward_evidence"]["ok"]


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS caffeinate")
def test_experiment_caffeinate_stops_only_its_own_process(tmp_path):
    helper = ROOT / "scripts" / "orchestration" / "shadow" / "mac_caffeinate.sh"
    pidfile = tmp_path / "caffeinate.pid"
    unrelated = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-t", "40"])    # stands in for another app's
    ours = subprocess.Popen(["/bin/bash", str(helper), "run", str(pidfile), "40"])
    try:
        for _ in range(50):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            time.sleep(0.1)
        pid = int(pidfile.read_text())
        assert pid == ours.pid                                       # exec keeps the pid: launchd owns it
        time.sleep(0.5)
        assertions = subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True).stdout
        assert f"pid {pid}(caffeinate)" in assertions
        status = subprocess.run(["/bin/bash", str(helper), "status", str(pidfile)], capture_output=True, text=True)
        assert status.returncode == 0 and f"running {pid}" in status.stdout
        stop = subprocess.run(["/bin/bash", str(helper), "stop", str(pidfile)], capture_output=True, text=True)
        assert f"stopped {pid}" in stop.stdout
        ours.wait(timeout=5)
        assert unrelated.poll() is None                              # the unrelated caffeinate keeps running
        assert not pidfile.exists()
        again = subprocess.run(["/bin/bash", str(helper), "stop", str(pidfile)], capture_output=True, text=True)
        assert "nothing stopped" in again.stdout                     # a stale pidfile never kills anything
    finally:
        for proc in (ours, unrelated):
            if proc.poll() is None:
                proc.kill()


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS plutil")
def test_launchd_templates_are_valid_disabled_and_not_installed():
    import plistlib

    agents = Path.home() / "Library" / "LaunchAgents"
    for name in ("com.footballpredictor.shadow.plist", "com.footballpredictor.shadow.caffeinate.plist"):
        path = ROOT / "deploy" / "shadow" / "macos" / name
        assert subprocess.run(["plutil", "-lint", str(path)], capture_output=True).returncode == 0
        data = plistlib.loads(path.read_bytes())
        assert data["Disabled"] is True and data.get("KeepAlive") is False
        assert not (agents / name).exists()
    cyc = plistlib.loads((ROOT / "deploy/shadow/macos/com.footballpredictor.shadow.plist").read_bytes())
    assert cyc["StartCalendarInterval"] == [{"Minute": 0}, {"Minute": 30}] and cyc["RunAtLoad"] is False
    caf = plistlib.loads((ROOT / "deploy/shadow/macos/com.footballpredictor.shadow.caffeinate.plist").read_bytes())
    assert caf["ProgramArguments"][-1] == "1296000"                  # bounded: 15 days at most


def test_mac_wrapper_stop_file_never_starts_python(tmp_path):
    wrapper = ROOT / "scripts" / "orchestration" / "shadow" / "mac_cycle.sh"
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "SHADOW_STOP").write_text("stop")
    r = subprocess.run(["/bin/bash", str(wrapper)], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "FOOTBALL_SHADOW_ROOT": str(tmp_path)})
    assert r.returncode == 0 and '"stopped"' in r.stdout
    assert not (tmp_path / "run" / "api_football_ledger.db").exists()

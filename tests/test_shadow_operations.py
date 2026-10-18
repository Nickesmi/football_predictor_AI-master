"""M3.4 Package D: shared daily budget across consumers, process lock, throttling, downtime, no back-filling."""
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from src.odds import ledger, quota
from src.shadow.cycle import PROFILES
from tests.test_shadow_validation import (KICKOFF, T0, FakeProvider, _odds_item, _run, _seed_history,  # noqa: F401
                                          shadow_db)

ROOT = Path(__file__).resolve().parent.parent


def _fetch(conn, endpoint, transport, now=T0):
    return quota.fetch_json(conn, "api_football", endpoint, "https://h" + endpoint, {}, cost=1,
                            remaining_header="x", transport=transport, now=now)


def _ok(url, headers, timeout):
    return 200, {}, b'{"errors": [], "response": []}'


def test_shared_ledger_enforces_one_daily_budget_across_consumers(tmp_path, monkeypatch, engine_db):
    import sqlite3

    from src.db.database import init_db

    monkeypatch.setenv("API_FOOTBALL_LEDGER", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "3")
    other = sqlite3.connect(tmp_path / "other.db")
    other.row_factory = sqlite3.Row
    init_db(other)                                              # a second consumer with its own local log
    monkeypatch.setenv("API_FOOTBALL_CONSUMER", "shadow")
    _fetch(engine_db, "/a", _ok)
    _fetch(engine_db, "/b", _ok)
    monkeypatch.setenv("API_FOOTBALL_CONSUMER", "settle")
    _fetch(other, "/c", _ok)
    with pytest.raises(quota.BudgetExceeded, match="used"):
        _fetch(other, "/d", _ok)                                  # its own log has 1, the key has used 3
    assert ledger.used_today("api_football", T0) == 3


def test_failed_requests_count_against_the_shared_budget(tmp_path, monkeypatch, engine_db):
    monkeypatch.setenv("API_FOOTBALL_LEDGER", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "2")

    def boom(url, headers, timeout):
        raise ConnectionError("down")

    for ep in ("/a", "/b"):
        with pytest.raises(quota.ProviderUnavailable):
            _fetch(engine_db, ep, boom)
    with pytest.raises(quota.BudgetExceeded):
        _fetch(engine_db, "/c", _ok)


def test_ledger_survives_restarts(tmp_path, monkeypatch, engine_db):
    monkeypatch.setenv("API_FOOTBALL_LEDGER", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "5")
    _fetch(engine_db, "/a", _ok)
    code = ("import sys; sys.path.insert(0, %r); from src.odds import ledger; from datetime import datetime, timezone;"
            " print(ledger.used_today('api_football', datetime(2030, 1, 5, 10, tzinfo=timezone.utc)))" % str(ROOT))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env={"API_FOOTBALL_LEDGER": str(tmp_path / "ledger.db"), "PATH": "/usr/bin:/bin"})
    assert r.stdout.strip() == "1", r.stderr


def test_provider_throttling_stops_the_cycle(shadow_db):
    class Throttled(FakeProvider):
        def __call__(self, url, headers, timeout):
            if "/odds" in url:
                self.calls.append(url)
                return 429, {}, b"too many"
            return super().__call__(url, headers, timeout)

    _seed_history(shadow_db)
    p = Throttled()
    p.fixtures.append({**p.fixtures[0], "fixture": {**p.fixtures[0]["fixture"], "id": 778}})
    r = _run(shadow_db, p, T0, cfg=PROFILES["medium"])
    assert r.get("throttled") is True and len([c for c in p.calls if "/odds" in c]) == 1


def test_no_retrospective_observation_after_downtime(shadow_db):
    _seed_history(shadow_db)
    p = FakeProvider()
    p.odds = [_odds_item(T0 - timedelta(minutes=30))]
    _run(shadow_db, p, T0 - timedelta(hours=3), cfg=PROFILES["medium"])           # discovery only
    r = _run(shadow_db, p, KICKOFF + timedelta(hours=1), cfg=PROFILES["medium"])  # host was down over kickoff
    assert r["observations"] == 0 and r["missed_slots"] >= 6
    assert shadow_db.execute("SELECT COUNT(*) FROM shadow_observations").fetchone()[0] == 0


def test_a_second_cycle_process_is_refused_while_one_runs(tmp_path):
    import fcntl
    import sqlite3

    db = tmp_path / "shadow.db"
    sqlite3.connect(db).close()
    held = open(tmp_path / "shadow_cycle.lock", "w")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "shadow_cycle.py"), "--db", str(db), "--budget",
                            "0"], capture_output=True, text=True, cwd=ROOT, timeout=120)
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        held.close()
    assert r.returncode == 0 and json.loads(r.stdout)["skipped"].startswith("another shadow cycle")


def test_report_separates_operational_success_from_model_evidence(shadow_db):
    from src.shadow import report

    _seed_history(shadow_db)
    p = FakeProvider()
    p.odds = [_odds_item(T0 - timedelta(minutes=30))]
    _run(shadow_db, p, T0, cfg=PROFILES["medium"])
    rep = report.build_report(shadow_db)
    op = rep["operational_summary"]
    assert op["genuine_forward_observations"] == 1 and op["integrity_ok"] and op["identity_resolved_share"] == 1.0
    assert "not evidence" in op["note"] and "not proof of profitability" in rep["model_evidence_note"]
    assert rep["history_completeness"]["sides"] == 2 and rep["history_completeness"]["median_matches"] == 1

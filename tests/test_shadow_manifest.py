"""M3.5: versioned shadow manifest, fail-closed cycle start, emergency stop, forward-protocol fields."""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.shadow import cycle
from src.shadow import manifest as sm

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from golden import test_golden_identity_rh1 as g  # noqa: E402


def _shadow_db(tmp_path) -> Path:
    conn = g.build_seed()
    cycle._ensure(conn)
    db = tmp_path / "engine_shadow.db"
    out = sqlite3.connect(db)
    conn.backup(out)
    out.close()
    conn.close()
    return db


def _profile(m):
    return dict(m["profile"])


@pytest.fixture
def built(tmp_path, monkeypatch):
    monkeypatch.delenv("PROBABILITY_INPUTS", raising=False)
    monkeypatch.delenv("BASELINE_FREEZE", raising=False)
    db = _shadow_db(tmp_path)
    before = sm.sha256_file(db)
    m = sm.build(db, None)
    assert sm.sha256_file(db) == before                       # building never writes the shadow DB
    monkeypatch.setenv("API_FOOTBALL_REQUIRE_LEDGER", "1")
    monkeypatch.setenv("API_FOOTBALL_LEDGER", str(tmp_path / "ledger.db"))
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    yield db, m, conn
    conn.close()


def test_manifest_pins_versions_history_and_odds_behaviour(built):
    _, m, _ = built
    assert m["manifest_version"] == "shadow-manifest-v2" and m["manifest_id"].startswith("m35-")
    assert m["identity"]["resolver_version"] == "team-identity-v1" and m["history_version"] == "rh1"
    assert m["history"]["reproducible"] and m["history"]["fixtures_used"] == len(g.FIXTURES_PLAYED) - 3   # 116, 71 untracked
    assert m["odds_schema"]["revision_column"] and m["odds_schema"]["revision_behaviour"]["ok"]
    assert m["profile"]["history"] == "rh1" and m["quota"]["per_cycle_cap"] == 20
    assert m["quota"]["daily_budget_all_consumers"] == 70 and m["quota"]["ledger_required"]
    assert set(m["goldens"]) == {p.name for p in (ROOT / "tests" / "golden").glob("*.json")}
    assert m["database"]["forward_evidence_at_creation"]["observations"] == 0
    assert "manifest_id" in m["database"]["shadow_observation_columns"]


def test_matching_runtime_has_no_problems(built):
    _, m, conn = built
    assert sm.problems_runtime(m, conn, _profile(m), 70) == []


@pytest.mark.parametrize("change, expected", [
    (lambda m, p, env: p.update(max_requests_per_cycle=21), "profile differs"),
    (lambda m, p, env: p.update(history="team_state"), "profile differs"),
    (lambda m, p, env: m["code_fingerprint"].update(sha256="0" * 64), "code fingerprint"),
    (lambda m, p, env: m["runtime"].update(python_major_minor="3.9"), "python"),
    (lambda m, p, env: m["prediction"].update(model_version="model-other"), "model_version"),
    (lambda m, p, env: m["identity"].update(registry_sha256="x"), "identity"),
    (lambda m, p, env: m["odds_schema"].update(odds_quotes_sql_sha256="x"), "odds_quotes schema"),
    (lambda m, p, env: env.setenv("PROBABILITY_INPUTS", "legacy"), "PROBABILITY_INPUTS"),
    (lambda m, p, env: env.delenv("API_FOOTBALL_LEDGER"), "API_FOOTBALL_LEDGER"),
])
def test_any_mismatch_fails_closed(built, monkeypatch, change, expected):
    _, m, conn = built
    m, p = json.loads(json.dumps(m)), _profile(m)
    change(m, p, monkeypatch)
    assert any(expected in x for x in sm.problems_runtime(m, conn, p, 70))


def test_budget_above_ceiling_fails_closed(built):
    _, m, conn = built
    assert any("daily budget" in x for x in sm.problems_runtime(m, conn, _profile(m), 71))


def test_missing_identity_registry_fails_closed(built, monkeypatch):
    from src.identity import registry

    _, m, conn = built
    monkeypatch.setattr(registry, "REGISTRY_FILE", Path("/nonexistent/team_registry.v1.json"))
    monkeypatch.setattr(registry.load, "__defaults__", (registry.REGISTRY_FILE, False))
    assert any("identity/version data unavailable" in x for x in sm.problems_runtime(m, conn, _profile(m), 70))


def test_lost_history_data_fails_closed(built):
    _, m, conn = built
    conn.execute("DELETE FROM matches WHERE id IN ('g0', 'g1')")
    assert any("history source shrank" in x for x in sm.problems_runtime(m, conn, _profile(m), 70))


def test_forward_evidence_rule(built):
    _, m, conn = built
    conn.execute("INSERT INTO shadow_cycles (cycle_id, started_at) VALUES ('c1', '2026-10-01T10:00:00+00:00')")
    base = dict(fixture_id="g0", decided_at="2026-10-01T10:00:00+00:00", kickoff="2026-10-01T12:00:00+00:00",
                minutes_to_kickoff=120, legacy_policy_version="l", v1_policy_version="v", candidates_json="[]",
                legacy_json="{}", v1_json="{}", v1_assuming_frozen_json="{}")
    rows = [dict(base, cycle_id="c1", manifest_id=m["manifest_id"]),                         # forward
            dict(base, fixture_id="g1", cycle_id="c1", manifest_id=m["manifest_id"],
                 decided_at="2026-10-01T12:00:00+00:00"),                                    # at kickoff
            dict(base, fixture_id="g2", cycle_id="replay", manifest_id=m["manifest_id"]),    # no cycle
            dict(base, fixture_id="g3", cycle_id="c1", manifest_id="m35-other")]             # other manifest
    for r in rows:
        conn.execute(f"INSERT INTO shadow_observations ({', '.join(r)}) VALUES ({', '.join('?' * len(r))})",
                     list(r.values()))
    f = sm.forward_evidence_check(conn, m["manifest_id"])
    assert f["forward"] == 1 and not f["ok"]
    assert (f["decided_at_or_after_kickoff"], f["without_cycle"], f["from_another_manifest"]) == (1, 1, 1)


def test_quarantine_check_detects_a_returned_row_and_a_changed_copy(engine_db):
    from src.integrity import evidence

    evidence.ensure_tables(engine_db)
    engine_db.execute("INSERT INTO matches (id, date, home_team, away_team, league_name, status) "
                      "VALUES ('m', '2026-01-01', 'A', 'B', 'L', 'FT')")
    assert sm.quarantine_check(engine_db)["ok"]
    for pk, text, sha in ((1, "{}", sm.hashlib.sha256(b"{}").hexdigest()), (2, "{}", "bad")):
        engine_db.execute("INSERT INTO evidence_quarantine VALUES ('prediction_errors', ?, 'X', 'r', 'v', ?, ?, 'now', "
                          "'now', NULL)", (pk, text, sha))
    q = sm.quarantine_check(engine_db)
    assert q["moved"] == 2 and q["copy_sha_mismatch"] == 1 and not q["ok"]


def test_observation_records_the_forward_protocol_fields(built, monkeypatch):
    from src.odds.model import iso_utc

    _, m, conn = built
    monkeypatch.setattr(cycle, "_bilqe", lambda match: {"skipped": "test"})
    monkeypatch.setenv("SHADOW_MANIFEST_ID", m["manifest_id"])
    kickoff = iso_utc(g.DECIDED_AT + timedelta(minutes=100))
    conn.execute("INSERT INTO matches (id, date, kickoff, home_team, away_team, league_name, league_id, status, "
                 "provider) VALUES ('up', '2026-10-01', ?, 'Everton', 'Chelsea', 'Premier League', 39, 'NS', "
                 "'api_football')", (kickoff,))
    match = {"id": "up", "home_team": "Everton", "away_team": "Chelsea", "league_name": "Premier League",
             "league_id": 39, "provider": "api_football", "country": "", "home_team_id": 45, "away_team_id": 49}
    row = cycle.observe(conn, match, kickoff, g.DECIDED_AT, "c", "rh1")
    assert row["manifest_id"] == m["manifest_id"] and row["competition_key"] == "england-premier-league"
    assert row["provider_league_id"] == 39 and (row["provider_home_team_id"], row["provider_away_team_id"]) == ("45", "49")
    assert row["home_canonical_team_id"].startswith("cth-") and row["away_canonical_team_id"].startswith("cth-")
    avail = json.loads(row["inputs_available_json"])
    assert avail["home"]["matches_played"] == 3 and avail["home"]["available_from"] <= iso_utc(g.DECIDED_AT)
    stored = conn.execute("SELECT manifest_id, home_canonical_team_id FROM shadow_observations WHERE fixture_id = 'up'"
                          ).fetchone()
    assert tuple(stored) == (m["manifest_id"], row["home_canonical_team_id"])


def _cycle(tmp_path, db, *args, **env):
    base = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": str(ROOT), "APP_ENV": "development",
            "API_FOOTBALL_KEY": "", "APIFOOTBALL_API_KEY": ""}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "shadow_cycle.py"), "--db", str(db), *args],
                          capture_output=True, text=True, cwd=tmp_path, timeout=180,
                          env={**base, **{k: str(v) for k, v in env.items()}})


def test_deployed_cycle_without_manifest_fails_closed(tmp_path):
    db = _shadow_db(tmp_path)
    r = _cycle(tmp_path, db, "--budget", "70", "--ledger", tmp_path / "l.db", API_FOOTBALL_REQUIRE_LEDGER=1)
    assert r.returncode == 2 and "--manifest is required" in r.stderr


def test_deployed_cycle_with_mismatching_manifest_fails_closed_before_any_request(tmp_path, built):
    db, m, _ = built
    m = json.loads(json.dumps(m))
    m["code_fingerprint"]["sha256"] = "0" * 64
    (tmp_path / "m.json").write_text(json.dumps(m))
    r = _cycle(tmp_path, db, "--budget", "70", "--ledger", tmp_path / "l.db", "--manifest", tmp_path / "m.json",
               API_FOOTBALL_REQUIRE_LEDGER=1)
    assert r.returncode == 2 and "code fingerprint" in r.stderr
    used = sqlite3.connect(tmp_path / "l.db").execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
    assert used == 0


def test_emergency_stop_file_stops_before_anything(tmp_path):
    db = _shadow_db(tmp_path)
    (tmp_path / "SHADOW_STOP").write_text("stop")
    r = _cycle(tmp_path, db, "--budget", "70", "--ledger", tmp_path / "l.db", API_FOOTBALL_REQUIRE_LEDGER=1)
    assert r.returncode == 0 and '"stopped"' in r.stdout
    assert not (tmp_path / "l.db").exists()
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM shadow_cycles").fetchone()[0] == 0


def test_manifest_script_never_rewrites_and_refuses_production(tmp_path):
    out = tmp_path / "m.json"
    out.write_text("{}")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "shadow_manifest.py"), "--db", str(tmp_path / "x.db"),
                        "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 2 and "never rewritten" in r.stderr and out.read_text() == "{}"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "shadow_manifest.py"), "--db",
                        str(ROOT / "data" / "engine.db"), "--out", str(tmp_path / "n.json")],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "production database" in r.stderr


def test_preflight_passes_offline_checks_and_never_counts_not_run_as_pass(tmp_path, built):
    db, m, _ = built
    (tmp_path / "m.json").write_text(json.dumps(m))
    env_file = tmp_path / "shadow.env"
    env_file.write_text(f"API_FOOTBALL_KEY=preflight-test-key-0001\nAPI_FOOTBALL_REQUIRE_LEDGER=1\n"
                        f"API_FOOTBALL_LEDGER={tmp_path / 'persist' / 'ledger.db'}\nODDS_REQUEST_BUDGET_API_FOOTBALL=70\n")
    os.chmod(env_file, 0o640)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": str(ROOT), "APP_ENV": "production"}
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "shadow_preflight.py"), "--manifest",
                        str(tmp_path / "m.json"), "--db", str(db), "--env-file", str(env_file)],
                       capture_output=True, text=True, cwd=tmp_path, env=env, timeout=300)
    doc = json.loads(r.stdout[r.stdout.index("{"):])
    status = {c["check"]: c["status"] for c in doc["checks"]}
    assert r.returncode == 1 and not doc["all_passed"], r.stderr[-2000:]
    assert status.pop("1_linux_tests_on_host") == "not_run" and status.pop("3_provider_quota") == "not_run"
    assert set(status.values()) == {"pass"}, doc
    assert sm.sha256_file(db) == m["database"]["sha256"]                 # the shadow DB was not written
    os.chmod(env_file, 0o644)                                          # world-readable key file: refused
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "shadow_preflight.py"), "--manifest",
                        str(tmp_path / "m.json"), "--db", str(db), "--env-file", str(env_file)],
                       capture_output=True, text=True, cwd=tmp_path, env=env, timeout=300)
    doc = json.loads(r.stdout[r.stdout.index("{"):])
    assert {c["check"]: c["status"] for c in doc["checks"]}["2_credentials"] == "FAIL"

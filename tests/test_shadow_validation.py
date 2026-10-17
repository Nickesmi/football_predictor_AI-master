"""M3 Phase F: shadow cycle end to end with a fake API-Football; nothing is ever counted."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from src.odds.model import iso_utc
from src.shadow import cycle, report
from src.shadow.cycle import ShadowConfig

T0 = datetime(2030, 1, 5, 10, 0, tzinfo=timezone.utc)
KICKOFF = T0 + timedelta(minutes=100)
HOST = "v3.football.api-sports.io"


def _fixture_item(status="NS", ft=(None, None), fid=777, league_id=39):
    return {"fixture": {"id": fid, "date": iso_utc(KICKOFF), "status": {"short": status}},
            "league": {"id": league_id, "name": "Premier League", "season": 2029},
            "teams": {"home": {"name": "Liverpool"}, "away": {"name": "Ipswich Town"}},
            "score": {"fulltime": {"home": ft[0], "away": ft[1]}}}


def _odds_item(update):
    return {"fixture": {"id": 777, "date": iso_utc(KICKOFF)}, "update": iso_utc(update),
            "bookmakers": [{"name": "Bet365", "bets": [
                {"name": "Match Winner", "values": [{"value": "Home", "odd": "1.50"}, {"value": "Draw", "odd": "4.50"},
                                                     {"value": "Away", "odd": "7.00"}]},
                {"name": "Goals Over/Under", "values": [{"value": "Over 2.5", "odd": "1.70"},
                                                        {"value": "Under 2.5", "odd": "2.20"}]},
                {"name": "Both Teams Score", "values": [{"value": "Yes", "odd": "1.80"}, {"value": "No", "odd": "2.00"}]},
            ]}]}


class FakeProvider:
    def __init__(self):
        self.calls, self.fixtures, self.odds = [], [_fixture_item(), _fixture_item(fid=888, league_id=9999)], []

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        q = parse_qs(urlparse(url).query)
        path = urlparse(url).path
        if path == "/fixtures":
            body = {"errors": [], "response": self.fixtures}
        elif path == "/odds" and "league" in q:
            body = {"errors": [], "paging": {"current": 1, "total": 1}, "response": self.odds}
        elif path == "/odds" and "fixture" in q:
            body = {"errors": [], "response": [o for o in self.odds if str(o["fixture"]["id"]) == q["fixture"][0]]}
        else:
            body = {"errors": {"unexpected": url}, "response": []}
        return 200, {"x-ratelimit-requests-remaining": "90"}, json.dumps(body).encode()


@pytest.fixture
def shadow_db(engine_db, monkeypatch):
    monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "50")
    monkeypatch.setenv("ODDS_MAX_AGE_MINUTES", "240")
    monkeypatch.setattr(cycle, "_bilqe", lambda match: {"layer_gate_results": {"data_quality": {"passed": True}},
                                                        "tier_counts": {"S": 0, "A": 0, "B": 0},
                                                        "rejections_by_failed_layer": {"market_edge": 3}})
    return engine_db


def _seed_history(conn, teams=("Liverpool", "Ipswich Town"), label="Premier League", league_id=39):
    for i, name in enumerate(teams):
        conn.execute("INSERT INTO team_state (team_name, team_name_lower, league, venue, rolling_scored, "
                     "rolling_conceded, matches_played, form_last5) VALUES (?, ?, ?, 'overall', 1.6, 1.1, 8, 0.6)",
                     (name, name.lower(), label))
        conn.execute("INSERT INTO matches (id, date, kickoff, home_team, away_team, league_name, league_id, status, "
                     "provider, home_goals, away_goals) "
                     "VALUES (?, '2029-12-01', '15:00', ?, 'Somebody', ?, ?, 'FT', 'api_football', 2, 1)",
                     (f"hist-{name}", name, label, league_id))
    conn.commit()


def _run(conn, provider, now, cfg=ShadowConfig(), **kw):
    return cycle.run_cycle(conn, "key", HOST, cfg, now=now, transport=provider, sleep=lambda s: None, **kw)


def test_full_cycle_discovers_collects_observes_and_settles(shadow_db):
    _seed_history(shadow_db)
    p = FakeProvider()
    p.odds = [_odds_item(T0 - timedelta(minutes=150))]
    r1 = _run(shadow_db, p, T0)
    assert r1["discovery_requests"] == 2 and r1["fixtures_inserted"] == 1  # untracked league ignored
    assert [c for c in p.calls if "/odds" in c] == [f"https://{HOST}/odds?fixture=777"]
    assert r1["observations"] == 1 and not r1["errors"]
    obs = shadow_db.execute("SELECT * FROM shadow_observations").fetchone()
    assert obs["quote_status"] == "QUOTED" and obs["quote_bucket"] == "121-240"
    assert obs["is_shadow"] == 1 and obs["counts_toward_baseline"] == 0
    assert json.loads(obs["candidates_json"]) and json.loads(obs["bilqe_json"])["tier_counts"]
    identity = json.loads(obs["identity_json"])
    assert identity["resolved"] and identity["history_version"] == "rh1"
    assert identity["home"]["history_league"] == "rh1:england-premier-league"
    assert identity["home"]["canonical_team_id"].startswith("cth-")
    assert identity["home"]["evidence"]["as_of_fixture_id"] == "api_football:hist-Liverpool"
    assert json.loads(obs["v1_1_json"])["funnel"]["selected"] == 0          # no evidence, no provisional list
    assert all(c["competition_key"] == "england-premier-league" for c in json.loads(obs["candidates_json"]))
    assert shadow_db.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 0

    # Closing capture before kickoff, then the result after the match.
    p.odds = [_odds_item(KICKOFF - timedelta(minutes=20))]
    r2 = _run(shadow_db, p, KICKOFF - timedelta(minutes=10))
    assert r2["odds_due"] == {"decision": 0, "closing": 1}
    assert shadow_db.execute("SELECT COUNT(*) FROM shadow_observations").fetchone()[0] == 1  # once per fixture
    p.fixtures = [_fixture_item("FT", (2, 0))]
    r3 = _run(shadow_db, p, KICKOFF + timedelta(hours=3))
    assert r3["outcomes_recorded"] == 1
    assert shadow_db.execute("SELECT status, home_goals, away_goals FROM matches WHERE id='777'").fetchone()[:] == \
        ("FT", 2, 0)

    rep = report.build_report(shadow_db)
    assert rep["observations"] == 1 and rep["settlement"]["observed_and_settled"] == 1
    assert rep["counts_toward_baseline"] == 0 and rep["reproducibility"]["mismatched"] == 0
    assert rep["odds_coverage"]["by_bucket"]["121-240"]["fixtures"] == 1
    assert rep["calibration"]["1X2"]["n"] == 1


def test_budget_zero_sends_nothing_and_still_observes(shadow_db, monkeypatch):
    monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "0")
    shadow_db.execute("INSERT INTO matches (id, date, kickoff, home_team, away_team, league_name, league_id, status, "
                      "provider) VALUES ('777', '2030-01-05', ?, 'Liverpool', 'Ipswich Town', 'Premier League', 39, "
                      "'NS', 'api_football')", (iso_utc(KICKOFF),))
    cycle.ensure_shadow_tables(shadow_db)
    shadow_db.execute("INSERT INTO shadow_fixtures (fixture_id, provider, league_id, season, first_seen_at, "
                      "last_seen_at, kickoff_utc, inserted_by_shadow) VALUES ('777', 'api_football', 39, 2029, ?, ?, ?, 1)",
                      (iso_utc(T0), iso_utc(T0), iso_utc(KICKOFF)))
    p = FakeProvider()
    r = _run(shadow_db, p, T0)
    assert p.calls == [] and r["observations"] == 0 and r["observations_deferred"] == 1
    r = _run(shadow_db, p, KICKOFF - timedelta(minutes=50))        # deferral ends 60 min before kickoff
    assert p.calls == [] and r["observations"] == 1
    obs = shadow_db.execute("SELECT quote_status FROM shadow_observations").fetchone()
    assert obs[0] == "NO_QUOTES"
    assert shadow_db.execute("SELECT COUNT(*) FROM provider_quota_log WHERE status='refused'").fetchone()[0] >= 1


def test_counting_columns_cannot_be_set(shadow_db):
    cycle.ensure_shadow_tables(shadow_db)
    shadow_db.execute("INSERT INTO matches (id, date, home_team, away_team, league_name) VALUES ('x', '2030-01-01', 'a', 'b', 'L')")
    with pytest.raises(sqlite3.IntegrityError):
        shadow_db.execute(
            "INSERT INTO shadow_observations (cycle_id, fixture_id, decided_at, kickoff, minutes_to_kickoff, "
            "legacy_policy_version, v1_policy_version, candidates_json, legacy_json, v1_json, "
            "v1_assuming_frozen_json, counts_toward_baseline) VALUES ('c', 'x', 't', 'k', 1, 'l', 'v', '[]', '{}', "
            "'{}', '{}', 1)")


@pytest.mark.parametrize("market, selection, score, won", [
    ("1X2", "home", (2, 0), True), ("1X2", "draw", (1, 1), True), ("1X2", "away", (1, 1), False),
    ("O/U 2.5", "over", (2, 1), True), ("O/U 2.5", "under", (2, 1), False),
    ("BTTS", "yes", (1, 0), False), ("BTTS", "no", (0, 0), True),
])
def test_settlement_rules(market, selection, score, won):
    assert cycle._won(market, selection, *score) is won


def test_shadow_cycle_script_refuses_live_db_without_flag(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, str(root / "scripts" / "shadow_cycle.py"), "--db",
                        str(root / "data" / "engine.db"), "--budget", "0"], capture_output=True, text=True, cwd=root)
    assert r.returncode == 2 and ("allow-production" in r.stderr or "service hold" in r.stderr)


def test_league_date_mode_for_paid_plans(shadow_db):
    p = FakeProvider()
    p.odds = [_odds_item(T0 - timedelta(minutes=30))]
    _run(shadow_db, p, T0, cfg=ShadowConfig(odds_mode="league_date"))
    assert any("/odds?league=39&season=2029&date=2030-01-05&page=1" in c for c in p.calls)
    assert shadow_db.execute("SELECT COUNT(*) FROM odds_quotes").fetchone()[0] == 7


def test_free_plan_season_error_is_a_failure_not_silence(engine_db, monkeypatch):
    from src.odds import collector

    monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "5")
    engine_db.execute("INSERT INTO matches (id, date, kickoff, home_team, away_team, league_name, league_id, status, "
                      "provider) VALUES ('777', '2030-01-05', ?, 'A', 'B', 'L', 39, 'NS', 'api_football')",
                      (iso_utc(KICKOFF),))

    def transport(url, headers, timeout):
        body = {"errors": {"plan": "Free plans do not have access to this season"}, "response": []}
        return 200, {}, json.dumps(body).encode()

    rows = [dict(r) for r in engine_db.execute("SELECT * FROM matches")]
    r = collector.collect_api_football_by_league(engine_db, rows, {"777": 2029}, "k", HOST, transport, T0,
                                                 sleep=lambda s: None)
    assert (r["requests_ok"], r["requests_failed"]) == (0, 1) and "Free plans" in r["notes"][0]


def test_unresolved_team_identity_records_no_forecast(shadow_db):
    p = FakeProvider()
    p.odds = [_odds_item(T0 - timedelta(minutes=30))]
    r = _run(shadow_db, p, T0)                      # no team_state history for either team
    assert r["observations"] == 1 and r.get("identity_unresolved") == 1 and not r["errors"]
    obs = shadow_db.execute("SELECT * FROM shadow_observations").fetchone()
    assert obs["error"] == "NO_FORECAST: TEAM_IDENTITY_UNRESOLVED"
    assert json.loads(obs["candidates_json"]) == [] and obs["forecast_json"] is None
    ident = json.loads(obs["identity_json"])
    assert not ident["resolved"] and ident["home"]["status"] == "UNRESOLVED_NO_HISTORY"
    assert ident["home"]["provider_team_id"] is None or ident["home"]["provider"] == "api_football"


def test_request_cap_per_cycle(shadow_db):
    p = FakeProvider()
    r = _run(shadow_db, p, T0, cfg=ShadowConfig(max_requests_per_cycle=1))
    assert len(p.calls) == 1 and r["requests_sent"] == 1
    assert any("request cap" in e for e in r["errors"])


def test_cycle_never_freezes_counts_or_recalibrates(shadow_db):
    _seed_history(shadow_db)
    before = {t: shadow_db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("calibration_models", "model_confidence_adjustments", "baseline_versions", "picks")}
    p = FakeProvider()
    p.odds = [_odds_item(T0 - timedelta(minutes=30))]
    _run(shadow_db, p, T0)
    p.fixtures = [_fixture_item("FT", (1, 1))]
    _run(shadow_db, p, KICKOFF + timedelta(hours=3))
    after = {t: shadow_db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before}
    assert after == before
    assert shadow_db.execute("SELECT COUNT(*) FROM shadow_selection_results "
                             "WHERE counts_toward_baseline != 0").fetchone()[0] == 0


def test_shadow_cycle_refuses_live_db_under_hold_even_with_allow_production(tmp_path, monkeypatch):
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    hold = root / "data" / "SERVICE_HOLD.json"
    if not hold.exists():
        pytest.skip("no service hold in this checkout")
    r = subprocess.run([sys.executable, str(root / "scripts" / "shadow_cycle.py"), "--db", str(root / "data" / "engine.db"),
                        "--budget", "0", "--allow-production"], capture_output=True, text=True, cwd=root)
    assert r.returncode == 2 and "service hold" in r.stderr


def test_scheduler_templates_are_disabled():
    from pathlib import Path

    d = Path(__file__).resolve().parent.parent / "scripts" / "orchestration" / "shadow"
    plist = (d / "com.footballpredictor.shadow.plist").read_text()
    assert "<key>Disabled</key><true/>" in plist and "<key>RunAtLoad</key><false/>" in plist
    assert "__BUDGET__" in plist and "__SHADOW_DB__" in plist           # cannot run without explicit values
    active = [l for l in (d / "crontab.txt").read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]
    assert active == []
    service = (d / "footballpredictor-shadow.service").read_text()
    assert "__BUDGET__" in service and "__SHADOW_DB__" in service and "--max-requests-per-cycle" in service
    assert "Persistent=false" in (d / "footballpredictor-shadow.timer").read_text()


def test_offline_replay_reproduces_and_never_uses_later_prices(shadow_db):
    from src.shadow.replay import replay

    _seed_history(shadow_db)
    p = FakeProvider()
    p.odds = [_odds_item(T0 - timedelta(minutes=30))]
    _run(shadow_db, p, T0)
    # a later price arrives after the decision; the replay must not see it
    p.odds = [_odds_item(KICKOFF - timedelta(minutes=20))]
    _run(shadow_db, p, KICKOFF - timedelta(minutes=10))
    calls = len(p.calls)
    [entry] = replay(shadow_db)
    assert len(p.calls) == calls                       # offline
    assert entry["kind"] == "RETROSPECTIVE_REPLAY" and entry["identity"] == "RESOLVED"
    recorded = json.loads(shadow_db.execute("SELECT candidates_json FROM shadow_observations").fetchone()[0])
    assert entry["candidates"] == len(recorded)
    assert shadow_db.execute("SELECT COUNT(*) FROM shadow_observations").fetchone()[0] == 1


@pytest.mark.parametrize("status, ft, settled", [
    ("FT", (2, 1), True), ("AET", (1, 1), True), ("PEN", (0, 0), True),
    ("PST", (None, None), False), ("ABD", (1, 0), False), ("CANC", (None, None), False), ("2H", (1, 0), False),
    ("FT", (None, None), False),
])
def test_only_verified_final_results_are_settled(shadow_db, status, ft, settled):
    _seed_history(shadow_db)
    p = FakeProvider()
    p.odds = [_odds_item(T0 - timedelta(minutes=30))]
    _run(shadow_db, p, T0)
    p.fixtures = [_fixture_item(status, ft)]
    _run(shadow_db, p, KICKOFF + timedelta(hours=3))
    n = shadow_db.execute("SELECT COUNT(*) FROM shadow_outcomes").fetchone()[0]
    assert n == (1 if settled else 0)
    if settled:  # regulation-time score, never extra time or penalties
        row = shadow_db.execute("SELECT home_goals_ft, away_goals_ft FROM shadow_outcomes").fetchone()
        assert tuple(row) == ft


def test_shadow_settle_script_refuses_live_db():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, str(root / "scripts" / "shadow_settle.py"), "--db",
                        str(root / "data" / "engine.db"), "--date", "2026-10-16", "--out", "/dev/null"],
                       capture_output=True, text=True, cwd=root)
    assert r.returncode == 2 and "isolated" in r.stderr


def test_overlapping_cycles_after_wake_are_skipped_not_duplicated(shadow_db):
    _seed_history(shadow_db)
    cycle.ensure_shadow_tables(shadow_db)
    cycle._ensure(shadow_db)
    shadow_db.execute("INSERT INTO shadow_cycles (cycle_id, started_at, discovery_ran, status) "
                      "VALUES ('stuck', ?, 0, 'running')", (iso_utc(T0 - timedelta(minutes=2)),))
    shadow_db.commit()
    p = FakeProvider()
    r = _run(shadow_db, p, T0)
    assert "skipped" in r and p.calls == []
    assert shadow_db.execute("SELECT status FROM shadow_cycles WHERE cycle_id = ?", (r["cycle_id"],)).fetchone()[0] \
        == "skipped"


def test_missed_slots_are_recorded_after_a_sleep(shadow_db):
    p = FakeProvider()
    _run(shadow_db, p, T0)
    r = _run(shadow_db, p, T0 + timedelta(minutes=161))   # host asleep: 4 half-hour slots missed
    assert (r["gap_minutes"], r["missed_slots"]) == (161.0, 4)
    rep = report.build_report(shadow_db)
    assert rep["cycle_health"]["missed_slots"] == 4 and rep["cycle_health"]["unfinished_cycles"] == 0


def test_daily_report_has_integrity_coverage_calibration_and_qualification(shadow_db):
    _seed_history(shadow_db)
    p = FakeProvider()
    p.odds = [_odds_item(T0 - timedelta(minutes=30))]
    _run(shadow_db, p, T0)
    rep = report.build_report(shadow_db)
    day = rep["daily"]["2030-01-05"]
    assert day["integrity"]["ok"] and day["observations"] == 1
    assert set(day) >= {"odds_coverage", "qualification", "identity"} and rep["integrity"]["ok"]


def test_observation_waits_for_the_decision_fetch_when_the_cap_is_hit(shadow_db):
    _seed_history(shadow_db)
    p = FakeProvider()
    p.odds = [_odds_item(T0 - timedelta(minutes=30))]
    r = _run(shadow_db, p, T0, cfg=ShadowConfig(max_requests_per_cycle=2))   # 2 discovery requests, no odds fetch
    assert r["observations"] == 0 and r["observations_deferred"] == 1
    r2 = _run(shadow_db, p, T0 + timedelta(minutes=30))                      # fetch, then observe with a fresh price
    assert r2["observations"] == 1
    obs = shadow_db.execute("SELECT quote_status FROM shadow_observations").fetchone()
    assert obs[0] == "QUOTED"


def test_observation_is_not_deferred_past_the_limit(shadow_db):
    _seed_history(shadow_db)
    p = FakeProvider()
    r = _run(shadow_db, p, KICKOFF - timedelta(minutes=50), cfg=ShadowConfig(max_requests_per_cycle=2))
    assert r["observations"] == 1                                            # recorded honestly as NO_QUOTES
    assert shadow_db.execute("SELECT quote_status FROM shadow_observations").fetchone()[0] in ("NO_QUOTES", "UNMAPPED")


def test_low_profile_observes_only_its_competitions(shadow_db):
    from src.shadow.cycle import PROFILES

    _seed_history(shadow_db)
    p = FakeProvider()
    p.fixtures = [_fixture_item(), _fixture_item(fid=889, league_id=40)]          # EPL and Championship
    p.odds = [_odds_item(T0 - timedelta(minutes=30))]
    r = _run(shadow_db, p, T0, cfg=PROFILES["low"])
    assert r["fixtures_inserted"] == 1                                             # Championship not tracked
    assert [c for c in p.calls if "/odds" in c] == [f"https://{HOST}/odds?fixture=777"]


def test_medium_profile_has_no_closing_capture(shadow_db):
    from src.shadow.cycle import PROFILES

    _seed_history(shadow_db)
    p = FakeProvider()
    p.odds = [_odds_item(T0 - timedelta(minutes=30))]
    _run(shadow_db, p, T0, cfg=PROFILES["medium"])
    r = _run(shadow_db, p, KICKOFF - timedelta(minutes=10), cfg=PROFILES["medium"])
    assert r["odds_due"] == {"decision": 0, "closing": 0}


def test_dates_are_not_rediscovered_within_the_window(shadow_db):
    p = FakeProvider()
    _run(shadow_db, p, T0, force_discovery=True)
    _run(shadow_db, p, T0 + timedelta(hours=13), force_discovery=True)            # 12 h interval passed
    fixture_calls = [c for c in p.calls if "/fixtures" in c]
    assert len(fixture_calls) == 3                                                 # 2 dates, then only the new date


def test_budget_estimates_rank_the_profiles():
    from src.shadow.budget import estimate
    from src.shadow.cycle import LOW_COST_COMPETITIONS

    low = estimate(LOW_COST_COMPETITIONS)
    medium = estimate()
    full = estimate(closing_capture=True)
    assert low["requests_14d"] < medium["requests_14d"] < full["requests_14d"]
    assert medium["within_daily_budget_on_peak"] and not full["within_daily_budget_on_peak"]


def test_cli_applies_the_requested_profile(tmp_path, monkeypatch):
    import importlib.util
    import sqlite3 as _sq
    import sys as _sys
    from pathlib import Path

    from src.shadow import cycle as cycle_mod

    root = Path(__file__).resolve().parent.parent
    db = tmp_path / "s.db"
    _sq.connect(db).close()
    seen = {}
    monkeypatch.setattr(cycle_mod, "run_cycle", lambda conn, key, host, cfg, **kw: seen.update(cfg=cfg) or {})
    monkeypatch.setattr("src.db.database._connection", _sq.connect(":memory:"))
    spec = importlib.util.spec_from_file_location("shadow_cycle_cli", root / "scripts" / "shadow_cycle.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    for profile, competitions, closing in (("medium", None, False), ("low", cycle_mod.LOW_COST_COMPETITIONS, False),
                                           ("full", None, True)):
        monkeypatch.setattr(_sys, "argv", ["shadow_cycle.py", "--db", str(db), "--budget", "0", "--profile", profile])
        assert cli.main() == 0
        assert (seen["cfg"].competitions, seen["cfg"].closing_capture, seen["cfg"].max_requests_per_cycle) == \
            (competitions, closing, 20)
    monkeypatch.setattr(_sys, "argv", ["shadow_cycle.py", "--db", str(db), "--budget", "0", "--profile", "medium",
                                       "--max-requests-per-cycle", "5"])
    cli.main()
    assert seen["cfg"].max_requests_per_cycle == 5 and seen["cfg"].closing_capture is False


class _Failing(FakeProvider):
    def __call__(self, url, headers, timeout):
        if "/odds" in url:
            self.calls.append(url)
            return 500, {}, b"error"
        return super().__call__(url, headers, timeout)


def test_daily_budget_holds_across_restarts_failures_and_retries(shadow_db, monkeypatch):
    from src.shadow.cycle import PROFILES

    monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "4")
    _seed_history(shadow_db)
    p = _Failing()
    for i in range(6):   # six cycles, each as if the process had restarted
        _run(shadow_db, p, T0 + timedelta(minutes=5 * i), cfg=PROFILES["medium"])
    logged = shadow_db.execute("SELECT status, COUNT(*) FROM provider_quota_log GROUP BY 1").fetchall()
    by = dict((r[0], r[1]) for r in logged)
    assert by.get("ok", 0) + by.get("error", 0) <= 4                 # never above the daily budget
    odds_attempts = shadow_db.execute(
        "SELECT COUNT(*) FROM provider_quota_log WHERE endpoint LIKE '/odds%' AND status != 'refused'").fetchone()[0]
    assert odds_attempts == 2                                          # failing fixture retried once, then capped

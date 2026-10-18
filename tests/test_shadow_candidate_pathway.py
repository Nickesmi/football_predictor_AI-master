"""M3.4 Package A: the forward shadow pathway is the candidate pipeline (rh1 + identity-v1 + K53 + bookmaker-only)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.odds import store
from src.odds.model import OddsType, Quote, iso_utc
from src.shadow import cycle
from src.shadow.cycle import PROFILES

DECIDED = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
KICKOFF = DECIDED + timedelta(minutes=100)


def _fx(conn, fid, home, away, date, kickoff, league_id=39, hg=None, ag=None, status="FT", label="Premier League"):
    conn.execute("INSERT INTO matches (id, date, kickoff, home_team, away_team, league_name, league_id, status, "
                 "provider, home_goals, away_goals) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'api_football', ?, ?)",
                 (fid, date, kickoff, home, away, label, league_id, status, hg, ag))


def _match(fid="up1", home="Everton", away="Chelsea"):
    return {"id": fid, "home_team": home, "away_team": away, "league_name": "Premier League", "league_id": 39,
            "provider": "api_football", "country": ""}


@pytest.fixture
def db(engine_db, monkeypatch):
    monkeypatch.setattr(cycle, "_bilqe", lambda m: {"skipped": "test"})
    cycle._ensure(engine_db)
    return engine_db


def test_medium_profile_is_the_candidate_configuration():
    cfg = PROFILES["medium"]
    assert (cfg.history, cfg.competitions, cfg.closing_capture, cfg.max_requests_per_cycle,
            cfg.max_attempts_per_window) == ("rh1", None, False, 20, 2)


def test_observation_uses_rh1_identity_and_never_team_state(db):
    from src.db.team_state import TeamState, upsert_team_state
    from src.history.reconstruct import reconstruct

    # a misleading team_state row (like production's contaminated records) must be ignored
    upsert_team_state(db, TeamState(team_name="Everton", league="Premier League", venue="overall", elo=1500,
                                    rolling_scored=4.0, rolling_conceded=0.1, form_last5=1.0, matches_played=30))
    _fx(db, "h1", "Everton", "Liverpool", "2026-09-20", "15:00", hg=1, ag=2)
    _fx(db, "h2", "Chelsea", "Everton", "2026-09-27", "15:00", hg=0, ag=0)
    _fx(db, "up1", "Everton", "Chelsea", "2026-10-01", iso_utc(KICKOFF), status="NS")
    reconstruct(db)
    row = cycle.observe(db, _match(), iso_utc(KICKOFF), DECIDED, "c1", history="rh1")
    ident = json.loads(row["identity_json"])
    inputs = json.loads(row["probability_inputs_json"])
    assert ident["resolved"] and ident["history_version"] == "rh1"
    assert ident["home"]["source"] == "rh1_competition_fixtures"
    assert ident["home"]["canonical_team_id"].startswith("cth-")
    assert ident["home"]["competition_ids"] == {"api_football": 39, "sofascore": 17}
    assert inputs["home_stats"]["matches_played"] == 2                     # rh1, not the team_state "30"
    assert inputs["home_stats"]["scored"] != round(4.0 * 1.12, 2)


def test_a_result_is_unusable_before_its_availability_timestamp(db):
    from src.history.reconstruct import reconstruct

    _fx(db, "h1", "Everton", "Liverpool", "2026-09-20", "15:00", hg=1, ag=2)
    _fx(db, "h0", "Chelsea", "Arsenal", "2026-09-20", "15:00", hg=1, ag=1)
    # Everton's latest match kicked off 2 h before the decision: its result is not yet "available" (+3 h)
    _fx(db, "h2", "Everton", "Brighton", "2026-10-01", iso_utc(DECIDED - timedelta(hours=2)), hg=5, ag=0)
    for fid in ("u1", "u2"):
        _fx(db, fid, "Everton", "Chelsea", "2026-10-01", iso_utc(KICKOFF), status="NS")
    reconstruct(db)
    early = cycle.observe(db, _match("u1"), iso_utc(KICKOFF), DECIDED, "c", history="rh1")
    later = cycle.observe(db, _match("u2"), iso_utc(KICKOFF), DECIDED + timedelta(hours=1, minutes=1), "c",
                          history="rh1")
    assert json.loads(early["probability_inputs_json"])["home_stats"]["matches_played"] == 1
    assert json.loads(later["probability_inputs_json"])["home_stats"]["matches_played"] == 2


def test_unresolved_or_unknown_teams_get_no_forecast(db):
    from src.history.reconstruct import reconstruct

    _fx(db, "h1", "Everton", "Liverpool", "2026-09-20", "15:00", hg=1, ag=2)
    _fx(db, "up1", "Everton", "Chelsea U21", "2026-10-01", iso_utc(KICKOFF), status="NS")
    reconstruct(db)
    row = cycle.observe(db, _match(away="Chelsea U21"), iso_utc(KICKOFF), DECIDED, "c", history="rh1")
    assert row["error"] == "NO_FORECAST: TEAM_IDENTITY_UNRESOLVED" and json.loads(row["candidates_json"]) == []


def _q(kind=OddsType.BOOKMAKER, book="bet365"):
    return Quote(provider="api_football", provider_event_id="e", bookmaker=book, market="1X2", selection="home",
                 line="", decimal_odds=2.1, observed_at=iso_utc(DECIDED - timedelta(minutes=60)),
                 commence_time=iso_utc(KICKOFF), odds_type=kind)


def test_only_genuine_bookmaker_prices_reach_a_shadow_decision(db, monkeypatch):
    from src.history.reconstruct import reconstruct

    _fx(db, "h1", "Everton", "Liverpool", "2026-09-20", "15:00", hg=1, ag=2)
    _fx(db, "h2", "Chelsea", "Everton", "2026-09-27", "15:00", hg=0, ag=0)
    _fx(db, "up1", "Everton", "Chelsea", "2026-10-01", iso_utc(KICKOFF), status="NS")
    reconstruct(db)
    store.store_quotes(db, "up1", [_q(OddsType.SYNTHETIC_TEST, "synthetic")], iso_utc(DECIDED - timedelta(minutes=50)))
    monkeypatch.setenv("APP_ENV", "development")                   # shadow never runs in the test environment
    row = cycle.observe(db, _match(), iso_utc(KICKOFF), DECIDED, "c", history="rh1")
    assert json.loads(row["candidates_json"]) == []                 # synthetic prices are invisible outside tests
    store.store_quotes(db, "up1", [_q()], iso_utc(DECIDED - timedelta(minutes=40)))
    db.execute("DELETE FROM shadow_observations")                   # a second, independent observation
    row = cycle.observe(db, _match("up1"), iso_utc(KICKOFF), DECIDED, "c2", history="rh1")
    cands = json.loads(row["candidates_json"])
    assert {c["odds_type"] for c in cands} == {"bookmaker"} and {c["bookmaker"] for c in cands} == {"bet365"}


def test_shadow_pathway_reproduces_the_candidate_rh1_golden(monkeypatch):
    """Forward-style: the shadow observation at the golden's decision time gives the golden's probabilities."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from golden import test_golden_identity_rh1 as g

    golden_file = g.FILES.get(sys.version_info[:2])
    if golden_file is None:
        pytest.skip("no rh1 golden for this Python")
    expected = json.loads(golden_file.read_text())
    import src.db.database as database

    conn = g.build_seed()                                            # the golden's own seeded database
    monkeypatch.setattr(database, "_connection", conn)
    monkeypatch.setattr(cycle, "_bilqe", lambda m: {"skipped": "test"})
    cycle._ensure(conn)
    checked = 0
    for home, away, league, lid in g.PREDICT:
        key = f"{home} v {away} [{league}:{lid}]"
        fid = f"p-{home}-{away}"
        conn.execute("INSERT INTO matches (id, date, kickoff, home_team, away_team, league_name, league_id, status, "
                     "provider) VALUES (?, '2026-10-01', ?, ?, ?, ?, ?, 'NS', 'api_football')",
                     (fid, iso_utc(g.DECIDED_AT + timedelta(minutes=100)), home, away, league, lid))
        m = {"id": fid, "home_team": home, "away_team": away, "league_name": league, "league_id": lid,
             "provider": "api_football", "country": ""}
        row = cycle.observe(conn, m, iso_utc(g.DECIDED_AT + timedelta(minutes=100)), g.DECIDED_AT, "golden", "rh1")
        want = expected[key]["rh1"]["probabilities"]
        if want == "NO_FORECAST":
            assert row["error"] == "NO_FORECAST: TEAM_IDENTITY_UNRESOLVED", key
        else:
            got = json.loads(row["raw_probabilities_json"])
            assert g._close(json.loads(json.dumps(want)), got), key
        checked += 1
    assert checked == len(g.PREDICT)

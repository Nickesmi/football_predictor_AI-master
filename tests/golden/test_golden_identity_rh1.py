"""Candidate golden reference: identity-v1 on reconstructed point-in-time history (identity-v1-rh1, M3.3).

All earlier references stay in place and pass unchanged (v3-m2.5 legacy, history/*,
identity-v1-m3.2). This reference captures, at a fixed decision time:
  * legacy: estimate_probabilities_from_inputs(resolve_probability_inputs(...))
    from the seeded team_state (which mixes clubs and competitions like production);
  * rh1: the same estimate from resolve_probability_inputs_rh1 on history rebuilt
    from the seeded fixtures, or the fail-closed status.
A fixture played after the decision time is seeded on purpose; it must not count.

Regenerate ONLY for an approved behaviour change:
    GOLDEN_UPDATE=1 python -m pytest tests/golden/test_golden_identity_rh1.py -q
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import src.db.database as database
import src.engine.isotonic_calibrator as isotonic_calibrator

GOLDEN_DIR = Path(__file__).parent
VERSION = "identity-v1-rh1-m3.3"
FILES = {(3, 11): GOLDEN_DIR / f"golden_identity.{VERSION}.py311.json",
         (3, 13): GOLDEN_DIR / f"golden_identity.{VERSION}.py313.json"}
FILE = FILES.get(sys.version_info[:2])
DECIDED_AT = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
ABS_TOL = 1e-9

# legacy team_state rows (label-keyed, contaminated like production)
TEAM_STATE = [
    ("Everton", "Premier League", 4, 1.30, 1.25), ("Chelsea", "Premier League", 4, 1.55, 1.00),
    ("Arsenal", "Premier League", 8, 0.80, 1.70),            # mostly another club's results
    ("Liverpool", "Premier League", 4, 1.70, 0.90),
    ("Internacional", "Serie A", 11, 1.50, 1.10),             # Brazil, same label
    ("Bologna", "Serie A", 4, 1.20, 1.20),
]
# (home, away, hg, ag, date, kickoff, league id, label)
FIXTURES_PLAYED = [
    ("Everton", "Chelsea", 1, 2, "2026-09-06", "15:00", 39, "Premier League"),
    ("Chelsea", "Liverpool", 2, 2, "2026-09-13", "15:00", 39, "Premier League"),
    ("Liverpool", "Everton", 3, 0, "2026-09-20", "15:00", 39, "Premier League"),
    ("Arsenal", "Everton", 2, 0, "2026-09-27", "15:00", 39, "Premier League"),
    ("Arsenal", "Dinamo B", 0, 3, "2026-09-02", "17:00", 116, "Premier League"),   # other country
    ("Arsenal", "Neman", 1, 4, "2026-09-16", "17:00", 116, "Premier League"),
    ("Inter", "Bologna", 2, 0, "2026-09-14", "20:45", 135, "Serie A"),
    ("Bologna", "Inter", 1, 1, "2026-09-21", "20:45", 135, "Serie A"),
    ("Internacional", "Gremio", 2, 1, "2026-09-15", "21:30", 71, "Serie A"),         # Brazil
    ("Middlesbrough", "Cardiff", 2, 0, "2026-09-13", "15:00", 40, "Championship"),
    ("Cardiff", "Middlesbrough", 1, 1, "2026-09-27", "15:00", 40, "Championship"),
    ("Gent", "Club Brugge", 1, 2, "2026-09-20", "18:00", 144, "Jupiler Pro League"),
    ("Rangers", "Hibernian", 3, 1, "2026-09-20", "15:00", 179, "Premiership"),
    ("Everton", "Liverpool", 0, 4, "2026-10-04", "15:00", 39, "Premier League"),     # AFTER the decision
]
PREDICT = [
    ("Everton", "Chelsea", "Premier League", 39), ("Arsenal", "Liverpool", "Premier League", 39),
    ("Inter", "Bologna", "Serie A", 135), ("Middlesbrough", "Cardiff", "Championship", 40),
    ("Gent", "Club Brugge", "Jupiler Pro League", 144), ("Rangers", "Hibernian", "Premiership", 179),
    ("Chelsea U21", "Everton U21", "Premier League", 39), ("Newcomer FC", "Chelsea", "Premier League", 39),
]


@pytest.fixture
def seeded(monkeypatch):
    from src.db.team_state import TeamState, upsert_team_state
    from src.history.reconstruct import reconstruct

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    database.init_db(conn)
    for name, label, played, scored, conceded in TEAM_STATE:
        upsert_team_state(conn, TeamState(team_name=name, league=label, venue="overall", elo=1500.0,
                                          rolling_scored=scored, rolling_conceded=conceded, form_last5=0.5,
                                          matches_played=played))
    for i, (h, a, hg, ag, d, k, lid, label) in enumerate(FIXTURES_PLAYED):
        conn.execute("INSERT INTO matches (id, date, kickoff, home_team, away_team, league_name, league_id, status, "
                     "provider, home_goals, away_goals) VALUES (?, ?, ?, ?, ?, ?, ?, 'FT', 'matches_db', ?, ?)",
                     (f"g{i}", d, k, h, a, label, lid, hg, ag))
    conn.commit()
    reconstruct(conn)
    monkeypatch.setattr(database, "_connection", conn)
    monkeypatch.setattr(isotonic_calibrator, "_calibrator_instance", None)
    yield conn
    conn.close()


def _capture(conn) -> dict:
    from src.engine.probability_engine import estimate_probabilities_from_inputs, resolve_probability_inputs
    from src.identity.inputs import UnresolvedTeamIdentity, resolve_probability_inputs_rh1

    out = {}
    for home, away, league, lid in PREDICT:
        m = {"id": f"{home}-{away}", "home_team": home, "away_team": away, "league_name": league, "league_id": lid,
             "provider": "api_football", "country": ""}
        entry = {"legacy": estimate_probabilities_from_inputs(resolve_probability_inputs(m))}
        try:
            inputs = resolve_probability_inputs_rh1(conn, m, DECIDED_AT)
            entry["rh1"] = {"probabilities": estimate_probabilities_from_inputs(inputs),
                            "teams": {s: {k: inputs["identity"][s][k] for k in (
                                "status", "matches_played", "canonical_team_id", "competition_ids")} | {
                                "as_of_fixture_id": inputs["identity"][s]["evidence"]["as_of_fixture_id"]}
                                for s in ("home", "away")}}
        except UnresolvedTeamIdentity as exc:
            entry["rh1"] = {"probabilities": "NO_FORECAST",
                            "teams": {s: {"status": r.status, "matches_played": r.matches_played}
                                      for s, r in exc.resolutions.items()}}
        out[f"{home} v {away} [{league}:{lid}]"] = entry
    return json.loads(json.dumps(out, sort_keys=True, default=str))


def _close(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= ABS_TOL
    return a == b


@pytest.mark.skipif(FILE is None, reason="no rh1 golden for this Python")
def test_rh1_candidate_golden(seeded):
    actual = _capture(seeded)
    if os.getenv("GOLDEN_UPDATE") == "1":
        FILE.write_text(json.dumps(actual, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
        pytest.skip("rh1 golden regenerated")
    expected = json.loads(FILE.read_text())
    bad = [k for k in sorted(set(expected) | set(actual)) if not _close(expected.get(k), actual.get(k))]
    assert not bad, f"rh1 golden differs for {bad}"


def test_known_and_unresolved_teams(seeded):
    data = _capture(seeded)
    rh1 = {k.split(" [")[0]: v["rh1"] for k, v in data.items()}
    # point in time: Everton's 2026-10-04 match is after the decision and does not count
    assert rh1["Everton v Chelsea"]["teams"]["home"]["matches_played"] == 3
    # Arsenal: only its English fixture (competition 39), never the other country's club
    assert rh1["Arsenal v Liverpool"]["teams"]["home"]["matches_played"] == 1
    # Inter: own Serie A (135) fixtures, never Internacional (Brazil)
    assert rh1["Inter v Bologna"]["teams"]["home"]["matches_played"] == 2
    assert rh1["Gent v Club Brugge"]["teams"]["home"]["matches_played"] == 1
    assert rh1["Rangers v Hibernian"]["teams"]["home"]["matches_played"] == 1
    # unresolved: youth sides and a team without observed fixtures get no forecast
    assert rh1["Chelsea U21 v Everton U21"]["probabilities"] == "NO_FORECAST"
    assert rh1["Newcomer FC v Chelsea"]["probabilities"] == "NO_FORECAST"
    assert rh1["Newcomer FC v Chelsea"]["teams"]["home"]["status"] == "UNRESOLVED_NO_HISTORY"


def test_poisson_mathematics_unchanged_when_inputs_are_equal(seeded):
    """Same stats in -> same probabilities out: only inputs differ between legacy and rh1."""
    from src.engine.probability_engine import estimate_probabilities_from_inputs
    from src.identity.inputs import resolve_probability_inputs_rh1

    m = {"home_team": "Everton", "away_team": "Chelsea", "league_name": "Premier League", "league_id": 39,
         "provider": "api_football", "country": ""}
    inputs = resolve_probability_inputs_rh1(seeded, m, DECIDED_AT)
    stripped = {k: v for k, v in inputs.items() if k != "identity"}
    assert estimate_probabilities_from_inputs(inputs) == estimate_probabilities_from_inputs(stripped)

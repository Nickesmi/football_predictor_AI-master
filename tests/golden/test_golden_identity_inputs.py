"""Golden references for identity-checked probability inputs (identity-v1, M3.2).

The legacy references (golden_probabilities.v3-m2.5.*, history/*) are untouched
and remain the production references. This file adds a separate, versioned
reference for the identity-checked pathway (src/identity), captured on a seeded
in-memory DB built from the real M3 mismatch cases:

  * legacy: estimate_probabilities_from_inputs(resolve_probability_inputs(...))
  * identity: per-side resolution (status, record, canonical id, competition ids,
    source) and estimate_probabilities_from_inputs(resolve_probability_inputs_v2(...)),
    or the fail-closed status;
  * identity_with_proposed_registry: the same with the proposed registry entries
    applied (isolated comparison mode).

Every fixture where legacy and identity probabilities differ must be explained
by a legacy source other than "the same team_state record" (hash statistics,
static tables, substring matches, wrong history label) — enforced below.

Regenerate ONLY for an approved behaviour change:
    GOLDEN_UPDATE=1 python -m pytest tests/golden/test_golden_identity_inputs.py -q
"""
import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

import src.db.database as database
import src.engine.isotonic_calibrator as isotonic_calibrator

GOLDEN_DIR = Path(__file__).parent
IDENTITY_GOLDEN_VERSION = "identity-v1-m3.2"
FILES = {
    (3, 11): GOLDEN_DIR / f"golden_identity.{IDENTITY_GOLDEN_VERSION}.py311.json",
    (3, 13): GOLDEN_DIR / f"golden_identity.{IDENTITY_GOLDEN_VERSION}.py313.json",
}
FILE = FILES.get(sys.version_info[:2])
ABS_TOL = 1e-9

# (team_state name, label, matches, rolling scored, rolling conceded)
TEAM_STATE = [
    ("Everton", "Premier League", 6, 1.4, 1.2), ("Chelsea", "Premier League", 6, 1.7, 0.9),
    ("Liverpool", "Premier League", 8, 2.0, 0.8), ("Arsenal", "Premier League", 9, 1.1, 1.0),
    ("Middlesbrough", "Championship", 6, 1.5, 1.0), ("Cardiff", "Championship", 5, 1.2, 1.3),
    ("Espanyol", "La Liga", 4, 1.1, 1.3), ("Getafe", "La Liga", 4, 1.0, 1.0),
    ("Atletico Madrid", "La Liga", 4, 1.8, 0.7), ("Atlético Madrid", "LaLiga", 1, 2.0, 1.0),
    ("Sevilla", "La Liga", 4, 1.2, 1.2),
    ("SV Werder Bremen", "Bundesliga", 4, 1.3, 1.6), ("1. FC Union Berlin", "Bundesliga", 4, 1.2, 1.1),
    ("Hibernian", "Premiership", 6, 1.3, 1.2),
    ("Lazio", "UEFA Europa League", 3, 1.6, 0.9), ("Porto", "UEFA Europa League", 3, 1.5, 1.0),
]
# (fixture name, label, league id, count) -> stored source fixtures behind the records
HISTORY = [
    ("Everton", "Premier League", 39, 3), ("Chelsea", "Premier League", 39, 3), ("Liverpool", "Premier League", 39, 4),
    ("Arsenal", "Premier League", 116, 7), ("Arsenal", "Premier League", 39, 1),
    ("Middlesbrough", "Championship", 40, 3), ("Cardiff", "Championship", 40, 3),
    ("Espanyol", "La Liga", 140, 2), ("Getafe", "La Liga", 140, 2), ("Atletico Madrid", "La Liga", 140, 2),
    ("Sevilla", "La Liga", 140, 2), ("Werder Bremen", "Bundesliga", 78, 2), ("Union Berlin", "Bundesliga", 78, 2),
    ("Hibernian", "Premiership", 179, 3), ("Lazio", "UEFA Europa League", 3, 2), ("Porto", "UEFA Europa League", 3, 2),
]
# (home, away, league name, league id, provider)
FIXTURES = [
    ("Everton", "Chelsea", "Premier League", 39, "api_football"),
    ("Everton", "Chelsea", "Premier League", 17, "sofascore"),
    ("Middlesbrough", "Cardiff", "Championship", 40, "api_football"),
    ("Espanyol", "Getafe", "La Liga", 140, "api_football"),
    ("Werder Bremen", "Union Berlin", "Bundesliga", 78, "api_football"),
    ("Arsenal", "Liverpool", "Premier League", 39, "api_football"),
    ("Rangers", "Hibernian", "Premiership", 179, "api_football"),
    ("Atletico Madrid", "Sevilla", "La Liga", 140, "api_football"),
    ("Lazio", "Porto", "UEFA Europa League", 3, "api_football"),
    ("Chelsea U21", "Everton U21", "Premier League", 39, "api_football"),
]
EXPLAINED = {"HASH_SYNTHETIC", "league_table_exact", "all_leagues_table_exact", "league_table_SUBSTRING",
             "all_leagues_table_SUBSTRING", "team_state_LIKE_SUBSTRING", "team_state_other_record"}


@pytest.fixture
def seeded(monkeypatch):
    from src.db.team_state import TeamState, upsert_team_state

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    database.init_db(conn)
    for name, label, played, scored, conceded in TEAM_STATE:
        upsert_team_state(conn, TeamState(team_name=name, league=label, venue="overall", elo=1500.0,
                                          rolling_scored=scored, rolling_conceded=conceded, form_last5=0.5,
                                          matches_played=played))
    n = 0
    for name, label, league_id, count in HISTORY:
        for _ in range(count):
            n += 1
            conn.execute("INSERT INTO matches (id, date, home_team, away_team, league_name, league_id, status, provider) "
                         "VALUES (?, '2026-09-01', ?, 'Opponent', ?, ?, 'FT', 'matches_db')",
                         (f"h{n}", name, label, league_id))
    conn.commit()
    monkeypatch.setattr(database, "_connection", conn)
    monkeypatch.setattr(isotonic_calibrator, "_calibrator_instance", None)
    yield conn
    conn.close()


def _legacy_source(conn, name, venue, league_name):
    spec = importlib.util.spec_from_file_location(
        "tir", GOLDEN_DIR.parent.parent / "scripts" / "team_identity_report.py")
    tir = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tir)
    from src.engine.probability_engine import LEAGUE_KEY_MAP
    from src.ml import team_stats_db

    return tir.legacy_source(conn, team_stats_db, name, venue, LEAGUE_KEY_MAP.get(league_name, "default"))


def _capture(conn) -> dict:
    from src.engine.probability_engine import estimate_probabilities_from_inputs, resolve_probability_inputs
    from src.identity import registry
    from src.identity.competitions import competition_for_fixture
    from src.identity.inputs import resolve_identity, resolve_probability_inputs_v2
    from src.identity.teams import resolve_team

    proposed = registry.load(allow_proposed=True)
    out = {}
    for home, away, league, league_id, provider in FIXTURES:
        key = f"{home} v {away} [{league} {provider}:{league_id}]"
        match = {"id": key, "home_team": home, "away_team": away, "league_name": league, "league_id": league_id,
                 "provider": provider, "country": ""}
        entry = {"legacy": estimate_probabilities_from_inputs(resolve_probability_inputs(match)),
                 "legacy_sources": {v: _legacy_source(conn, match[f"{v}_team"], v, league) for v in ("home", "away")}}
        for label, reg in (("identity", None), ("identity_with_proposed_registry", proposed)):
            if reg is None:
                res = resolve_identity(conn, match)
            else:
                comp = competition_for_fixture(provider, league_id)
                res = {s: resolve_team(conn, comp, match[f"{s}_team"], provider, registry=reg) for s in ("home", "away")}
            sides = {s: {k: getattr(r, k) for k in ("status", "team_name_lower", "history_league", "matches_played",
                                                     "source", "canonical_team_id", "competition_ids")}
                     for s, r in res.items()}
            if all(r.resolved for r in res.values()):
                if reg is None:
                    inputs = resolve_probability_inputs_v2(conn, match)
                else:
                    inputs = _inputs_from(conn, match, res)
                entry[label] = {"teams": sides, "probabilities": estimate_probabilities_from_inputs(inputs)}
            else:
                entry[label] = {"teams": sides, "probabilities": "NO_FORECAST"}
        out[key] = entry
    return json.loads(json.dumps(out, sort_keys=True, default=str))


def _inputs_from(conn, match, resolutions):
    from dataclasses import asdict

    from src.engine.probability_engine import LEAGUE_KEY_MAP
    from src.identity.inputs import _venue_stats

    key = LEAGUE_KEY_MAP.get(match["league_name"], "default")
    return {"home_team": match["home_team"], "away_team": match["away_team"], "league_name": match["league_name"],
            "country": "", "league_key": key,
            "home_stats": asdict(_venue_stats(conn, resolutions["home"], "home", key)),
            "away_stats": asdict(_venue_stats(conn, resolutions["away"], "away", key))}


def _close(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= ABS_TOL
    return a == b


@pytest.mark.skipif(FILE is None, reason="no identity golden for this Python")
def test_identity_golden(seeded):
    actual = _capture(seeded)
    if os.getenv("GOLDEN_UPDATE") == "1":
        FILE.write_text(json.dumps(actual, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
        pytest.skip("identity golden regenerated")
    assert FILE.exists(), "identity golden missing; capture with GOLDEN_UPDATE=1"
    expected = json.loads(FILE.read_text())
    bad = [k for k in sorted(set(expected) | set(actual)) if not _close(expected.get(k), actual.get(k))]
    assert not bad, f"identity golden differs for {bad}"


def test_every_probability_change_is_attributed(seeded):
    data = _capture(seeded)
    for key, e in data.items():
        new = e["identity"]["probabilities"]
        if new == "NO_FORECAST":
            continue
        same_record = all(
            e["legacy_sources"][s] == "team_state_under_profile_key"
            and e["identity"]["teams"][s]["history_league"] in ("Premier League",)  # profile key == history label
            for s in ("home", "away"))
        if same_record:
            assert _close(e["legacy"], new), f"{key}: same record but different probabilities"
        else:
            changed = not _close({m: e["legacy"][m] for m in ("1X2", "O/U 2.5", "BTTS")},
                                 {m: new[m] for m in ("1X2", "O/U 2.5", "BTTS")})
            if changed:
                assert any(e["legacy_sources"][s].split(":")[0] in EXPLAINED
                           or e["identity"]["teams"][s]["history_league"] != e["legacy_sources"][s]
                           for s in ("home", "away")), key


def test_expected_fail_closed_cases(seeded):
    data = _capture(seeded)
    status = {k.split(" [")[0]: {s: v["identity"]["teams"][s]["status"] for s in ("home", "away")}
              for k, v in data.items()}
    assert status["Arsenal v Liverpool"]["home"] == "CONTAMINATED_HISTORY"
    assert status["Rangers v Hibernian"]["home"] == "UNRESOLVED_NO_HISTORY"
    assert status["Atletico Madrid v Sevilla"]["home"] == "AMBIGUOUS"
    assert status["Chelsea U21 v Everton U21"] == {"home": "UNRESOLVED_NO_HISTORY", "away": "UNRESOLVED_NO_HISTORY"}
    atl = next(v for k, v in data.items() if k.startswith("Atletico"))
    assert atl["identity_with_proposed_registry"]["teams"]["home"]["source"] == "registry:ESP-ATLETICO"


PINNED_LEGACY_REFERENCES = {
    "golden_probabilities.v3-m2.5.py311.json": "26a54d249723855346bd3f56f0112ae958abc77c50ce0ccf6439cb73a200a37a",
    "golden_probabilities.v3-m2.5.py313.json": "974df3535330b66bc7e2f26a79df2fc58756872b23310cdd4a4460cd18be5d53",
}


def test_legacy_references_are_untouched():
    import hashlib

    for name, sha in PINNED_LEGACY_REFERENCES.items():
        assert hashlib.sha256((GOLDEN_DIR / name).read_bytes()).hexdigest() == sha, name

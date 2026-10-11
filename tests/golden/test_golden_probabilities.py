"""Golden-probability regression test.

Freezes the production probability outputs of `estimate_probabilities()` (the
Poisson + synthetic-XGBoost blend used by run_pipeline) and of
`_compute_match_analysis()` (the user-facing analysis, incl. tiers, runtime
gate and BILQE verdicts) on a fixed, fully isolated input set.

Tolerance: absolute 1e-9 on every float. Production code already rounds its
outputs to <= 4 decimals, so this is effectively exact equality; the epsilon
only absorbs float repr noise.

Regenerate ONLY for an approved behaviour change:
    GOLDEN_UPDATE=1 python -m pytest tests/golden -q
"""
import json
import math
import os
import random
import re
import sqlite3
from pathlib import Path

import pytest

import src.db.database as database
import src.engine.isotonic_calibrator as isotonic_calibrator
from src.db.team_state import TeamState, upsert_team_state

GOLDEN_DIR = Path(__file__).parent
GOLDEN_FILE = GOLDEN_DIR / "golden_probabilities.json"
LEGACY_TABLES_SQL = GOLDEN_DIR / "legacy_learning_tables.sql"
ABS_TOL = 1e-9

SEEDED_TEAM_STATE = [
    ("Arsenal", "Premier League", 12, 1.9, 0.8, 0.8),
    ("Chelsea", "Premier League", 12, 1.5, 1.1, 0.6),
    ("Leeds United", "Premier League", 3, 1.0, 1.7, 0.2),
    ("Bologna", "Serie A", 4, 1.3, 1.2, 0.5),
    ("Friendly Town", "default", 2, 1.1, 1.4, 0.4),
    ("Friendly City", "default", 2, 1.3, 1.0, 0.6),
]

FIXTURES = [
    ("Arsenal", "Chelsea", "Premier League", "England"),
    ("Leeds United", "Arsenal", "Premier League", "England"),
    ("Liverpool", "Manchester City", "Premier League", "England"),
    ("Inter", "Bologna", "Serie A", "Italy"),
    ("Bayern Munich", "Borussia Dortmund", "Bundesliga", "Germany"),
    ("Real Madrid", "Barcelona", "LaLiga", "Spain"),
    ("Galatasaray", "Fenerbahce", "Süper Lig", "Turkey"),
    ("PSV", "Ajax", "Eredivisie", "Netherlands"),
    ("Celtic", "Rangers", "Scottish Premiership", "Scotland"),
    ("Malmo FF", "Liverpool", "UEFA Champions League", "Europe"),
    ("Zzyzx United", "Qwerty Athletic", "Allsvenskan", "Sweden"),
    ("Arsenal Women", "Chelsea Women", "Women's Super League", "England"),
    ("Brazil", "Argentina", "World Cup", "World"),
    ("Arsenal U21", "Chelsea U21", "Premier League 2", "England"),
    ("Friendly Town", "Friendly City", "Club Friendly Games", "World"),
]

ANALYSIS_FIXTURES = FIXTURES[:4] + [FIXTURES[10], FIXTURES[12], FIXTURES[14]]

# suggested_pick is a Fisher-Yates shuffle by design (match_analysis_service.py:1436);
# seeding makes it reproducible without changing production code.
SUGGESTED_PICK_SEED = 20261011

_DATE_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}")


@pytest.fixture
def isolated_engine(monkeypatch):
    """Seeded in-memory DB injected as the get_db() singleton; never touches data/engine.db."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    database.init_db(conn)
    conn.executescript(LEGACY_TABLES_SQL.read_text())
    for team, league, played, scored, conceded, form in SEEDED_TEAM_STATE:
        upsert_team_state(conn, TeamState(
            team_name=team, league=league, venue="overall",
            elo=1500.0 + 10 * played,
            rolling_scored=scored, rolling_conceded=conceded,
            form_last5=form, matches_played=played,
        ))
    conn.commit()
    monkeypatch.setattr(database, "_connection", conn)
    monkeypatch.setattr(isotonic_calibrator, "_calibrator_instance", None)
    yield conn
    conn.close()


def _canonical(value):
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, str) and _DATE_LIKE.match(value):
        return "<date>"
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _capture() -> dict:
    from api.services.match_analysis_service import _compute_match_analysis
    from src.engine.probability_engine import estimate_probabilities

    out = {"estimate_probabilities": {}, "match_analysis": {}}
    for home, away, league, country in FIXTURES:
        key = f"{home} v {away} [{league}]"
        out["estimate_probabilities"][key] = estimate_probabilities({
            "home_team": home, "away_team": away,
            "league_name": league, "country": country,
        })
    for home, away, league, _country in ANALYSIS_FIXTURES:
        key = f"{home} v {away} [{league}]"
        random.seed(SUGGESTED_PICK_SEED)
        out["match_analysis"][key] = _compute_match_analysis(
            home, away, league, shuffle_tiers=False, fixture_id="",
        )
    return _canonical(out)


def _diff(expected, actual, path="$"):
    if isinstance(expected, dict) and isinstance(actual, dict):
        for k in sorted(set(expected) | set(actual)):
            if k not in actual:
                yield f"{path}.{k}: missing"
            elif k not in expected:
                yield f"{path}.{k}: unexpected"
            else:
                yield from _diff(expected[k], actual[k], f"{path}.{k}")
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            yield f"{path}: length {len(expected)} != {len(actual)}"
        for i, (e, a) in enumerate(zip(expected, actual)):
            yield from _diff(e, a, f"{path}[{i}]")
    elif isinstance(expected, float) or isinstance(actual, float):
        if not (isinstance(expected, (int, float)) and isinstance(actual, (int, float))
                and not isinstance(expected, bool) and not isinstance(actual, bool)
                and abs(expected - actual) <= ABS_TOL):
            yield f"{path}: {expected!r} != {actual!r}"
    elif expected != actual:
        yield f"{path}: {expected!r} != {actual!r}"


def test_production_probabilities_match_golden(isolated_engine):
    actual = _capture()
    if os.getenv("GOLDEN_UPDATE") == "1":
        GOLDEN_FILE.write_text(json.dumps(actual, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
        pytest.skip("golden file regenerated")
    assert GOLDEN_FILE.exists(), "golden file missing; capture it with GOLDEN_UPDATE=1"
    expected = json.loads(GOLDEN_FILE.read_text())
    diffs = list(_diff(expected, actual))
    assert not diffs, f"{len(diffs)} golden differences, first 20:\n" + "\n".join(diffs[:20])


def test_golden_run_is_deterministic(isolated_engine):
    first = _capture()
    isotonic_calibrator._calibrator_instance = None
    second = _capture()
    assert list(_diff(first, second)) == []


def test_golden_inputs_exercise_the_synthetic_xgb_blend_both_ways():
    data = json.loads(GOLDEN_FILE.read_text())["estimate_probabilities"]
    sources = {v["source"] for v in data.values()}
    assert "hybrid" in sources, "no fixture exercises the nonzero synthetic-XGB weight path"
    assert any(v["data_quality"] < 45 for v in data.values()), "no fixture exercises the DQ<45 zero-weight path"

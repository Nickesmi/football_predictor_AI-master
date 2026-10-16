"""M3 Phase D: baseline selection policy v1 proposal (src/policy/baseline_v1.py)."""
import dataclasses
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.policy import baseline_v1 as v1
from src.policy.baseline_v1 import LeagueRecord, PolicyConfig

NOW = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
FROZEN = {"model_version": "m1", "calibration_version": "c1", "calibration_frozen": True}
APPROVED = PolicyConfig(approved_leagues=("Premier League",))


def _iso(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


def _c(match_id="m1", market="1X2", selection="home", prob=0.55, odds=2.10, **kw):
    base = {"match_id": match_id, "market": market, "selection": selection, "model_prob": prob, "odds": odds,
            "edge": round(prob - 1 / odds, 4), "odds_type": "bookmaker", "odds_observed_at": _iso(100),
            "odds_fetched_at": _iso(10), "data_quality": 75.0, "model_source": "poisson",
            "league_name": "Premier League"}
    base.update(kw)
    return base


def _codes(decision):
    return {r["code"] for r in decision.reasons}


def test_clean_candidate_in_approved_league_is_selected_with_flat_stake():
    [d] = v1.evaluate([_c()], NOW, FROZEN, config=APPROVED)
    assert d.selected and d.reasons == [] and d.stake_units == 1.0
    assert d.risk_adjusted_ev == round((0.55 - 0.03) * 2.10 - 1, 4)
    assert d.policy_version == v1.version(APPROVED)


@pytest.mark.parametrize("override, code", [
    (dict(market="Corners O/U 9.5"), "MARKET_NOT_ALLOWED"),
    (dict(market="AH"), "MARKET_NOT_ALLOWED"),
    (dict(odds_type="synthetic_test"), "ODDS_NOT_BOOKMAKER"),
    (dict(odds_type="crowd_vote"), "ODDS_NOT_BOOKMAKER"),
    (dict(odds_observed_at=_iso(241)), "ODDS_STALE"),
    (dict(odds_observed_at=None), "ODDS_AGE_UNKNOWN"),
    (dict(odds_observed_at=_iso(-5)), "ODDS_STALE"),            # a price from the future
    (dict(odds_fetched_at=_iso(61)), "ODDS_NOT_RECONFIRMED"),
    (dict(odds_fetched_at=None), "ODDS_FETCH_TIME_UNKNOWN"),
    (dict(odds=1.25, prob=0.90), "ODDS_OUT_OF_RANGE"),
    (dict(odds=7.0, prob=0.20), "ODDS_OUT_OF_RANGE"),
    (dict(data_quality=59.9), "INPUT_QUALITY_LOW"),
    (dict(data_quality=None), "INPUT_QUALITY_LOW"),
    (dict(model_source="fallback"), "MODEL_FALLBACK"),
    (dict(prob=0.70, odds=2.10), "EDGE_IMPLAUSIBLE"),
    (dict(prob=0.50, odds=2.06), "RISK_ADJUSTED_EV_TOO_LOW"),   # positive raw edge, not after margin
])
def test_each_rule_rejects_with_its_code(override, code):
    [d] = v1.evaluate([_c(**override)], NOW, FROZEN, config=APPROVED)
    assert not d.selected and code in _codes(d)


def test_unfrozen_or_missing_provenance_is_rejected():
    for prov in ({}, {**FROZEN, "calibration_frozen": False}, {**FROZEN, "model_version": None}):
        [d] = v1.evaluate([_c()], NOW, prov, config=APPROVED)
        assert "PROVENANCE_NOT_FROZEN" in _codes(d)


def test_unknown_league_never_qualifies_by_default():
    [d] = v1.evaluate([_c(league_name="Unknown League")], NOW, FROZEN, config=APPROVED)
    assert "LEAGUE_UNPROVEN" in _codes(d)
    [d] = v1.evaluate([_c()], NOW, FROZEN)  # nothing approved, no record
    assert "LEAGUE_UNPROVEN" in _codes(d)


def test_verified_record_qualifies_only_above_both_thresholds():
    ok = {"Premier League": LeagueRecord(settled=100, reliability=60.0)}
    few = {"Premier League": LeagueRecord(settled=99, reliability=90.0)}
    weak = {"Premier League": LeagueRecord(settled=500, reliability=59.9)}
    assert v1.evaluate([_c()], NOW, FROZEN, ok)[0].selected
    assert "LEAGUE_UNPROVEN" in _codes(v1.evaluate([_c()], NOW, FROZEN, few)[0])
    assert "LEAGUE_UNRELIABLE" in _codes(v1.evaluate([_c()], NOW, FROZEN, weak)[0])


def test_at_most_one_selection_per_match_keeps_highest_risk_adjusted_ev():
    cands = [_c(selection="home", prob=0.55, odds=2.10),
             _c(market="O/U 2.5", selection="over", prob=0.60, odds=1.95),
             _c(market="BTTS", selection="yes", prob=0.58, odds=1.90),
             _c(match_id="m2", prob=0.55, odds=2.10)]
    ds = v1.evaluate(cands, NOW, FROZEN, config=APPROVED)
    selected = [(d.match_id, d.market) for d in ds if d.selected]
    assert selected == [("m1", "O/U 2.5"), ("m2", "1X2")]
    assert all("CORRELATED_EXPOSURE" in _codes(d) for d in ds if d.match_id == "m1" and not d.selected)


def test_all_failures_are_reported_not_only_the_first():
    [d] = v1.evaluate([_c(market="Cards", odds_type="crowd_vote", data_quality=10)], NOW, {}, config=APPROVED)
    assert {"PROVENANCE_NOT_FROZEN", "MARKET_NOT_ALLOWED", "ODDS_NOT_BOOKMAKER", "INPUT_QUALITY_LOW"} <= _codes(d)


def test_thresholds_are_frozen_and_versioned():
    with pytest.raises(dataclasses.FrozenInstanceError):
        v1.DEFAULT_CONFIG.min_ev = 0.0
    relaxed = PolicyConfig(min_ev=0.0)
    assert v1.version(relaxed) != v1.version()
    assert v1.version() == v1.version(PolicyConfig())


def test_funnel_counts_reasons():
    ds = v1.evaluate([_c(), _c(match_id="m2", market="Cards")], NOW, FROZEN, config=APPROVED)
    f = v1.funnel(ds)
    assert (f["candidates"], f["selected"], f["rejections_by_reason"]) == (2, 1, {"MARKET_NOT_ALLOWED": 1})


def test_league_records_come_only_from_verified_settled_evidence(tmp_path):
    from src.db.database import init_db
    from src.integrity import evidence

    conn = sqlite3.connect(tmp_path / "e.db")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    assert v1.load_league_records(conn) == {}
    for i in range(3):
        conn.execute("INSERT INTO matches (id, date, kickoff, home_team, away_team, league_name) "
                     "VALUES (?, '2026-07-01', '20:00', ?, 'B', 'L')", (str(i), f"A{i}"))
        conn.execute(
            """INSERT INTO prediction_errors (fixture_id, match_date, league_name, home_team, away_team,
                   predicted_result, home_win_pct, draw_pct, away_win_pct, confidence, confidence_bucket,
                   actual_result, home_goals, away_goals, correct, created_at)
               VALUES (?, '2026-07-01', 'L', ?, 'B', 'home', ?, ?, ?, ?, 'x', 'home', 1, 0, 1, ?)""",
            (str(i), f"A{i}", *((33.0, 33.0, 33.0, 33.0) if i == 2 else (60.0, 25.0, 15.0, 60.0)),
             "2026-07-01 08:00:00"))
    conn.commit()
    evidence.record_classification(conn, evidence.classify_all(conn))
    records = v1.load_league_records(conn)
    assert records["L"].settled == 2  # the 33/33/33 placeholder is excluded

"""Full decision chain: fixture -> probabilities -> calibration -> real odds -> edge -> risk -> grade -> recorded decision.

Prices here are EXPLICITLY SYNTHETIC test quotes (odds_type 'synthetic_test'),
stored only in the per-test in-memory database and labelled on display.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from api.services.portfolio_service import auto_settle_picks
from src.engine.calibration import ProbabilityCalibrator
from src.engine.pipeline import _pick_calibrator, reproduce_probabilities, run_pipeline
from src.odds import store
from src.odds.model import iso_utc
from tests.fakes.odds import recent, synthetic_quote

DATE = "2030-01-01"
KICKOFF = "2030-01-01T00:00:00+00:00"
EVENT = {
    "id": "chain-1", "tournament": {"uniqueTournament": {"id": 17, "name": "Premier League"}},
    "homeTeam": {"name": "Liverpool"}, "awayTeam": {"name": "Ipswich Town"},
    "status": {"type": "notstarted"}, "homeScore": {}, "awayScore": {}, "startTimestamp": 1893456000,
    "vote": {"vote1": 10, "votex": 45, "vote2": 45},
}


def _decisions(conn, run_id):
    rows = conn.execute(
        """SELECT market, selection, decision, reasons_json, odds, odds_type, grade, forecast_json
           FROM selection_decisions WHERE run_id = ? ORDER BY market IS NOT NULL, market, selection""",
        (run_id,),
    ).fetchall()
    return [dict(r) | {"reasons": [x["code"] for x in json.loads(r["reasons_json"])]} for r in rows]


def _forecast(conn):
    """First run without any price: a forecast but NO ACTIONABLE BET."""
    result = run_pipeline(DATE, [EVENT], conn)
    [match_row] = _decisions(conn, result["decision_run_id"])
    return result, match_row


def _favourite(forecast, market="1X2"):
    """Most likely selection. Model inputs depend on shared team state, so tests never assume which side."""
    selection = max(forecast[market], key=forecast[market].get)
    return selection, forecast[market][selection]


def _price(conn, prices, market="1X2", line=""):
    quotes = [synthetic_quote(market, sel, price, KICKOFF, line=line) for sel, price in prices.items()]
    store.store_quotes(conn, "chain-1", quotes, recent(0))


def test_without_real_odds_the_forecast_is_kept_and_no_bet_is_actionable(engine_db):
    result, match_row = _forecast(engine_db)
    assert (result["final_picks"], result["no_actionable_bet"]) == (0, 1)
    assert (match_row["decision"], match_row["reasons"]) == ("NO_ACTIONABLE_BET", ["NO_REAL_ODDS"])
    forecast = json.loads(match_row["forecast_json"])
    assert sum(forecast["1X2"].values()) == pytest.approx(1.0, abs=1e-3)
    assert engine_db.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 0


def test_value_selection_becomes_a_labelled_pick_and_every_other_selection_has_a_reason(engine_db):
    _, match_row = _forecast(engine_db)
    forecast = json.loads(match_row["forecast_json"])
    favourite, _ = _favourite(forecast)
    # the favourite priced 20 pp under the model; the other two priced above it (no value)
    _price(engine_db, {sel: round(1 / (p - 0.20 if sel == favourite else p + 0.05), 2)
                       for sel, p in forecast["1X2"].items()})
    result = run_pipeline(DATE, [EVENT], engine_db)
    rows = _decisions(engine_db, result["decision_run_id"])
    assert [(r["market"], r["selection"], r["decision"], r["reasons"]) for r in rows] == [(None, None, "ACTIONABLE", [])] + [
        ("1X2", sel, "PICK" if sel == favourite else "REJECTED", [] if sel == favourite else ["NO_POSITIVE_EDGE"])
        for sel in ("away", "draw", "home")
    ]
    assert {r["odds_type"] for r in rows if r["market"]} == {"synthetic_test"}
    [shown] = result["picks"]
    assert shown["odds_source"] == "SYNTHETIC TEST ODDS (not a bookmaker price)"
    pick = dict(engine_db.execute("SELECT * FROM picks").fetchone())
    assert json.loads(pick["inputs_json"])["odds"]["odds_type"] == "synthetic_test"


def test_high_probability_without_edge_is_not_value(engine_db):
    _, match_row = _forecast(engine_db)
    favourite, p = _favourite(json.loads(match_row["forecast_json"]))
    _price(engine_db, {favourite: round(1 / (p + 0.03), 2)})  # the likeliest outcome, priced above the model
    result = run_pipeline(DATE, [EVENT], engine_db)
    rows = _decisions(engine_db, result["decision_run_id"])
    assert [(r["selection"], r["decision"], r["reasons"]) for r in rows] == [
        (None, "NO_ACTIONABLE_BET", ["ALL_SELECTIONS_REJECTED"]),
        (favourite, "REJECTED", ["NO_POSITIVE_EDGE"]),
    ]


def test_small_positive_edge_is_rejected_with_the_risk_threshold_it_failed(engine_db):
    _, match_row = _forecast(engine_db)
    favourite, p = _favourite(json.loads(match_row["forecast_json"]))
    _price(engine_db, {favourite: round(1 / (p - 0.02), 3)})  # ~2 pp edge, league minimum is 4 pp
    result = run_pipeline(DATE, [EVENT], engine_db)
    [_, home] = _decisions(engine_db, result["decision_run_id"])
    assert home["decision"] == "REJECTED"
    reason = json.loads(home["reasons_json"])[0]
    assert (reason["code"], reason["threshold"]) == ("EDGE_BELOW_LEAGUE_MINIMUM", 0.04)
    assert 0 < reason["value"] < 0.04


def test_stale_prices_and_markets_the_model_does_not_price_are_ignored(engine_db):
    _forecast(engine_db)
    old = iso_utc(datetime.now(timezone.utc) - timedelta(hours=3))
    store.store_quotes(engine_db, "chain-1", [synthetic_quote("1X2", "home", 9.0, KICKOFF, observed=old)], recent(0))
    _price(engine_db, {"home": 1.9, "away": 2.0}, market="AH", line="-0.5")
    _price(engine_db, {"over": 3.0}, market="TOTALS", line="3.5")
    result = run_pipeline(DATE, [EVENT], engine_db)
    rows = _decisions(engine_db, result["decision_run_id"])
    assert [(r["decision"], r["reasons"]) for r in rows] == [("NO_ACTIONABLE_BET", ["NO_REAL_ODDS"])]


def test_decisions_are_reproducible_from_identical_inputs(engine_db):
    _, match_row = _forecast(engine_db)
    favourite, p = _favourite(json.loads(match_row["forecast_json"]))
    _price(engine_db, {favourite: round(1 / (p - 0.20), 2)})
    first = run_pipeline(DATE, [EVENT], engine_db)
    second = run_pipeline(DATE, [EVENT], engine_db)

    assert _decisions(engine_db, first["decision_run_id"]) == _decisions(engine_db, second["decision_run_id"])
    pick = dict(engine_db.execute("SELECT * FROM picks ORDER BY id LIMIT 1").fetchone())
    calibrator = _pick_calibrator(engine_db)
    assert isinstance(calibrator, ProbabilityCalibrator)
    reproduced = reproduce_probabilities(pick["inputs_json"], calibrator)
    assert reproduced["calibrated"]["1X2"][favourite] == pytest.approx(pick["model_prob"], abs=1e-4)


def test_policy_gap_correlated_selections_on_one_match_are_not_limited(engine_db):
    """Characterises current policy (docs/KNOWN_ISSUES.md): the official pipeline has no
    correlated-exposure limit, so value on 1X2 and on O/U 2.5 in one match yields two picks."""
    _, match_row = _forecast(engine_db)
    forecast = json.loads(match_row["forecast_json"])
    result_side, p_result = _favourite(forecast)
    goals_side, p_goals = _favourite(forecast, "O/U 2.5")
    _price(engine_db, {result_side: round(1 / (p_result - 0.20), 2)})
    _price(engine_db, {goals_side: round(1 / (p_goals - 0.20), 2)}, market="TOTALS", line="2.5")
    result = run_pipeline(DATE, [EVENT], engine_db)
    picked = [(r["market"], r["selection"]) for r in _decisions(engine_db, result["decision_run_id"])
              if r["decision"] == "PICK"]
    assert picked == [("1X2", result_side), ("O/U 2.5", goals_side)]


def test_shadow_pick_settles_from_the_final_score_and_never_counts_toward_the_baseline(engine_db):
    from src.db.picks_repo import get_experiment_progress

    _, match_row = _forecast(engine_db)
    favourite, p = _favourite(json.loads(match_row["forecast_json"]))
    price = round(1 / (p - 0.20), 2)
    _price(engine_db, {favourite: price})
    run_pipeline(DATE, [EVENT], engine_db)
    assert auto_settle_picks(engine_db) == {"settled": 0, "remaining": 0}  # not finished yet

    home_goals, away_goals = {"home": (2, 0), "draw": (1, 1), "away": (0, 2)}[favourite]
    engine_db.execute("UPDATE matches SET status = 'FT', home_goals = ?, away_goals = ? WHERE id = 'chain-1'",
                      (home_goals, away_goals))
    engine_db.commit()
    assert auto_settle_picks(engine_db) == {"settled": 1, "remaining": 0}
    pick = dict(engine_db.execute("SELECT result, pnl_units, stake_units FROM picks").fetchone())
    assert pick["result"] == "won"
    assert pick["pnl_units"] == pytest.approx(round(pick["stake_units"] * (price - 1), 3))
    assert get_experiment_progress(engine_db)["settled_eligible"] == 0

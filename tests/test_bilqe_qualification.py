"""BILQE qualification: league-reliability wiring, no-odds rule, structured outcomes."""
import pytest

from src.engine import qualification_engine as qe

LAYER_KEYS = {
    "data_quality", "model_consensus", "calibration", "market_edge", "volatility",
    "xg_reality", "clv", "historical_roi", "liquidity", "similar_match",
}


def _analysis(probability=72.0, data_quality=88.0):
    return {
        "data_quality_score": data_quality,
        "poisson": {"xg": {"home": 1.6, "away": 1.1}},
        "averages": {"home": {"avg_goals_scored": 1.5}, "away": {"avg_goals_scored": 1.1}},
        "xgboost_predictions": [],
        "top_picks": [{"market": "Over 1.5 Goals", "probability": probability}],
    }


def _seed_adjustment(conn, league, sample_count, reliability, overconfidence=2.0):
    conn.execute(
        """INSERT INTO model_confidence_adjustments
               (league_name, sample_count, avg_confidence, actual_hit_rate, adjustment_factor,
                overconfidence, brier_score, reliability_score, status)
           VALUES (?, ?, 70.0, 68.0, 1.0, ?, 0.2, ?, 'trusted')""",
        (league, sample_count, overconfidence, reliability),
    )
    conn.commit()


def _sharp_odds(market, odds):
    # Explicitly synthetic test prices (never stored, never shown as bookmaker data).
    return [
        {"market": market, "odds": odds, "bookmaker": "pinnacle"},
        {"market": market, "odds": odds, "bookmaker": "bet365"},
    ]


def test_league_reliability_is_read_from_the_adjustment_dict(engine_db):
    _seed_adjustment(engine_db, "Test League", sample_count=600, reliability=82.0)
    result = qe.qualify_picks(_analysis(), "A", "B", "Test League", conn=engine_db)
    gate = result.layer_gate_results["calibration"]
    assert gate == {"passed": True, "score": 82.0, "rejection": ""}


def test_low_measured_reliability_is_a_legitimate_rejection(engine_db):
    _seed_adjustment(engine_db, "Weak League", sample_count=600, reliability=38.2)
    result = qe.qualify_picks(_analysis(), "A", "B", "Weak League", conn=engine_db)
    assert result.layer_gate_results["calibration"]["rejection"] == "League reliability 38 < 60 minimum"


def test_league_without_active_adjustment_keeps_the_unknown_default(engine_db):
    _seed_adjustment(engine_db, "Young League", sample_count=12, reliability=10.0)  # weight 0 -> inactive
    result = qe.qualify_picks(_analysis(), "A", "B", "Young League", conn=engine_db)
    assert result.layer_gate_results["calibration"] == {"passed": True, "score": 65.0, "rejection": ""}


def test_missing_odds_never_qualifies_a_pick(engine_db):
    _seed_adjustment(engine_db, "Strong League", sample_count=600, reliability=99.0)
    result = qe.qualify_picks(_analysis(probability=95.0, data_quality=100.0), "A", "B", "Strong League", conn=engine_db)
    assert result.qualified_picks == []
    [pick] = result.rejected_picks
    assert pick.rejection_reason == qe.NO_REAL_ODDS_REASON
    assert pick.layer_outcomes["market_edge"] == {"passed": False, "score": 0.0, "reason": qe.NO_REAL_ODDS_REASON}
    assert result.to_dict()["rejected_picks"][0]["failed_layers"] == ["market_edge"]


def test_real_edge_can_qualify_and_every_layer_is_recorded(engine_db):
    _seed_adjustment(engine_db, "Strong League", sample_count=600, reliability=90.0)
    # model 72 % vs implied 62.5 % -> edge 9.5 pp
    result = qe.qualify_picks(
        _analysis(probability=72.0, data_quality=90.0), "A", "B", "Strong League",
        bookmaker_odds=_sharp_odds("Over 1.5 Goals", 1.6), conn=engine_db,
    )
    [pick] = result.qualified_picks
    assert pick.tier in ("S", "A", "B")
    assert pick.edge == pytest.approx(9.5)
    assert set(pick.layer_outcomes) == LAYER_KEYS
    assert all(o["passed"] for o in pick.layer_outcomes.values())


def test_positive_probability_without_edge_is_rejected(engine_db):
    _seed_adjustment(engine_db, "Strong League", sample_count=600, reliability=90.0)
    # model 72 % vs implied 71.4 % -> edge 0.6 pp
    result = qe.qualify_picks(
        _analysis(probability=72.0), "A", "B", "Strong League",
        bookmaker_odds=_sharp_odds("Over 1.5 Goals", 1.4), conn=engine_db,
    )
    [pick] = result.rejected_picks
    assert pick.rejection_reason == "Edge 0.6% < 4% minimum"

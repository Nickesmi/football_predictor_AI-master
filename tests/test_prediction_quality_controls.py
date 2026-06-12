import sqlite3
import importlib.util
from pathlib import Path

from src.engine.calibration import ProbabilityCalibrator
from src.engine.probability_engine import estimate_probabilities
from src.engine.risk_control import apply_risk_filter, score_confidence


def test_estimate_probabilities_uses_coherent_market_groups():
    probs = estimate_probabilities({
        "id": "quality-test-1",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "league_name": "Premier League",
        "league_id": 17,
    })

    assert probs["source"] in {"poisson", "hybrid"}
    assert probs["data_quality"] >= 0
    assert round(sum(probs["1X2"].values()), 4) == 1.0
    assert round(sum(probs["O/U 2.5"].values()), 4) == 1.0
    assert round(sum(probs["BTTS"].values()), 4) == 1.0
    for market in ("1X2", "O/U 2.5", "BTTS"):
        assert all(0.0 <= p <= 1.0 for p in probs[market].values())


def test_calibrator_prefers_market_selection_history_when_available():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE picks (
            market TEXT,
            selection TEXT,
            model_prob REAL,
            result TEXT,
            created_at TEXT
        )"""
    )

    rows = []
    rows.extend(("BTTS", "yes", 0.72, "won", str(i)) for i in range(28))
    rows.extend(("BTTS", "yes", 0.72, "lost", str(i)) for i in range(2))
    rows.extend(("1X2", "home", 0.72, "lost", str(i)) for i in range(30))
    conn.executemany("INSERT INTO picks VALUES (?, ?, ?, ?, ?)", rows)

    calibrator = ProbabilityCalibrator(n_bins=10)
    calibrator.fit_from_db(conn)

    btts_yes = calibrator.calibrate(0.72, "BTTS", "yes")
    home_win = calibrator.calibrate(0.72, "1X2", "home")

    assert btts_yes > 0.85
    assert home_win < 0.30


def test_risk_filter_rejects_low_quality_longshot_edges():
    candidate = {
        "edge": 0.03,
        "odds": 10.0,
        "model_prob": 0.13,
        "home_team": "A",
        "away_team": "B",
        "market": "1X2",
        "selection": "away",
        "data_quality": 25.0,
    }
    profile = {
        "reliability_score": 4.0,
        "min_edge_threshold": 0.02,
        "max_stake_units": 0.25,
    }

    score_confidence(candidate, profile, "fallback")

    assert "thin_or_low_quality_data" in candidate["data_quality_flags"]
    assert apply_risk_filter([candidate]) == []


def test_main_analysis_keeps_realistic_probability_spread():
    module_path = Path(__file__).resolve().parents[1] / "api" / "main.py"
    spec = importlib.util.spec_from_file_location("football_predictor_api_main", module_path)
    api_main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api_main)

    analysis = api_main._compute_match_analysis(
        "Arsenal",
        "Chelsea",
        "Premier League",
        shuffle_tiers=False,
    )
    all_markets = [
        market
        for markets in analysis["full_analysis"].values()
        for market in markets
    ]
    top_picks = analysis["top_picks"]
    tiers = analysis["tiers"]

    assert top_picks
    assert min(pick["probability"] for pick in top_picks) >= 60.0
    markets_above_60 = [
        market for market in all_markets
        if market["probability"] >= 60.0
    ]
    assert len(top_picks) == len(markets_above_60)
    assert {pick["market"] for pick in top_picks} == {market["market"] for market in markets_above_60}
    assert [tier["id"] for tier in tiers] == ["tier1", "tier2", "tier3"]
    assert all("picks" in tier for tier in tiers)
    assert sum(tier["count"] for tier in tiers) == analysis["total_confident_picks"]
    assert sum(len(tier["picks"]) for tier in tiers) == len(top_picks)
    tier_prob_ranges = [
        [pick["probability"] for pick in tier["picks"]]
        for tier in tiers
    ]
    if tier_prob_ranges[0] and tier_prob_ranges[1]:
        assert min(tier_prob_ranges[0]) >= max(tier_prob_ranges[1])
    if tier_prob_ranges[1] and tier_prob_ranges[2]:
        assert min(tier_prob_ranges[1]) >= max(tier_prob_ranges[2])
    assert max(market["probability"] for market in all_markets) >= 75.0

    exact_or_sparse = [
        market for market in all_markets
        if "Exact" in market["market"] or "Win by" in market["market"]
    ]
    assert exact_or_sparse
    assert min(market["probability"] for market in exact_or_sparse) < 20.0

    handicap_markets = [
        market for market in all_markets
        if market.get("section") == "Handicaps"
    ]
    assert handicap_markets
    assert max(market["probability"] for market in handicap_markets) <= 78.0
    assert all("fair_odds" in market for market in handicap_markets)


def test_runtime_pick_gate_blocks_weak_market_family():
    from src.engine.performance_gate import RuntimePickGate

    gate = RuntimePickGate(
        market_performance={
            "btts": {
                "samples": 926,
                "hit_rate": 54.3,
                "avg_probability": 56.5,
                "brier": 0.251,
            },
            "goals": {
                "samples": 926,
                "hit_rate": 90.3,
                "avg_probability": 76.4,
                "brier": 0.111,
            },
        },
        league_reliability={"sample_count": 60, "actual_hit_rate": 72.0, "reliability_score": 70.0},
        data_quality=85.0,
    )

    assert not gate.evaluate("btts", 86.0).allowed
    assert gate.evaluate("goals", 86.0).allowed


def test_runtime_pick_gate_blocks_unreliable_league():
    from src.engine.performance_gate import RuntimePickGate

    gate = RuntimePickGate(
        market_performance={
            "goals": {
                "samples": 926,
                "hit_rate": 90.3,
                "avg_probability": 76.4,
                "brier": 0.111,
            },
        },
        league_reliability={"sample_count": 60, "actual_hit_rate": 44.0, "reliability_score": 30.0},
        data_quality=85.0,
    )

    assert not gate.evaluate("goals", 90.0).allowed

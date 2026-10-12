"""Synthetic-trained XGBoost must have zero influence on actionable probabilities and selection."""
import src.engine.qualification_engine as qe
import src.ml.hybrid_predictor as hp
from src.ml.feature_builder import TeamProfile
from tests.golden.test_golden_probabilities import isolated_engine  # noqa: F401  (fixture)


def _validated(monkeypatch, value: bool):
    fake = lambda: {"provenance": "TEST", "production_validated": value}  # noqa: E731
    monkeypatch.setattr(qe, "synthetic_model_provenance", fake)
    monkeypatch.setattr(hp, "synthetic_model_provenance", fake)


def _consensus_inputs(monkeypatch, analysis):
    seen = []
    real = qe._layer2_model_consensus

    def spy(model_probs):
        seen.append(dict(model_probs))
        return real(model_probs)

    monkeypatch.setattr(qe, "_layer2_model_consensus", spy)
    qe.qualify_picks(analysis, "Arsenal", "Chelsea", "Premier League")
    return seen


def _analysis_with_conflicting_xgb():
    from api.services.match_analysis_service import _compute_match_analysis

    analysis = _compute_match_analysis("Arsenal", "Chelsea", "Premier League", shuffle_tiers=False, fixture_id="")
    analysis["xgboost_predictions"] = [{"market_key": k, "probability": 1.0} for k in (
        "home_win", "draw", "away_win", "btts", "over_1_5", "over_2_5", "over_3_5", "ht_over_0_5",
    )]
    return analysis


def test_bilqe_consensus_ignores_synthetic_xgboost(isolated_engine, monkeypatch):  # noqa: F811
    analysis = _analysis_with_conflicting_xgb()
    seen = _consensus_inputs(monkeypatch, analysis)
    assert len(seen) == len(analysis["top_picks"])
    assert all(set(probs) == {"poisson"} for probs in seen)


def test_bilqe_gate_is_provenance_driven_not_hardcoded(isolated_engine, monkeypatch):  # noqa: F811
    _validated(monkeypatch, True)
    analysis = _analysis_with_conflicting_xgb()
    seen = _consensus_inputs(monkeypatch, analysis)
    mapped = [p for p in analysis["top_picks"] if qe._market_to_xgb_key(p["market"])]
    assert sum("xgboost" in probs for probs in seen) == len(mapped)


def _profile(name, scored, conceded):
    return TeamProfile(
        team_name=name, matches_played=20, avg_scored=scored, avg_conceded=conceded,
        avg_total_goals=scored + conceded, btts_rate=0.5, clean_sheet_rate=0.3, failed_to_score_rate=0.25,
        over_1_5_rate=0.75, over_2_5_rate=0.5, over_0_5_ht_rate=0.7, form_last5=9.0, goal_diff=5.0,
    )


def test_live_odds_hybrid_uses_poisson_only():
    prediction = hp.HybridPredictor().predict(_profile("A", 1.9, 0.9), _profile("B", 1.2, 1.4), "A", "B")
    with_xgb = [m for m in prediction.unified_markets if m["xgb_prob"] is not None]
    assert len(with_xgb) > 0  # the synthetic model still ran and is shown for reference
    for market in prediction.unified_markets:
        assert market["source"] == "poisson"
        assert market["probability"] == market["poisson_prob"]


def test_live_odds_hybrid_blend_is_provenance_driven(monkeypatch):
    _validated(monkeypatch, True)
    prediction = hp.HybridPredictor().predict(_profile("A", 1.9, 0.9), _profile("B", 1.2, 1.4), "A", "B")
    assert {m["source"] for m in prediction.unified_markets if m["xgb_prob"] is not None} == {"hybrid"}

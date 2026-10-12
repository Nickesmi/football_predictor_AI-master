"""Outcome records feeding league reliability must hold the real prediction, never a 33/33/33 placeholder."""
from fastapi import BackgroundTasks

from src.db.error_intelligence import prediction_from_analysis
from tests.fakes import providers


def test_helper_extracts_the_real_prediction():
    analysis = {
        "poisson": {"result": {"home_win": 57.2, "draw": 24.2, "away_win": 18.6}},
        "score_prediction": {"expected_goals": {"home": 1.9, "away": 0.8}},
    }
    assert prediction_from_analysis(analysis) == {
        "home_win_pct": 57.2, "draw_pct": 24.2, "away_win_pct": 18.6,
        "pred_home_goals": 1.9, "pred_away_goals": 0.8,
    }


def test_helper_never_invents_a_prediction():
    assert prediction_from_analysis({}) is None
    assert prediction_from_analysis({"prediction": {"result": {}}}) is None
    assert prediction_from_analysis({"poisson": {"result": {"home_win": 50.0, "draw": 30.0}}}) is None


def test_results_verification_records_the_analysis_probabilities(monkeypatch):
    from api.services.match_analysis_service import _compute_match_analysis
    from api.services.results_service import get_results_verification_service
    from src.db.database import get_db

    providers.install(monkeypatch)
    get_results_verification_service("2026-07-04", BackgroundTasks())

    row = get_db().execute(
        "SELECT home_win_pct, draw_pct, away_win_pct FROM prediction_errors WHERE fixture_id = '9000000'"
    ).fetchone()
    expected = _compute_match_analysis("Liverpool", "Manchester City", "Premier League", shuffle_tiers=False)
    result = expected["poisson"]["result"]
    assert tuple(row) == (result["home_win"], result["draw"], result["away_win"])
    assert tuple(row) != (33.0, 33.0, 33.0)

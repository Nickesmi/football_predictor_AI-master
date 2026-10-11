"""
Regression tests for lineup_info.py and no_bet_engine.py (Phase 3 §6/§15).
"""

from __future__ import annotations

import pytest

from src.prediction_service.lineup_info import (
    Availability, LineupInfo, PlayerStatus, compute_lineup_uncertainty_penalty, DEFAULT_UNKNOWN_PENALTY,
)
from src.prediction_service.no_bet_engine import evaluate_bet


# ── lineup_info ──────────────────────────────────────────────────────

def test_no_lineup_data_source_defaults_to_unknown_penalty_not_zero():
    penalty, reasons = compute_lineup_uncertainty_penalty(None)
    assert penalty == DEFAULT_UNKNOWN_PENALTY
    assert "UNKNOWN" in reasons[0]


def test_all_players_confirmed_gives_zero_penalty():
    info = LineupInfo(
        home_team="A", away_team="B", source="test", retrieved_at="2024-01-01T00:00:00Z",
        home_players=[PlayerStatus("Star Player", Availability.CONFIRMED, is_key_player=True)],
        away_players=[PlayerStatus("Other Player", Availability.CONFIRMED)],
    )
    penalty, reasons = compute_lineup_uncertainty_penalty(info)
    assert penalty == 0.0


def test_unknown_key_player_penalized_more_than_unknown_squad_player():
    key_unknown = LineupInfo(
        home_team="A", away_team="B", source="test", retrieved_at="2024-01-01T00:00:00Z",
        home_players=[PlayerStatus("Star Striker", Availability.UNKNOWN, is_key_player=True)],
    )
    squad_unknown = LineupInfo(
        home_team="A", away_team="B", source="test", retrieved_at="2024-01-01T00:00:00Z",
        home_players=[PlayerStatus("Backup Defender", Availability.UNKNOWN, is_key_player=False)],
    )
    p_key, _ = compute_lineup_uncertainty_penalty(key_unknown)
    p_squad, _ = compute_lineup_uncertainty_penalty(squad_unknown)
    assert p_key > p_squad


def test_penalty_never_fabricates_availability_it_only_ever_subtracts():
    """Structural guard: this module has no code path that sets a player's
    status FROM the penalty function — it only reads statuses in."""
    import inspect
    from src.prediction_service import lineup_info
    source = inspect.getsource(lineup_info.compute_lineup_uncertainty_penalty)
    assert "Availability.CONFIRMED" not in source.split("if p.availability")[0]  # never assigns confirmed as a default


def test_penalty_is_capped():
    many_unknown = LineupInfo(
        home_team="A", away_team="B", source="test", retrieved_at="2024-01-01T00:00:00Z",
        home_players=[PlayerStatus(f"P{i}", Availability.UNKNOWN, is_key_player=True) for i in range(10)],
    )
    penalty, _ = compute_lineup_uncertainty_penalty(many_unknown)
    assert penalty <= 15.0


# ── no_bet_engine ────────────────────────────────────────────────────

def test_no_odds_means_unavailable_never_fabricated():
    rec = evaluate_bet(calibrated_probability=0.6, prediction_timestamp="2024-01-01T12:00:00Z", odds=None)
    assert rec.status == "UNAVAILABLE"
    assert rec.expected_value is None


def test_positive_ev_recommends_bet():
    # p=0.6, odds=2.0 -> implied=0.5, EV = 0.6*2.0 - 1 = 0.2
    rec = evaluate_bet(
        calibrated_probability=0.6, prediction_timestamp="2024-01-01T12:00:00Z",
        odds=2.0, odds_timestamp="2024-01-01T10:00:00Z",
    )
    assert rec.status == "BET"
    assert rec.expected_value == pytest.approx(0.2)


def test_negative_ev_recommends_no_bet():
    # p=0.4, odds=2.0 -> EV = 0.4*2.0 - 1 = -0.2
    rec = evaluate_bet(
        calibrated_probability=0.4, prediction_timestamp="2024-01-01T12:00:00Z",
        odds=2.0, odds_timestamp="2024-01-01T10:00:00Z",
    )
    assert rec.status == "NO_BET"
    assert rec.expected_value == pytest.approx(-0.2)


def test_odds_from_after_the_prediction_timestamp_are_rejected():
    rec = evaluate_bet(
        calibrated_probability=0.6, prediction_timestamp="2024-01-01T12:00:00Z",
        odds=2.0, odds_timestamp="2024-01-01T18:00:00Z",  # AFTER prediction
    )
    assert rec.status == "UNAVAILABLE"
    assert "AFTER" in rec.reason


def test_invalid_odds_value_rejected():
    rec = evaluate_bet(calibrated_probability=0.6, prediction_timestamp="2024-01-01T12:00:00Z", odds=0.9)
    assert rec.status == "UNAVAILABLE"


def test_ev_exactly_at_threshold_is_a_bet():
    # p=0.51, odds=2.0 -> EV = 0.51*2.0 - 1 = 0.02, threshold default 0.02
    rec = evaluate_bet(
        calibrated_probability=0.51, prediction_timestamp="2024-01-01T12:00:00Z",
        odds=2.0, odds_timestamp="2024-01-01T10:00:00Z", min_ev_threshold=0.02,
    )
    assert rec.status == "BET"

"""
No-Bet engine (Phase 3 §15): keeps PREDICTION strictly separate from
BETTING RECOMMENDATION.

A calibrated probability is always a valid output on its own — "Home win
= 72%" is a complete, honest statement with no odds involved. A betting
recommendation additionally requires REAL, timestamped odds; this module
never invents odds to produce one (consistent with AUDIT_REPORT.md /
REAL_DATA_BACKTEST_REPORT.md: no legitimate historical or live odds source
is reachable in this environment, so in practice every call here returns
UNAVAILABLE unless a caller supplies real odds explicitly).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class BettingRecommendation:
    status: str          # "BET" | "NO_BET" | "UNAVAILABLE"
    reason: str
    expected_value: Optional[float] = None
    implied_probability: Optional[float] = None
    edge: Optional[float] = None
    odds_used: Optional[float] = None
    odds_timestamp: Optional[str] = None


def evaluate_bet(
    calibrated_probability: float,
    prediction_timestamp,
    odds: Optional[float] = None,
    odds_timestamp: Optional[str] = None,
    min_ev_threshold: float = 0.02,
) -> BettingRecommendation:
    """
    Args:
        calibrated_probability: the model's calibrated probability for the
            selection being priced (0-1).
        odds: REAL decimal odds, if legitimately available. Never pass a
            fabricated/estimated number here.
        odds_timestamp: when those odds were actually observed. Must be
            <= prediction_timestamp — odds from AFTER the prediction was
            made cannot have informed it (the same timestamp discipline as
            every other input in this pipeline).
        min_ev_threshold: minimum expected value (per unit staked) required
            to recommend a bet, e.g. 0.02 = need a 2%+ edge in expectation.
    """
    if odds is None:
        return BettingRecommendation(
            status="UNAVAILABLE",
            reason="no real odds supplied — a prediction may still exist on its own, "
                   "but a betting recommendation requires real odds and none were provided",
        )

    if odds <= 1.0:
        return BettingRecommendation(status="UNAVAILABLE", reason=f"invalid odds value ({odds}) — decimal odds must be > 1.0")

    if odds_timestamp is not None:
        pred_ts = pd.Timestamp(prediction_timestamp)
        odds_ts = pd.Timestamp(odds_timestamp)
        if odds_ts > pred_ts:
            return BettingRecommendation(
                status="UNAVAILABLE",
                reason=f"odds_timestamp ({odds_ts.isoformat()}) is AFTER prediction_timestamp "
                       f"({pred_ts.isoformat()}) — these odds were not available when the prediction was made",
            )

    implied_prob = 1.0 / odds
    edge = calibrated_probability - implied_prob
    # Expected value per unit staked at decimal odds: p*(odds-1) - (1-p)*1 == p*odds - 1
    ev = calibrated_probability * odds - 1.0

    if ev >= min_ev_threshold:
        return BettingRecommendation(
            status="BET", reason=f"expected value {ev:.4f} meets the {min_ev_threshold:.4f} threshold",
            expected_value=round(ev, 4), implied_probability=round(implied_prob, 4),
            edge=round(edge, 4), odds_used=odds, odds_timestamp=odds_timestamp,
        )

    return BettingRecommendation(
        status="NO_BET", reason=f"expected value {ev:.4f} is below the {min_ev_threshold:.4f} threshold",
        expected_value=round(ev, 4), implied_probability=round(implied_prob, 4),
        edge=round(edge, 4), odds_used=odds, odds_timestamp=odds_timestamp,
    )

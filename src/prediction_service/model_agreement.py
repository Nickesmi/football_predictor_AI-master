"""
Model agreement / disagreement (Phase 3 §7).

Computes how much the available models disagree on a market's probability
for one match. High disagreement should REDUCE confidence; low
disagreement MAY increase it — but this module only measures disagreement,
it never averages incompatible models together (no naive ensembling here;
src/ml/model_provenance.py and champion_registry.py already decide which
single model's number is actually used as the prediction).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.ml.baselines import MarketProbs

_MARKET_FIELD = {
    "home_win": "p_home", "draw": "p_draw", "away_win": "p_away",
    "over_1_5": "p_over_1_5", "over_2_5": "p_over_2_5", "over_3_5": "p_over_3_5",
    "btts": "p_btts_yes",
    "home_over_0_5": "p_home_over_0_5", "away_over_0_5": "p_away_over_0_5",
}
_UNDER_TO_OVER = {"under_1_5": "over_1_5", "under_2_5": "over_2_5", "under_3_5": "over_3_5"}


@dataclass
class AgreementResult:
    market: str
    n_models_compared: int
    model_probabilities: dict[str, float]
    mean_probability: float
    std_dev: float
    max_pairwise_spread: float
    agreement_level: str    # "high" | "medium" | "low"


def _extract_market_probability(probs: MarketProbs, market: str) -> float | None:
    if market in _UNDER_TO_OVER:
        over_field = _MARKET_FIELD[_UNDER_TO_OVER[market]]
        val = getattr(probs, over_field)
        return None if val is None else 1.0 - val
    field = _MARKET_FIELD.get(market)
    if field is None:
        raise ValueError(f"unknown market {market!r}")
    return getattr(probs, field)


def compute_agreement(model_predictions: dict[str, MarketProbs], market: str) -> AgreementResult:
    """model_predictions: {model_name: MarketProbs}, typically from
    src.prediction_service.live_models.predict_all_available_models().
    Models that don't cover `market` (None) are excluded, not treated as 0."""
    values = {}
    for name, probs in model_predictions.items():
        p = _extract_market_probability(probs, market)
        if p is not None:
            values[name] = p

    n = len(values)
    if n == 0:
        return AgreementResult(market, 0, {}, mean_probability=float("nan"), std_dev=float("nan"),
                                max_pairwise_spread=float("nan"), agreement_level="unknown")

    probs_list = list(values.values())
    mean_p = sum(probs_list) / n
    variance = sum((p - mean_p) ** 2 for p in probs_list) / n
    std_dev = math.sqrt(variance)
    max_spread = max(probs_list) - min(probs_list) if n > 1 else 0.0

    if n == 1:
        level = "unknown"
    elif max_spread < 0.08:
        level = "high"
    elif max_spread < 0.20:
        level = "medium"
    else:
        level = "low"

    return AgreementResult(
        market=market, n_models_compared=n, model_probabilities=values,
        mean_probability=round(mean_p, 4), std_dev=round(std_dev, 4),
        max_pairwise_spread=round(max_spread, 4), agreement_level=level,
    )

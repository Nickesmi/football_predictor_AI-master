"""
tennis_model.py
===============
V1 tennis prediction model.

Architecture (3 layers):

  1. Surface-adjusted Elo probability  (primary signal)
     rating_diff = p1_surface_elo - p2_surface_elo
     raw_prob = 1 / (1 + 10^(-rating_diff/400))

  2. Form + ranking adjustment          (secondary signal)
     Blend raw_prob with recent win rate and rank-based probability.

  3. Data quality shrinkage             (governance layer)
     final_prob = quality_weight * model_prob + (1 - quality_weight) * 0.5
     Low data quality → probabilities shrink toward coin flip.

Invariant: player_1_win + player_2_win == 1.0  (always)

Markets (rule-based v1):
  - Match Winner
  - Player -1.5 Sets  (only when favourite prob > 0.68)
  - Total Games O/U    (based on relative serve dominance proxy)
  - First Set Winner

No Poisson. No football model logic.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

logger = logging.getLogger("football_predictor.tennis")

# ── Model constants ───────────────────────────────────────────────────────────
_ELO_K     = 400.0      # Standard Elo denominator
_ELO_BLEND = 0.60       # Weight on Elo vs form in the blend
_FORM_BLEND = 0.25      # Weight on recent form
_RANK_BLEND = 0.15      # Weight on ranking signal


def _elo_probability(elo_diff: float) -> float:
    """Standard Elo win probability for player 1."""
    return 1.0 / (1.0 + math.pow(10.0, -elo_diff / _ELO_K))


def _rank_probability(rank_1: Optional[int], rank_2: Optional[int]) -> float:
    """
    Simple ranking-based probability.
    Uses a logistic function on rank difference.
    Higher rank number = weaker player.
    """
    r1 = rank_1 or 250   # unknown → assign mid-tier rank
    r2 = rank_2 or 250
    diff = r2 - r1       # positive = p1 is better ranked
    # Logistic: every 100-rank gap ≈ +10% probability
    return 1.0 / (1.0 + math.exp(-diff / 150.0))


def _form_probability(
    win_rate_5_p1:  Optional[float],
    win_rate_10_p1: Optional[float],
    win_rate_5_p2:  Optional[float],
    win_rate_10_p2: Optional[float],
) -> Optional[float]:
    """
    Aggregate recent form into a win probability for p1.
    Returns None if no form data exists.
    """
    vals_p1 = [v for v in [win_rate_5_p1, win_rate_10_p1] if v is not None]
    vals_p2 = [v for v in [win_rate_5_p2, win_rate_10_p2] if v is not None]

    if not vals_p1 or not vals_p2:
        return None

    form_p1 = sum(vals_p1) / len(vals_p1)
    form_p2 = sum(vals_p2) / len(vals_p2)
    total = form_p1 + form_p2

    if total == 0:
        return 0.5
    return form_p1 / total


def _quality_shrinkage(prob: float, data_quality: float) -> float:
    """
    Shrink probability toward 0.5 based on data quality (0–100).
    At quality=100 → no shrinkage.
    At quality=0   → fully shrunk to 0.5.
    """
    weight = data_quality / 100.0
    return weight * prob + (1.0 - weight) * 0.5


def _safe_fair_odds(prob: float) -> float:
    """Convert probability to fair decimal odds."""
    if prob <= 0.0:
        return 99.0
    if prob >= 1.0:
        return 1.01
    return round(1.0 / prob, 3)


def _confidence_label(prob: float, data_quality: float) -> str:
    """Categorise confidence as HIGH / MEDIUM / LOW."""
    if data_quality < 40:
        return "LOW"
    if prob >= 0.65 and data_quality >= 70:
        return "HIGH"
    if prob >= 0.55 and data_quality >= 50:
        return "MEDIUM"
    return "LOW"


# ── Core prediction function ──────────────────────────────────────────────────

def predict_match(features: dict) -> dict:
    """
    Given a feature dict from tennis_feature_builder.build_features(),
    return a structured prediction dict including all market outputs.

    Invariant guarantee: player_1_win + player_2_win == 1.0
    """
    dq = float(features.get("data_quality_score", 50))

    # ── Layer 1: Elo ──────────────────────────────────────────────────────────
    elo_diff = float(features.get("elo_diff", 0.0))
    elo_prob = _elo_probability(elo_diff)

    # ── Layer 2: Rank ─────────────────────────────────────────────────────────
    rank_prob = _rank_probability(
        features.get("rank_1"),
        features.get("rank_2"),
    )

    # ── Layer 2: Form ─────────────────────────────────────────────────────────
    form_prob = _form_probability(
        features.get("win_rate_last5_p1"),
        features.get("win_rate_last10_p1"),
        features.get("win_rate_last5_p2"),
        features.get("win_rate_last10_p2"),
    )

    # ── Blend signals ─────────────────────────────────────────────────────────
    if form_prob is not None:
        blended = (
            _ELO_BLEND  * elo_prob +
            _RANK_BLEND * rank_prob +
            _FORM_BLEND * form_prob
        )
    else:
        # No form data — shift weight to Elo and ranking
        elo_w  = _ELO_BLEND  + _FORM_BLEND * 0.6
        rank_w = _RANK_BLEND + _FORM_BLEND * 0.4
        blended = elo_w * elo_prob + rank_w * rank_prob

    # ── Surface form adjustment ────────────────────────────────────────────────
    surf_p1 = features.get("surface_win_rate_p1")
    surf_p2 = features.get("surface_win_rate_p2")
    if surf_p1 is not None and surf_p2 is not None:
        total_surf = surf_p1 + surf_p2
        if total_surf > 0:
            surf_prob = surf_p1 / total_surf
            blended = 0.85 * blended + 0.15 * surf_prob

    # ── H2H adjustment ────────────────────────────────────────────────────────
    h2h_win = features.get("h2h_win_p1")
    h2h_n   = features.get("h2h_total", 0)
    if h2h_win is not None and h2h_n >= 3:
        blended = 0.90 * blended + 0.10 * h2h_win

    # ── Fatigue adjustment ────────────────────────────────────────────────────
    fat_p1 = features.get("fatigue_matches_p1", 0)
    fat_p2 = features.get("fatigue_matches_p2", 0)
    if fat_p1 > fat_p2 + 2:
        blended -= 0.02   # p1 significantly more fatigued
    elif fat_p2 > fat_p1 + 2:
        blended += 0.02

    # ── Clamp before shrinkage ────────────────────────────────────────────────
    blended = max(0.02, min(0.98, blended))

    # ── Layer 3: Data quality shrinkage ───────────────────────────────────────
    p1_win = _quality_shrinkage(blended, dq)
    p2_win = 1.0 - p1_win

    # ── Match Winner market ───────────────────────────────────────────────────
    mw_conf = _confidence_label(max(p1_win, p2_win), dq)
    match_winner = {
        "player_1_win": round(p1_win * 100, 1),
        "player_2_win": round(p2_win * 100, 1),
        "fair_odds_p1": _safe_fair_odds(p1_win),
        "fair_odds_p2": _safe_fair_odds(p2_win),
        "confidence":   mw_conf,
    }

    # ── Sets markets (rule-based v1) ──────────────────────────────────────────
    best_of = features.get("best_of", 3)
    sets_markets = _compute_sets_markets(p1_win, p2_win, dq, best_of)

    # ── Top picks assembly ────────────────────────────────────────────────────
    top_picks = _build_top_picks(match_winner, sets_markets, features)

    # ── Warnings ──────────────────────────────────────────────────────────────
    warnings = []
    if dq < 40:
        warnings.append(f"Low data quality ({dq:.0f}/100) — predictions shrunk toward 50%")
    if "elo_p1_new_player" in features.get("missing_features", []):
        warnings.append("Player 1 has no Elo history — using default rating")
    if "elo_p2_new_player" in features.get("missing_features", []):
        warnings.append("Player 2 has no Elo history — using default rating")

    return {
        "model_version":  "v1.0-elo",
        "data_quality":   dq,
        "missing_features": features.get("missing_features", []),
        "match_winner":   match_winner,
        "sets_markets":   sets_markets,
        "top_picks":      top_picks,
        "warnings":       warnings,
        # Raw signal components (for calibration / debugging)
        "_signals": {
            "elo_prob":   round(elo_prob, 4),
            "rank_prob":  round(rank_prob, 4),
            "form_prob":  round(form_prob, 4) if form_prob is not None else None,
            "blended":    round(blended, 4),
        },
    }


def _compute_sets_markets(p1_win: float, p2_win: float, dq: float, best_of: int) -> dict:
    """
    Rule-based sets handicap and total games markets.
    Only proposed when favourite probability is sufficiently high.
    """
    favourite_prob  = max(p1_win, p2_win)
    favourite_is_p1 = p1_win >= p2_win

    markets = {}

    # ── Player -1.5 Sets (straight sets win) ──────────────────────────────────
    if favourite_prob >= 0.68 and dq >= 50:
        # Estimate straight sets probability:
        # If p(win) = 0.70, rough p(straight sets) ≈ win_prob^(sets_needed)
        sets_needed = 2 if best_of == 3 else 3
        straight_prob = favourite_prob ** sets_needed * 1.3  # adjustment factor
        straight_prob = min(straight_prob, 0.90)
        straight_prob = _quality_shrinkage(straight_prob, dq)

        selection = "player_1 -1.5 sets" if favourite_is_p1 else "player_2 -1.5 sets"
        markets["favourite_minus_1_5_sets"] = {
            "selection":  selection,
            "probability": round(straight_prob * 100, 1),
            "fair_odds":   _safe_fair_odds(straight_prob),
            "confidence":  _confidence_label(straight_prob, dq),
        }

    # ── First Set Winner ──────────────────────────────────────────────────────
    # First set follows match winner probability with slight regression
    first_set_p1 = 0.5 + (p1_win - 0.5) * 0.75
    first_set_p1 = _quality_shrinkage(first_set_p1, dq)
    markets["first_set_winner"] = {
        "player_1_win":  round(first_set_p1 * 100, 1),
        "player_2_win":  round((1 - first_set_p1) * 100, 1),
        "fair_odds_p1":  _safe_fair_odds(first_set_p1),
        "fair_odds_p2":  _safe_fair_odds(1 - first_set_p1),
        "confidence":    _confidence_label(max(first_set_p1, 1 - first_set_p1), dq),
    }

    return markets


def _build_top_picks(match_winner: dict, sets_markets: dict, features: dict) -> list[dict]:
    """Select highest-confidence picks to surface in the UI."""
    picks = []
    dq = features.get("data_quality_score", 50)

    # Match winner pick
    if match_winner["confidence"] in ("HIGH", "MEDIUM"):
        if match_winner["player_1_win"] >= match_winner["player_2_win"]:
            picks.append({
                "market":      "Match Winner",
                "selection":   "Player 1",
                "probability": match_winner["player_1_win"],
                "fair_odds":   match_winner["fair_odds_p1"],
                "confidence":  match_winner["confidence"],
                "data_quality": dq,
            })
        else:
            picks.append({
                "market":      "Match Winner",
                "selection":   "Player 2",
                "probability": match_winner["player_2_win"],
                "fair_odds":   match_winner["fair_odds_p2"],
                "confidence":  match_winner["confidence"],
                "data_quality": dq,
            })

    # Sets handicap
    sets_h = sets_markets.get("favourite_minus_1_5_sets")
    if sets_h and sets_h["confidence"] in ("HIGH", "MEDIUM"):
        picks.append({
            "market":      "Sets Handicap",
            "selection":   sets_h["selection"],
            "probability": sets_h["probability"],
            "fair_odds":   sets_h["fair_odds"],
            "confidence":  sets_h["confidence"],
            "data_quality": dq,
        })

    return picks


# ── Elo update (called after match settlement) ────────────────────────────────

def update_elo(
    elo_winner: float,
    elo_loser:  float,
    k_factor:   float = 32.0,
    is_best_of_5: bool = False,
) -> tuple[float, float]:
    """
    Update Elo ratings after a settled match.
    Returns (new_elo_winner, new_elo_loser).
    K-factor is higher for Grand Slams (best_of_5).
    """
    if is_best_of_5:
        k_factor = 40.0

    expected_winner = _elo_probability(elo_winner - elo_loser)
    expected_loser  = 1.0 - expected_winner

    new_winner = elo_winner + k_factor * (1.0 - expected_winner)
    new_loser  = elo_loser  + k_factor * (0.0 - expected_loser)

    return round(new_winner, 2), round(new_loser, 2)

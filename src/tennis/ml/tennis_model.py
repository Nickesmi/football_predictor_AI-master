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
    90+ -> 95% model weight
    60  -> 70% model weight
    <40 -> NO PICK (handled in predict_match), but scales aggressively here.
    """
    if data_quality >= 90:
        weight = 0.95
    elif data_quality >= 60:
        weight = 0.70 + ((data_quality - 60) / 30.0) * 0.25
    else:
        weight = (max(0, data_quality) / 60.0) * 0.70

    return weight * prob + (1.0 - weight) * 0.5


def _safe_fair_odds(prob: float) -> float:
    """Convert probability to fair decimal odds."""
    if prob <= 0.0:
        return 99.0
    if prob >= 1.0:
        return 1.01
    return round(1.0 / prob, 3)


def _confidence_label(prob: float, data_quality: float, elo_diff: float = 0.0, fatigue: int = 0) -> str:
    """Categorise confidence as HIGH / MEDIUM / LOW / NO PICK."""
    if data_quality < 40:
        return "LOW"
    if prob >= 0.70 and data_quality >= 90 and abs(elo_diff) >= 150 and fatigue <= 10:
        return "HIGH"
    if prob >= 0.60 and data_quality >= 70:
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
    allow_low_quality_markets = bool(features.get("allow_low_quality_markets", False))

    # ── Governance Rule: NO PICK for poor data unless UI explicitly asks for
    # conservative display markets.
    if dq < 40 and not allow_low_quality_markets:
        return {
            "model_version":  "v1.0-elo",
            "data_quality":   dq,
            "missing_features": features.get("missing_features", []),
            "match_winner":   None,
            "sets_markets":   {},
            "market_groups":  {},
            "all_picks":      [],
            "top_picks":      [],
            "warnings":       ["NO PICK: data quality too low (< 40). Model requires more historical data."],
            "_signals":       None,
        }

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
    fav_fatigue = features.get("fatigue_matches_p1", 0) if p1_win >= p2_win else features.get("fatigue_matches_p2", 0)
    mw_conf = _confidence_label(max(p1_win, p2_win), dq, elo_diff, fav_fatigue)
    match_winner = {
        "player_1_win": round(p1_win * 100, 1),
        "player_2_win": round(p2_win * 100, 1),
        "fair_odds_p1": _safe_fair_odds(p1_win),
        "fair_odds_p2": _safe_fair_odds(p2_win),
        "confidence":   mw_conf,
    }

    # ── Tennis markets (rule-based v1) ────────────────────────────────────────
    best_of = features.get("best_of", 3)
    sets_markets = _compute_sets_markets(p1_win, p2_win, dq, best_of, features)
    market_groups, all_picks = _build_market_board(match_winner, sets_markets, p1_win, p2_win, dq, best_of, features)

    # ── Top picks assembly ────────────────────────────────────────────────────
    top_picks = _build_top_picks(all_picks, features)

    # ── Warnings ──────────────────────────────────────────────────────────────
    warnings = []
    if dq < 40:
        warnings.append(f"Low data quality ({dq:.0f}/100) — showing conservative low-confidence markets")
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
        "market_groups":  market_groups,
        "all_picks":      all_picks,
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


def _compute_sets_markets(p1_win: float, p2_win: float, dq: float, best_of: int, features: dict) -> dict:
    """
    Rule-based sets handicap and total games markets.
    Uses straight sets rate and fatigue for smart handicap qualification.
    """
    favourite_prob  = max(p1_win, p2_win)
    favourite_is_p1 = p1_win >= p2_win

    markets = {}

    # ── Player -1.5 Sets (straight sets win) ──────────────────────────────────
    if favourite_prob >= 0.65 and dq >= 50:
        if favourite_is_p1:
            ss_rate = features.get("straight_sets_rate_p1") or 0.0
            fatigue = features.get("fatigue_matches_p1", 0) * 2 + features.get("fatigue_sets_p1", 0)
        else:
            ss_rate = features.get("straight_sets_rate_p2") or 0.0
            fatigue = features.get("fatigue_matches_p2", 0) * 2 + features.get("fatigue_sets_p2", 0)

        # Smart handicap rule: need good straight sets history and low fatigue
        if ss_rate > 0.60 and fatigue < 20:
            sets_needed = 2 if best_of == 3 else 3
            straight_prob = favourite_prob ** sets_needed * 1.3  # adjustment factor
            straight_prob = min(straight_prob, 0.90)
            straight_prob = _quality_shrinkage(straight_prob, dq)

            selection = "player_1 -1.5 sets" if favourite_is_p1 else "player_2 -1.5 sets"
            markets["favourite_minus_1_5_sets"] = {
                "selection":  selection,
                "probability": round(straight_prob * 100, 1),
                "fair_odds":   _safe_fair_odds(straight_prob),
                "confidence":  _confidence_label(straight_prob, dq, 0.0, fatigue),
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
        "confidence":    _confidence_label(max(first_set_p1, 1 - first_set_p1), dq, 0.0, 0),
    }

    return markets


def _market(market: str, market_type: str, selection: str, prob: float, dq: float) -> dict:
    """Normalize one market row for API, storage, and frontend display."""
    prob = max(0.03, min(0.97, prob))
    return {
        "market": market,
        "market_type": market_type,
        "selection": selection,
        "probability": round(prob * 100, 1),
        "fair_odds": _safe_fair_odds(prob),
        "confidence": _confidence_label(prob, dq),
        "confidence_score": round(prob, 4),
    }


def _logistic_cover_probability(line: float, expected_margin: float, spread: float) -> float:
    """Approximate cover probability from expected game margin."""
    return 1.0 / (1.0 + math.exp(-(expected_margin + line) / spread))


def _expected_game_profile(p1_win: float, best_of: int, features: dict) -> tuple[float, float, float]:
    """Estimate total games and player game shares from match strength."""
    closeness = 1.0 - abs(p1_win - 0.5) * 2.0
    base_total = 22.0 if best_of == 3 else 38.5
    expected_total = base_total + closeness * (3.0 if best_of == 3 else 6.0)

    serve_gap = (
        float(features.get("surface_win_rate_p1") or 0.5) -
        float(features.get("surface_win_rate_p2") or 0.5)
    )
    p1_game_share = 0.5 + (p1_win - 0.5) * 0.44 + serve_gap * 0.08
    p1_game_share = max(0.36, min(0.64, p1_game_share))
    p1_games = expected_total * p1_game_share
    p2_games = expected_total - p1_games
    return expected_total, p1_games, p2_games


def _total_over_probability(expected_total: float, line: float, best_of: int) -> float:
    spread = 3.8 if best_of == 3 else 6.2
    return 1.0 / (1.0 + math.exp(-(expected_total - line) / spread))


def _build_market_board(
    match_winner: dict,
    sets_markets: dict,
    p1_win: float,
    p2_win: float,
    dq: float,
    best_of: int,
    features: dict,
) -> tuple[dict, list[dict]]:
    """Build every tennis market currently supported by the model."""
    p1_name = features.get("player_1") or "Player 1"
    p2_name = features.get("player_2") or "Player 2"
    expected_total, p1_games, p2_games = _expected_game_profile(p1_win, best_of, features)
    expected_margin = p1_games - p2_games
    spread = 4.2 if best_of == 3 else 7.0

    groups = {
        "match_winner": [
            _market("Match Winner", "match_winner", p1_name, p1_win, dq),
            _market("Match Winner", "match_winner", p2_name, p2_win, dq),
        ],
        "sets_handicap": [],
        "game_handicap": [],
        "total_games": [],
        "player_games": [],
        "first_set_winner": [],
    }

    sets_h = sets_markets.get("favourite_minus_1_5_sets")
    if sets_h:
        groups["sets_handicap"].append({
            "market": "Sets Handicap",
            "market_type": "sets_handicap",
            "selection": sets_h["selection"].replace("player_1", p1_name).replace("player_2", p2_name),
            "probability": sets_h["probability"],
            "fair_odds": sets_h["fair_odds"],
            "confidence": sets_h["confidence"],
            "confidence_score": round(float(sets_h["probability"]) / 100.0, 4),
        })

    set_line = 1.5 if best_of == 3 else 2.5
    groups["sets_handicap"].extend([
        _market("Sets Handicap", "sets_handicap", f"{p1_name} +{set_line} sets", min(0.95, p1_win + 0.24), dq),
        _market("Sets Handicap", "sets_handicap", f"{p2_name} +{set_line} sets", min(0.95, p2_win + 0.24), dq),
    ])

    handicap_lines = [1.5, 2.5, 3.5] if best_of == 3 else [2.5, 3.5, 4.5]
    for line in handicap_lines:
        p1_cover = _quality_shrinkage(_logistic_cover_probability(line, expected_margin, spread), dq)
        p2_cover = _quality_shrinkage(_logistic_cover_probability(line, -expected_margin, spread), dq)
        groups["game_handicap"].append(_market("Game Handicap", "game_handicap", f"{p1_name} +{line} games", p1_cover, dq))
        groups["game_handicap"].append(_market("Game Handicap", "game_handicap", f"{p2_name} +{line} games", p2_cover, dq))

    total_lines = [20.5, 21.5, 22.5, 23.5] if best_of == 3 else [35.5, 36.5, 37.5, 38.5]
    for line in total_lines:
        over_prob = _quality_shrinkage(_total_over_probability(expected_total, line, best_of), dq)
        groups["total_games"].append(_market("Total Games / Points", "total_games", f"Over {line}", over_prob, dq))
        groups["total_games"].append(_market("Total Games / Points", "total_games", f"Under {line}", 1.0 - over_prob, dq))

    player_lines = [10.5, 11.5, 12.5] if best_of == 3 else [17.5, 18.5, 19.5]
    for player_name, expected_games in [(p1_name, p1_games), (p2_name, p2_games)]:
        for line in player_lines:
            over_prob = _quality_shrinkage(_total_over_probability(expected_games, line, best_of), dq)
            groups["player_games"].append(_market("Player Total Games", "player_games", f"{player_name} Over {line}", over_prob, dq))
            groups["player_games"].append(_market("Player Total Games", "player_games", f"{player_name} Under {line}", 1.0 - over_prob, dq))

    fsw = sets_markets.get("first_set_winner")
    if fsw:
        groups["first_set_winner"].extend([
            _market("First Set Winner", "first_set_winner", p1_name, float(fsw["player_1_win"]) / 100.0, dq),
            _market("First Set Winner", "first_set_winner", p2_name, float(fsw["player_2_win"]) / 100.0, dq),
        ])

    all_picks = [pick for picks in groups.values() for pick in picks]
    all_picks.sort(key=lambda item: item.get("probability", 0), reverse=True)
    return groups, all_picks


def _build_top_picks(all_picks: list[dict], features: dict) -> list[dict]:
    """Select highest-confidence picks to surface in the UI."""
    dq = features.get("data_quality_score", 50)
    ranked = [
        {**pick, "data_quality": dq}
        for pick in all_picks
        if pick.get("confidence") in ("HIGH", "MEDIUM") or float(pick.get("probability", 0)) >= 58.0
    ]
    return ranked[:8]


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

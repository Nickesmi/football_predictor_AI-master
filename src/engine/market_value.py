"""
Agent 4: Market Value Detector

Compares model probabilities with bookmaker implied probabilities
to find mispriced markets (positive expected value).
"""

import logging
import sqlite3
from src.db.odds_repo import get_odds_for_match

logger = logging.getLogger("football_predictor")


def find_value(
    match: dict,
    model_probs: dict,
    conn: sqlite3.Connection,
) -> list[dict]:
    """
    Compare model probabilities to bookmaker odds for all markets.
    Returns a list of value candidates with edge calculations.

    Each candidate:
        match_id, market, selection, model_prob, implied_prob, edge, odds
    """
    match_id = match["id"]
    odds_rows = get_odds_for_match(conn, match_id)

    if not odds_rows:
        logger.debug(f"No odds for match {match_id}, skipping value scan")
        return []

    # Build odds lookup: (market, selection) → best odds
    odds_lookup: dict[tuple[str, str], float] = {}
    for row in odds_rows:
        key = (row["market"], row["selection"])
        current_best = odds_lookup.get(key, 0)
        if row["odds"] > current_best:
            odds_lookup[key] = row["odds"]

    candidates = []

    # Scan all markets from model output
    for market in ["1X2", "O/U 2.5", "BTTS"]:
        if market not in model_probs:
            continue

        for selection, model_prob in model_probs[market].items():
            odds_key = (market, selection)
            if odds_key not in odds_lookup:
                continue

            decimal_odds = odds_lookup[odds_key]
            implied_prob = 1.0 / decimal_odds if decimal_odds > 1.0 else 1.0
            edge = model_prob - implied_prob

            candidates.append({
                "match_id": match_id,
                "home_team": match["home_team"],
                "away_team": match["away_team"],
                "league_name": match["league_name"],
                "league_id": match.get("league_id", 0),
                "market": market,
                "selection": selection,
                "model_prob": round(model_prob, 4),
                "implied_prob": round(implied_prob, 4),
                "edge": round(edge, 4),
                "odds": round(decimal_odds, 3),
                "data_quality": float(model_probs.get("data_quality", 50.0)),
                "model_source": model_probs.get("source", "unknown"),
            })

    # Sort by edge descending
    candidates.sort(key=lambda c: c["edge"], reverse=True)
    return candidates


def filter_positive_edge(candidates: list[dict], min_edge: float = 0.0) -> list[dict]:
    """Keep only candidates with positive edge above threshold."""
    return [c for c in candidates if c["edge"] > min_edge]

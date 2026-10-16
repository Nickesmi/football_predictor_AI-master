"""
Agent 4: Market Value Detector

Compares model probabilities with bookmaker implied probabilities
to find mispriced markets (positive expected value).
"""

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from src.odds.freshness import age_minutes
from src.odds.store import actionable_quotes, synthetic_odds_allowed

logger = logging.getLogger("football_predictor")

# Canonical (market, line) of a real quote -> the model's market key.
MODEL_MARKETS = {("1X2", ""): "1X2", ("TOTALS", "2.5"): "O/U 2.5", ("BTTS", ""): "BTTS"}


def odds_max_age() -> timedelta:
    """How old a bookmaker price may be and still support a decision (ODDS_MAX_AGE_MINUTES, default 60)."""
    try:
        minutes = int(os.getenv("ODDS_MAX_AGE_MINUTES", "60"))
    except ValueError:
        minutes = 60
    return timedelta(minutes=max(1, minutes))


def find_value(
    match: dict,
    model_probs: dict,
    conn: sqlite3.Connection,
    now: datetime | None = None,
) -> list[dict]:
    """
    Compare model probabilities to real bookmaker odds for all markets.
    Returns a list of value candidates with edge calculations.

    Only fresh, pre-match quotes from odds_quotes are used: each bookmaker's
    latest price, then the best of those across bookmakers. Crowd votes and
    model fair odds are never prices. Explicitly synthetic test prices are only
    read in an isolated test database. No usable quote means no candidate.

    Each candidate:
        match_id, market, selection, model_prob, implied_prob, edge, odds,
        bookmaker, odds_provider, odds_type, odds_observed_at
    """
    match_id = match["id"]
    decided_at = now or datetime.now(timezone.utc)
    quotes = actionable_quotes(
        conn, match_id, decided_at, odds_max_age(),
        include_synthetic=synthetic_odds_allowed(conn),
    )

    if not quotes:
        logger.debug(f"No fresh bookmaker odds for match {match_id}, skipping value scan")
        return []

    # (model market, selection) -> best fresh quote (quotes arrive best price first)
    best: dict[tuple[str, str], dict] = {}
    for quote in quotes:
        model_market = MODEL_MARKETS.get((quote["market"], quote["line"]))
        if model_market:
            best.setdefault((model_market, quote["selection"]), quote)

    candidates = []

    # Scan all markets from model output
    for market in ["1X2", "O/U 2.5", "BTTS"]:
        if market not in model_probs:
            continue

        for selection, model_prob in model_probs[market].items():
            quote = best.get((market, selection))
            if quote is None:
                continue

            decimal_odds = quote["decimal_odds"]
            implied_prob = 1.0 / decimal_odds
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
                "bookmaker": quote["bookmaker"],
                "odds_provider": quote["provider"],
                "odds_type": quote["odds_type"],
                "odds_observed_at": quote["observed_at"],
                "odds_fetched_at": quote["fetched_at"],
                "odds_age_minutes": round(age_minutes(decided_at, quote["observed_at"]), 1),
                "data_quality": float(model_probs.get("data_quality", 50.0)),
                "model_source": model_probs.get("source", "unknown"),
            })

    # Sort by edge descending
    candidates.sort(key=lambda c: c["edge"], reverse=True)
    return candidates


def filter_positive_edge(candidates: list[dict], min_edge: float = 0.0) -> list[dict]:
    """Keep only candidates with positive edge above threshold."""
    return [c for c in candidates if c["edge"] > min_edge]

"""
Execution Service Module.

Provides business logic for finding executable positive-EV betting opportunities,
querying execution rules and tradable market whitelists, and simulating bet calculations.
"""

from typing import Any, Dict, List
from src.engine.execution_engine import (
    find_executable_bets,
    generate_simulated_odds,
    compute_ev,
    compute_edge,
    compute_kelly,
    MIN_ODDS,
    MAX_ODDS,
    MIN_CALIBRATED_PROB,
    MIN_EDGE,
    MIN_EV,
    MAX_BETS_PER_MATCH,
    MAX_BETS_PER_DAY,
    KELLY_FRACTION,
    MAX_STAKE_PCT,
    TRADABLE_MARKETS,
)
from src.data.odds_fetcher import fetch_normalized_odds_for_match, SPORT_KEYS, get_api_key


def get_execution_opportunities(
    home: str, away: str, league: str, use_live_odds: bool = False
) -> Dict[str, Any]:
    """
    Find executable, positive-EV betting opportunities for a match.

    Generates calibrated probabilities across all markets, fetches live or simulated bookmaker odds,
    computes EV/edge/Kelly sizing, and filters down to tradable opportunities.

    Args:
        home: Name of home team.
        away: Name of away team.
        league: Name of league or competition.
        use_live_odds: If True, fetches from TheOddsAPI; otherwise uses simulated odds.

    Returns:
        Dictionary containing match metadata, summary counts, and executable opportunities list.
    """
    from api.main import _compute_match_analysis

    try:
        analysis = _compute_match_analysis(home, away, league, shuffle_tiers=False)
    except Exception as e:
        return {"error": f"Analysis failed: {str(e)}", "opportunities": []}

    if use_live_odds:
        if not get_api_key():
            return {
                "error": "ODDS_API_KEY not set. Use use_live_odds=false for simulation.",
                "opportunities": [],
            }

        sport_key = None
        for sk, _lid in SPORT_KEYS.items():
            if league.lower() in sk.lower():
                sport_key = sk
                break
        if not sport_key:
            sport_key = "soccer_epl"

        bookmaker_odds = fetch_normalized_odds_for_match(sport_key, home, away)
    else:
        bookmaker_odds = generate_simulated_odds(analysis, home, away)

    opportunities = find_executable_bets(
        analysis=analysis,
        bookmaker_odds=bookmaker_odds,
        home_name=home,
        away_name=away,
        league_name=league,
        match_id=f"{home}_vs_{away}",
    )

    total_markets_scanned = len(bookmaker_odds)
    total_tradable = sum(
        1
        for o in bookmaker_odds
        if any(
            m["market"] == o["market"]
            for sec in analysis.get("full_analysis", {}).values()
            for m in sec
        )
    )

    return {
        "match": f"{home} vs {away}",
        "league": league,
        "odds_source": "live" if use_live_odds else "simulated",
        "total_bookmaker_markets": total_markets_scanned,
        "total_matched_to_model": total_tradable,
        "opportunities_found": len(opportunities),
        "opportunities": [bet.to_dict() for bet in opportunities],
        "filter_summary": {
            "min_odds": MIN_ODDS,
            "max_odds": MAX_ODDS,
            "min_calibrated_prob": MIN_CALIBRATED_PROB,
            "min_edge": MIN_EDGE,
            "min_ev": MIN_EV,
            "max_bets_per_match": MAX_BETS_PER_MATCH,
            "kelly_fraction": KELLY_FRACTION,
            "max_stake_pct": MAX_STAKE_PCT,
        },
    }


def get_execution_rules() -> Dict[str, Any]:
    """
    Retrieve current execution rules, thresholds, and tradable market whitelist.

    Returns:
        Dictionary detailing filter thresholds and tradable/excluded market lists.
    """
    return {
        "rules": {
            "min_odds": MIN_ODDS,
            "max_odds": MAX_ODDS,
            "min_calibrated_probability": MIN_CALIBRATED_PROB,
            "min_edge_pct": MIN_EDGE,
            "min_ev": MIN_EV,
            "max_bets_per_match": MAX_BETS_PER_MATCH,
            "max_bets_per_day": MAX_BETS_PER_DAY,
            "kelly_fraction": KELLY_FRACTION,
            "max_stake_pct": MAX_STAKE_PCT,
        },
        "tradable_markets": sorted(TRADABLE_MARKETS),
        "excluded_market_types": ["cs", "cards", "corners", "combo"],
        "focus_market_types": ["goals", "btts", "result", "handicap", "half"],
    }


def simulate_execution(calibrated_prob: float = 75.0, odds: float = 1.55) -> Dict[str, Any]:
    """
    Test EV, Edge, and Kelly computation on a single hypothetical bet scenario.

    Args:
        calibrated_prob: Calibrated model probability percentage (default 75.0).
        odds: Decimal odds offered by bookmaker (default 1.55).

    Returns:
        Dictionary showing implied probability, edge, EV, Kelly fraction, and filter pass status.
    """
    implied = 100.0 / odds
    edge = compute_edge(calibrated_prob, odds)
    ev = compute_ev(calibrated_prob, odds)
    kelly = compute_kelly(calibrated_prob, odds)

    passes_filters = (
        MIN_ODDS <= odds <= MAX_ODDS
        and calibrated_prob >= MIN_CALIBRATED_PROB
        and edge >= MIN_EDGE
        and ev >= MIN_EV
    )

    return {
        "calibrated_prob": calibrated_prob,
        "odds": odds,
        "implied_prob": round(implied, 1),
        "edge": round(edge, 1),
        "ev": round(ev, 3),
        "ev_pct": round(ev * 100, 1),
        "kelly_stake_pct": round(kelly, 2),
        "passes_all_filters": passes_filters,
        "verdict": "✅ EXECUTABLE" if passes_filters else "❌ REJECTED",
        "rejection_reasons": [
            r for r in [
                f"odds {odds} outside [{MIN_ODDS}, {MAX_ODDS}]" if not (MIN_ODDS <= odds <= MAX_ODDS) else None,
                f"prob {calibrated_prob}% < min {MIN_CALIBRATED_PROB}%" if calibrated_prob < MIN_CALIBRATED_PROB else None,
                f"edge {edge:.1f}% < min {MIN_EDGE}%" if edge < MIN_EDGE else None,
                f"EV {ev:.3f} < min {MIN_EV}" if ev < MIN_EV else None,
            ] if r
        ],
    }


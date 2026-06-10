"""
Agent 5: Risk Control

Scores uncertainty, data quality, and league reliability.
Filters or downgrades picks that don't meet trust thresholds.
"""

import logging
import sqlite3

logger = logging.getLogger("football_predictor")


def get_league_profile(conn: sqlite3.Connection, league_id: int) -> dict:
    """Get league profile from DB, or return conservative defaults."""
    row = conn.execute(
        "SELECT * FROM league_profiles WHERE league_id = ?", (league_id,)
    ).fetchone()

    if row:
        return dict(row)

    # Conservative defaults for unknown leagues
    return {
        "league_id": league_id,
        "name": "Unknown",
        "reliability_score": 4.0,
        "min_edge_threshold": 0.10,
        "max_stake_units": 0.25,
    }


def score_confidence(
    candidate: dict,
    league_profile: dict,
    model_source: str = "poisson",
) -> dict:
    """
    Compute a composite confidence score for a value candidate.

    Factors:
        1. Edge strength (higher edge → more confident)
        2. League reliability (from DB profile)
        3. Model source quality (poisson > fallback)
        4. Odds sanity (extreme odds are less trustworthy)

    Returns the candidate dict enriched with:
        confidence, league_reliability, data_quality_flags
    """
    edge = candidate["edge"]
    odds = candidate["odds"]
    reliability = league_profile.get("reliability_score", 5.0)
    data_quality = float(candidate.get("data_quality", 50.0))

    # Factor 1: Edge strength (0-0.3)
    edge_score = min(edge * 3.0, 0.3)

    # Factor 2: League reliability (0-0.3)
    league_score = (reliability / 10.0) * 0.3

    # Factor 3: Model source (0-0.2)
    source_scores = {
        "poisson": 0.18,
        "xgboost": 0.20,
        "hybrid": 0.20,
        "fallback": 0.05,
    }
    source_score = source_scores.get(model_source, 0.10)

    # Factor 3b: Data quality (0-0.15)
    data_quality_score = max(0.0, min(data_quality / 100.0, 1.0)) * 0.15

    # Factor 4: Odds sanity (0-0.2)
    # Very high odds (>8.0) or very low (<1.15) are noisier
    if 1.3 <= odds <= 6.0:
        odds_score = 0.20
    elif 1.15 <= odds <= 8.0:
        odds_score = 0.12
    else:
        odds_score = 0.05

    confidence = round(edge_score + league_score + source_score + data_quality_score + odds_score, 3)
    confidence = min(confidence, 1.0)

    # Data quality flags
    flags = []
    if model_source == "fallback":
        flags.append("fallback_model")
    if reliability < 6.0:
        flags.append("low_reliability_league")
    if odds > 8.0:
        flags.append("longshot")
    if odds < 1.15:
        flags.append("heavy_favorite")
    if data_quality < 60:
        flags.append("thin_or_low_quality_data")

    candidate["confidence"] = confidence
    candidate["league_reliability"] = reliability
    candidate["data_quality"] = data_quality
    candidate["data_quality_flags"] = flags
    candidate["min_edge_threshold"] = league_profile.get("min_edge_threshold", 0.05)
    candidate["max_stake_units"] = league_profile.get("max_stake_units", 1.0)

    return candidate


def apply_risk_filter(candidates: list[dict]) -> list[dict]:
    """
    Remove candidates that don't pass risk thresholds.
    A candidate must have edge > league-specific minimum.
    """
    passed = []
    for c in candidates:
        min_edge = c.get("min_edge_threshold", 0.05)
        min_prob = 0.46
        if c.get("odds", 0) >= 4.0:
            min_prob = 0.34
        if c.get("odds", 0) >= 7.0:
            min_prob = 0.24

        data_quality = float(c.get("data_quality", 50.0))
        confidence_floor = 0.35 if data_quality >= 60 else 0.45

        if (
            c["edge"] >= min_edge
            and c["confidence"] >= confidence_floor
            and c["model_prob"] >= min_prob
            and data_quality >= 35
        ):
            passed.append(c)
        else:
            logger.debug(
                f"Risk filter removed: {c['home_team']} vs {c['away_team']} "
                f"{c['market']}/{c['selection']} edge={c['edge']:.3f} "
                f"(min={min_edge}) prob={c['model_prob']:.3f} "
                f"quality={data_quality:.1f}"
            )
    return passed

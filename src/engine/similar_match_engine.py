"""
Similar Match Engine — Layer 10 of BILQE

Finds historically similar matches and computes actual hit rates
to reality-check model predictions.

Similarity based on:
    - Rating difference (Elo-like)
    - Expected goals environment
    - Form differential
    - Home advantage factor
    - League strength tier

This prevents overconfidence by grounding predictions in
actual historical outcomes from comparable matchups.
"""

from __future__ import annotations

import math
import logging
from typing import Optional

logger = logging.getLogger("football_predictor")

# Maximum number of similar matches to retrieve
MAX_SIMILAR = 100

# Similarity feature weights
SIMILARITY_WEIGHTS = {
    "rating_diff": 0.30,
    "xg_total": 0.25,
    "form_diff": 0.20,
    "home_advantage": 0.15,
    "league_tier": 0.10,
}

# League tier mapping (higher = stronger data, more reliable baseline)
LEAGUE_TIERS = {
    "Premier League": 5, "LaLiga": 5, "Serie A": 5, "Bundesliga": 5, "Ligue 1": 5,
    "Champions League": 5, "Europa League": 4, "Championship": 4,
    "Eredivisie": 4, "Primeira Liga": 4, "Süper Lig": 3, "Scottish Premiership": 3,
    "Brasileirão Série A": 4, "MLS": 3, "Liga MX": 3,
}

DEFAULT_LEAGUE_TIER = 2


def _compute_similarity(
    target: dict,
    candidate: dict,
) -> float:
    """
    Compute similarity score (0-1) between target match and historical candidate.
    
    Lower distance = more similar.
    
    Features:
        rating_diff: absolute Elo difference between teams
        xg_total: expected total goals environment
        form_diff: form differential (home form - away form)
        home_advantage: home team's home advantage factor
        league_tier: league quality tier
    """
    # Rating difference similarity
    target_rd = target.get("rating_diff", 0)
    cand_rd = candidate.get("rating_diff", 0)
    rd_dist = abs(target_rd - cand_rd) / 500.0  # normalize to ~0-1 range
    rd_sim = max(0, 1.0 - rd_dist)

    # xG total similarity
    target_xg = target.get("xg_total", 2.5)
    cand_xg = candidate.get("xg_total", 2.5)
    xg_dist = abs(target_xg - cand_xg) / 3.0
    xg_sim = max(0, 1.0 - xg_dist)

    # Form difference similarity
    target_form = target.get("form_diff", 0)
    cand_form = candidate.get("form_diff", 0)
    form_dist = abs(target_form - cand_form) / 10.0
    form_sim = max(0, 1.0 - form_dist)

    # Home advantage similarity
    target_ha = target.get("home_advantage", 0.3)
    cand_ha = candidate.get("home_advantage", 0.3)
    ha_dist = abs(target_ha - cand_ha) / 0.5
    ha_sim = max(0, 1.0 - ha_dist)

    # League tier similarity
    target_lt = target.get("league_tier", 2)
    cand_lt = candidate.get("league_tier", 2)
    lt_dist = abs(target_lt - cand_lt) / 5.0
    lt_sim = max(0, 1.0 - lt_dist)

    # Weighted composite
    similarity = (
        SIMILARITY_WEIGHTS["rating_diff"] * rd_sim +
        SIMILARITY_WEIGHTS["xg_total"] * xg_sim +
        SIMILARITY_WEIGHTS["form_diff"] * form_sim +
        SIMILARITY_WEIGHTS["home_advantage"] * ha_sim +
        SIMILARITY_WEIGHTS["league_tier"] * lt_sim
    )

    return similarity


def find_similar_match_stats(
    conn,
    home_name: str,
    away_name: str,
    league_name: str,
    predicted_home_xg: float,
    predicted_away_xg: float,
) -> dict:
    """
    Find similar historical matches and compute actual hit rates.
    
    Returns:
        dict mapping market_name → {hit_rate, count}
        
    Example:
        {
            "Home Win": {"hit_rate": 65.7, "count": 143},
            "Over 2.5 Goals": {"hit_rate": 52.3, "count": 143},
        }
    """
    if conn is None:
        return {}

    # Build target profile
    target_xg_total = predicted_home_xg + predicted_away_xg
    target_xg_diff = predicted_home_xg - predicted_away_xg
    league_tier = LEAGUE_TIERS.get(league_name, DEFAULT_LEAGUE_TIER)

    # Get Elo ratings for target teams
    target_rating_diff = 0.0
    target_home_form = 0.5
    target_away_form = 0.5
    target_home_advantage = 0.3

    try:
        from src.db.team_intelligence import get_team_rating, get_home_advantage
        h_elo, h_mom, _, _ = get_team_rating(conn, home_name, league_name)
        a_elo, a_mom, _, _ = get_team_rating(conn, away_name, league_name)
        target_rating_diff = h_elo - a_elo

        target_home_advantage = get_home_advantage(conn, home_name) / 100.0
    except Exception:
        pass

    try:
        from src.db.team_state import get_team_state
        h_state = get_team_state(conn, home_name, league_name, "overall")
        a_state = get_team_state(conn, away_name, league_name, "overall")
        if h_state:
            target_home_form = h_state.form_last5
        if a_state:
            target_away_form = a_state.form_last5
    except Exception:
        pass

    target = {
        "rating_diff": target_rating_diff,
        "xg_total": target_xg_total,
        "form_diff": target_home_form - target_away_form,
        "home_advantage": target_home_advantage,
        "league_tier": league_tier,
    }

    # Load historical matches
    try:
        rows = conn.execute(
            """SELECT match_id, league, home_team, away_team,
                      home_goals, away_goals,
                      home_elo_before, away_elo_before,
                      home_xg, away_xg
               FROM match_history
               WHERE home_goals IS NOT NULL
               ORDER BY match_date DESC
               LIMIT 5000"""
        ).fetchall()
    except Exception as e:
        logger.debug(f"Similar match engine: cannot query match_history: {e}")
        return {}

    if not rows:
        return {}

    # Score each historical match for similarity
    scored_matches = []
    for row in rows:
        cand_home_goals = row[4]
        cand_away_goals = row[5]
        cand_home_elo = row[6] or 1500
        cand_away_elo = row[7] or 1500
        cand_home_xg = row[8] or (cand_home_goals * 0.9)
        cand_away_xg = row[9] or (cand_away_goals * 0.9)
        cand_league = row[1]

        candidate = {
            "rating_diff": cand_home_elo - cand_away_elo,
            "xg_total": cand_home_xg + cand_away_xg,
            "form_diff": 0,  # not tracked per-match in history
            "home_advantage": 0.3,  # default
            "league_tier": LEAGUE_TIERS.get(cand_league, DEFAULT_LEAGUE_TIER),
        }

        similarity = _compute_similarity(target, candidate)

        scored_matches.append({
            "similarity": similarity,
            "home_goals": cand_home_goals,
            "away_goals": cand_away_goals,
            "total_goals": cand_home_goals + cand_away_goals,
        })

    # Sort by similarity descending, take top N
    scored_matches.sort(key=lambda x: x["similarity"], reverse=True)
    top_matches = scored_matches[:MAX_SIMILAR]

    if not top_matches:
        return {}

    # Compute hit rates for key markets
    results = {}
    n = len(top_matches)

    # Home Win
    home_wins = sum(1 for m in top_matches if m["home_goals"] > m["away_goals"])
    results["Home Win"] = {"hit_rate": round(home_wins / n * 100, 1), "count": n}

    # Draw
    draws = sum(1 for m in top_matches if m["home_goals"] == m["away_goals"])
    results["Draw"] = {"hit_rate": round(draws / n * 100, 1), "count": n}

    # Away Win
    away_wins = sum(1 for m in top_matches if m["home_goals"] < m["away_goals"])
    results["Away Win"] = {"hit_rate": round(away_wins / n * 100, 1), "count": n}

    # Over/Under goals
    for threshold in [0.5, 1.5, 2.5, 3.5, 4.5]:
        over = sum(1 for m in top_matches if m["total_goals"] > threshold)
        under = n - over
        results[f"Over {threshold} Goals"] = {"hit_rate": round(over / n * 100, 1), "count": n}
        results[f"Under {threshold} Goals"] = {"hit_rate": round(under / n * 100, 1), "count": n}

    # BTTS
    btts_yes = sum(1 for m in top_matches if m["home_goals"] >= 1 and m["away_goals"] >= 1)
    results["BTTS - Yes"] = {"hit_rate": round(btts_yes / n * 100, 1), "count": n}
    results["BTTS - No"] = {"hit_rate": round((n - btts_yes) / n * 100, 1), "count": n}

    # FH markets (approximate — use 45% of total)
    for threshold in [0.5, 1.5]:
        approx_fh_goals = [m["total_goals"] * 0.45 for m in top_matches]
        over_fh = sum(1 for g in approx_fh_goals if g > threshold)
        results[f"FH Over {threshold} Goals"] = {"hit_rate": round(over_fh / n * 100, 1), "count": n}
        results[f"FH Under {threshold} Goals"] = {"hit_rate": round((n - over_fh) / n * 100, 1), "count": n}

    return results

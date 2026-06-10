"""
Agent 3: Probability Engine

Wraps existing Poisson + XGBoost models to produce per-market probabilities.
"""

import logging
import math
from src.ml.poisson_model import PoissonGoalModel
from src.ml.team_stats_db import get_team_stats
from src.ml.predictor import XGBoostPredictor
from src.ml.feature_builder import FeatureBuilder, TeamProfile

logger = logging.getLogger("football_predictor")

# Singleton models
_poisson = PoissonGoalModel()
_xgb = XGBoostPredictor()

# Map SofaScore league names → Poisson profile keys
LEAGUE_KEY_MAP = {
    "Premier League": "Premier League",
    "LaLiga": "LaLiga",
    "La Liga": "LaLiga",
    "Serie A": "Serie A",
    "Bundesliga": "Bundesliga",
    "Ligue 1": "Ligue 1",
    "Championship": "Premier League",  # closest proxy
    "Eredivisie": "Eredivisie",
    "VriendenLoterij Eredivisie": "Eredivisie",
    "Primeira Liga": "Serie A",
    "Liga Portugal": "Serie A",
    "Liga Portugal Betclic": "Serie A",
    "Belgian Pro League": "Ligue 1",
    "Jupiler Pro League": "Ligue 1",
    "Pro League": "Ligue 1",
    "Süper Lig": "Süper Lig",
    "Trendyol Süper Lig": "Süper Lig",
    "Super Lig": "Süper Lig",
    "Scottish Premiership": "Premier League",
    "Champions League": "Champions League",
    "UEFA Champions League": "Champions League",
    "Europa League": "Champions League",
    "UEFA Europa League": "Champions League",
    "Conference League": "Champions League",
    "UEFA Europa Conference League": "Champions League",
}


def _poisson_over_prob(lam: float, threshold: int) -> float:
    """Probability that a Poisson total is greater than threshold."""
    if lam <= 0:
        return 0.0
    cumulative = sum(math.exp(-lam) * (lam ** k) / math.factorial(k) for k in range(threshold + 1))
    return max(0.0, min(1.0, 1.0 - cumulative))


def _profile_from_stats(team_name: str, stats) -> TeamProfile:
    """Convert runtime team stats into the richer feature profile XGBoost expects."""
    scored = float(getattr(stats, "scored", 1.2))
    conceded = float(getattr(stats, "conceded", 1.2))
    matches = int(getattr(stats, "matches_played", 0) or 0)
    avg_total = scored + conceded

    raw_form = float(getattr(stats, "form_last5", 0.5) or 0.5)
    form_last5 = raw_form * 15.0 if raw_form <= 1.0 else raw_form

    return TeamProfile(
        team_name=team_name,
        matches_played=matches,
        avg_scored=scored,
        avg_conceded=conceded,
        avg_total_goals=avg_total,
        btts_rate=(1 - math.exp(-scored)) * (1 - math.exp(-conceded)),
        clean_sheet_rate=math.exp(-conceded),
        failed_to_score_rate=math.exp(-scored),
        over_1_5_rate=_poisson_over_prob(avg_total, 1),
        over_2_5_rate=_poisson_over_prob(avg_total, 2),
        over_0_5_ht_rate=_poisson_over_prob(avg_total * 0.45, 0),
        form_last5=max(0.0, min(15.0, form_last5)),
        goal_diff=(scored - conceded) * max(matches, 1),
    )


def _blend(poisson_prob: float, xgb_prob: float | None, weight: float) -> float:
    if xgb_prob is None:
        return poisson_prob
    return poisson_prob * (1.0 - weight) + xgb_prob * weight


def _market_weight(xgb_prediction, market: str, data_quality: float) -> float:
    """
    Choose a conservative XGBoost blend weight.

    The bundled models are useful as a second opinion, but the stored metrics show
    modest AUC, so Poisson remains the anchor until richer real match history exists.
    """
    if xgb_prediction is None or data_quality < 45:
        return 0.0

    metrics = getattr(_xgb.trainer, "metrics", {}).get(market, {})
    auc = float(metrics.get("roc_auc_mean", 0.5) or 0.5)
    if auc < 0.53:
        return 0.10
    if auc < 0.57:
        return 0.18
    return 0.25


def estimate_probabilities(match: dict) -> dict:
    """
    Produce probability estimates for all supported markets.

    Returns dict with:
        "1X2": {"home": p, "draw": p, "away": p},
        "O/U 2.5": {"over": p, "under": p},
        "BTTS": {"yes": p, "no": p},
        "goals": {"exp_home": float, "exp_away": float},
        "source": "poisson" | "xgboost" | "hybrid"
    """
    home_team = match["home_team"]
    away_team = match["away_team"]
    league_name = match.get("league_name", "")
    league_key = LEAGUE_KEY_MAP.get(league_name, "default")

    # Get team stats - home team gets home venue stats, away gets away
    home_stats = get_team_stats(home_team, "home", league_key)
    away_stats = get_team_stats(away_team, "away", league_key)

    # --- Poisson model ---
    data_quality = 50.0
    xgb_prediction = None
    try:
        # TeamVenueStats has .scored, .conceded, .corners, .cards attributes
        # Handle both dict and TeamVenueStats objects
        if hasattr(home_stats, 'scored'):
            home_gf = home_stats.scored
            home_ga = home_stats.conceded
        else:
            home_gf = home_stats.get("scored", home_stats.get("gf", 1.4))
            home_ga = home_stats.get("conceded", home_stats.get("ga", 1.1))

        if hasattr(away_stats, 'scored'):
            away_gf = away_stats.scored
            away_ga = away_stats.conceded
        else:
            away_gf = away_stats.get("scored", away_stats.get("gf", 1.2))
            away_ga = away_stats.get("conceded", away_stats.get("ga", 1.3))

        # Create a league-specific Poisson instance
        poisson = PoissonGoalModel(league=league_key)

        prediction = poisson.predict(
            home_scored=float(home_gf),
            home_conceded=float(home_ga),
            away_scored=float(away_gf),
            away_conceded=float(away_ga),
            home_team=home_team,
            away_team=away_team,
        )

        exp_home = prediction.lambda_home
        exp_away = prediction.lambda_away

        # Convert from 0-100 scale to 0-1 scale
        probs_1x2 = {
            "home": prediction.home_win / 100.0,
            "draw": prediction.draw / 100.0,
            "away": prediction.away_win / 100.0,
        }

        probs_ou = {
            "over": prediction.over_2_5 / 100.0,
            "under": prediction.under_2_5 / 100.0,
        }

        probs_btts = {
            "yes": prediction.btts_yes / 100.0,
            "no": prediction.btts_no / 100.0,
        }

        source = "poisson"

    except Exception as e:
        logger.warning(f"Poisson failed for {home_team} vs {away_team}: {e}")
        # Fallback to baseline
        exp_home, exp_away = 1.4, 1.1
        probs_1x2 = {"home": 0.42, "draw": 0.27, "away": 0.31}
        probs_ou = {"over": 0.52, "under": 0.48}
        probs_btts = {"yes": 0.50, "no": 0.50}
        source = "fallback"

    # --- XGBoost second opinion ---
    try:
        home_profile = _profile_from_stats(home_team, home_stats)
        away_profile = _profile_from_stats(away_team, away_stats)
        data_quality = FeatureBuilder.compute_data_quality(
            home_profile,
            away_profile,
            league_name=league_name,
            country=match.get("country", ""),
        )

        if _xgb.ensure_loaded():
            xgb_prediction = _xgb.predict(home_profile, away_profile)
            xgb_probs = {p.market: p.probability for p in xgb_prediction.predictions}

            hw_w = _market_weight(xgb_prediction, "home_win", data_quality)
            draw_w = _market_weight(xgb_prediction, "draw", data_quality)
            o25_w = _market_weight(xgb_prediction, "over_2_5", data_quality)
            btts_w = _market_weight(xgb_prediction, "btts", data_quality)

            probs_1x2["home"] = _blend(probs_1x2["home"], xgb_probs.get("home_win"), hw_w)
            probs_1x2["draw"] = _blend(probs_1x2["draw"], xgb_probs.get("draw"), draw_w)
            probs_1x2["away"] = max(0.03, 1.0 - probs_1x2["home"] - probs_1x2["draw"])

            probs_ou["over"] = _blend(probs_ou["over"], xgb_probs.get("over_2_5"), o25_w)
            probs_ou["under"] = 1.0 - probs_ou["over"]

            probs_btts["yes"] = _blend(probs_btts["yes"], xgb_probs.get("btts"), btts_w)
            probs_btts["no"] = 1.0 - probs_btts["yes"]

            if any(w > 0 for w in (hw_w, draw_w, o25_w, btts_w)):
                source = "hybrid"
    except Exception as e:
        logger.warning(f"XGBoost blend failed for {home_team} vs {away_team}: {e}")

    # Normalize 1X2 to sum to 1.0
    total = sum(probs_1x2.values())
    if total > 0 and abs(total - 1.0) > 0.01:
        probs_1x2 = {k: v / total for k, v in probs_1x2.items()}

    return {
        "1X2": {k: round(v, 4) for k, v in probs_1x2.items()},
        "O/U 2.5": {k: round(v, 4) for k, v in probs_ou.items()},
        "BTTS": {k: round(v, 4) for k, v in probs_btts.items()},
        "goals": {"exp_home": round(exp_home, 2), "exp_away": round(exp_away, 2)},
        "source": source,
        "data_quality": round(data_quality, 1),
    }

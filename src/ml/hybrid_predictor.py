"""
Hybrid Prediction Engine — Poisson + XGBoost.

Architecture:
  - Poisson model handles GOALS markets (Over/Under, exact scores, BTTS)
  - XGBoost handles NON-GOALS markets (cards, corners) and validates goals
  - Both outputs are merged into a single unified prediction

This is the "correct architecture" for football prediction:
  Data Layer → Feature Engineering → Model → Calibration → Market Output
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.config import logger
from src.ml.poisson_model import PoissonGoalModel, PoissonPrediction
from src.ml.predictor import XGBoostPredictor, MatchPrediction
from src.ml.feature_builder import TeamProfile
from src.ml.model_provenance import synthetic_model_provenance


@dataclass
class HybridPrediction:
    """Unified prediction combining Poisson and XGBoost outputs."""
    home_team: str
    away_team: str
    
    # Poisson-derived data
    poisson: Optional[dict] = None
    
    # XGBoost-derived data
    xgboost: Optional[dict] = None
    
    # Unified market predictions (best of both)
    unified_markets: list[dict] = field(default_factory=list)
    
    def to_api_response(self) -> dict:
        """Format for the frontend API response."""
        return {
            "poisson": self.poisson,
            "xgboost_predictions": self.xgboost.get("predictions", []) if self.xgboost else [],
            "unified_markets": self.unified_markets,
        }


class HybridPredictor:
    """
    Combines Poisson goal model with XGBoost for a complete prediction.
    
    Poisson covers: Over/Under goals, BTTS, exact scores, result
    XGBoost covers: cards, corners, and validates Poisson outputs
    """

    def __init__(self, league: str = "default"):
        self.poisson = PoissonGoalModel(league)
        self.xgboost = XGBoostPredictor()

    def predict(
        self,
        home_profile: TeamProfile,
        away_profile: TeamProfile,
        home_team: str = "Home",
        away_team: str = "Away",
    ) -> HybridPrediction:
        """
        Generate a full hybrid prediction.
        
        The Poisson model creates per-match expected goals based on
        attacking strength × defensive weakness — making Arsenal vs Brighton
        completely different from Bournemouth vs Brentford.
        """
        # Phase 1: Poisson for goals (primary for goal markets)
        poisson_pred = self.poisson.predict(
            home_scored=home_profile.avg_scored,
            home_conceded=home_profile.avg_conceded,
            away_scored=away_profile.avg_scored,
            away_conceded=away_profile.avg_conceded,
            home_team=home_team,
            away_team=away_team,
        )

        # Phase 2: XGBoost for interaction-based markets
        xgb_pred = self.xgboost.predict(home_profile, away_profile)

        # Phase 3: Build unified market list
        unified = self._merge_predictions(poisson_pred, xgb_pred)

        logger.info(
            "Hybrid prediction: %s (λ=%.2f) vs %s (λ=%.2f) | Total xG=%.2f",
            home_team, poisson_pred.lambda_home,
            away_team, poisson_pred.lambda_away,
            poisson_pred.expected_total,
        )

        return HybridPrediction(
            home_team=home_team,
            away_team=away_team,
            poisson=poisson_pred.to_dict(),
            xgboost=xgb_pred.to_dict(),
            unified_markets=unified,
        )

    def _merge_predictions(
        self,
        poisson: PoissonPrediction,
        xgb: MatchPrediction,
    ) -> list[dict]:
        """
        Merge Poisson and XGBoost into a unified market list.
        
        Rule: Poisson is authoritative for goals, XGBoost for everything else.
        When both have a prediction for the same market, average them
        with Poisson getting 60% weight (more theoretically grounded for goals).
        """
        markets = []

        # Poisson-derived markets (primary)
        poisson_markets = {
            "Over 0.5 Goals": poisson.over_0_5,
            "Over 1.5 Goals": poisson.over_1_5,
            "Over 2.5 Goals": poisson.over_2_5,
            "Over 3.5 Goals": poisson.over_3_5,
            "Over 4.5 Goals": poisson.over_4_5,
            "Under 2.5 Goals": poisson.under_2_5,
            "BTTS - Yes": poisson.btts_yes,
            "BTTS - No": poisson.btts_no,
            "Home Win": poisson.home_win,
            "Draw": poisson.draw,
            "Away Win": poisson.away_win,
        }

        # XGBoost lookup
        xgb_lookup = {}
        for pred in xgb.predictions:
            xgb_lookup[pred.display_name] = pred.confidence_pct

        # Synthetic-trained XGBoost is shown for reference only; it gets no blend weight.
        xgb_blend_allowed = synthetic_model_provenance()["production_validated"]

        for market, prob in poisson_markets.items():
            xgb_prob = xgb_lookup.get(market)

            if xgb_prob is not None and xgb_blend_allowed:
                # Blend: 60% Poisson + 40% XGBoost for goals
                blended = prob * 0.6 + xgb_prob * 0.4
                source = "hybrid"
            else:
                blended = prob
                source = "poisson"

            conf = self._tier(blended)
            markets.append({
                "market": market,
                "probability": round(blended, 1),
                "confidence": conf,
                "source": source,
                "poisson_prob": round(prob, 1),
                "xgb_prob": round(xgb_prob, 1) if xgb_prob else None,
            })

        # Sort by probability descending
        markets.sort(key=lambda m: m["probability"], reverse=True)
        return markets

    @staticmethod
    def _tier(prob: float) -> str:
        if prob >= 75: return "Very High"
        if prob >= 60: return "High"
        if prob >= 45: return "Medium"
        if prob >= 30: return "Low"
        return "Very Low"

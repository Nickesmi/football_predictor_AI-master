"""
Data models for picks (betting decisions) and portfolio entries.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Pick:
    """A single betting decision from the pipeline."""
    match_id: str
    home_team: str
    away_team: str
    league_name: str
    market: str              # "1X2", "O/U 2.5", "BTTS"
    selection: str           # "home", "over", "yes", etc.
    model_prob: float        # our estimated probability (0-1)
    implied_prob: float      # bookmaker implied probability (0-1)
    edge: float              # model_prob - implied_prob
    odds_at_pick: float      # decimal odds
    confidence: float        # composite trust score (0-1)
    league_reliability: float  # league trust (0-10)
    grade: str               # "A+", "A", "B", "Pass", "Trap"
    stake_units: float       # 0 to 2.0

    @property
    def expected_value(self) -> float:
        """Expected value per unit staked."""
        return round((self.model_prob * (self.odds_at_pick - 1))
                     - ((1 - self.model_prob) * 1), 4)

    def to_db_dict(self) -> dict:
        return {
            "match_id": self.match_id,
            "market": self.market,
            "selection": self.selection,
            "model_prob": round(self.model_prob, 4),
            "implied_prob": round(self.implied_prob, 4),
            "edge": round(self.edge, 4),
            "odds_at_pick": round(self.odds_at_pick, 3),
            "confidence": round(self.confidence, 3),
            "league_reliability": round(self.league_reliability, 2),
            "grade": self.grade,
            "stake_units": round(self.stake_units, 2),
        }

    def to_display_dict(self) -> dict:
        return {
            "match": f"{self.home_team} vs {self.away_team}",
            "league": self.league_name,
            "market": self.market,
            "selection": self.selection,
            "odds": self.odds_at_pick,
            "model_prob": f"{self.model_prob * 100:.1f}%",
            "implied_prob": f"{self.implied_prob * 100:.1f}%",
            "edge": f"{self.edge * 100:.1f}%",
            "grade": self.grade,
            "stake": f"{self.stake_units}u",
            "ev_per_unit": f"{self.expected_value:+.3f}",
        }


GRADE_RULES = {
    # (min_edge, min_confidence) → (grade, base_stake)
    "A+": {"min_edge": 0.08, "min_confidence": 0.7, "base_stake": 1.75},
    "A":  {"min_edge": 0.05, "min_confidence": 0.5, "base_stake": 1.25},
    "B":  {"min_edge": 0.04, "min_confidence": 0.3, "base_stake": 0.75},
}


def assign_grade(edge: float, confidence: float, league_reliability: float, model_prob: float = 0.5, odds: float = 2.0) -> tuple[str, float]:
    """
    Assign a grade and suggested stake based on edge, confidence, league reliability, and Kelly fraction.
    Returns (grade, stake_units).
    """
    # Negative Edge = Instant Pass
    if edge <= 0:
        return "Pass", 0.0
        
    # League-adjusted minimum edge threshold
    if league_reliability >= 8.0:
        edge_boost = 0.0     # Top leagues: no adjustment
    elif league_reliability >= 6.5:
        edge_boost = 0.015   # Mid leagues: need 1.5% more edge
    else:
        edge_boost = 0.04    # Low leagues: need 4% more edge

    adjusted_edge = edge - edge_boost
    
    # Calculate pure Kelly Fraction
    # f* = (bp - q) / b  where b = odds - 1, p = model_prob, q = 1 - p
    b = odds - 1.0
    if b <= 0: b = 0.01
    q = 1.0 - model_prob
    kelly_fraction = ((b * model_prob) - q) / b
    
    # Cap pure kelly at 15% of bankroll to avoid ruin, scale it normally as 0.0 - 0.15
    kelly_cap = min(max(kelly_fraction, 0.0), 0.15)
    
    # We define 1 "unit" as 1% of the bankroll. So a kelly_cap of 0.10 means 10 units.
    # To keep standard betting sizes (0.5u - 3.0u), we will use a Quarter Kelly strategy.
    quarter_kelly_units = (kelly_cap * 100) * 0.25

    for grade_name, rules in GRADE_RULES.items():
        if adjusted_edge >= rules["min_edge"] and confidence >= rules["min_confidence"]:
            # Scale stake by league trust and model confidence
            league_scale = min(league_reliability / 9.0, 1.0)
            confidence_scale = min(confidence / 0.8, 1.0)
            
            # The final stake blends the grade's base stake with the Kelly suggestion
            kelly_stake = quarter_kelly_units * league_scale * confidence_scale
            
            # Blend 50/50 with the base rule for stability
            blended_stake = (rules["base_stake"] + kelly_stake) / 2.0
            
            stake = round(blended_stake, 2)
            stake = min(max(stake, 0.25), 3.0)  # hard cap between 0.25 and 3.0 units
            return grade_name, stake

    return "Pass", 0.0

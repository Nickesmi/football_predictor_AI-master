"""
Feature Engineering for XGBoost Prediction.

Builds ONE ROW PER MATCH with interaction features that capture
the relational dynamics between Team A and Team B.

Key insight: Football is relational. P(Event | A vs B) ≠ P(Event | A) ∩ P(Event | B)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class TeamProfile:
    """Aggregated venue-specific statistics for one team."""
    team_name: str
    matches_played: int
    avg_scored: float
    avg_conceded: float
    avg_total_goals: float
    btts_rate: float         # % matches where both teams scored
    clean_sheet_rate: float  # % matches with clean sheet
    failed_to_score_rate: float
    over_1_5_rate: float
    over_2_5_rate: float
    over_0_5_ht_rate: float
    form_last5: float        # Points from last 5 matches (W=3,D=1,L=0, max=15)
    goal_diff: float         # Total goal difference


@dataclass
class MatchFeatures:
    """One row of features for a single match prediction."""
    # Home team at home
    home_avg_scored: float
    home_avg_conceded: float
    home_btts_rate: float
    home_clean_sheet_rate: float
    home_fts_rate: float       # Failed to score
    home_over_2_5_rate: float
    home_form: float
    home_goal_diff: float

    # Away team away
    away_avg_scored: float
    away_avg_conceded: float
    away_btts_rate: float
    away_clean_sheet_rate: float
    away_fts_rate: float
    away_over_2_5_rate: float
    away_form: float
    away_goal_diff: float

    # Interaction features (the key differentiator vs pattern intersection)
    home_attack_vs_away_defense: float   # home_scored - away_conceded
    away_attack_vs_home_defense: float   # away_scored - home_conceded
    expected_total_goals: float          # sum of expected goals
    strength_diff: float                 # home_goal_diff - away_goal_diff
    btts_combined: float                 # avg of btts rates
    defensive_matchup: float             # home_clean_sheet vs away_fts_rate
    offensive_matchup: float             # combined scoring rates

    def to_dict(self) -> dict:
        return {
            "home_avg_scored": self.home_avg_scored,
            "home_avg_conceded": self.home_avg_conceded,
            "home_btts_rate": self.home_btts_rate,
            "home_clean_sheet_rate": self.home_clean_sheet_rate,
            "home_fts_rate": self.home_fts_rate,
            "home_over_2_5_rate": self.home_over_2_5_rate,
            "home_form": self.home_form,
            "home_goal_diff": self.home_goal_diff,
            "away_avg_scored": self.away_avg_scored,
            "away_avg_conceded": self.away_avg_conceded,
            "away_btts_rate": self.away_btts_rate,
            "away_clean_sheet_rate": self.away_clean_sheet_rate,
            "away_fts_rate": self.away_fts_rate,
            "away_over_2_5_rate": self.away_over_2_5_rate,
            "away_form": self.away_form,
            "away_goal_diff": self.away_goal_diff,
            "home_attack_vs_away_defense": self.home_attack_vs_away_defense,
            "away_attack_vs_home_defense": self.away_attack_vs_home_defense,
            "expected_total_goals": self.expected_total_goals,
            "strength_diff": self.strength_diff,
            "btts_combined": self.btts_combined,
            "defensive_matchup": self.defensive_matchup,
            "offensive_matchup": self.offensive_matchup,
        }

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([self.to_dict()])


FEATURE_COLUMNS = list(MatchFeatures(
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
).to_dict().keys())


class FeatureBuilder:
    """Builds interaction feature rows from team profiles."""

    @staticmethod
    def build(home: TeamProfile, away: TeamProfile) -> MatchFeatures:
        """
        Build a match feature row from home and away team profiles.

        This captures the RELATIONAL DYNAMICS between teams:
        - How does home's attack match up against away's defense?
        - How does away's attack match up against home's defense?
        - What is the expected goal environment?
        """
        home_exp = (home.avg_scored + away.avg_conceded) / 2
        away_exp = (away.avg_scored + home.avg_conceded) / 2

        return MatchFeatures(
            # Raw home stats
            home_avg_scored=home.avg_scored,
            home_avg_conceded=home.avg_conceded,
            home_btts_rate=home.btts_rate,
            home_clean_sheet_rate=home.clean_sheet_rate,
            home_fts_rate=home.failed_to_score_rate,
            home_over_2_5_rate=home.over_2_5_rate,
            home_form=home.form_last5,
            home_goal_diff=home.goal_diff,

            # Raw away stats
            away_avg_scored=away.avg_scored,
            away_avg_conceded=away.avg_conceded,
            away_btts_rate=away.btts_rate,
            away_clean_sheet_rate=away.clean_sheet_rate,
            away_fts_rate=away.failed_to_score_rate,
            away_over_2_5_rate=away.over_2_5_rate,
            away_form=away.form_last5,
            away_goal_diff=away.goal_diff,

            # Interaction features
            home_attack_vs_away_defense=home.avg_scored - away.avg_conceded,
            away_attack_vs_home_defense=away.avg_scored - home.avg_conceded,
            expected_total_goals=home_exp + away_exp,
            strength_diff=home.goal_diff - away.goal_diff,
            btts_combined=(home.btts_rate + away.btts_rate) / 2,
            defensive_matchup=(home.clean_sheet_rate + away.failed_to_score_rate) / 2,
            offensive_matchup=(home.avg_scored + away.avg_scored) / 2,
        )

    @staticmethod
    def compute_data_quality(home: TeamProfile, away: TeamProfile, league_name: str, country: str = "") -> float:
        """
        Compute a Data Quality score from 0-100 based on matches available and league context.
        """
        score = 100.0

        # Penalty for lack of history
        min_matches = min(home.matches_played, away.matches_played)
        if min_matches < 15:
            score -= (15 - min_matches) * 2  # up to 30 penalty

        if min_matches < 5:
            score -= 20  # extra severe penalty if basically no data

        # Category penalty
        from src.db.competition_tracker import _infer_category
        cat = _infer_category(league_name, country)
        
        if cat == 'friendly':
            score -= 40
        elif cat == 'youth':
            score -= 30
        elif cat == 'women':
            score -= 15  # often fewer historical matches available consistently
        elif cat == 'international':
            score -= 20  # International teams play infrequently

        # Ensure bounds
        return max(0.0, min(100.0, score))

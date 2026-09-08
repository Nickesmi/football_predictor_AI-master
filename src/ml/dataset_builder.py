"""
Synthetic Dataset Builder for XGBoost Training.

Generates realistic football match data with proper statistical distributions
for training when API data is unavailable. Uses league-specific priors.

IMPORTANT — this is 100% synthetic data (random team profiles + Poisson-
sampled outcomes drawn from those same profiles). It contains no real
match history, so:
  - Cross-validated metrics computed on it (see src/ml/trainer.py and
    models/training_metrics.json) measure how well a model reconstructs
    this generator's own formula, NOT real-world predictive skill. Do not
    quote those AUC/accuracy numbers as evidence of real accuracy.
  - This is why src/engine/probability_engine.py deliberately treats the
    XGBoost models as a low-weight "second opinion" on top of the
    Poisson model (which uses real, live team_state data), gated further
    by data_quality and the stored AUC.
  - Once enough real matches accumulate in the `match_history` table
    (populated by src/engine/live_updater.on_match_finished), the correct
    fix is to add a builder that turns match_history into point-in-time
    features (state as of each match's date, not "current" state) and
    retrain/validate with walk-forward splits — never a random shuffle
    split — before raising the XGBoost blend weight.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional

from src.ml.feature_builder import FeatureBuilder, TeamProfile, MatchFeatures, FEATURE_COLUMNS


# League-wide priors (realistic distributions)
LEAGUE_PRIORS = {
    "Premier League": {"avg_goals": 2.75, "btts_rate": 0.53, "home_win": 0.44, "draw": 0.25},
    "LaLiga":         {"avg_goals": 2.55, "btts_rate": 0.50, "home_win": 0.46, "draw": 0.24},
    "Serie A":        {"avg_goals": 2.60, "btts_rate": 0.52, "home_win": 0.43, "draw": 0.27},
    "Bundesliga":     {"avg_goals": 2.95, "btts_rate": 0.57, "home_win": 0.43, "draw": 0.23},
    "Ligue 1":        {"avg_goals": 2.60, "btts_rate": 0.48, "home_win": 0.45, "draw": 0.25},
}


def _random_team_profile(rng: np.random.Generator, league: str, venue: str) -> TeamProfile:
    """Generate a realistic random team profile based on league priors."""
    priors = LEAGUE_PRIORS.get(league, LEAGUE_PRIORS["Premier League"])
    avg_goals = priors["avg_goals"]
    
    # Stronger teams at home, weaker away (realistic venue gap)
    if venue == "home":
        scored = rng.normal(avg_goals * 0.58, 0.4)   # Home advantage
        conceded = rng.normal(avg_goals * 0.42, 0.35)
    else:
        scored = rng.normal(avg_goals * 0.40, 0.35)
        conceded = rng.normal(avg_goals * 0.55, 0.4)

    scored = max(0.3, scored)
    conceded = max(0.2, conceded)

    btts = np.clip(rng.normal(priors["btts_rate"], 0.12), 0.15, 0.90)
    cs = np.clip(rng.normal(0.30, 0.12), 0.05, 0.60)
    fts = np.clip(rng.normal(0.18, 0.10), 0.02, 0.50)
    o25 = np.clip(rng.normal(0.50, 0.15), 0.15, 0.85)
    o05ht = np.clip(rng.normal(0.68, 0.12), 0.30, 0.95)
    form = rng.uniform(3, 13)  # 5 matches, max 15 points

    return TeamProfile(
        team_name=f"Team_{rng.integers(1000)}",
        matches_played=rng.integers(15, 20),
        avg_scored=round(scored, 2),
        avg_conceded=round(conceded, 2),
        avg_total_goals=round(scored + conceded, 2),
        btts_rate=round(btts, 3),
        clean_sheet_rate=round(cs, 3),
        failed_to_score_rate=round(fts, 3),
        over_1_5_rate=round(np.clip(rng.normal(0.75, 0.12), 0.40, 0.98), 3),
        over_2_5_rate=round(o25, 3),
        over_0_5_ht_rate=round(o05ht, 3),
        form_last5=round(form, 1),
        goal_diff=round((scored - conceded) * rng.integers(15, 20), 1),
    )


def _simulate_outcome(
    rng: np.random.Generator,
    home: TeamProfile,
    away: TeamProfile,
    league: str,
) -> dict:
    """Simulate match outcome based on team profiles (Poisson-inspired)."""
    priors = LEAGUE_PRIORS.get(league, LEAGUE_PRIORS["Premier League"])
    
    # Expected goals with interaction
    home_exp = (home.avg_scored + away.avg_conceded) / 2
    away_exp = (away.avg_scored + home.avg_conceded) / 2

    # Poisson-sampled goals
    home_goals = rng.poisson(max(0.3, home_exp))
    away_goals = rng.poisson(max(0.2, away_exp))
    total = home_goals + away_goals

    return {
        "btts": int(home_goals > 0 and away_goals > 0),
        "over_1_5": int(total > 1),
        "over_2_5": int(total > 2),
        "over_3_5": int(total > 3),
        "home_win": int(home_goals > away_goals),
        "draw": int(home_goals == away_goals),
        "ht_over_0_5": int(rng.random() < home.over_0_5_ht_rate),
        "home_goals": home_goals,
        "away_goals": away_goals,
    }


class DatasetBuilder:
    """Build training datasets for XGBoost models."""

    def __init__(self, seed: int = 42):
        self.rng = np.random.default_rng(seed)

    def build_synthetic(
        self,
        n_matches: int = 2000,
        leagues: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        """
        Generate a synthetic training dataset with realistic football statistics.

        Each row is one match with:
        - 23 interaction features (from FeatureBuilder)
        - 7 binary targets (btts, over_1_5, over_2_5, over_3_5, home_win, draw, ht_over_0_5)
        """
        if leagues is None:
            leagues = list(LEAGUE_PRIORS.keys())

        rows = []
        builder = FeatureBuilder()

        for i in range(n_matches):
            league = leagues[i % len(leagues)]
            home = _random_team_profile(self.rng, league, "home")
            away = _random_team_profile(self.rng, league, "away")

            features = builder.build(home, away)
            outcome = _simulate_outcome(self.rng, home, away, league)

            row = features.to_dict()
            row.update(outcome)
            row["league"] = league
            rows.append(row)

        df = pd.DataFrame(rows)
        return df

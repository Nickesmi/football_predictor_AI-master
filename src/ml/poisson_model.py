"""
Poisson Goal Model — The Classical Football Prediction Engine.

Calculates per-match expected goals (λ) using attacking strength
and defensive weakness ratios, then derives exact probabilities
for every goal market via the Poisson distribution.

Key formulas:
  attack_strength = team_avg_scored / league_avg_scored
  defense_weakness = opponent_avg_conceded / league_avg_conceded
  λ_home = attack_home × defense_away × league_avg_home_goals
  λ_away = attack_away × defense_home × league_avg_away_goals

This makes Arsenal at home vs Brighton away
COMPLETELY DIFFERENT from Bournemouth at home vs Brentford away.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LeagueProfile:
    """League-wide averages for normalization."""
    avg_home_goals: float   # League average goals scored by home teams
    avg_away_goals: float   # League average goals scored by away teams
    avg_total_goals: float  # League average total goals per match

    @property
    def avg_home_conceded(self) -> float:
        return self.avg_away_goals  # Home team concedes what away scores

    @property
    def avg_away_conceded(self) -> float:
        return self.avg_home_goals  # Away team concedes what home scores


# Pre-computed league profiles (2023/24 season averages)
LEAGUE_PROFILES = {
    "Premier League":    LeagueProfile(1.55, 1.20, 2.75),
    "LaLiga":            LeagueProfile(1.48, 1.07, 2.55),
    "Serie A":           LeagueProfile(1.50, 1.10, 2.60),
    "Bundesliga":        LeagueProfile(1.65, 1.30, 2.95),
    "Ligue 1":           LeagueProfile(1.50, 1.10, 2.60),
    "Champions League":  LeagueProfile(1.55, 1.25, 2.80),
    "Süper Lig":         LeagueProfile(1.48, 1.25, 2.73),
    "Eredivisie":        LeagueProfile(1.70, 1.40, 3.10),
    "Liga Profesional":  LeagueProfile(1.13, 0.82, 1.95),
    "default":           LeagueProfile(1.50, 1.15, 2.65),
}


@dataclass
class TeamStrength:
    """Attacking and defensive strength ratios for one team at one venue."""
    team_name: str
    avg_scored: float       # e.g., Arsenal scores 2.2 at home
    avg_conceded: float     # e.g., Arsenal concedes 0.8 at home
    attack_strength: float  # avg_scored / league_avg → >1 means above average
    defense_weakness: float # avg_conceded / league_avg → <1 means strong defense


@dataclass
class PoissonPrediction:
    """Full Poisson-derived prediction for a single match."""
    home_team: str
    away_team: str
    lambda_home: float       # Expected goals for home team
    lambda_away: float       # Expected goals for away team
    expected_total: float    # λ_home + λ_away

    # Derived probabilities (all 0-100 scale)
    over_0_5: float = 0.0
    over_1_5: float = 0.0
    over_2_5: float = 0.0
    over_3_5: float = 0.0
    over_4_5: float = 0.0
    under_0_5: float = 0.0
    under_1_5: float = 0.0
    under_2_5: float = 0.0
    under_3_5: float = 0.0
    btts_yes: float = 0.0
    btts_no: float = 0.0
    home_win: float = 0.0
    draw: float = 0.0
    away_win: float = 0.0
    home_clean_sheet: float = 0.0
    away_clean_sheet: float = 0.0

    # First Half specific
    fh_over_0_5: float = 0.0
    fh_over_1_5: float = 0.0
    fh_home_win: float = 0.0
    fh_draw: float = 0.0
    fh_away_win: float = 0.0

    # Top 5 most likely scorelines
    top_scorelines: list[dict] = field(default_factory=list)
    
    # Complete list of all computed scorelines (filtered >= 0.01%)
    all_scorelines: list[dict] = field(default_factory=list)

    # Strength ratios for display
    home_attack_strength: float = 0.0
    home_defense_weakness: float = 0.0
    away_attack_strength: float = 0.0
    away_defense_weakness: float = 0.0

    def to_dict(self) -> dict:
        return {
            "home_team": self.home_team,
            "away_team": self.away_team,
            "lambda_home": round(self.lambda_home, 2),
            "lambda_away": round(self.lambda_away, 2),
            "expected_total": round(self.expected_total, 2),
            "strengths": {
                "home_attack": round(self.home_attack_strength, 2),
                "home_defense": round(self.home_defense_weakness, 2),
                "away_attack": round(self.away_attack_strength, 2),
                "away_defense": round(self.away_defense_weakness, 2),
            },
            "goals_markets": [
                {"market": "Over 0.5 Goals", "probability": round(self.over_0_5, 1)},
                {"market": "Under 0.5 Goals", "probability": round(self.under_0_5, 1)},
                {"market": "Over 1.5 Goals", "probability": round(self.over_1_5, 1)},
                {"market": "Under 1.5 Goals", "probability": round(self.under_1_5, 1)},
                {"market": "Over 2.5 Goals", "probability": round(self.over_2_5, 1)},
                {"market": "Under 2.5 Goals", "probability": round(self.under_2_5, 1)},
                {"market": "Over 3.5 Goals", "probability": round(self.over_3_5, 1)},
                {"market": "Under 3.5 Goals", "probability": round(self.under_3_5, 1)},
                {"market": "Over 4.5 Goals", "probability": round(self.over_4_5, 1)},
                {"market": "Under 4.5 Goals", "probability": round(100 - self.over_4_5, 1)},
            ],
            "btts": {"yes": round(self.btts_yes, 1), "no": round(self.btts_no, 1)},
            "result": {
                "home_win": round(self.home_win, 1),
                "draw": round(self.draw, 1),
                "away_win": round(self.away_win, 1),
            },
            "double_chance": {
                "home_or_draw": round(self.home_win + self.draw, 1),
                "away_or_draw": round(self.away_win + self.draw, 1),
                "home_or_away": round(self.home_win + self.away_win, 1)
            },
            "first_half": {
                "goals_markets": [
                    {"market": "Over 0.5 FH Goals", "probability": round(self.fh_over_0_5, 1)},
                    {"market": "Under 0.5 FH Goals", "probability": round(100 - self.fh_over_0_5, 1)},
                    {"market": "Over 1.5 FH Goals", "probability": round(self.fh_over_1_5, 1)},
                    {"market": "Under 1.5 FH Goals", "probability": round(100 - self.fh_over_1_5, 1)},
                ],
                "result": {
                    "home_win": round(self.fh_home_win, 1),
                    "draw": round(self.fh_draw, 1),
                    "away_win": round(self.fh_away_win, 1),
                }
            },
            "top_scorelines": self.top_scorelines[:6],
            "all_scorelines": self.all_scorelines,
            "xg": {
                "home": round(self.lambda_home, 2),
                "away": round(self.lambda_away, 2)
            }
        }


def _poisson_pmf(k: int, lam: float) -> float:
    """Poisson probability mass function: P(X = k)."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


class PoissonGoalModel:
    """
    Classical Poisson goal prediction model.

    Makes every match unique by computing attacking strength × defensive
    weakness × league context. Arsenal 2.2 at home vs Brighton 1.9 conceded
    produces a VERY different λ than Bournemouth 1.1 vs Brentford 1.0.
    """

    MAX_GOALS = 8  # Compute PMF up to this many goals

    def __init__(self, league: str = "default"):
        self.profile = LEAGUE_PROFILES.get(league, LEAGUE_PROFILES["default"])

    def compute_strengths(
        self,
        team_scored: float,
        team_conceded: float,
        venue: str,  # "home" or "away"
    ) -> TeamStrength:
        """Calculate attacking strength and defensive weakness ratios."""
        if venue == "home":
            attack_base = self.profile.avg_home_goals
            defense_base = self.profile.avg_home_conceded
        else:
            attack_base = self.profile.avg_away_goals
            defense_base = self.profile.avg_away_conceded

        attack = team_scored / max(attack_base, 0.01)
        defense = team_conceded / max(defense_base, 0.01)

        return TeamStrength(
            team_name="",
            avg_scored=team_scored,
            avg_conceded=team_conceded,
            attack_strength=round(attack, 3),
            defense_weakness=round(defense, 3),
        )

    def predict(
        self,
        home_scored: float,
        home_conceded: float,
        away_scored: float,
        away_conceded: float,
        home_team: str = "Home",
        away_team: str = "Away",
    ) -> PoissonPrediction:
        """
        Compute full Poisson prediction for a match.

        Args:
            home_scored: Home team's average goals scored at home
            home_conceded: Home team's average goals conceded at home
            away_scored: Away team's average goals scored away
            away_conceded: Away team's average goals conceded away
        """
        # Step 1: Calculate strength ratios
        home_str = self.compute_strengths(home_scored, home_conceded, "home")
        away_str = self.compute_strengths(away_scored, away_conceded, "away")

        # Step 2: Calculate expected goals (λ)
        # λ_home = home_attack × away_defense_weakness × league_avg_home_goals
        lambda_home = home_str.attack_strength * away_str.defense_weakness * self.profile.avg_home_goals
        lambda_away = away_str.attack_strength * home_str.defense_weakness * self.profile.avg_away_goals

        # Clamp to reasonable range — no single team realistically scores 3+ or 0.3 on average
        lambda_home = max(0.4, min(lambda_home, 3.5))
        lambda_away = max(0.3, min(lambda_away, 3.0))

        # Regression to mean: pull extreme lambdas toward league average (prevents
        # lopsided stats from producing 3%/88% result probabilities)
        REGRESS_FACTOR = 0.15  # 15% pull toward league mean
        mean_home = self.profile.avg_home_goals
        mean_away = self.profile.avg_away_goals
        lambda_home = lambda_home * (1 - REGRESS_FACTOR) + mean_home * REGRESS_FACTOR
        lambda_away = lambda_away * (1 - REGRESS_FACTOR) + mean_away * REGRESS_FACTOR

        # Step 3: Build probability matrix
        matrix = {}
        for h in range(self.MAX_GOALS + 1):
            for a in range(self.MAX_GOALS + 1):
                matrix[(h, a)] = _poisson_pmf(h, lambda_home) * _poisson_pmf(a, lambda_away)

        # Step 4: Derive all market probabilities
        total_goals_prob = {}
        for g in range(self.MAX_GOALS * 2 + 1):
            total_goals_prob[g] = sum(
                matrix[(h, a)] for h in range(self.MAX_GOALS + 1)
                for a in range(self.MAX_GOALS + 1) if h + a == g
            )

        # Over X.5 goals
        def over_x(x):
            return sum(p for g, p in total_goals_prob.items() if g > x) * 100

        # Result probabilities
        home_win_p = sum(matrix[(h, a)] for h in range(self.MAX_GOALS + 1)
                         for a in range(self.MAX_GOALS + 1) if h > a) * 100
        draw_p = sum(matrix[(h, a)] for h in range(self.MAX_GOALS + 1)
                     for a in range(self.MAX_GOALS + 1) if h == a) * 100
        away_win_p = sum(matrix[(h, a)] for h in range(self.MAX_GOALS + 1)
                         for a in range(self.MAX_GOALS + 1) if h < a) * 100

        # BTTS
        btts_yes_p = sum(matrix[(h, a)] for h in range(1, self.MAX_GOALS + 1)
                         for a in range(1, self.MAX_GOALS + 1)) * 100
        btts_no_p = 100 - btts_yes_p

        # Clean sheets
        home_cs = sum(matrix[(h, 0)] for h in range(self.MAX_GOALS + 1)) * 100
        away_cs = sum(matrix[(0, a)] for a in range(self.MAX_GOALS + 1)) * 100

        # Top scorelines & All scorelines with ranks
        scorelines_sorted = sorted(matrix.items(), key=lambda x: x[1], reverse=True)
        top_scores = [
            {"score": f"{h}-{a}", "probability": round(p * 100, 1)}
            for (h, a), p in scorelines_sorted[:6]
        ]
        
        all_scorelines = []
        for i, ((h, a), p) in enumerate(scorelines_sorted):
            prob_pct = p * 100
            if prob_pct >= 0.01:  # Filter out extremely unlikely tails to save space
                all_scorelines.append({
                    "score": f"{h}-{a}",
                    "probability": round(prob_pct, 2),
                    "rank": i + 1
                })

        # ── First Half Calculations ──
        fh_matrix = {}
        fh_lam_h = lambda_home * 0.45
        fh_lam_a = lambda_away * 0.45
            
        for h in range(self.MAX_GOALS + 1):
            for a in range(self.MAX_GOALS + 1):
                fh_matrix[(h, a)] = _poisson_pmf(h, fh_lam_h) * _poisson_pmf(a, fh_lam_a)

        # Calculate FH market probabilities from fh_matrix
        fh_over_0_5_p = sum(fh_matrix[(h, a)] for h in range(self.MAX_GOALS + 1)
                         for a in range(self.MAX_GOALS + 1) if h + a > 0.5) * 100
        fh_over_1_5_p = sum(fh_matrix[(h, a)] for h in range(self.MAX_GOALS + 1)
                         for a in range(self.MAX_GOALS + 1) if h + a > 1.5) * 100
        
        fh_home_win_p = sum(fh_matrix[(h, a)] for h in range(self.MAX_GOALS + 1)
                         for a in range(self.MAX_GOALS + 1) if h > a) * 100
        fh_draw_p = sum(fh_matrix[(h, a)] for h in range(self.MAX_GOALS + 1)
                     for a in range(self.MAX_GOALS + 1) if h == a) * 100
        fh_away_win_p = sum(fh_matrix[(h, a)] for h in range(self.MAX_GOALS + 1)
                         for a in range(self.MAX_GOALS + 1) if h < a) * 100

        return PoissonPrediction(
            home_team=home_team,
            away_team=away_team,
            lambda_home=lambda_home,
            lambda_away=lambda_away,
            expected_total=lambda_home + lambda_away,
            over_0_5=over_x(0),
            over_1_5=over_x(1),
            over_2_5=over_x(2),
            over_3_5=over_x(3),
            over_4_5=over_x(4),
            under_0_5=100 - over_x(0),
            under_1_5=100 - over_x(1),
            under_2_5=100 - over_x(2),
            under_3_5=100 - over_x(3),
            btts_yes=btts_yes_p,
            btts_no=btts_no_p,
            home_win=home_win_p,
            draw=draw_p,
            away_win=away_win_p,
            home_clean_sheet=home_cs,
            away_clean_sheet=away_cs,
            fh_over_0_5=fh_over_0_5_p,
            fh_over_1_5=fh_over_1_5_p,
            fh_home_win=fh_home_win_p,
            fh_draw=fh_draw_p,
            fh_away_win=fh_away_win_p,
            top_scorelines=top_scores,
            all_scorelines=all_scorelines,
            home_attack_strength=home_str.attack_strength,
            home_defense_weakness=home_str.defense_weakness,
            away_attack_strength=away_str.attack_strength,
            away_defense_weakness=away_str.defense_weakness,
        )

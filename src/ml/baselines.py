"""
Baseline models for the real-data walk-forward backtest (Phase 2, §6).

Every baseline is fit ONLY on a training slice and evaluated on a
different (later, chronologically disjoint) slice — never fit on the data
it's scored against. That is the walk-forward backtest's calibration-
leakage guard applied to the baselines themselves, not just the ML model.

Markets covered: 1X2 (home/draw/away), Over/Under 1.5/2.5/3.5, BTTS.
Correct-score and market-implied-odds baselines are explicitly marked
unavailable — see NotImplementedBaseline below — rather than faked.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from sklearn.linear_model import LogisticRegression

EPS = 1e-7


class _ConstantProbaClassifier:
    """Fallback used when a training slice has only one observed class for
    a binary target (e.g. a very small/thin training window). Predicts the
    observed (clipped) base rate for every input rather than letting
    sklearn raise — this is a legitimate edge case for small real-data
    folds, not just a test-fixture quirk."""

    def __init__(self, p_true: float):
        self.p_true = min(max(p_true, EPS), 1 - EPS)
        self.classes_ = np.array([False, True])

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        n = len(x)
        return np.column_stack([np.full(n, 1 - self.p_true), np.full(n, self.p_true)])


def _fit_binary_logistic(x: np.ndarray, y: np.ndarray):
    """LogisticRegression, with a constant-probability fallback if the
    training slice only contains one class (sklearn cannot fit that)."""
    y = np.asarray(y)
    if len(np.unique(y)) < 2:
        return _ConstantProbaClassifier(p_true=float(y.mean()) if len(y) else 0.5)
    return LogisticRegression(max_iter=1000).fit(x, y)


def add_outcome_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["result"] = np.select(
        [df["home_goals"] > df["away_goals"], df["home_goals"] == df["away_goals"]],
        ["H", "D"], default="A",
    )
    df["total_goals"] = df["home_goals"] + df["away_goals"]
    df["over_1_5"] = df["total_goals"] > 1
    df["over_2_5"] = df["total_goals"] > 2
    df["over_3_5"] = df["total_goals"] > 3
    df["btts"] = (df["home_goals"] > 0) & (df["away_goals"] > 0)
    return df


@dataclass
class MarketProbs:
    """Probabilities for every covered market, for one match."""
    p_home: float
    p_draw: float
    p_away: float
    p_over_1_5: float
    p_over_2_5: float
    p_over_3_5: float
    p_btts_yes: float


# ══════════════════════════════════════════════════════════════════════
# Baseline 1 — historical class-frequency model
# ══════════════════════════════════════════════════════════════════════

class FrequencyBaseline:
    """Ignores the specific match entirely; always predicts the training
    set's overall empirical rate for every market."""

    name = "frequency"

    def fit(self, train: pd.DataFrame) -> "FrequencyBaseline":
        train = add_outcome_columns(train)
        n = len(train)
        self.p_home = float((train["result"] == "H").mean())
        self.p_draw = float((train["result"] == "D").mean())
        self.p_away = float((train["result"] == "A").mean())
        self.p_over_1_5 = float(train["over_1_5"].mean())
        self.p_over_2_5 = float(train["over_2_5"].mean())
        self.p_over_3_5 = float(train["over_3_5"].mean())
        self.p_btts_yes = float(train["btts"].mean())
        self.n_train = n
        return self

    def predict(self, test: pd.DataFrame) -> list[MarketProbs]:
        row = MarketProbs(
            self.p_home, self.p_draw, self.p_away,
            self.p_over_1_5, self.p_over_2_5, self.p_over_3_5, self.p_btts_yes,
        )
        return [row for _ in range(len(test))]


# ══════════════════════════════════════════════════════════════════════
# Baseline 2 — naive home-team baseline (degenerate "always the home team")
# ══════════════════════════════════════════════════════════════════════

class HomeTeamBaseline:
    """The trivial strategy: always predict a home win with (near-)full
    confidence, and always predict Over 2.5 / BTTS-Yes at the training
    rate's majority class as a hard call. Included specifically because it
    is a strong, well-known trap — high accuracy on the majority class,
    terrible log loss/Brier — a legitimate model must beat this on
    probability quality, not just hit-rate.
    """

    name = "home_baseline"

    def fit(self, train: pd.DataFrame) -> "HomeTeamBaseline":
        train = add_outcome_columns(train)
        # Still clip away from exactly 0/1 (a truly zero-probability claim
        # is unfalsifiable and log loss would be -inf on a single miss).
        self.p_home = 1.0 - 2 * EPS
        self.p_draw = EPS
        self.p_away = EPS
        self.p_over_2_5 = 1.0 - EPS if train["over_2_5"].mean() >= 0.5 else EPS
        self.p_over_1_5 = 1.0 - EPS if train["over_1_5"].mean() >= 0.5 else EPS
        self.p_over_3_5 = 1.0 - EPS if train["over_3_5"].mean() >= 0.5 else EPS
        self.p_btts_yes = 1.0 - EPS if train["btts"].mean() >= 0.5 else EPS
        return self

    def predict(self, test: pd.DataFrame) -> list[MarketProbs]:
        row = MarketProbs(
            self.p_home, self.p_draw, self.p_away,
            self.p_over_1_5, self.p_over_2_5, self.p_over_3_5, self.p_btts_yes,
        )
        return [row for _ in range(len(test))]


# ══════════════════════════════════════════════════════════════════════
# Baseline 3 — Elo baseline
# ══════════════════════════════════════════════════════════════════════

class EloBaseline:
    """Uses ONLY the point-in-time Elo ratings (home_elo_before /
    away_elo_before, themselves leakage-safe by construction — see
    src/ml/point_in_time.py). The Elo-diff -> outcome mapping (including
    goals-market proxies) is a multinomial/binary logistic regression FIT
    ON THE TRAINING SPLIT ONLY.
    """

    name = "elo"

    def fit(self, train: pd.DataFrame) -> "EloBaseline":
        train = add_outcome_columns(train)
        x = (train["home_elo_before"] + 65 - train["away_elo_before"]).values.reshape(-1, 1)

        self._result_clf = LogisticRegression(max_iter=1000)
        self._result_clf.fit(x, train["result"].values)
        self._result_classes = list(self._result_clf.classes_)

        # Goals markets: Elo diff magnitude alone is a weak proxy (a big
        # mismatch tends to correlate with fewer "even" low-scoring games),
        # so these are secondary/experimental within this baseline — fit
        # the same way, on training only.
        x_abs = np.abs(x)
        self._o15 = _fit_binary_logistic(x_abs, train["over_1_5"].values)
        self._o25 = _fit_binary_logistic(x_abs, train["over_2_5"].values)
        self._o35 = _fit_binary_logistic(x_abs, train["over_3_5"].values)
        self._btts = _fit_binary_logistic(x_abs, train["btts"].values)
        return self

    def predict(self, test: pd.DataFrame) -> list[MarketProbs]:
        x = (test["home_elo_before"] + 65 - test["away_elo_before"]).values.reshape(-1, 1)
        x_abs = np.abs(x)

        result_probs = self._result_clf.predict_proba(x)
        idx = {c: i for i, c in enumerate(self._result_classes)}

        def _get(probs_row, cls):
            return float(probs_row[idx[cls]]) if cls in idx else 0.0

        p_o15 = self._proba_true(self._o15, x_abs)
        p_o25 = self._proba_true(self._o25, x_abs)
        p_o35 = self._proba_true(self._o35, x_abs)
        p_btts = self._proba_true(self._btts, x_abs)

        out = []
        for i in range(len(test)):
            out.append(MarketProbs(
                p_home=_get(result_probs[i], "H"),
                p_draw=_get(result_probs[i], "D"),
                p_away=_get(result_probs[i], "A"),
                p_over_1_5=float(p_o15[i]), p_over_2_5=float(p_o25[i]), p_over_3_5=float(p_o35[i]),
                p_btts_yes=float(p_btts[i]),
            ))
        return out

    @staticmethod
    def _proba_true(clf: LogisticRegression, x: np.ndarray) -> np.ndarray:
        classes = list(clf.classes_)
        proba = clf.predict_proba(x)
        if True in classes:
            return proba[:, classes.index(True)]
        return np.zeros(len(x))


# ══════════════════════════════════════════════════════════════════════
# Baselines 4/5 — Poisson and Dixon-Coles, fed by point-in-time rolling stats
# ══════════════════════════════════════════════════════════════════════

def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _dc_tau(hg: int, ag: int, lam_h: float, lam_a: float, rho: float) -> float:
    """Dixon-Coles low-score dependence correction factor."""
    if hg == 0 and ag == 0:
        return 1 - lam_h * lam_a * rho
    if hg == 0 and ag == 1:
        return 1 + lam_h * rho
    if hg == 1 and ag == 0:
        return 1 + lam_a * rho
    if hg == 1 and ag == 1:
        return 1 - rho
    return 1.0


@dataclass
class _LeagueProfile:
    avg_home_goals: float
    avg_away_goals: float


class _PoissonFamilyBaseline:
    """Shared machinery for the plain-Poisson and Dixon-Coles baselines.
    Both use point-in-time rolling scored/conceded as attack/defense
    inputs; only the scoreline matrix construction differs (rho=0 for
    plain Poisson).
    """

    MAX_GOALS = 8
    use_dixon_coles = False

    def fit(self, train: pd.DataFrame) -> "_PoissonFamilyBaseline":
        train = add_outcome_columns(train)
        self._league_profiles: dict[str, _LeagueProfile] = {}
        for league, g in train.groupby("league"):
            self._league_profiles[league] = _LeagueProfile(
                avg_home_goals=float(g["home_goals"].mean()),
                avg_away_goals=float(g["away_goals"].mean()),
            )
        self._default_profile = _LeagueProfile(
            avg_home_goals=float(train["home_goals"].mean()),
            avg_away_goals=float(train["away_goals"].mean()),
        )
        self.rho = 0.0
        if self.use_dixon_coles:
            self.rho = self._fit_rho(train)
        return self

    def _lambdas(self, row) -> tuple[float, float]:
        profile = self._league_profiles.get(row["league"], self._default_profile)
        home_attack = row["home_scored_avg_10"] / max(profile.avg_home_goals, 0.05)
        home_defense = row["home_conceded_avg_10"] / max(profile.avg_away_goals, 0.05)
        away_attack = row["away_scored_avg_10"] / max(profile.avg_away_goals, 0.05)
        away_defense = row["away_conceded_avg_10"] / max(profile.avg_home_goals, 0.05)

        lam_h = home_attack * away_defense * profile.avg_home_goals
        lam_a = away_attack * home_defense * profile.avg_away_goals
        lam_h = max(0.25, min(lam_h, 4.0))
        lam_a = max(0.2, min(lam_a, 3.5))
        # Mild regression to the league mean — same rationale as
        # src/ml/poisson_model.py: prevents thin-sample rolling stats from
        # producing absurdly lopsided lambdas.
        lam_h = lam_h * 0.85 + profile.avg_home_goals * 0.15
        lam_a = lam_a * 0.85 + profile.avg_away_goals * 0.15
        return lam_h, lam_a

    def _matrix(self, lam_h: float, lam_a: float) -> dict[tuple[int, int], float]:
        matrix = {}
        for h in range(self.MAX_GOALS + 1):
            for a in range(self.MAX_GOALS + 1):
                p = _poisson_pmf(h, lam_h) * _poisson_pmf(a, lam_a)
                if self.use_dixon_coles:
                    p *= _dc_tau(h, a, lam_h, lam_a, self.rho)
                matrix[(h, a)] = max(p, 0.0)
        total = sum(matrix.values())
        if total > 0:
            matrix = {k: v / total for k, v in matrix.items()}
        return matrix

    def _fit_rho(self, train: pd.DataFrame, n_sample: int = 3000) -> float:
        """Fit Dixon-Coles rho by maximizing log-likelihood of the actual
        low-score results in the training set, holding lambdas fixed at
        their point-in-time estimates (a simplified, but real, MLE fit —
        the classical Dixon-Coles paper jointly fits static attack/defense
        strengths + rho; here attack/defense come from the point-in-time
        rolling stats instead, and only rho is fit by MLE)."""
        sample = train if len(train) <= n_sample else train.sample(n=n_sample, random_state=42)
        lams = [self._lambdas(row) for _, row in sample.iterrows()]
        goals = list(zip(sample["home_goals"].values, sample["away_goals"].values))

        def neg_log_lik(rho: float) -> float:
            ll = 0.0
            for (lam_h, lam_a), (hg, ag) in zip(lams, goals):
                base = _poisson_pmf(hg, lam_h) * _poisson_pmf(ag, lam_a)
                tau = _dc_tau(hg, ag, lam_h, lam_a, rho)
                p = max(base * tau, 1e-10)
                ll += math.log(p)
            return -ll

        res = minimize_scalar(neg_log_lik, bounds=(-0.25, 0.25), method="bounded")
        return float(res.x)

    def predict(self, test: pd.DataFrame) -> list[MarketProbs]:
        out = []
        for _, row in test.iterrows():
            lam_h, lam_a = self._lambdas(row)
            matrix = self._matrix(lam_h, lam_a)

            p_home = sum(p for (h, a), p in matrix.items() if h > a)
            p_draw = sum(p for (h, a), p in matrix.items() if h == a)
            p_away = sum(p for (h, a), p in matrix.items() if h < a)
            p_o15 = sum(p for (h, a), p in matrix.items() if h + a > 1)
            p_o25 = sum(p for (h, a), p in matrix.items() if h + a > 2)
            p_o35 = sum(p for (h, a), p in matrix.items() if h + a > 3)
            p_btts = sum(p for (h, a), p in matrix.items() if h > 0 and a > 0)

            out.append(MarketProbs(p_home, p_draw, p_away, p_o15, p_o25, p_o35, p_btts))
        return out


class PoissonBaseline(_PoissonFamilyBaseline):
    name = "poisson_real_data"
    use_dixon_coles = False


class DixonColesBaseline(_PoissonFamilyBaseline):
    name = "dixon_coles"
    use_dixon_coles = True


# ══════════════════════════════════════════════════════════════════════
# Baseline 6 — market-implied odds: UNAVAILABLE
# ══════════════════════════════════════════════════════════════════════

class MarketOddsBaselineUnavailable:
    """No legitimate historical odds were obtained for this dataset (the
    openfootball match results carry no odds, and this environment's
    network policy blocks the odds-provider APIs the live system uses —
    see AUDIT_REPORT.md Phase-1 findings). Rather than fabricate odds,
    this baseline is explicitly marked unavailable and excluded from the
    comparison table.
    """

    name = "market_implied_odds"
    available = False

    def fit(self, train: pd.DataFrame):
        return self

    def predict(self, test: pd.DataFrame):
        raise NotImplementedError(
            "market_implied_odds baseline has no real data source in this "
            "environment; do not fabricate odds to fill this in."
        )


ALL_BASELINES = [FrequencyBaseline, HomeTeamBaseline, EloBaseline, PoissonBaseline, DixonColesBaseline]

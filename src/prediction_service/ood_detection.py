"""
Out-of-distribution detection (Phase 3 §10).

Compares a live match's point-in-time feature vector against the
distribution of that same feature across the real training history. A
match sitting far outside where the models have ever seen data (extreme
Elo gap, extreme rolling goal rates, a team with almost no history) is
exactly the situation Phase 3 says must reduce confidence or refuse a
prediction — models are not reliable extrapolators.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from src.ml.point_in_time import FEATURE_COLUMNS

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Below this many prior matches for a team, treat it as a genuine cold
# start / newly-promoted-team situation (matches the audit's fix in
# AUDIT_REPORT.md §1.1 — never let low real history pass as "normal" data).
MIN_RELIABLE_TEAM_HISTORY = 10

# Percentile bounds used to flag "extreme" feature values. 1st/99th is
# deliberately generous (roughly 1-in-100 events) — this should catch
# genuinely unusual situations, not routine variation.
_LOWER_PCT, _UPPER_PCT = 1, 99

_bounds_cache: Optional[dict[str, tuple[float, float]]] = None


def _training_distribution_bounds(historical_features: pd.DataFrame) -> dict[str, tuple[float, float]]:
    global _bounds_cache
    if _bounds_cache is not None:
        return _bounds_cache
    bounds = {}
    for col in FEATURE_COLUMNS:
        if col == "data_sufficiency":
            continue  # handled separately via MIN_RELIABLE_TEAM_HISTORY, not a percentile bound
        lo = float(historical_features[col].quantile(_LOWER_PCT / 100))
        hi = float(historical_features[col].quantile(_UPPER_PCT / 100))
        bounds[col] = (lo, hi)
    _bounds_cache = bounds
    return bounds


@dataclass
class OODResult:
    is_ood: bool
    severity: str    # "none" | "mild" | "severe"
    reasons: list[str] = field(default_factory=list)


def check_ood(
    feature_row: dict,
    historical_features: pd.DataFrame,
    home_is_cold_start: bool,
    away_is_cold_start: bool,
    data_sufficiency: int,
) -> OODResult:
    reasons = []
    severe = False

    if home_is_cold_start or away_is_cold_start:
        reasons.append(
            "one or both teams have ZERO prior real matches in history (true cold start) — "
            "any prediction here is an extrapolation from league-wide priors, not team-specific evidence"
        )
        severe = True
    elif data_sufficiency < MIN_RELIABLE_TEAM_HISTORY:
        reasons.append(
            f"data_sufficiency={data_sufficiency} prior matches, below the "
            f"MIN_RELIABLE_TEAM_HISTORY={MIN_RELIABLE_TEAM_HISTORY} floor"
        )

    bounds = _training_distribution_bounds(historical_features)
    for col, (lo, hi) in bounds.items():
        val = feature_row.get(col)
        if val is None:
            continue
        if val < lo or val > hi:
            reasons.append(f"{col}={val:.2f} is outside the training distribution's [{lo:.2f}, {hi:.2f}] "
                            f"({_LOWER_PCT}-{_UPPER_PCT} percentile) range")

    is_ood = len(reasons) > 0
    if severe:
        severity = "severe"
    elif is_ood:
        severity = "mild"
    else:
        severity = "none"

    return OODResult(is_ood=is_ood, severity=severity, reasons=reasons)

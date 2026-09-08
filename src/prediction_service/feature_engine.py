"""
Canonical live point-in-time feature engine (Phase 3 §2).

THE central design decision here: generate_features() does NOT reimplement
rolling-stat/Elo logic. It calls the EXACT SAME function used for
backtesting — src.ml.point_in_time.build_point_in_time_features() — by
appending the match being predicted as one more row to the historical
match table and letting the batch vectorized function compute its
features the normal way. Then it reads off just that one row.

This is the direct fix for the failure mode Phase 3 calls out explicitly:

    BACKTEST FEATURE LOGIC != PRODUCTION FEATURE LOGIC

There is no second implementation to drift out of sync — there is exactly
one feature-computation code path, used both by scripts/walk_forward_backtest.py
and by this module. scripts/historical_simulation.py (§21) proves this by
replaying real historical matches through generate_features() and
confirming the numbers match the original backtest exactly.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from src.ml.point_in_time import build_point_in_time_features, FEATURE_COLUMNS


class FeatureGenerationError(Exception):
    """Raised when features cannot be safely generated. Callers MUST treat
    this as a hard stop — never substitute a default/fabricated feature
    row. See prediction_pipeline.py's fail-closed handling."""


@dataclass
class FeatureSnapshot:
    match_id: str
    home_team: str
    away_team: str
    league: str
    prediction_timestamp: str          # the point-in-time cutoff being predicted for
    feature_snapshot_timestamp: str    # wall-clock time this snapshot was actually computed
    features: dict                     # FEATURE_COLUMNS -> value, exactly as used by the model
    home_is_cold_start: bool
    away_is_cold_start: bool
    data_sufficiency: int              # min(home, away) prior matches — same field the backtest uses for OOD
    n_history_matches_used: int
    n_future_rows_rejected: int        # matches in the source table at/after prediction_timestamp — must be 0
    raw_row: dict = field(default_factory=dict)  # full row incl. non-feature columns (elo, rolling splits) for audit


def _deterministic_match_id(home_team: str, away_team: str, prediction_timestamp: pd.Timestamp) -> str:
    key = f"live|{home_team}|{away_team}|{prediction_timestamp.isoformat()}"
    return "live_" + hashlib.sha1(key.encode()).hexdigest()[:16]


def generate_features(
    home_team: str,
    away_team: str,
    prediction_timestamp,
    league: str,
    historical_matches: pd.DataFrame,
    match_id: Optional[str] = None,
    season_label: str = "live",
) -> FeatureSnapshot:
    """The single canonical entry point for turning
    (home_team, away_team, prediction_timestamp) into the feature vector a
    champion model actually consumes.

    Args:
        historical_matches: real, validated match history (same schema as
            data/real_historical/matches.csv — match_id, date, league,
            season, home_team, away_team, home_goals, away_goals). This
            function NEVER trusts that the caller has already filtered out
            future rows — it filters defensively itself (§4: every stage
            validates).
        prediction_timestamp: the moment the prediction is being made "as
            of" — for a real live prediction this is "now"; for a
            historical simulation it's the timestamp being replayed.

    Raises:
        FeatureGenerationError if history is empty, if home_team == away_team,
        or if pandas fails to compute a well-formed row.
    """
    if home_team == away_team:
        raise FeatureGenerationError(f"home_team == away_team ({home_team!r}) — refusing to generate features")
    if not home_team or not away_team:
        raise FeatureGenerationError("home_team/away_team must be non-empty")

    pred_ts = pd.Timestamp(prediction_timestamp)
    pred_date_str = pred_ts.normalize().strftime("%Y-%m-%d")

    if historical_matches is None or len(historical_matches) == 0:
        raise FeatureGenerationError("no historical match data available — cannot generate point-in-time features")

    required_cols = {"match_id", "date", "home_team", "away_team", "home_goals", "away_goals"}
    missing = required_cols - set(historical_matches.columns)
    if missing:
        raise FeatureGenerationError(f"historical_matches is missing required columns: {missing}")

    # §4/§14: defensively reject anything at or after the prediction cutoff,
    # regardless of what the caller passed in.
    match_dates = pd.to_datetime(historical_matches["date"])
    is_prior = match_dates < pd.Timestamp(pred_date_str)
    history = historical_matches[is_prior].copy()
    n_rejected = int(len(historical_matches) - len(history))

    if len(history) == 0:
        raise FeatureGenerationError(
            f"no historical matches exist before {pred_date_str} — cannot compute point-in-time features "
            "(this is a hard stop, not a cold-start case: cold-start is handled per-team once at least SOME "
            "league history exists to compute an expanding baseline from)"
        )

    if match_id is None:
        match_id = _deterministic_match_id(home_team, away_team, pred_ts)

    pending_row = pd.DataFrame([{
        "match_id": match_id,
        "date": pred_date_str,
        "league": league,
        "season": season_label,
        "home_team": home_team,
        "away_team": away_team,
        # Placeholder outcome — NEVER read back. This row is chronologically
        # last by construction (its date >= every row in `history`), so its
        # own goals only affect state that would apply to matches AFTER it,
        # which we never compute or return. If this invariant is ever
        # violated, tests/prediction_service/test_feature_engine.py's
        # placeholder-outcome-invariance test will fail loudly.
        "home_goals": 0,
        "away_goals": 0,
    }])

    combined = pd.concat([history, pending_row], ignore_index=True)
    combined = combined.sort_values("date", kind="stable").reset_index(drop=True)

    try:
        feats = build_point_in_time_features(combined)
    except Exception as e:
        raise FeatureGenerationError(f"point-in-time feature computation failed: {e}") from e

    match_rows = feats[feats["match_id"] == match_id]
    if len(match_rows) != 1:
        raise FeatureGenerationError(
            f"expected exactly 1 feature row for match_id={match_id}, got {len(match_rows)} "
            "(likely a duplicate match_id collision with real history)"
        )
    row = match_rows.iloc[0]

    feature_values = {col: float(row[col]) for col in FEATURE_COLUMNS}

    return FeatureSnapshot(
        match_id=match_id,
        home_team=home_team,
        away_team=away_team,
        league=league,
        prediction_timestamp=pred_ts.isoformat(),
        feature_snapshot_timestamp=datetime.utcnow().isoformat() + "Z",
        features=feature_values,
        home_is_cold_start=bool(row["home_is_cold_start"]),
        away_is_cold_start=bool(row["away_is_cold_start"]),
        data_sufficiency=int(row["data_sufficiency"]),
        n_history_matches_used=int(len(history)),
        n_future_rows_rejected=n_rejected,
        raw_row={k: (v.item() if hasattr(v, "item") else v) for k, v in row.to_dict().items()},
    )

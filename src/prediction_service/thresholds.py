"""
Market-specific confidence/probability thresholds (Phase 3 §16).

Learned from folds 1-3 of the walk-forward backtest ONLY (train seasons
2015-16 through 2022-23, test seasons 2021-22/2022-23/2023-24) — the
frozen final holdout (2024-25, fold 4) is never touched here, exactly the
same discipline the walk-forward backtest itself applies to calibration.

Method: for each market, fit that market's CHAMPION model (from
champion_registry.py) on each of folds 1-3's train split, predict on that
fold's test split, and pool all three folds' (probability, actual_outcome)
pairs. Then find the lowest probability threshold T such that predictions
with probability >= T have a pooled empirical hit-rate at or above a
target floor, with an adequate sample size backing it. Markets/champions
that never clear the floor at any usable sample size get NO threshold
("cannot recommend a confidence gate — insufficient evidence"), which
downstream code must treat as "do not present this market as a bankroll
pick", not as "use probability 0.5".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from src.ml.point_in_time import build_point_in_time_features
from src.ml.baselines import ALL_BASELINES, add_outcome_columns
from src.prediction_service.champion_registry import load_registry, MARKETS as CHAMPION_MARKETS

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_MATCHES_CSV = _PROJECT_ROOT / "data" / "real_historical" / "matches.csv"
_THRESHOLDS_PATH = _PROJECT_ROOT / "models" / "market_thresholds.json"

_NON_FINAL_FOLDS = [
    (["2015-16", "2016-17", "2017-18", "2018-19", "2019-20"], "2021-22"),
    (["2015-16", "2016-17", "2017-18", "2018-19", "2019-20", "2020-21"], "2022-23"),
    (["2015-16", "2016-17", "2017-18", "2018-19", "2019-20", "2020-21", "2021-22"], "2023-24"),
]
# NOTE: fold 4 (train through 2022-23, test 2024-25) is deliberately absent
# from this list — see module docstring.

MIN_SAMPLES_PER_THRESHOLD = 40
TARGET_HIT_RATE_FLOOR = 0.55

_BASELINE_BY_NAME = {cls.name: cls for cls in ALL_BASELINES}

_MARKET_FIELD = {
    "home_win": "p_home", "draw": "p_draw", "away_win": "p_away",
    "over_1_5": "p_over_1_5", "over_2_5": "p_over_2_5", "over_3_5": "p_over_3_5",
    "btts": "p_btts_yes", "home_over_0_5": "p_home_over_0_5", "away_over_0_5": "p_away_over_0_5",
}


def _binary_outcome(df: pd.DataFrame, market: str) -> np.ndarray:
    if market == "home_win":
        return (df["result"] == "H").astype(int).values
    if market == "draw":
        return (df["result"] == "D").astype(int).values
    if market == "away_win":
        return (df["result"] == "A").astype(int).values
    return df[market].astype(int).values


def _pooled_predictions_for_market(feats: pd.DataFrame, market: str, champion_name: str) -> tuple[np.ndarray, np.ndarray]:
    if champion_name not in _BASELINE_BY_NAME:
        return np.array([]), np.array([])  # champion isn't a baseline we can refit here (e.g. xgboost) — see limitations

    field = _MARKET_FIELD[market]
    all_p, all_y = [], []
    for train_seasons, test_season in _NON_FINAL_FOLDS:
        train = feats[feats["season"].isin(train_seasons)]
        test = feats[feats["season"] == test_season]
        model = _BASELINE_BY_NAME[champion_name]().fit(train)
        preds = model.predict(test)
        p = np.array([getattr(mp, field) for mp in preds], dtype=float)
        valid = ~pd.isna(p)
        y = _binary_outcome(add_outcome_columns(test), market)
        all_p.append(p[valid])
        all_y.append(y[valid])
    return np.concatenate(all_p), np.concatenate(all_y)


def _find_threshold(p: np.ndarray, y: np.ndarray) -> Optional[dict]:
    if len(p) == 0:
        return None
    # Use the ACTUAL observed probability values as candidate thresholds —
    # rounding to a display precision (e.g. 2dp) first can round a
    # threshold UP past every real value that produced it (e.g. three raw
    # values 0.7757/0.7779/0.7780 all round to "0.78", which then excludes
    # all three under a naive `p >= 0.78` comparison). Round only when
    # reporting the final chosen threshold, never before searching.
    candidates = sorted(set(p.tolist()))
    best = None
    for t in candidates:
        mask = p >= t
        n = int(mask.sum())
        if n < MIN_SAMPLES_PER_THRESHOLD:
            continue
        hit_rate = float(y[mask].mean())
        if hit_rate >= TARGET_HIT_RATE_FLOOR:
            # Prefer the LOWEST threshold that still clears the floor with
            # enough samples (maximizes coverage without violating the floor).
            if best is None or t < best["threshold"]:
                best = {"threshold": round(float(t), 2), "pooled_hit_rate": round(hit_rate, 4), "pooled_n": n}
    return best


def learn_thresholds() -> dict:
    matches = pd.read_csv(_MATCHES_CSV).sort_values("date").reset_index(drop=True)
    feats = add_outcome_columns(build_point_in_time_features(matches))
    registry = load_registry()

    result = {"target_hit_rate_floor": TARGET_HIT_RATE_FLOOR, "min_samples": MIN_SAMPLES_PER_THRESHOLD,
              "folds_used": [f"train_through_{fs[-1]}_test_{ts}" for fs, ts in _NON_FINAL_FOLDS],
              "markets": {}}

    for market in CHAMPION_MARKETS:
        entry = registry["markets"].get(market, {})
        champion = entry.get("champion")
        if champion is None:
            result["markets"][market] = {"champion": None, "threshold": None, "reason": "no champion selected"}
            continue

        p, y = _pooled_predictions_for_market(feats, market, champion)
        threshold_info = _find_threshold(p, y)
        if threshold_info is None:
            result["markets"][market] = {
                "champion": champion, "threshold": None,
                "reason": (
                    f"champion '{champion}' is not a refittable baseline in this module"
                    if champion not in _BASELINE_BY_NAME else
                    f"no probability level pooled from folds 1-3 reached {TARGET_HIT_RATE_FLOOR:.0%} "
                    f"hit-rate with >= {MIN_SAMPLES_PER_THRESHOLD} samples"
                ),
            }
        else:
            result["markets"][market] = {"champion": champion, **threshold_info}

    return result


def save_thresholds(thresholds: Optional[dict] = None) -> Path:
    thresholds = thresholds or learn_thresholds()
    _THRESHOLDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_THRESHOLDS_PATH, "w") as f:
        json.dump(thresholds, f, indent=2)
    return _THRESHOLDS_PATH


def load_thresholds() -> dict:
    if not _THRESHOLDS_PATH.exists():
        raise FileNotFoundError(f"{_THRESHOLDS_PATH} not found — run scripts/build_market_thresholds.py first.")
    with open(_THRESHOLDS_PATH) as f:
        return json.load(f)


def get_threshold(market: str) -> Optional[float]:
    thresholds = load_thresholds()
    entry = thresholds["markets"].get(market, {})
    return entry.get("threshold")

#!/usr/bin/env python3
"""
Real-data walk-forward backtest.

Chronological TRAIN -> VALIDATE -> TEST, rolled forward season by season.
The LAST fold's test season (2024-25) is the frozen final holdout: no
feature, hyperparameter, calibration, or threshold decision in this
script is made by looking at it. It is evaluated exactly once, at the end.

Usage: python3 scripts/walk_forward_backtest.py
Writes: data/real_historical/backtest_results.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.ml.point_in_time import build_point_in_time_features, assert_no_leakage, FEATURE_COLUMNS
from src.ml.baselines import ALL_BASELINES, add_outcome_columns
from src.ml.real_data_trainer import RealDataTrainer, MARKETS

SEASON_ORDER = [
    "2015-16", "2016-17", "2017-18", "2018-19", "2019-20",
    "2020-21", "2021-22", "2022-23", "2023-24", "2024-25",
]

# (train seasons, validation season, test season) — chronological, non-overlapping,
# rolled forward one season at a time. The last row's test season is the
# frozen final holdout.
FOLDS = [
    (SEASON_ORDER[0:5], SEASON_ORDER[5], SEASON_ORDER[6]),   # train 2015-20, val 2020-21, test 2021-22
    (SEASON_ORDER[0:6], SEASON_ORDER[6], SEASON_ORDER[7]),   # train 2015-21, val 2021-22, test 2022-23
    (SEASON_ORDER[0:7], SEASON_ORDER[7], SEASON_ORDER[8]),   # train 2015-22, val 2022-23, test 2023-24
    (SEASON_ORDER[0:8], SEASON_ORDER[8], SEASON_ORDER[9]),   # train 2015-23, val 2023-24, test 2024-25 <- FINAL, FROZEN
]

EPS = 1e-7


def _binary_metrics(p: np.ndarray, y: np.ndarray) -> dict:
    p = np.clip(p, EPS, 1 - EPS)
    brier = float(np.mean((p - y) ** 2))
    logloss = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    acc = float(np.mean((p >= 0.5).astype(int) == y))
    return {"n": int(len(y)), "positive_rate": round(float(y.mean()), 4),
            "brier": round(brier, 4), "log_loss": round(logloss, 4), "accuracy": round(acc, 4)}


def _ece(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> float:
    """Expected Calibration Error: |predicted - actual| averaged over bins,
    weighted by bin size."""
    bins = np.clip((p * n_bins).astype(int), 0, n_bins - 1)
    total_gap = 0.0
    for b in range(n_bins):
        mask = bins == b
        if mask.sum() == 0:
            continue
        avg_p = p[mask].mean()
        avg_y = y[mask].mean()
        total_gap += mask.sum() * abs(avg_p - avg_y)
    return float(total_gap / len(p))


def _bootstrap_brier_diff_ci(p_a: np.ndarray, p_b: np.ndarray, y: np.ndarray, n_boot: int = 2000, seed: int = 42):
    """Bootstrap 95% CI for (Brier_a - Brier_b) on paired predictions over
    the same test matches. If the CI excludes 0, the difference is
    statistically significant at the 95% level."""
    rng = np.random.default_rng(seed)
    n = len(y)
    diffs = np.empty(n_boot)
    brier_a_full = (np.clip(p_a, EPS, 1 - EPS) - y) ** 2
    brier_b_full = (np.clip(p_b, EPS, 1 - EPS) - y) ** 2
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        diffs[i] = brier_a_full[idx].mean() - brier_b_full[idx].mean()
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    point = float(brier_a_full.mean() - brier_b_full.mean())
    return {"point_estimate": round(point, 5), "ci_95": [round(float(lo), 5), round(float(hi), 5)],
            "significant_at_95": bool(lo > 0 or hi < 0)}


def market_probs_to_arrays(preds, market: str) -> np.ndarray:
    if market == "home_win":
        return np.array([p.p_home for p in preds])
    if market == "draw":
        return np.array([p.p_draw for p in preds])
    if market == "away_win":
        return np.array([p.p_away for p in preds])
    if market == "over_1_5":
        return np.array([p.p_over_1_5 for p in preds])
    if market == "over_2_5":
        return np.array([p.p_over_2_5 for p in preds])
    if market == "over_3_5":
        return np.array([p.p_over_3_5 for p in preds])
    if market == "btts":
        return np.array([p.p_btts_yes for p in preds])
    if market == "home_over_0_5":
        vals = [p.p_home_over_0_5 for p in preds]
        return None if any(v is None for v in vals) else np.array(vals)
    if market == "away_over_0_5":
        vals = [p.p_away_over_0_5 for p in preds]
        return None if any(v is None for v in vals) else np.array(vals)
    raise ValueError(market)


LIGHT_MARKETS = ["home_over_0_5", "away_over_0_5"]


def run_fold(feats: pd.DataFrame, train_seasons, val_season, test_season, is_final: bool) -> dict:
    train = feats[feats["season"].isin(train_seasons)].copy()
    val = feats[feats["season"] == val_season].copy()
    test = feats[feats["season"] == test_season].copy()

    fold_result = {
        "train_seasons": train_seasons, "val_season": val_season, "test_season": test_season,
        "is_final_frozen_holdout": is_final,
        "n_train": len(train), "n_val": len(val), "n_test": len(test),
        "models": {},
    }

    # ── Baselines: fit on TRAIN only, evaluate on TEST only ──
    # MARKETS gets the full XGBoost-comparable treatment; LIGHT_MARKETS
    # (home/away team over 0.5 goals) are only modeled by the
    # frequency/Poisson/Dixon-Coles baselines (see MarketProbs docstring
    # in src/ml/baselines.py) — a None prediction means "not covered by
    # this baseline", so it's skipped for that market rather than scored
    # as if it were a real (and wrong) 0% prediction.
    for cls in ALL_BASELINES:
        model = cls().fit(train)
        preds = model.predict(test)
        market_metrics = {}
        for market in MARKETS + LIGHT_MARKETS:
            p = market_probs_to_arrays(preds, market)
            if p is None:
                continue
            y = _binary_target(test, market)
            m = _binary_metrics(p, y)
            m["ece"] = round(_ece(p, y), 4)
            market_metrics[market] = m
        fold_result["models"][model.name] = market_metrics

    # ── Real-data XGBoost: fit on TRAIN, calibrate on VAL, evaluate on TEST ──
    trainer = RealDataTrainer()
    trainer.fit(train)
    trainer.calibrate(val)
    raw_metrics = trainer.evaluate(test, calibrated=False)
    cal_metrics = trainer.evaluate(test, calibrated=True)
    for market in cal_metrics:
        p = trainer.predict_proba(test, calibrated=True)[f"p_{market}"].values
        y = _binary_target(add_outcome_columns(test), market)
        cal_metrics[market]["ece"] = round(_ece(p, y), 4)
    fold_result["models"]["xgboost_real_data_raw"] = raw_metrics
    fold_result["models"]["xgboost_real_data_calibrated"] = cal_metrics

    if is_final:
        fold_result["xgb_trainer"] = trainer  # kept in-process only, not JSON-serialized
        fold_result["_test_df"] = test

    return fold_result


def _binary_target(df: pd.DataFrame, market: str) -> np.ndarray:
    df = add_outcome_columns(df)
    if market == "home_win":
        return (df["result"] == "H").astype(int).values
    if market == "draw":
        return (df["result"] == "D").astype(int).values
    if market == "away_win":
        return (df["result"] == "A").astype(int).values
    return df[market].astype(int).values


def main():
    matches = pd.read_csv(PROJECT_ROOT / "data" / "real_historical" / "matches.csv")
    matches = matches.sort_values("date").reset_index(drop=True)
    feats = build_point_in_time_features(matches)
    assert_no_leakage(feats, matches)
    print(f"Loaded {len(feats)} real matches, leakage self-check passed.\n")

    all_folds = []
    final_fold = None
    for i, (train_seasons, val_season, test_season) in enumerate(FOLDS):
        is_final = (i == len(FOLDS) - 1)
        print(f"Fold {i+1}/{len(FOLDS)}: train={train_seasons[0]}..{train_seasons[-1]} "
              f"val={val_season} test={test_season}{' [FROZEN FINAL HOLDOUT]' if is_final else ''}")
        fold = run_fold(feats, train_seasons, val_season, test_season, is_final)
        all_folds.append(fold)
        if is_final:
            final_fold = fold

    # ── Statistical significance on the FINAL frozen holdout only ──
    test_df = final_fold.pop("_test_df")
    trainer: RealDataTrainer = final_fold.pop("xgb_trainer")
    significance = {}
    for market in MARKETS:
        y = _binary_target(test_df, market)
        p_xgb = trainer.predict_proba(test_df, calibrated=True)[f"p_{market}"].values

        # Compare vs Poisson (real-data) and vs Elo, the two strongest baselines.
        for baseline_cls in [b for b in ALL_BASELINES if b.name in ("poisson_real_data", "elo")]:
            train_final = feats[feats["season"].isin(FOLDS[-1][0])]
            baseline_model = baseline_cls().fit(train_final)
            baseline_preds = baseline_model.predict(test_df)
            p_base = market_probs_to_arrays(baseline_preds, market)
            key = f"{market}__xgb_vs_{baseline_cls.name}"
            significance[key] = _bootstrap_brier_diff_ci(p_xgb, p_base, y)

    # ── Out-of-distribution slice on the final holdout ──
    test_feat_df = build_point_in_time_features(matches)
    test_feat_df = test_feat_df[test_feat_df["season"] == FOLDS[-1][2]]
    thin = test_feat_df["data_sufficiency"] < 10
    ood_report = {
        "thin_history_matches": int(thin.sum()),
        "sufficient_history_matches": int((~thin).sum()),
    }
    if thin.sum() > 0:
        y_thin = _binary_target(test_feat_df[thin], "home_win")
        p_thin = trainer.predict_proba(test_feat_df[thin], calibrated=True)["p_home_win"].values
        ood_report["thin_history_home_win_brier"] = _binary_metrics(p_thin, y_thin)["brier"]
    if (~thin).sum() > 0:
        y_suff = _binary_target(test_feat_df[~thin], "home_win")
        p_suff = trainer.predict_proba(test_feat_df[~thin], calibrated=True)["p_home_win"].values
        ood_report["sufficient_history_home_win_brier"] = _binary_metrics(p_suff, y_suff)["brier"]

    output = {
        "data_summary": {
            "n_real_matches": int(len(matches)),
            "leagues": sorted(matches["league"].unique().tolist()),
            "seasons": sorted(matches["season"].unique().tolist()),
            "date_range": [str(matches["date"].min()), str(matches["date"].max())],
        },
        "folds": all_folds,
        "final_holdout_significance_vs_baselines": significance,
        "final_holdout_ood_report": ood_report,
    }

    out_path = PROJECT_ROOT / "data" / "real_historical" / "backtest_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nWrote {out_path}")

    # Also persist the final-fold real-data model + provenance metadata
    trainer.save(PROJECT_ROOT / "models" / "real_data",
                 extra_metadata={"trained_through_season": FOLDS[-1][0][-1],
                                  "validated_on_season": FOLDS[-1][1],
                                  "tested_on_season_frozen_holdout": FOLDS[-1][2]})
    print(f"Saved real-data model + provenance metadata to models/real_data/")

    return output


if __name__ == "__main__":
    main()

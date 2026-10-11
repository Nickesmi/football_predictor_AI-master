#!/usr/bin/env python3
"""
Export per-match, per-market predictions for the FROZEN 2024-25 holdout only.

This does not run a new experiment: it re-fits the same train/val split as
the final fold of scripts/walk_forward_backtest.py (train 2015-16..2022-23,
calibrate on 2023-24) and evaluates once on the 2024-25 test season, exactly
as walk_forward_backtest.py already does. The only difference is this script
writes one row per (match, market) instead of only aggregate metrics, so the
underlying picks can be audited independently instead of trusting a summary.

Usage: python3 scripts/export_holdout_picks.py
Writes: data/real_historical/holdout_picks_2024_25.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.ml.point_in_time import build_point_in_time_features, assert_no_leakage
from src.ml.baselines import EloBaseline, add_outcome_columns
from src.ml.real_data_trainer import RealDataTrainer, MARKETS
from scripts.walk_forward_backtest import FOLDS, market_probs_to_arrays, _binary_target

MARKET_PROB_FIELD = {
    "home_win": "p_home_win", "draw": "p_draw", "away_win": "p_away_win",
    "over_1_5": "p_over_1_5", "over_2_5": "p_over_2_5", "over_3_5": "p_over_3_5",
    "btts": "p_btts",
}


def main():
    matches = pd.read_csv(PROJECT_ROOT / "data" / "real_historical" / "matches.csv")
    matches = matches.sort_values("date").reset_index(drop=True)
    feats = build_point_in_time_features(matches)
    assert_no_leakage(feats, matches)

    train_seasons, val_season, test_season = FOLDS[-1]
    train = feats[feats["season"].isin(train_seasons)].copy()
    val = feats[feats["season"] == val_season].copy()
    test = feats[feats["season"] == test_season].copy()
    print(f"Frozen holdout fold: train={train_seasons[0]}..{train_seasons[-1]} "
          f"val={val_season} test={test_season} (n_test={len(test)})")

    elo = EloBaseline().fit(train)
    elo_preds = elo.predict(test)

    trainer = RealDataTrainer()
    trainer.fit(train)
    trainer.calibrate(val)
    xgb_proba = trainer.predict_proba(test, calibrated=True)

    test_with_outcomes = add_outcome_columns(test)
    match_meta = matches.set_index("match_id")[
        ["date", "league", "home_team", "away_team", "home_goals", "away_goals"]
    ]

    rows = []
    for market in MARKETS:
        y = _binary_target(test_with_outcomes, market)
        p_elo = market_probs_to_arrays(elo_preds, market)
        p_xgb = xgb_proba[MARKET_PROB_FIELD[market]].values
        match_ids = test["match_id"].values
        for i, mid in enumerate(match_ids):
            meta = match_meta.loc[mid]
            rows.append({
                "match_id": mid,
                "date": meta["date"],
                "league": meta["league"],
                "home_team": meta["home_team"],
                "away_team": meta["away_team"],
                "final_score": f"{int(meta['home_goals'])}-{int(meta['away_goals'])}",
                "market": market,
                "actual_outcome": int(y[i]),
                "elo_prob": round(float(p_elo[i]), 4) if p_elo is not None else None,
                "xgb_calibrated_prob": round(float(p_xgb[i]), 4),
                "elo_brier_contribution": round((float(p_elo[i]) - y[i]) ** 2, 4) if p_elo is not None else None,
                "xgb_brier_contribution": round((float(p_xgb[i]) - y[i]) ** 2, 4),
            })

    out = pd.DataFrame(rows).sort_values(["date", "match_id", "market"]).reset_index(drop=True)
    out_path = PROJECT_ROOT / "data" / "real_historical" / "holdout_picks_2024_25.csv"
    out.to_csv(out_path, index=False)
    print(f"Wrote {len(out)} rows ({out['match_id'].nunique()} matches x {len(MARKETS)} markets) to {out_path}")

    print("\nSanity check: aggregate Brier per market recomputed from this CSV "
          "(should match data/real_historical/backtest_results.json final fold):")
    for market in MARKETS:
        sub = out[out["market"] == market]
        print(f"  {market}: elo_brier={sub['elo_brier_contribution'].mean():.4f} "
              f"xgb_brier={sub['xgb_brier_contribution'].mean():.4f}  n={len(sub)}")


if __name__ == "__main__":
    main()

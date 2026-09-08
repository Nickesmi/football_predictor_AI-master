#!/usr/bin/env python3
"""
Historical simulation through the SAME production path (Phase 3 §21).

This is the parity proof the whole architecture rests on. It does NOT
just assert this in the abstract — it actually:

  1. Rebuilds the reference feature table the walk-forward backtest used
     (build_point_in_time_features() over the full real dataset).
  2. Fits each tested market's champion model on EXACTLY the frozen
     fold's train seasons (2015-16..2022-23) — identical to
     scripts/walk_forward_backtest.py's fold 4.
  3. For a sample of real 2024-25 matches, calls the PRODUCTION feature
     engine (src.prediction_service.feature_engine.generate_features) as
     if predicting that match live, using its real kickoff date as the
     prediction timestamp.
  4. Compares every FEATURE_COLUMNS value the production path computed
     against the reference backtest table's value for that same match,
     and compares the champion model's prediction computed from each path.

If backtest feature logic and production feature logic had drifted apart
(the exact failure mode Phase 3 §2 calls out), this would show up here as
non-matching numbers, not just as a passing unit test on a hand-built toy
fixture (tests/prediction_service/test_feature_engine.py already covers
that; this is the same claim proven on real, large-scale data instead).
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from src.ml.point_in_time import build_point_in_time_features, FEATURE_COLUMNS
from src.ml.baselines import ALL_BASELINES, add_outcome_columns
from src.prediction_service.feature_engine import generate_features
from src.prediction_service.champion_registry import load_registry

_BASELINE_BY_NAME = {cls.name: cls for cls in ALL_BASELINES}

FOLD4_TRAIN_SEASONS = ["2015-16", "2016-17", "2017-18", "2018-19", "2019-20", "2020-21", "2021-22", "2022-23"]
FOLD4_TEST_SEASON = "2024-25"

_MARKET_FIELD = {
    "home_win": "p_home", "draw": "p_draw", "away_win": "p_away",
    "over_1_5": "p_over_1_5", "over_2_5": "p_over_2_5", "over_3_5": "p_over_3_5",
    "btts": "p_btts_yes",
}

SAMPLE_SIZE = 150
FEATURE_TOLERANCE = 1e-6
PROBABILITY_TOLERANCE = 1e-9


def main():
    matches = pd.read_csv(PROJECT_ROOT / "data" / "real_historical" / "matches.csv").sort_values("date").reset_index(drop=True)
    reference_feats = add_outcome_columns(build_point_in_time_features(matches))

    train_ref = reference_feats[reference_feats["season"].isin(FOLD4_TRAIN_SEASONS)]
    test_ref = reference_feats[reference_feats["season"] == FOLD4_TEST_SEASON]

    registry = load_registry()
    markets_to_check = []
    for market in ["home_win", "draw", "away_win", "over_1_5", "over_2_5", "over_3_5", "btts"]:
        champion = registry["markets"].get(market, {}).get("champion")
        if champion in _BASELINE_BY_NAME:
            markets_to_check.append((market, champion))
        else:
            print(f"SKIP {market}: champion '{champion}' is not a refittable baseline in this simulation "
                  f"(XGBoost artifacts are loaded, not refit, by design — see live_models.py)")

    rng = np.random.default_rng(42)
    sample_idx = rng.choice(test_ref.index, size=min(SAMPLE_SIZE, len(test_ref)), replace=False)
    sample = test_ref.loc[sample_idx]

    print(f"\nReplaying {len(sample)} real 2024-25 matches through the production feature engine "
          f"and comparing against the reference backtest computation...\n")

    feature_mismatches = []
    prediction_mismatches = []
    n_feature_checks = 0
    n_prediction_checks = 0

    fitted_models = {}
    for market, champion in markets_to_check:
        fitted_models[(market, champion)] = _BASELINE_BY_NAME[champion]().fit(train_ref)

    for _, ref_row in sample.iterrows():
        match_id = ref_row["match_id"]
        live_snapshot = generate_features(
            ref_row["home_team"], ref_row["away_team"], ref_row["date"], ref_row["league"],
            matches, match_id=match_id,
        )

        for col in FEATURE_COLUMNS:
            n_feature_checks += 1
            ref_val = float(ref_row[col])
            live_val = float(live_snapshot.features[col])
            if abs(ref_val - live_val) > FEATURE_TOLERANCE:
                feature_mismatches.append({
                    "match_id": match_id, "home": ref_row["home_team"], "away": ref_row["away_team"],
                    "date": ref_row["date"], "feature": col, "reference": ref_val, "production": live_val,
                })

        live_row_df = pd.DataFrame([live_snapshot.raw_row])
        ref_row_df = pd.DataFrame([ref_row])
        for market, champion in markets_to_check:
            model = fitted_models[(market, champion)]
            n_prediction_checks += 1
            live_pred = getattr(model.predict(live_row_df)[0], _MARKET_FIELD[market])
            ref_pred = getattr(model.predict(ref_row_df)[0], _MARKET_FIELD[market])
            if abs(live_pred - ref_pred) > PROBABILITY_TOLERANCE:
                prediction_mismatches.append({
                    "match_id": match_id, "market": market, "champion": champion,
                    "reference_probability": ref_pred, "production_probability": live_pred,
                })

    print(f"Feature comparisons: {n_feature_checks - len(feature_mismatches)}/{n_feature_checks} exact matches "
          f"(tolerance {FEATURE_TOLERANCE})")
    print(f"Prediction comparisons: {n_prediction_checks - len(prediction_mismatches)}/{n_prediction_checks} exact matches "
          f"(tolerance {PROBABILITY_TOLERANCE})")

    if feature_mismatches:
        print(f"\n{len(feature_mismatches)} FEATURE MISMATCHES (showing up to 10):")
        for m in feature_mismatches[:10]:
            print(f"  {m['home']} vs {m['away']} ({m['date']}) [{m['feature']}]: "
                  f"reference={m['reference']:.6f} production={m['production']:.6f}")
    if prediction_mismatches:
        print(f"\n{len(prediction_mismatches)} PREDICTION MISMATCHES (showing up to 10):")
        for m in prediction_mismatches[:10]:
            print(f"  {m['match_id']} [{m['market']}/{m['champion']}]: "
                  f"reference={m['reference_probability']:.6f} production={m['production_probability']:.6f}")

    if not feature_mismatches and not prediction_mismatches:
        print("\nPARITY CONFIRMED: production feature/model path exactly reproduces the backtest's "
              "numbers on real, sampled 2024-25 matches. No separate/drifted code path exists.")
    else:
        print("\nPARITY BROKEN — see mismatches above. Do not claim backtest/production parity until "
              "these are understood and fixed.")

    return 0 if (not feature_mismatches and not prediction_mismatches) else 1


if __name__ == "__main__":
    sys.exit(main())

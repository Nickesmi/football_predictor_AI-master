"""
Real-data XGBoost trainer — trains on point-in-time features reconstructed
from real historical matches (src/ml/point_in_time.py), NOT the synthetic
generator in src/ml/dataset_builder.py.

Strict separation of data:
    TRAIN      -> fits the XGBoost classifiers only
    VALIDATION -> fits calibration (isotonic) only; never touches the raw
                  classifier's .fit()
    TEST       -> touched exactly once, read-only, for final metrics

Every model this trainer produces is tagged with provenance="REAL_DATA_TRAINED"
in its saved metadata (see save()), which src/engine/probability_engine.py's
production gate checks before ever blending a model into a live prediction —
see PHASE2_PRODUCTION_GATING.md / the provenance check added to
probability_engine.py.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from xgboost import XGBClassifier

from src.ml.point_in_time import FEATURE_COLUMNS
from src.ml.baselines import add_outcome_columns

MARKETS = ["home_win", "draw", "away_win", "over_1_5", "over_2_5", "over_3_5", "btts"]

MARKET_TARGET_COL = {
    "home_win": "result", "draw": "result", "away_win": "result",  # derived below
    "over_1_5": "over_1_5", "over_2_5": "over_2_5", "over_3_5": "over_3_5",
    "btts": "btts",
}


def _binary_target(df: pd.DataFrame, market: str) -> np.ndarray:
    if market == "home_win":
        return (df["result"] == "H").astype(int).values
    if market == "draw":
        return (df["result"] == "D").astype(int).values
    if market == "away_win":
        return (df["result"] == "A").astype(int).values
    return df[market].astype(int).values


class RealDataTrainer:
    PROVENANCE = "REAL_DATA_TRAINED"

    def __init__(self):
        self.models: dict[str, XGBClassifier] = {}
        self.calibrators: dict[str, IsotonicRegression] = {}
        self.metrics: dict[str, dict] = {}
        self.training_info: dict = {}

    def fit(self, train: pd.DataFrame) -> "RealDataTrainer":
        train = add_outcome_columns(train)
        X = train[FEATURE_COLUMNS].values

        for market in MARKETS:
            y = _binary_target(train, market)
            if y.sum() < 20 or (len(y) - y.sum()) < 20:
                # Not enough of one class to fit meaningfully — skip rather
                # than train on noise (this shouldn't happen with thousands
                # of real matches, but the guard costs nothing).
                continue

            model = XGBClassifier(
                n_estimators=200, max_depth=4, learning_rate=0.06,
                subsample=0.8, colsample_bytree=0.8, min_child_weight=5,
                reg_alpha=0.2, reg_lambda=1.5,
                eval_metric="logloss", random_state=42, verbosity=0,
            )
            model.fit(X, y)
            self.models[market] = model

        self.training_info = {
            "n_train_matches": int(len(train)),
            "train_seasons": sorted(train["season"].unique().tolist()),
            "train_leagues": sorted(train["league"].unique().tolist()),
            "feature_columns": FEATURE_COLUMNS,
        }
        return self

    def calibrate(self, validation: pd.DataFrame) -> "RealDataTrainer":
        """Fit isotonic calibration on the VALIDATION split only — the raw
        classifiers above never see this data during .fit()."""
        validation = add_outcome_columns(validation)
        Xv = validation[FEATURE_COLUMNS].values

        for market, model in self.models.items():
            y = _binary_target(validation, market)
            raw_p = model.predict_proba(Xv)[:, 1]
            iso = IsotonicRegression(out_of_bounds="clip")
            iso.fit(raw_p, y)
            self.calibrators[market] = iso
        return self

    def predict_proba(self, df: pd.DataFrame, calibrated: bool = True) -> pd.DataFrame:
        X = df[FEATURE_COLUMNS].values
        out = {}
        for market, model in self.models.items():
            raw_p = model.predict_proba(X)[:, 1]
            if calibrated and market in self.calibrators:
                out[f"p_{market}"] = self.calibrators[market].predict(raw_p)
            else:
                out[f"p_{market}"] = raw_p
        return pd.DataFrame(out, index=df.index)

    def evaluate(self, test: pd.DataFrame, calibrated: bool = True) -> dict:
        """Compute Brier / log loss / accuracy per market on TEST (touch once)."""
        test = add_outcome_columns(test)
        preds = self.predict_proba(test, calibrated=calibrated)
        eps = 1e-7
        results = {}
        for market in self.models:
            y = _binary_target(test, market)
            p = np.clip(preds[f"p_{market}"].values, eps, 1 - eps)
            brier = float(np.mean((p - y) ** 2))
            logloss = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
            acc = float(np.mean((p >= 0.5).astype(int) == y))
            results[market] = {
                "n": int(len(y)), "positive_rate": float(y.mean()),
                "brier": round(brier, 4), "log_loss": round(logloss, 4), "accuracy": round(acc, 4),
            }
        self.metrics = results
        return results

    def save(self, model_dir: Path, extra_metadata: Optional[dict] = None) -> None:
        model_dir.mkdir(parents=True, exist_ok=True)
        for market, model in self.models.items():
            with open(model_dir / f"real_xgb_{market}.pkl", "wb") as f:
                pickle.dump(model, f)
        for market, cal in self.calibrators.items():
            with open(model_dir / f"real_xgb_{market}_calibrator.pkl", "wb") as f:
                pickle.dump(cal, f)

        metadata = {
            "provenance": self.PROVENANCE,
            "markets": list(self.models.keys()),
            "training_info": self.training_info,
            "test_metrics": self.metrics,
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        with open(model_dir / "real_data_model_metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)

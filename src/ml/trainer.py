"""
XGBoost Model Trainer with Platt Calibration.

Trains separate binary classifiers for each target market:
  btts, over_1_5, over_2_5, over_3_5, home_win, draw, ht_over_0_5

Key design:
  - XGBoost for non-linear interaction learning
  - CalibratedClassifierCV for real-world probability calibration
  - 5-fold stratified cross-validation
  - Feature importance extraction for explainability
"""

from __future__ import annotations

import json
import os
import pickle
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import StratifiedKFold, cross_val_score
from xgboost import XGBClassifier

from src.config import logger
from src.ml.feature_builder import FEATURE_COLUMNS


TARGET_MARKETS = ["btts", "over_1_5", "over_2_5", "over_3_5", "home_win", "draw", "ht_over_0_5"]

DEFAULT_MODEL_DIR = Path(__file__).parent.parent.parent / "models"


class XGBoostTrainer:
    """Train and calibrate XGBoost models for football prediction."""

    def __init__(self, model_dir: Optional[Path] = None):
        self.model_dir = model_dir or DEFAULT_MODEL_DIR
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.models: dict[str, CalibratedClassifierCV] = {}
        self.metrics: dict[str, dict] = {}

    def train(self, df: pd.DataFrame) -> dict:
        """
        Train XGBoost classifiers for all target markets.

        Args:
            df: DataFrame with FEATURE_COLUMNS + TARGET_MARKETS columns.

        Returns:
            Dictionary of training metrics per target.
        """
        X = df[FEATURE_COLUMNS].values
        results = {}

        for target in TARGET_MARKETS:
            if target not in df.columns:
                logger.warning("Target '%s' not found in dataset — skipping.", target)
                continue

            y = df[target].values
            logger.info("Training XGBoost for '%s' (positive rate: %.1f%%)", target, y.mean() * 100)

            # Base XGBoost classifier
            base_model = XGBClassifier(
                n_estimators=200,
                max_depth=5,
                learning_rate=0.08,
                subsample=0.8,
                colsample_bytree=0.8,
                min_child_weight=3,
                reg_alpha=0.1,
                reg_lambda=1.0,
                use_label_encoder=False,
                eval_metric="logloss",
                random_state=42,
                verbosity=0,
            )

            # Platt calibration via CalibratedClassifierCV
            calibrated = CalibratedClassifierCV(
                base_model,
                cv=5,
                method="sigmoid",  # Platt scaling
            )
            calibrated.fit(X, y)

            # Cross-validated metrics
            cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
            auc_scores = cross_val_score(base_model, X, y, cv=cv, scoring="roc_auc")
            acc_scores = cross_val_score(base_model, X, y, cv=cv, scoring="accuracy")

            metrics = {
                "roc_auc_mean": round(float(auc_scores.mean()), 4),
                "roc_auc_std": round(float(auc_scores.std()), 4),
                "accuracy_mean": round(float(acc_scores.mean()), 4),
                "positive_rate": round(float(y.mean()), 4),
                "n_samples": int(len(y)),
            }

            # Feature importance (from base estimators)
            importances = {}
            for est in calibrated.calibrated_classifiers_:
                base = est.estimator
                if hasattr(base, 'feature_importances_'):
                    for i, col in enumerate(FEATURE_COLUMNS):
                        importances[col] = importances.get(col, 0) + float(base.feature_importances_[i])
            n_est = len(calibrated.calibrated_classifiers_)
            importances = {k: round(float(v) / n_est, 4) for k, v in importances.items()}
            top_features = sorted(importances.items(), key=lambda x: x[1], reverse=True)[:7]
            metrics["top_features"] = [{"name": k, "importance": v} for k, v in top_features]

            self.models[target] = calibrated
            self.metrics[target] = metrics
            results[target] = metrics

            logger.info(
                "  %s: AUC=%.3f±%.3f | Acc=%.3f | Top feature: %s",
                target, metrics["roc_auc_mean"], metrics["roc_auc_std"],
                metrics["accuracy_mean"], top_features[0][0] if top_features else "N/A",
            )

        self._save_models()
        return results

    def _save_models(self):
        """Persist trained models to disk."""
        for target, model in self.models.items():
            path = self.model_dir / f"xgb_{target}.pkl"
            with open(path, "wb") as f:
                pickle.dump(model, f)

        # Save metrics
        metrics_path = self.model_dir / "training_metrics.json"
        with open(metrics_path, "w") as f:
            json.dump(self.metrics, f, indent=2)

        logger.info("Models saved to %s", self.model_dir)

    def load_models(self) -> bool:
        """Load previously trained models from disk."""
        loaded = 0
        for target in TARGET_MARKETS:
            path = self.model_dir / f"xgb_{target}.pkl"
            if path.exists():
                with open(path, "rb") as f:
                    self.models[target] = pickle.load(f)
                loaded += 1

        metrics_path = self.model_dir / "training_metrics.json"
        if metrics_path.exists():
            with open(metrics_path, "r") as f:
                self.metrics = json.load(f)

        logger.info("Loaded %d/%d XGBoost models from disk.", loaded, len(TARGET_MARKETS))
        return loaded > 0

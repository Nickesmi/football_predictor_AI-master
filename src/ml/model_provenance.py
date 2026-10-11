"""
Model provenance gate — Phase 2 production safety requirement:

"The production system must clearly identify whether a model is
REAL-DATA TRAINED or SYNTHETIC/EXPERIMENTAL. Synthetic models must NEVER
be presented as validated production predictors."

This module is the single source of truth other code must consult before
letting any ML model influence a live prediction with nonzero weight.

Two models exist in this repo:
  - src/ml/trainer.py's XGBoost models (models/xgb_*.pkl) — trained
    exclusively on src/ml/dataset_builder.py's SYNTHETIC data. Always
    SYNTHETIC_EXPERIMENTAL, always production_validated=False, no
    exceptions, regardless of what its self-referential AUC says.
  - src/ml/real_data_trainer.py's XGBoost models (models/real_data/) —
    trained on real historical matches with point-in-time features and a
    frozen final-season holdout (see scripts/walk_forward_backtest.py).
    provenance=REAL_DATA_TRAINED, but production_validated is decided
    PER MARKET from data/real_historical/backtest_results.json's
    statistical-significance section — being real-data-trained is
    necessary but not sufficient; it must have beaten the strongest
    baselines (Elo, Poisson) with a 95% CI that excludes zero, AND not be
    significantly worse than either, before it's allowed to influence a
    live bet. See REAL_DATA_BACKTEST_REPORT.md for the actual result: as
    of the last run, most markets did NOT clear this bar.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_REAL_MODEL_METADATA = _PROJECT_ROOT / "models" / "real_data" / "real_data_model_metadata.json"
_BACKTEST_RESULTS = _PROJECT_ROOT / "data" / "real_historical" / "backtest_results.json"


def synthetic_model_provenance() -> dict:
    """The bundled src/ml/trainer.py models. Always experimental — see
    AUDIT_REPORT.md §3 for why their AUC cannot be trusted."""
    return {
        "provenance": "SYNTHETIC_EXPERIMENTAL",
        "production_validated": False,
        "reason": (
            "Trained exclusively on synthetic data (src/ml/dataset_builder.py). "
            "No real match was ever used to fit or evaluate this model. "
            "Must never be presented as a validated production predictor."
        ),
    }


def _load_real_data_significance() -> dict:
    if not _BACKTEST_RESULTS.exists():
        return {}
    with open(_BACKTEST_RESULTS) as f:
        data = json.load(f)
    return data.get("final_holdout_significance_vs_baselines", {})


def real_data_model_provenance(market: str) -> dict:
    """provenance + production_validated for the real-data model on one market.

    production_validated criterion (deliberately strict): on the frozen
    final-season holdout, the model's Brier score must be
      (a) NOT significantly worse than Elo, AND
      (b) NOT significantly worse than Poisson (real-data), AND
      (c) significantly better than at least one of them
    all at the 95% bootstrap CI level. Being "real-data trained" alone
    does not satisfy this — see module docstring.
    """
    if not _REAL_MODEL_METADATA.exists():
        return {
            "provenance": "REAL_DATA_TRAINED",
            "production_validated": False,
            "reason": "No trained real-data model found on disk yet — run scripts/walk_forward_backtest.py.",
        }

    with open(_REAL_MODEL_METADATA) as f:
        meta = json.load(f)

    significance = _load_real_data_significance()
    key_elo = f"{market}__xgb_vs_elo"
    key_poisson = f"{market}__xgb_vs_poisson_real_data"
    sig_elo = significance.get(key_elo)
    sig_poisson = significance.get(key_poisson)

    if sig_elo is None or sig_poisson is None:
        return {
            "provenance": "REAL_DATA_TRAINED",
            "production_validated": False,
            "reason": f"No significance comparison recorded for market '{market}'.",
            "model_metadata": meta,
        }

    def _worse_than(sig: dict) -> bool:
        # point_estimate = brier_xgb - brier_baseline; positive & significant = xgb worse
        return sig["significant_at_95"] and sig["point_estimate"] > 0

    def _better_than(sig: dict) -> bool:
        return sig["significant_at_95"] and sig["point_estimate"] < 0

    worse_than_either = _worse_than(sig_elo) or _worse_than(sig_poisson)
    better_than_either = _better_than(sig_elo) or _better_than(sig_poisson)

    validated = (not worse_than_either) and better_than_either

    return {
        "provenance": "REAL_DATA_TRAINED",
        "production_validated": validated,
        "significance_vs_elo": sig_elo,
        "significance_vs_poisson": sig_poisson,
        "reason": (
            "Passed: not significantly worse than Elo or Poisson, and significantly "
            "better than at least one, on the frozen final-season holdout."
            if validated else
            "Failed strict production bar (see significance_vs_* fields) — "
            "stays EXPERIMENTAL, must not receive nonzero production weight."
        ),
        "trained_through_season": meta.get("training_info", {}).get("train_seasons", [None])[-1],
        "tested_on_frozen_season": meta.get("tested_on_season_frozen_holdout"),
    }

"""
Live model registry — fits/loads the models the production pipeline is
actually allowed to call (Phase 3 §1/§7).

Important methodological distinction from the walk-forward backtest:
scripts/walk_forward_backtest.py deliberately fits each fold's models on
only a historical window ending before that fold's validation/test season,
to measure genuine out-of-sample skill. Here, for a REAL live prediction
about a genuinely future match, there is no future data to leak from — so
every baseline is fit on the FULL real historical dataset (all real
matches on disk), which is the correct, standard production posture: use
everything you legitimately know as of right now.

The bundled XGBoost artifacts (models/real_data/*.pkl) are loaded as-is
(already fit through 2022-23, calibrated on 2023-24) rather than refit
here, because they are the exact artifacts the Phase 2 backtest validated
— refitting them on more recent data here would silently invalidate that
validation. If/when a future session retrains on more real data, it must
re-run the full walk-forward backtest before this module trusts the new
artifacts (see src/ml/model_provenance.py).
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Optional

import pandas as pd

from src.ml.baselines import (
    FrequencyBaseline, HomeTeamBaseline, EloBaseline, PoissonBaseline, DixonColesBaseline, MarketProbs,
    add_outcome_columns,
)
from src.ml.point_in_time import FEATURE_COLUMNS, build_point_in_time_features
from src.ml.real_data_trainer import MARKETS as XGB_MARKETS

_full_history_features_cache: Optional[pd.DataFrame] = None


def get_full_history_features(historical_matches: pd.DataFrame) -> pd.DataFrame:
    """Point-in-time features + outcome columns for the ENTIRE real
    historical dataset, cached — this is what every baseline is fit on for
    live production (see module docstring for why this differs from the
    backtest's per-fold training windows)."""
    global _full_history_features_cache
    if _full_history_features_cache is None:
        feats = build_point_in_time_features(historical_matches.sort_values("date").reset_index(drop=True))
        _full_history_features_cache = add_outcome_columns(feats)
    return _full_history_features_cache

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_REAL_DATA_MODEL_DIR = _PROJECT_ROOT / "models" / "real_data"

_BASELINE_CLASSES = {
    "frequency": FrequencyBaseline,
    "home_baseline": HomeTeamBaseline,
    "elo": EloBaseline,
    "poisson_real_data": PoissonBaseline,
    "dixon_coles": DixonColesBaseline,
}

_fitted_cache: dict[str, object] = {}
_xgb_models_cache: Optional[dict] = None
_xgb_calibrators_cache: Optional[dict] = None


def get_fitted_baseline(name: str, historical_matches: pd.DataFrame):
    """Fit (once, cached) a baseline model on the full real historical
    dataset's point-in-time FEATURES (not the raw match table — Elo/Poisson
    need home_elo_before etc., the same columns build_point_in_time_features
    produces during backtesting)."""
    if name not in _BASELINE_CLASSES:
        raise ValueError(f"unknown baseline model {name!r}")
    if name in _fitted_cache:
        return _fitted_cache[name]
    train_features = get_full_history_features(historical_matches)
    model = _BASELINE_CLASSES[name]().fit(train_features)
    _fitted_cache[name] = model
    return model


def _load_xgb_artifacts():
    global _xgb_models_cache, _xgb_calibrators_cache
    if _xgb_models_cache is not None:
        return _xgb_models_cache, _xgb_calibrators_cache

    models, calibrators = {}, {}
    for market in XGB_MARKETS:
        model_path = _REAL_DATA_MODEL_DIR / f"real_xgb_{market}.pkl"
        cal_path = _REAL_DATA_MODEL_DIR / f"real_xgb_{market}_calibrator.pkl"
        if model_path.exists():
            with open(model_path, "rb") as f:
                models[market] = pickle.load(f)
        if cal_path.exists():
            with open(cal_path, "rb") as f:
                calibrators[market] = pickle.load(f)

    _xgb_models_cache, _xgb_calibrators_cache = models, calibrators
    return models, calibrators


def predict_xgboost(feature_row: dict) -> MarketProbs:
    """Predict every market the bundled real-data XGBoost models cover, for
    one feature row (as produced by src.prediction_service.feature_engine.
    generate_features()). Markets it doesn't cover stay None on MarketProbs,
    same convention as the other baselines."""
    models, calibrators = _load_xgb_artifacts()
    X = [[feature_row[col] for col in FEATURE_COLUMNS]]

    def _predict_one(market: str) -> Optional[float]:
        if market not in models:
            return None
        raw = models[market].predict_proba(X)[0][1]
        if market in calibrators:
            return float(calibrators[market].predict([raw])[0])
        return float(raw)

    return MarketProbs(
        p_home=_predict_one("home_win") or 0.0,
        p_draw=_predict_one("draw") or 0.0,
        p_away=_predict_one("away_win") or 0.0,
        p_over_1_5=_predict_one("over_1_5") or 0.0,
        p_over_2_5=_predict_one("over_2_5") or 0.0,
        p_over_3_5=_predict_one("over_3_5") or 0.0,
        p_btts_yes=_predict_one("btts") or 0.0,
    )


def predict_all_available_models(feature_row: dict, historical_matches: pd.DataFrame) -> dict[str, MarketProbs]:
    """Run every eligible model (all baselines + XGBoost) on one match's
    features, for model-agreement computation (Phase 3 §7). Returns
    {model_name: MarketProbs}."""
    out = {}
    for name in _BASELINE_CLASSES:
        model = get_fitted_baseline(name, historical_matches)
        # Baselines' predict() takes a DataFrame of feature rows — wrap
        # the single row into a 1-row frame using the same columns it expects.
        row_df = pd.DataFrame([feature_row])
        out[name] = model.predict(row_df)[0]
    out["xgboost_real_data"] = predict_xgboost(feature_row)
    return out

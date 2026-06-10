"""
Probability Calibration using Isotonic Regression.

Corrects probability inflation by mapping raw probabilities 
to historically observed hit rates.
"""

from __future__ import annotations
import json
import logging
import sqlite3
import numpy as np
from sklearn.isotonic import IsotonicRegression

logger = logging.getLogger("football_predictor")

# Cache to hold in-memory loaded models
_CALIBRATION_MODELS = {}


def train_calibration_models(conn: sqlite3.Connection):
    """
    Fetch prediction log data and fit an Isotonic Regression model for each market type.
    """
    market_types = conn.execute("SELECT DISTINCT market_type FROM prediction_log WHERE actual_outcome IS NOT NULL").fetchall()
    
    for row in market_types:
        market_type = row[0]
        # Fetch data
        data = conn.execute(
            "SELECT predicted_prob, actual_outcome FROM prediction_log WHERE market_type = ? AND actual_outcome IS NOT NULL",
            (market_type,)
        ).fetchall()
        
        if len(data) < 100:
            logger.info(f"Not enough data to calibrate {market_type} ({len(data)} samples)")
            continue
            
        probs = [min(0.99, max(0.01, r[0]/100.0)) for r in data]
        outcomes = [float(r[1]) for r in data]
        
        ir = IsotonicRegression(out_of_bounds='clip')
        ir.fit(probs, outcomes)
        
        # Calculate Brier Score before and after
        brier_before = np.mean((np.array(probs) - np.array(outcomes))**2)
        calibrated_probs = ir.predict(probs)
        brier_after = np.mean((calibrated_probs - np.array(outcomes))**2)
        
        # Extract breakpoints and store in JSON
        fitted_json = json.dumps({
            "X_thresholds": ir.X_thresholds_.tolist() if hasattr(ir, 'X_thresholds_') else [],
            "y_thresholds": ir.y_thresholds_.tolist() if hasattr(ir, 'y_thresholds_') else [],
        })
        
        # Save to DB
        conn.execute("""
            INSERT INTO calibration_models (market_type, fitted_json, samples, brier_score)
            VALUES (?, ?, ?, ?)
        """, (market_type, fitted_json, len(data), brier_after))
        conn.commit()
        
        logger.info(f"Calibrated {market_type}: Brier {brier_before:.4f} -> {brier_after:.4f}")
        
        # Invalidate cache
        if market_type in _CALIBRATION_MODELS:
            del _CALIBRATION_MODELS[market_type]


def calibrate_probability(market_type: str, raw_prob: float) -> float:
    """
    Apply Isotonic Regression calibration to a raw probability (0-100 scale).
    Returns calibrated probability (0-100 scale).
    """
    global _CALIBRATION_MODELS
    
    # Load model if not in cache
    if market_type not in _CALIBRATION_MODELS:
        try:
            from src.db.database import get_db
            conn = get_db()
            row = conn.execute(
                "SELECT fitted_json FROM calibration_models WHERE market_type = ? ORDER BY created_at DESC LIMIT 1",
                (market_type,)
            ).fetchone()
            
            if row:
                model_data = json.loads(row[0])
                X_thresholds = np.array(model_data.get("X_thresholds", []))
                y_thresholds = np.array(model_data.get("y_thresholds", []))
                if len(X_thresholds) > 0 and len(y_thresholds) > 0:
                    ir = IsotonicRegression(out_of_bounds='clip')
                    # Trick to reconstruct IsotonicRegression
                    ir.X_min_ = X_thresholds[0]
                    ir.X_max_ = X_thresholds[-1]
                    ir.f_ = lambda x: np.interp(x, X_thresholds, y_thresholds)
                    ir.X_thresholds_ = X_thresholds
                    ir.y_thresholds_ = y_thresholds
                    _CALIBRATION_MODELS[market_type] = ir
                else:
                    _CALIBRATION_MODELS[market_type] = None
            else:
                _CALIBRATION_MODELS[market_type] = None
        except Exception as e:
            logger.warning(f"Error loading calibration model for {market_type}: {e}")
            _CALIBRATION_MODELS[market_type] = None

    ir = _CALIBRATION_MODELS.get(market_type)
    if not ir:
        return raw_prob
        
    try:
        # Scale to 0-1, apply f_, scale back to 0-100
        p_val = max(0.01, min(0.99, raw_prob / 100.0))
        # Use interpolation explicitly for stability
        cal_val = np.interp([p_val], ir.X_thresholds_, ir.y_thresholds_)[0]
        return float(max(0.1, min(99.9, cal_val * 100.0)))
    except Exception as e:
        logger.debug(f"Calibration failed for {raw_prob}: {e}")
        return raw_prob

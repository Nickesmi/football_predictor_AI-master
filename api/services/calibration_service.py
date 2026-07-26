"""
Calibration Service Module.

Provides business logic and data processing for probability calibration status,
isotonic calibration curves, model testing, and automated engine retraining.
"""

import sqlite3
from typing import Any, Dict, List
from src.db.prediction_logger import get_calibration_data, get_all_market_types
from src.engine.isotonic_calibrator import get_isotonic_calibrator
from src.db.error_intelligence import rebuild_confidence_adjustments, get_model_health


def get_calibration_status(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Retrieve overview of calibration readiness per betting market type.

    Args:
        conn: Active SQLite database connection.

    Returns:
        Dictionary containing sample counts and isotonic calibration readiness per market type.
    """
    return {"market_types": get_all_market_types(conn)}


def get_market_calibration(conn: sqlite3.Connection, market_type: str) -> Dict[str, Any]:
    """
    Retrieve per-market-type calibration curve (predicted vs actual hit rates in 5% buckets).

    Args:
        conn: Active SQLite database connection.
        market_type: Target market type (e.g., goals, result, btts).

    Returns:
        Dictionary with calibration curve data and bucket hit rates.
    """
    return get_calibration_data(conn, market_type)


def get_isotonic_status(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Get readiness and fitting status of all isotonic calibration models.

    Args:
        conn: Active SQLite database connection.

    Returns:
        Dictionary listing fitted isotonic models and sample metrics.
    """
    cal = get_isotonic_calibrator(conn)
    return {"models": cal.get_status()}


def test_isotonic_calibration(
    conn: sqlite3.Connection, raw_prob: float, market_type: str
) -> Dict[str, Any]:
    """
    Test the isotonic calibrator transformation on a single probability value.

    Args:
        conn: Active SQLite database connection.
        raw_prob: The raw uncalibrated probability (0.0 to 100.0).
        market_type: Target market type (e.g., goals).

    Returns:
        Dictionary showing raw probability, calibrated probability, and correction amount.
    """
    cal = get_isotonic_calibrator(conn)
    calibrated = cal.calibrate(raw_prob, market_type)
    return {
        "raw_prob": raw_prob,
        "calibrated_prob": calibrated,
        "market_type": market_type,
        "has_fitted_model": market_type in cal._models,
        "correction": round(calibrated - raw_prob, 1),
    }


def get_isotonic_curve(conn: sqlite3.Connection, market_type: str) -> Dict[str, Any]:
    """
    Retrieve the isotonic calibration transform mapping curve for a specific market type.

    Args:
        conn: Active SQLite database connection.
        market_type: Target market type.

    Returns:
        Dictionary with the raw-to-calibrated probability mapping curve.
    """
    cal = get_isotonic_calibrator(conn)
    curve = cal.get_calibration_curve(market_type)
    return {
        "market_type": market_type,
        "curve": curve,
        "has_model": market_type in cal._models,
    }


def retrain_engine(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Retrain global engine components: fit isotonic models and rebuild confidence adjustments.

    Args:
        conn: Active SQLite database connection.

    Returns:
        Dictionary summarizing fitted model counts, calibration details, and updated model health.
    """
    cal = get_isotonic_calibrator(conn)
    calibration_summary = cal.fit_all(conn)

    rebuild_summary = rebuild_confidence_adjustments(conn)
    health = get_model_health(conn)

    return {
        "status": "ok",
        "models_fitted": sum(1 for v in calibration_summary.values() if v.get("fitted")),
        "calibration_details": calibration_summary,
        "rebuild_summary": rebuild_summary,
        "health": health,
    }

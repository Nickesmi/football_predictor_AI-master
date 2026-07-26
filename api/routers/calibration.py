"""
Calibration Router.

Handles endpoints for probability calibration readiness, isotonic transformation curves,
model testing, and admin-triggered engine retraining.
Strictly delegates all business logic to calibration_service.
"""

import sqlite3
from typing import Any, Dict
from fastapi import APIRouter, Depends
from api.dependencies import verify_admin_key
from api.services import calibration_service

router = APIRouter(prefix="/api", tags=["Calibration & Engine"])


def _get_db() -> sqlite3.Connection:
    """Dependency to retrieve database connection."""
    from src.db.database import get_db

    return get_db()


@router.get("/calibration/status")
def get_calibration_status(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """Overview of calibration readiness per market type."""
    return calibration_service.get_calibration_status(conn)


@router.get("/calibration/isotonic/status")
def get_isotonic_status(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """Get status of all fitted isotonic calibration models."""
    return calibration_service.get_isotonic_status(conn)


@router.get("/calibration/isotonic/test")
def test_isotonic_calibration(
    raw_prob: float = 82.0,
    market_type: str = "goals",
    conn: sqlite3.Connection = Depends(_get_db),
) -> Dict[str, Any]:
    """Test the isotonic calibrator on a single probability value."""
    return calibration_service.test_isotonic_calibration(conn, raw_prob, market_type)


@router.get("/calibration/isotonic/curve/{market_type}")
def get_isotonic_curve(
    market_type: str, conn: sqlite3.Connection = Depends(_get_db)
) -> Dict[str, Any]:
    """Get the calibration transform mapping curve for a market type."""
    return calibration_service.get_isotonic_curve(conn, market_type)


@router.get("/calibration/{market_type}")
def get_market_calibration(
    market_type: str, conn: sqlite3.Connection = Depends(_get_db)
) -> Dict[str, Any]:
    """Get per-market-type calibration curve."""
    return calibration_service.get_market_calibration(conn, market_type)


@router.post("/admin/retrain-engine", dependencies=[Depends(verify_admin_key)])
def retrain_engine(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """Retrain global engine components (isotonic calibration & confidence adjustments)."""
    return calibration_service.retrain_engine(conn)

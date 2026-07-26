"""
Analytics Router.

Handles high-level data warehouse stats, ROI/CLV analytics, calibration gap reports,
league/market performance breakdowns, structural bias detection, and system health checks.
Strictly delegates all business logic and SQL queries to the analytics service layer.
"""

import sqlite3
from typing import Any, Dict, List
from fastapi import APIRouter, Depends
from api.services import analytics_service

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])


def _get_db() -> sqlite3.Connection:
    """Dependency to retrieve database connection."""
    from src.db.database import get_db

    return get_db()


@router.get("/warehouse")
def get_warehouse_stats(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """Return high-level metrics on data warehouse size and progress toward target."""
    return analytics_service.get_warehouse_stats(conn)


@router.get("/roi")
def get_roi_analytics(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """Return ROI aggregated across all settled picks."""
    return analytics_service.get_roi_analytics(conn)


@router.get("/clv")
def get_clv_analytics(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """Return average Closing Line Value (CLV) across settled picks."""
    return analytics_service.get_clv_analytics(conn)


@router.get("/calibration")
def get_calibration_analytics(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """Return global calibration metrics (Brier score and ECE)."""
    return analytics_service.get_calibration_analytics(conn)


@router.get("/leagues")
def get_leagues_analytics(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """Return ROI and calibration metrics grouped by league."""
    return analytics_service.get_leagues_analytics(conn)


@router.get("/markets")
def get_markets_analytics(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """Return ROI and volume grouped by market type."""
    return analytics_service.get_markets_analytics(conn)


@router.get("/model-bias")
def get_model_bias(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """Audit the model for structural biases using historical prediction logs."""
    return analytics_service.get_model_bias_analytics(conn)


@router.get("/debug/system-health")
def get_system_health(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """Return system health and readiness status by inspecting workers and unresolved predictions."""
    return analytics_service.get_system_health_analytics(conn)


@router.get("/league-pnl")
def get_league_pnl(conn: sqlite3.Connection = Depends(_get_db)) -> Any:
    """Return P&L breakdown by league."""
    return analytics_service.get_league_pnl_analytics(conn)


@router.get("/confidence-buckets")
def get_confidence_buckets(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """Return performance breakdown by model confidence range."""
    return analytics_service.get_confidence_buckets_analytics(conn)

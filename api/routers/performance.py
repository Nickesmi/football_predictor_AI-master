"""
Performance Router.

Handles endpoints for backtesting summaries, daily historical performance,
and system-wide performance overview dashboards.
Strictly delegates all business logic to performance_service.
"""

import sqlite3
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends
from api.services import performance_service

router = APIRouter(prefix="/api", tags=["Performance & Backtesting"])


def _get_db() -> sqlite3.Connection:
    """Dependency to retrieve database connection."""
    from src.db.database import get_db

    return get_db()


@router.get("/backtest/summary")
def get_backtest_summary(
    market_type: Optional[str] = None, conn: sqlite3.Connection = Depends(_get_db)
) -> Dict[str, Any]:
    """Retrieve backtesting summary: accuracy, calibration gap, and per-tier breakdown."""
    return performance_service.get_backtest_summary(conn, market_type)


@router.get("/performance/daily")
def get_daily_performance(
    days: int = 30, conn: sqlite3.Connection = Depends(_get_db)
) -> Dict[str, Any]:
    """Retrieve daily performance history for ROI dashboard."""
    return performance_service.get_daily_performance(conn, days)


@router.get("/performance/overview")
def get_performance_overview(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """Retrieve high-level performance overview across all logged predictions."""
    return performance_service.get_performance_overview(conn)

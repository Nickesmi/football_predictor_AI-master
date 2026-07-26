"""
Performance Service Module.

Provides business logic and data aggregation for backtesting summaries,
daily historical ROI performance, and system-wide performance overview reports.
"""

import sqlite3
from typing import Any, Dict, List, Optional
from src.db.prediction_logger import (
    get_backtest_summary as repo_get_backtest_summary,
    get_performance_history,
    get_calibration_data,
    get_all_market_types,
)


def get_backtest_summary(
    conn: sqlite3.Connection, market_type: Optional[str] = None
) -> Dict[str, Any]:
    """
    Retrieve backtest performance summary including accuracy, calibration gap, and per-tier stats.

    Args:
        conn: Active SQLite database connection.
        market_type: Optional filter by betting market type (e.g., goals, result, btts).

    Returns:
        Dictionary containing aggregated backtesting metrics.
    """
    return repo_get_backtest_summary(conn, market_type)


def get_daily_performance(conn: sqlite3.Connection, days: int = 30) -> Dict[str, Any]:
    """
    Retrieve daily historical performance metrics over a specified number of past days.

    Args:
        conn: Active SQLite database connection.
        days: Number of recent days to include in the report (default 30).

    Returns:
        Dictionary with the number of days requested and the historical daily breakdown.
    """
    history = get_performance_history(conn, days)
    return {"days": days, "history": history}


def get_performance_overview(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Aggregate high-level performance overview across all logged predictions.

    Calculates overall metrics, per-market-type calibration gaps, and recent 7-day trend.

    Args:
        conn: Active SQLite database connection.

    Returns:
        Dictionary with overall backtest stats, per-market breakdown, and recent trend.
    """
    overall = repo_get_backtest_summary(conn)
    market_types = get_all_market_types(conn)

    type_gaps: List[Dict[str, Any]] = []
    for mt in market_types:
        cal = get_calibration_data(conn, mt["market_type"], min_samples=20)
        type_gaps.append(
            {
                "market_type": mt["market_type"],
                "samples": mt["total"],
                "settled": mt["settled"],
                "hit_rate": mt["hit_rate"],
                "avg_predicted": mt["avg_predicted"],
                "calibration_ready": mt["calibration_ready"],
                "buckets": cal["buckets"],
            }
        )

    trend = get_performance_history(conn, 7)

    return {
        "overall": overall,
        "market_types": type_gaps,
        "recent_trend": trend,
    }

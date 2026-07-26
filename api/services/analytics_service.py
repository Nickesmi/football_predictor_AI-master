"""
Analytics Service Module.

Provides business logic and SQL execution for data warehouse metrics,
ROI/CLV analytics, model calibration gap calculation, league/market PnL,
structural bias detection, and system health checks.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


def get_warehouse_stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Compute high-level metrics on data warehouse size and progress toward target.

    Args:
        conn: Active SQLite database connection.

    Returns:
        Dictionary containing counts of settled/active predictions, odds snapshots, and progress.
    """
    settled_preds = conn.execute(
        "SELECT COUNT(*) FROM prediction_log WHERE actual_outcome IS NOT NULL"
    ).fetchone()[0]

    active_preds = conn.execute(
        "SELECT COUNT(*) FROM prediction_log WHERE actual_outcome IS NULL"
    ).fetchone()[0]

    odds_snaps = conn.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone()[0]

    settled_picks = conn.execute(
        "SELECT COUNT(*) FROM picks WHERE result IS NOT NULL"
    ).fetchone()[0]

    days_of_history = conn.execute(
        "SELECT COUNT(DISTINCT match_date) FROM match_history"
    ).fetchone()[0]

    return {
        "settled_predictions": settled_preds,
        "active_predictions": active_preds,
        "total_odds_snapshots": odds_snaps,
        "settled_picks": settled_picks,
        "days_of_history": days_of_history,
        "target": 10000,
        "progress_pct": round(min(100.0, (settled_preds / 10000.0) * 100.0), 2),
    }


def get_roi_analytics(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Calculate aggregated Return on Investment (ROI) across all settled picks.

    Args:
        conn: Active SQLite database connection.

    Returns:
        Dictionary containing sample size, ROI percentage, total PnL units, and health status.
    """
    row = conn.execute(
        """SELECT COUNT(*), SUM(pnl_units)
           FROM picks 
           WHERE result IS NOT NULL"""
    ).fetchone()

    sample_size = row[0] or 0
    total_pnl = row[1] or 0.0

    invested = float(sample_size)
    roi = (total_pnl / invested) * 100.0 if invested > 0 else 0.0

    return {
        "sample_size": sample_size,
        "roi_pct": round(roi, 2),
        "total_pnl_units": round(total_pnl, 2),
        "status": "Healthy" if roi > 0 else ("Watchlist" if sample_size < 200 else "Toxic"),
    }


def get_clv_analytics(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Calculate average Closing Line Value (CLV) across settled picks.

    Args:
        conn: Active SQLite database connection.

    Returns:
        Dictionary containing sample size, average CLV percentage, and health status.
    """
    row = conn.execute(
        """SELECT COUNT(*), AVG(clv_pct)
           FROM picks 
           WHERE clv_pct IS NOT NULL AND result IS NOT NULL"""
    ).fetchone()

    sample_size = row[0] or 0
    avg_clv = row[1] or 0.0

    return {
        "sample_size": sample_size,
        "avg_clv_pct": round(avg_clv, 2),
        "status": "Healthy" if avg_clv > 0 else ("Watchlist" if sample_size < 200 else "Toxic"),
    }


def get_calibration_analytics(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Compute global calibration metrics including Brier Score and Expected Calibration Error (ECE).

    Args:
        conn: Active SQLite database connection.

    Returns:
        Dictionary with sample size, Brier score, ECE percentage, and calibration gap percentage.
    """
    rows = conn.execute(
        """SELECT predicted_prob, actual_outcome
           FROM prediction_log 
           WHERE actual_outcome IS NOT NULL"""
    ).fetchall()

    sample_size = len(rows)
    if sample_size == 0:
        return {"sample_size": 0, "brier_score": 0, "ece_pct": 0, "calibration_gap_pct": 0}

    brier_sum = 0.0
    sum_prob = 0.0
    sum_hits = 0.0

    buckets = {i: {"count": 0, "hits": 0.0, "sum_prob": 0.0} for i in range(10)}

    for r in rows:
        prob = r[0] / 100.0 if r[0] > 1 else r[0]
        outcome = float(r[1])
        brier_sum += (prob - outcome) ** 2
        sum_prob += prob
        sum_hits += outcome

        idx = min(9, int(prob * 10))
        buckets[idx]["count"] += 1
        buckets[idx]["hits"] += outcome
        buckets[idx]["sum_prob"] += prob

    brier = brier_sum / float(sample_size)
    avg_prob = sum_prob / float(sample_size)
    actual_rate = sum_hits / float(sample_size)
    cal_gap = avg_prob - actual_rate

    ece = 0.0
    for b in buckets.values():
        if b["count"] > 0:
            b_avg_prob = b["sum_prob"] / float(b["count"])
            b_actual_rate = b["hits"] / float(b["count"])
            ece += (float(b["count"]) / float(sample_size)) * abs(b_avg_prob - b_actual_rate)

    return {
        "sample_size": sample_size,
        "brier_score": round(brier, 4),
        "ece_pct": round(ece * 100.0, 2),
        "calibration_gap_pct": round(cal_gap * 100.0, 2),
    }


def get_leagues_analytics(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Calculate ROI and performance health scores grouped by competition/league.

    Args:
        conn: Active SQLite database connection.

    Returns:
        Dictionary containing a list of league performance records.
    """
    rows = conn.execute(
        """SELECT m.league_name, COUNT(p.id) as sample_size, SUM(p.pnl_units) as total_pnl
           FROM picks p
           JOIN matches m ON p.match_id = m.id
           WHERE p.result IS NOT NULL
           GROUP BY m.league_name
           ORDER BY sample_size DESC"""
    ).fetchall()

    leagues: List[Dict[str, Any]] = []
    for r in rows:
        lname, sample_size, total_pnl = r[0], r[1] or 0, r[2] or 0.0
        roi = (total_pnl / float(sample_size)) * 100.0 if sample_size > 0 else 0.0

        status = "HEALTHY"
        if sample_size >= 200:
            if roi < -5.0:
                status = "TOXIC"
            elif roi < 0.0:
                status = "WATCHLIST"
        else:
            status = "PENDING_DATA"

        leagues.append(
            {
                "league_name": lname,
                "sample_size": sample_size,
                "roi_pct": round(roi, 2),
                "total_pnl_units": round(total_pnl, 2),
                "health_score": status,
            }
        )

    return {"leagues": leagues}


def get_markets_analytics(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Calculate ROI and volume grouped by betting market type.

    Args:
        conn: Active SQLite database connection.

    Returns:
        Dictionary containing a list of market performance records.
    """
    rows = conn.execute(
        """SELECT market, COUNT(*) as sample_size, SUM(pnl_units) as total_pnl
           FROM picks 
           WHERE result IS NOT NULL
           GROUP BY market
           ORDER BY sample_size DESC"""
    ).fetchall()

    markets: List[Dict[str, Any]] = []
    for r in rows:
        market, sample_size, total_pnl = r[0], r[1] or 0, r[2] or 0.0
        roi = (total_pnl / float(sample_size)) * 100.0 if sample_size > 0 else 0.0
        markets.append(
            {
                "market": market,
                "sample_size": sample_size,
                "roi_pct": round(roi, 2),
                "total_pnl_units": round(total_pnl, 2),
            }
        )

    return {"markets": markets}


def get_model_bias_analytics(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Audit historical prediction logs to detect structural biases (home win, over goals, favorite, underdog).

    Args:
        conn: Active SQLite database connection.

    Returns:
        Dictionary detailing bias metrics and detected bias statuses across segments.
    """
    home_stats = conn.execute(
        """SELECT COUNT(*), AVG(predicted_prob), AVG(actual_outcome * 100.0)
           FROM prediction_log
           WHERE market = 'Home Win' AND actual_outcome IS NOT NULL"""
    ).fetchone()

    over_stats = conn.execute(
        """SELECT COUNT(*), AVG(predicted_prob), AVG(actual_outcome * 100.0)
           FROM prediction_log
           WHERE market = 'Over 2.5 Goals' AND actual_outcome IS NOT NULL"""
    ).fetchone()

    def process_stats(row: Any, name: str) -> Dict[str, Any]:
        sz = row[0] or 0
        pred = row[1] or 0.0
        act = row[2] or 0.0
        gap = pred - act
        bias = "None"
        if sz >= 200:
            if gap > 5.0:
                bias = f"Overestimating {name}"
            elif gap < -5.0:
                bias = f"Underestimating {name}"
        return {
            "sample_size": sz,
            "avg_predicted": round(pred, 2),
            "avg_actual": round(act, 2),
            "calibration_gap": round(gap, 2),
            "detected_bias": bias if sz >= 200 else "Pending Data",
        }

    fav_stats = conn.execute(
        """SELECT COUNT(*), AVG(model_prob), AVG(CASE WHEN result='W' THEN 100.0 ELSE 0.0 END)
           FROM picks
           WHERE implied_prob >= 50.0 AND result IS NOT NULL"""
    ).fetchone()

    dog_stats = conn.execute(
        """SELECT COUNT(*), AVG(model_prob), AVG(CASE WHEN result='W' THEN 100.0 ELSE 0.0 END)
           FROM picks
           WHERE implied_prob < 50.0 AND result IS NOT NULL"""
    ).fetchone()

    return {
        "home_team_bias": process_stats(home_stats, "Home Teams"),
        "over_goals_bias": process_stats(over_stats, "Over Goals"),
        "favorite_bias": process_stats(fav_stats, "Favorites"),
        "underdog_bias": process_stats(dog_stats, "Underdogs"),
    }


def get_system_health_analytics(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Check system health and readiness status by inspecting background workers and unresolved predictions.

    Args:
        conn: Active SQLite database connection.

    Returns:
        Dictionary with worker health statuses, timestamps, unresolved prediction count, and blockers.
    """

    def check_worker(log_name: str) -> tuple[str, str]:
        path = Path(f"logs/{log_name}")
        if not path.exists():
            return "missing", "Never"

        mtime = path.stat().st_mtime
        last_run = datetime.fromtimestamp(mtime).isoformat()

        age_hours = (datetime.now().timestamp() - mtime) / 3600.0
        if "collection" in log_name and age_hours > 25.0:
            return "stale", last_run
        if age_hours > 7.0:
            return "stale", last_run

        return "healthy", last_run

    col_status, col_last = check_worker("collection_worker.log")
    odds_status, odds_last = check_worker("odds_worker.log")
    settle_status, settle_last = check_worker("settlement_worker.log")

    unresolved = conn.execute(
        "SELECT COUNT(*) FROM prediction_log WHERE actual_outcome IS NULL AND match_date < date('now')"
    ).fetchone()[0]

    picks_today = conn.execute(
        "SELECT COUNT(*) FROM picks WHERE date(created_at) = date('now')"
    ).fetchone()[0]

    blockers: List[str] = []
    if col_status != "healthy":
        blockers.append("collection_worker is not healthy")
    if odds_status != "healthy":
        blockers.append("odds_worker is not healthy")
    if settle_status != "healthy":
        blockers.append("settlement_worker is not healthy")
    if unresolved > 0:
        blockers.append(f"Catch-up required: {unresolved} unresolved predictions")
    if picks_today == 0:
        blockers.append("No warehouse growth today")

    return {
        "collection_worker": col_status,
        "odds_worker": odds_status,
        "settlement_worker": settle_status,
        "last_collection": col_last,
        "last_odds_snapshot": odds_last,
        "last_settlement": settle_last,
        "unresolved_predictions": unresolved,
        "warehouse_growth_today": picks_today,
        "readiness_blockers": blockers,
    }


def get_league_pnl_analytics(conn: sqlite3.Connection) -> Any:
    """
    Retrieve P&L breakdown grouped by league from the prediction logger database.

    Args:
        conn: Active SQLite database connection.

    Returns:
        List of dictionaries with league PnL statistics.
    """
    from src.db.picks_repo import get_league_pnl

    return get_league_pnl(conn)


def get_confidence_buckets_analytics(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Analyze prediction accuracy and performance broken down by model confidence ranges.

    Args:
        conn: Active SQLite database connection.

    Returns:
        Dictionary containing bucket analysis results.
    """
    from src.engine.calibration import ConfidenceBucketer

    bucketer = ConfidenceBucketer()
    return {"buckets": bucketer.analyze(conn)}

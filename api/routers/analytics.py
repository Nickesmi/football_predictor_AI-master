from fastapi import APIRouter, Depends
from typing import Dict, Any
import sqlite3

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])

def _get_db() -> sqlite3.Connection:
    from src.db.database import get_db
    return get_db()

@router.get("/warehouse")
def get_warehouse_stats(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """
    Returns high-level metrics on data warehouse size.
    Target: 10000+ settled predictions.
    """
    settled_preds = conn.execute(
        "SELECT COUNT(*) FROM prediction_log WHERE actual_outcome IS NOT NULL"
    ).fetchone()[0]
    
    active_preds = conn.execute(
        "SELECT COUNT(*) FROM prediction_log WHERE actual_outcome IS NULL"
    ).fetchone()[0]
    
    odds_snaps = conn.execute(
        "SELECT COUNT(*) FROM odds_snapshots"
    ).fetchone()[0]
    
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
        "progress_pct": round(min(100, (settled_preds / 10000) * 100), 2)
    }

@router.get("/roi")
def get_roi_analytics(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """
    Returns ROI aggregated across all settled picks.
    Includes sample_size in response.
    """
    row = conn.execute(
        """SELECT COUNT(*), SUM(pnl_units)
           FROM picks 
           WHERE result IS NOT NULL"""
    ).fetchone()
    
    sample_size = row[0] or 0
    total_pnl = row[1] or 0.0
    
    # Assume 1 unit flat stake
    invested = sample_size
    roi = (total_pnl / invested) * 100 if invested > 0 else 0.0
    
    return {
        "sample_size": sample_size,
        "roi_pct": round(roi, 2),
        "total_pnl_units": round(total_pnl, 2),
        "status": "Healthy" if roi > 0 else ("Watchlist" if sample_size < 200 else "Toxic")
    }

@router.get("/clv")
def get_clv_analytics(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """
    Returns average Closing Line Value (CLV).
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
        "status": "Healthy" if avg_clv > 0 else ("Watchlist" if sample_size < 200 else "Toxic")
    }

@router.get("/calibration")
def get_calibration_analytics(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """
    Returns global calibration metrics (Brier, ECE).
    """
    rows = conn.execute(
        """SELECT predicted_prob, actual_outcome
           FROM prediction_log 
           WHERE actual_outcome IS NOT NULL"""
    ).fetchall()
    
    sample_size = len(rows)
    if sample_size == 0:
        return {"sample_size": 0, "brier_score": 0, "ece_pct": 0, "calibration_gap": 0}
        
    brier_sum = 0.0
    sum_prob = 0.0
    sum_hits = 0.0
    
    buckets = {i: {'count': 0, 'hits': 0, 'sum_prob': 0.0} for i in range(10)}
    
    for r in rows:
        prob = r[0] / 100.0 if r[0] > 1 else r[0]
        outcome = float(r[1])
        brier_sum += (prob - outcome) ** 2
        sum_prob += prob
        sum_hits += outcome
        
        idx = min(9, int(prob * 10))
        buckets[idx]['count'] += 1
        buckets[idx]['hits'] += outcome
        buckets[idx]['sum_prob'] += prob
        
    brier = brier_sum / sample_size
    avg_prob = sum_prob / sample_size
    actual_rate = sum_hits / sample_size
    cal_gap = avg_prob - actual_rate
    
    ece = 0.0
    for b in buckets.values():
        if b['count'] > 0:
            b_avg_prob = b['sum_prob'] / b['count']
            b_actual_rate = b['hits'] / b['count']
            ece += (b['count'] / sample_size) * abs(b_avg_prob - b_actual_rate)
            
    return {
        "sample_size": sample_size,
        "brier_score": round(brier, 4),
        "ece_pct": round(ece * 100, 2),
        "calibration_gap_pct": round(cal_gap * 100, 2)
    }

@router.get("/leagues")
def get_leagues_analytics(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """
    Returns ROI and calibration metrics grouped by league.
    Computes league_health_score.
    """
    # Join picks to matches to get league_name for picks if not directly stored
    rows = conn.execute(
        """SELECT m.league_name, COUNT(p.id) as sample_size, SUM(p.pnl_units) as total_pnl
           FROM picks p
           JOIN matches m ON p.match_id = m.id
           WHERE p.result IS NOT NULL
           GROUP BY m.league_name
           ORDER BY sample_size DESC"""
    ).fetchall()
    
    leagues = []
    for r in rows:
        lname, sample_size, total_pnl = r[0], r[1] or 0, r[2] or 0.0
        roi = (total_pnl / sample_size) * 100 if sample_size > 0 else 0.0
        
        # League Health Detection
        status = "HEALTHY"
        if sample_size >= 200:
            if roi < -5.0:
                status = "TOXIC"
            elif roi < 0.0:
                status = "WATCHLIST"
        else:
            status = "PENDING_DATA"
            
        leagues.append({
            "league_name": lname,
            "sample_size": sample_size,
            "roi_pct": round(roi, 2),
            "total_pnl_units": round(total_pnl, 2),
            "health_score": status
        })
        
    return {"leagues": leagues}

@router.get("/markets")
def get_markets_analytics(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """
    Returns ROI and volume grouped by market type.
    """
    rows = conn.execute(
        """SELECT market, COUNT(*) as sample_size, SUM(pnl_units) as total_pnl
           FROM picks 
           WHERE result IS NOT NULL
           GROUP BY market
           ORDER BY sample_size DESC"""
    ).fetchall()
    
    markets = []
    for r in rows:
        market, sample_size, total_pnl = r[0], r[1] or 0, r[2] or 0.0
        roi = (total_pnl / sample_size) * 100 if sample_size > 0 else 0.0
        markets.append({
            "market": market,
            "sample_size": sample_size,
            "roi_pct": round(roi, 2),
            "total_pnl_units": round(total_pnl, 2)
        })
        
    return {"markets": markets}

@router.get("/model-bias")
def get_model_bias(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """
    Audits the model for structural biases using historical prediction_log.
    Detects:
    - Home team bias
    - Favorite bias
    - Underdog bias
    - Over goals bias
    """
    # Home Team Bias (Calibration on Home Win predictions)
    home_stats = conn.execute(
        """SELECT COUNT(*), AVG(predicted_prob), AVG(actual_outcome * 100.0)
           FROM prediction_log
           WHERE market = 'Home Win' AND actual_outcome IS NOT NULL"""
    ).fetchone()
    
    # Over 2.5 Goals Bias
    over_stats = conn.execute(
        """SELECT COUNT(*), AVG(predicted_prob), AVG(actual_outcome * 100.0)
           FROM prediction_log
           WHERE market = 'Over 2.5 Goals' AND actual_outcome IS NOT NULL"""
    ).fetchone()
    
    def process_stats(row, name):
        sz = row[0] or 0
        pred = row[1] or 0.0
        act = row[2] or 0.0
        gap = pred - act
        bias = "None"
        if sz >= 200:
            if gap > 5.0: bias = f"Overestimating {name}"
            elif gap < -5.0: bias = f"Underestimating {name}"
        return {
            "sample_size": sz,
            "avg_predicted": round(pred, 2),
            "avg_actual": round(act, 2),
            "calibration_gap": round(gap, 2),
            "detected_bias": bias if sz >= 200 else "Pending Data"
        }
        
    # Favorite vs Underdog bias (Implied prob > 50% vs < 50%)
    # Requires implied_prob from picks table
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

import os
from datetime import datetime

@router.get("/debug/system-health")
def get_system_health(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """
    Returns system health and readiness status, checking background workers
    and unresolved predictions.
    """
    def check_worker(log_name: str) -> tuple[str, str]:
        path = Path(f"logs/{log_name}")
        if not path.exists():
            return "missing", "Never"
        
        mtime = path.stat().st_mtime
        last_run = datetime.fromtimestamp(mtime).isoformat()
        
        # Stale if older than 24h for collection, 6h for odds/settlement
        age_hours = (datetime.now().timestamp() - mtime) / 3600
        if "collection" in log_name and age_hours > 25:
            return "stale", last_run
        if age_hours > 7:
            return "stale", last_run
            
        return "healthy", last_run

    from pathlib import Path
    
    col_status, col_last = check_worker("collection_worker.log")
    odds_status, odds_last = check_worker("odds_worker.log")
    settle_status, settle_last = check_worker("settlement_worker.log")
    
    unresolved = conn.execute(
        "SELECT COUNT(*) FROM prediction_log WHERE actual_outcome IS NULL AND match_date < date('now')"
    ).fetchone()[0]
    
    # Growth today
    picks_today = conn.execute(
        "SELECT COUNT(*) FROM picks WHERE date(created_at) = date('now')"
    ).fetchone()[0]
    
    blockers = []
    if col_status != "healthy": blockers.append("collection_worker is not healthy")
    if odds_status != "healthy": blockers.append("odds_worker is not healthy")
    if settle_status != "healthy": blockers.append("settlement_worker is not healthy")
    if unresolved > 0: blockers.append(f"Catch-up required: {unresolved} unresolved predictions")
    if picks_today == 0: blockers.append("No warehouse growth today")
    
    return {
        "collection_worker": col_status,
        "odds_worker": odds_status,
        "settlement_worker": settle_status,
        "last_collection": col_last,
        "last_odds_snapshot": odds_last,
        "last_settlement": settle_last,
        "unresolved_predictions": unresolved,
        "warehouse_growth_today": picks_today,
        "readiness_blockers": blockers
    }


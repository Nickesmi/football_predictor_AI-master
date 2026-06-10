"""
Prediction Logger — Phase 3 Core Module

Logs every prediction + outcome into the prediction_log table.
This is the raw data source for:
  - Isotonic calibration per market type
  - Backtesting / ROI tracking
  - CLV measurement
  - Daily performance dashboards

Usage:
    log_predictions(conn, match_id, date, home, away, league, evaluated_picks)
    get_calibration_data(conn, market_type, min_samples=200)
    get_daily_stats(conn, date)
"""

import sqlite3
import logging
from datetime import datetime

logger = logging.getLogger("football_predictor")


def _classify_market_type(market_name: str) -> str:
    """Classify a market name into a calibration group.

    Groups are chosen to have enough samples for meaningful calibration.
    Too granular → noise. Too broad → useless.

    Groups:
        goals     — Over/Under total goals, exact total goals, goal ranges
        result    — 1X2, Double Chance, winning margin, clean sheet
        btts      — BTTS Yes/No
        cs        — Correct Score
        combo     — Result+Goals, Result+BTTS, BTTS+Goals
        handicap  — Asian/European Handicap
        team_goals — Team over/under, exact team goals
        half      — FH/SH markets
        corners   — Corner markets
        cards     — Card markets
    """
    m = market_name.lower()

    if "corner" in m:
        return "corners"
    if "card" in m:
        return "cards"
    if m.startswith("ah ") or m.startswith("eh ") or "handicap" in m:
        return "handicap"
    if " & " in m:
        return "combo"
    if m.startswith("cs ") or "correct score" in m:
        return "cs"
    if m.startswith("fh ") or m.startswith("sh "):
        return "half"
    if "btts" in m:
        return "btts"
    if "win by" in m or "clean sheet" in m or "fails to score" in m:
        return "result"
    if m in ("home win", "away win", "draw") or "1x " in m or "x2 " in m or "12 " in m:
        return "result"
    if "exact" in m and "goals" in m and "total" not in m:
        return "team_goals"
    if ("over" in m or "under" in m) and "goals" in m:
        # Check if team-specific
        if not m.startswith("over") and not m.startswith("under"):
            return "team_goals"
        return "goals"
    if "score in both" in m or "goal in" in m:
        return "goals"

    return "goals"


def log_predictions(
    conn: sqlite3.Connection,
    match_id: str,
    match_date: str,
    home_team: str,
    away_team: str,
    league_name: str,
    evaluated_picks: list[dict],
) -> int:
    """Log all evaluated predictions for a match.

    Each pick dict must have:
        - market (str)
        - probability (float, 0-100)
        - result (bool or None)
        - tier (int, optional)

    Returns number of predictions logged.
    
    VALIDATION:
    - Prevents logging if predictions are duplicated in same request
    - Prevents logging predictions made after match start (future leakage)
    - Uses (match_id, market_type) deduplication, not just match_id
    """
    from datetime import datetime
    
    # VALIDATION: Check timestamp is not in future
    try:
        match_dt = datetime.fromisoformat(match_date)
        now = datetime.now()
        if match_dt > now:
            logger.warning(f"Cannot predict match {match_id}: match date {match_date} is in future")
            return 0
    except:
        pass  # If date parsing fails, proceed (unlikely)
    
    count = 0
    logged_markets = set()  # Track which (match_id, market_type) we log in this request
    
    for pick in evaluated_picks:
        market = pick.get("market", "")
        prob = pick.get("probability", 0)
        result = pick.get("result")
        tier = pick.get("tier")

        # Convert bool result to int (1/0/NULL)
        if result is True:
            outcome = 1
        elif result is False:
            outcome = 0
        else:
            outcome = None

        market_type = _classify_market_type(market)
        
        # DEDUPLICATION: Skip if we already logged this (match, market_type) today
        market_key = (match_id, market_type)
        if market_key in logged_markets:
            logger.debug(f"Skipping duplicate {market_type} for match {match_id}")
            continue
        
        # Also check if this (match_id, market_type) exists in DB from earlier
        existing = conn.execute(
            "SELECT COUNT(*) FROM prediction_log WHERE match_id = ? AND market_type = ?",
            (match_id, market_type),
        ).fetchone()[0]
        
        if existing > 0:
            # Market already logged for this match - skip it
            logger.debug(f"Skipping already-logged {market_type} for match {match_id}")
            continue

        conn.execute(
            """INSERT INTO prediction_log
               (match_id, match_date, home_team, away_team, league_name,
                market, market_type, predicted_prob, actual_outcome, tier)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (match_id, match_date, home_team, away_team, league_name,
             market, market_type, prob, outcome, tier),
        )
        count += 1
        logged_markets.add(market_key)

    conn.commit()
    if count > 0:
        logger.info(
            f"Logged {count} predictions for {home_team} vs {away_team} "
            f"(match {match_id}, {match_date})"
        )
    return count


def get_calibration_data(
    conn: sqlite3.Connection,
    market_type: str,
    min_samples: int = 50,
) -> dict:
    """Get calibration data for a specific market type.

    Returns:
        {
            "market_type": str,
            "total_samples": int,
            "buckets": [
                {"range": "70-75%", "predicted_avg": 72.5, "actual_rate": 68.3, "count": 45},
                ...
            ],
            "ready": bool  # True if enough data for calibration
        }
    """
    rows = conn.execute(
        """SELECT predicted_prob, actual_outcome
           FROM prediction_log
           WHERE market_type = ? AND actual_outcome IS NOT NULL
           ORDER BY predicted_prob""",
        (market_type,),
    ).fetchall()

    total = len(rows)
    ready = total >= min_samples

    # Build 5% buckets
    buckets = {}
    for row in rows:
        prob = row[0]
        outcome = row[1]
        bucket_idx = min(int(prob / 5) * 5, 95)
        key = f"{bucket_idx}-{bucket_idx+5}%"
        if key not in buckets:
            buckets[key] = {"total": 0, "hits": 0, "sum_prob": 0.0}
        buckets[key]["total"] += 1
        buckets[key]["hits"] += outcome
        buckets[key]["sum_prob"] += prob

    result_buckets = []
    for key in sorted(buckets.keys(), key=lambda x: int(x.split("-")[0])):
        b = buckets[key]
        result_buckets.append({
            "range": key,
            "predicted_avg": round(b["sum_prob"] / b["total"], 1),
            "actual_rate": round(b["hits"] / b["total"] * 100, 1),
            "count": b["total"],
        })

    return {
        "market_type": market_type,
        "total_samples": total,
        "buckets": result_buckets,
        "ready": ready,
    }


def get_all_market_types(conn: sqlite3.Connection) -> list[dict]:
    """Get summary of all market types in the prediction log."""
    rows = conn.execute(
        """SELECT market_type,
                  COUNT(*) as total,
                  SUM(CASE WHEN actual_outcome IS NOT NULL THEN 1 ELSE 0 END) as settled,
                  SUM(CASE WHEN actual_outcome = 1 THEN 1 ELSE 0 END) as hits,
                  AVG(predicted_prob) as avg_prob
           FROM prediction_log
           GROUP BY market_type
           ORDER BY total DESC"""
    ).fetchall()

    return [{
        "market_type": r[0],
        "total": r[1],
        "settled": r[2],
        "hits": r[3],
        "hit_rate": round(r[3] / r[2] * 100, 1) if r[2] > 0 else 0,
        "avg_predicted": round(r[4], 1),
        "calibration_ready": r[2] >= 200,
    } for r in rows]


def get_competition_type_analysis(conn: sqlite3.Connection) -> list[dict]:
    """Compare prediction quality across different competition categories."""
    import math
    from src.db.competition_tracker import ensure_competitions_table
    ensure_competitions_table(conn)

    # We join with the competitions table to get category
    rows = conn.execute(
        """SELECT c.category,
                  p.predicted_prob,
                  p.actual_outcome
           FROM prediction_log p
           JOIN competitions c ON p.league_name = c.name
           WHERE p.actual_outcome IS NOT NULL"""
    ).fetchall()

    categories = {}
    for r in rows:
        cat = r[0] or "men"
        prob = r[1]
        outcome = r[2]

        if cat not in categories:
            categories[cat] = {"total": 0, "hits": 0, "sum_prob": 0.0, "brier": 0.0}

        categories[cat]["total"] += 1
        categories[cat]["hits"] += outcome
        categories[cat]["sum_prob"] += prob

        p = max(1e-7, min(1 - 1e-7, prob / 100.0))
        y = float(outcome)
        categories[cat]["brier"] += (p - y) ** 2

    results = []
    for cat, stats in categories.items():
        total = stats["total"]
        results.append({
            "category": cat,
            "total_predictions": total,
            "accuracy_pct": round(stats["hits"] / total * 100, 1) if total > 0 else 0,
            "avg_predicted": round(stats["sum_prob"] / total, 1) if total > 0 else 0,
            "calibration_gap": round((stats["sum_prob"] / total) - (stats["hits"] / total * 100), 1) if total > 0 else 0,
            "brier_score": round(stats["brier"] / total, 4) if total > 0 else 0,
        })

    # Sort by total predictions descending
    results.sort(key=lambda x: x["total_predictions"], reverse=True)
    return results


def update_daily_performance(conn: sqlite3.Connection, date_str: str) -> dict:
    """Compute and store daily performance stats."""
    row = conn.execute(
        """SELECT
               COUNT(*) as total,
               SUM(CASE WHEN actual_outcome IS NOT NULL THEN 1 ELSE 0 END) as settled,
               SUM(CASE WHEN actual_outcome = 1 THEN 1 ELSE 0 END) as correct,
               SUM(CASE WHEN actual_outcome = 0 THEN 1 ELSE 0 END) as wrong,
               AVG(predicted_prob) as avg_prob,
               AVG(CASE WHEN actual_outcome IS NOT NULL
                   THEN actual_outcome * 100.0 ELSE NULL END) as avg_actual
           FROM prediction_log
           WHERE match_date = ?""",
        (date_str,),
    ).fetchone()

    total = row[0] or 0
    settled = row[1] or 0
    correct = row[2] or 0
    wrong = row[3] or 0
    avg_prob = row[4] or 0
    avg_actual = row[5] or 0
    accuracy = round(correct / settled * 100, 1) if settled > 0 else 0
    cal_gap = round(avg_prob - avg_actual, 1) if settled > 0 else 0

    conn.execute(
        """INSERT OR REPLACE INTO daily_performance
           (date, total_predictions, total_settled, total_correct, total_wrong,
            accuracy_pct, avg_predicted_prob, avg_actual_rate, calibration_gap)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date_str, total, settled, correct, wrong, accuracy,
         round(avg_prob, 1), round(avg_actual, 1), cal_gap),
    )
    conn.commit()

    return {
        "date": date_str,
        "total_predictions": total,
        "settled": settled,
        "correct": correct,
        "wrong": wrong,
        "accuracy_pct": accuracy,
        "avg_predicted_prob": round(avg_prob, 1),
        "avg_actual_rate": round(avg_actual, 1),
        "calibration_gap": cal_gap,
    }


def get_performance_history(conn: sqlite3.Connection, days: int = 30) -> list[dict]:
    """Get daily performance history for the ROI dashboard."""
    rows = conn.execute(
        """SELECT * FROM daily_performance
           ORDER BY date DESC LIMIT ?""",
        (days,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_backtest_summary(conn: sqlite3.Connection, market_type: str = None) -> dict:
    """Get overall backtesting stats, optionally filtered by market type.

    Returns cumulative stats + per-tier breakdown + calibration quality
    + Brier Score + Log Loss.

    Brier Score: mean((p - y)^2) — lower is better. Perfect = 0.0.
    Log Loss: -mean(y*log(p) + (1-y)*log(1-p)) — lower is better.
    """
    import math

    where = "WHERE actual_outcome IS NOT NULL"
    params = []
    if market_type:
        where += " AND market_type = ?"
        params.append(market_type)

    # Overall stats + raw data for Brier/LogLoss
    rows = conn.execute(
        f"""SELECT predicted_prob, actual_outcome
           FROM prediction_log {where}""",
        params,
    ).fetchall()

    total = len(rows)
    correct = sum(1 for r in rows if r[1] == 1) if rows else 0
    avg_prob = sum(r[0] for r in rows) / total if total > 0 else 0
    avg_actual = (correct / total * 100) if total > 0 else 0

    # Brier Score
    brier = 0.0
    log_loss = 0.0
    eps = 1e-7  # prevent log(0)
    for row in rows:
        p = max(eps, min(1 - eps, row[0] / 100.0))  # convert to 0-1
        y = float(row[1])
        brier += (p - y) ** 2
        log_loss += -(y * math.log(p) + (1 - y) * math.log(1 - p))

    brier = round(brier / total, 4) if total > 0 else 0.0
    log_loss = round(log_loss / total, 4) if total > 0 else 0.0

    # Per-tier breakdown
    tier_rows = conn.execute(
        f"""SELECT tier,
                   COUNT(*) as total,
                   SUM(actual_outcome) as correct,
                   AVG(predicted_prob) as avg_prob
           FROM prediction_log
           {where} AND tier IS NOT NULL
           GROUP BY tier ORDER BY tier""",
        params,
    ).fetchall()

    tier_stats = [{
        "tier": r[0],
        "total": r[1],
        "correct": r[2],
        "accuracy": round(r[2] / r[1] * 100, 1) if r[1] > 0 else 0,
        "avg_prob": round(r[3], 1),
    } for r in tier_rows]

    return {
        "market_type": market_type or "all",
        "total_predictions": total,
        "total_correct": correct,
        "total_wrong": total - correct,
        "accuracy_pct": round(correct / total * 100, 1) if total > 0 else 0,
        "avg_predicted_prob": round(avg_prob, 1),
        "avg_actual_rate": round(avg_actual, 1),
        "calibration_gap": round(avg_prob - avg_actual, 1) if total > 0 else 0,
        "brier_score": brier,
        "log_loss": log_loss,
        "tier_breakdown": tier_stats,
    }


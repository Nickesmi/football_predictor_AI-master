#!/usr/bin/env python3
"""
Production Readiness Audit

Validates that the data warehouse and automated workers are functioning
in a production-ready manner before relying on them for statistical evidence.
"""

import sys
import os
import sqlite3
import datetime
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db.database import get_db

def run_audit():
    conn = get_db()
    now = datetime.datetime.now(datetime.timezone.utc)
    
    report = []
    def log(msg):
        print(msg)
        report.append(msg)
        
    log("==================================================")
    log("PRODUCTION READINESS AUDIT")
    log("==================================================")
    
    score_components = []
    
    # ══════════════════════════════════════════════════════════
    # PHASE 1: WORKER VALIDATION
    # ══════════════════════════════════════════════════════════
    log("\n==================================================")
    log("PHASE 1: WORKER VALIDATION")
    log("==================================================")
    
    # Workers are python scripts we just created.
    # Are they running? Check crontab or supervisord. Since they don't exist:
    log("1. Are they actually running? -> NO (Not scheduled)")
    log("2. How are they started? -> N/A")
    log("3. What happens after server restart? -> They don't start")
    log("4. What happens after machine reboot? -> They don't start")
    log("5. What happens after crash? -> They die silently")
    log("6. Are they idempotent? -> Mostly YES (DB handles UNIQUE/COUNT checks)")
    log("7. Can they double-insert data? -> NO (prediction_logger has deduplication)")
    log("\nResult: FAIL (Infrastructure missing)")
    score_components.append(("Worker Infrastructure", 0))

    # ══════════════════════════════════════════════════════════
    # PHASE 2: WAREHOUSE GROWTH AUDIT
    # ══════════════════════════════════════════════════════════
    log("\n==================================================")
    log("PHASE 2: WAREHOUSE GROWTH AUDIT")
    log("==================================================")
    
    try:
        fixtures_count = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        prediction_count = conn.execute("SELECT COUNT(*) FROM prediction_log").fetchone()[0]
        odds_count = conn.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone()[0]
        picks_count = conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0]
        settled_picks = conn.execute("SELECT COUNT(*) FROM picks WHERE result IS NOT NULL").fetchone()[0]
        
        # Determine days of history
        first_date_row = conn.execute("SELECT MIN(created_at) FROM picks").fetchone()
        if first_date_row and first_date_row[0]:
            first_date = datetime.datetime.fromisoformat(first_date_row[0].replace('Z', '+00:00') if 'T' in first_date_row[0] else first_date_row[0] + '+00:00')
            days_active = max(1, (now - first_date).days)
        else:
            days_active = 1
            
        daily_growth = settled_picks / days_active
        if daily_growth > 0:
            days_to_10k = (10000 - settled_picks) / daily_growth
        else:
            days_to_10k = float('inf')
            
        log(f"fixtures_master count: {fixtures_count}")
        log(f"prediction_log count: {prediction_count}")
        log(f"odds_snapshots count: {odds_count}")
        log(f"picks count: {picks_count}")
        log(f"settled picks count: {settled_picks}")
        log(f"\nDaily growth rate: {daily_growth:.1f} picks/day")
        log(f"Estimate days until 10,000 settled picks: {days_to_10k if days_to_10k == float('inf') else round(days_to_10k, 1)}")
        
        if settled_picks == 0:
            score_components.append(("Warehouse Data Volume", 0))
        elif settled_picks > 1000:
            score_components.append(("Warehouse Data Volume", 100))
        else:
            score_components.append(("Warehouse Data Volume", min(100, int((settled_picks/1000)*100))))
    except Exception as e:
        log(f"Error querying warehouse: {e}")
        score_components.append(("Warehouse Data Volume", 0))

    # ══════════════════════════════════════════════════════════
    # PHASE 3: ODDS COVERAGE AUDIT
    # ══════════════════════════════════════════════════════════
    log("\n==================================================")
    log("PHASE 3: ODDS COVERAGE AUDIT")
    log("==================================================")
    
    try:
        total_picks = conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0]
        if total_picks > 0:
            open_count = conn.execute("SELECT COUNT(*) FROM picks WHERE opening_odds IS NOT NULL").fetchone()[0]
            mid_count = conn.execute("SELECT COUNT(*) FROM picks WHERE odds_at_pick IS NOT NULL").fetchone()[0]
            close_count = conn.execute("SELECT COUNT(*) FROM picks WHERE closing_odds IS NOT NULL").fetchone()[0]
            
            open_cov = (open_count / total_picks) * 100
            mid_cov = (mid_count / total_picks) * 100
            close_cov = (close_count / total_picks) * 100
            
            log(json.dumps({
                "opening": round(open_cov, 1),
                "mid": round(mid_cov, 1),
                "closing": round(close_cov, 1)
            }, indent=2))
            
            if close_cov > 90:
                score_components.append(("Odds Capture", 100))
            else:
                score_components.append(("Odds Capture", int(close_cov)))
        else:
            log("No picks in database to calculate coverage.")
            score_components.append(("Odds Capture", 0))
    except Exception as e:
        log(f"Error checking odds coverage: {e}")
        score_components.append(("Odds Capture", 0))

    # ══════════════════════════════════════════════════════════
    # PHASE 4: SETTLEMENT AUDIT
    # ══════════════════════════════════════════════════════════
    log("\n==================================================")
    log("PHASE 4: SETTLEMENT AUDIT")
    log("==================================================")
    
    try:
        # finished matches logic: assume matches before today should be finished
        today_str = now.strftime("%Y-%m-%d")
        
        preds_no_settlement = conn.execute("SELECT COUNT(*) FROM prediction_log WHERE actual_outcome IS NULL AND match_date < ?", (today_str,)).fetchone()[0]
        picks_no_pnl = conn.execute("SELECT COUNT(*) FROM picks WHERE pnl_units IS NULL AND match_id IN (SELECT id FROM matches WHERE date < ?)", (today_str,)).fetchone()[0]
        fixtures_not_settled = conn.execute("SELECT COUNT(*) FROM matches WHERE status IN ('FT', 'AET', 'PEN') AND id NOT IN (SELECT match_id FROM match_history)").fetchone()[0]
        
        log(f"predictions without settlements: {preds_no_settlement}")
        log(f"picks without pnl: {picks_no_pnl}")
        log(f"fixtures finished but not settled: {fixtures_not_settled}")
        
        if preds_no_settlement == 0 and picks_no_pnl == 0 and fixtures_not_settled == 0:
            score_components.append(("Settlement", 100))
        else:
            score_components.append(("Settlement", 0))
    except Exception as e:
        log(f"Error checking settlement: {e}")
        score_components.append(("Settlement", 0))

    # ══════════════════════════════════════════════════════════
    # PHASE 5: ANALYTICS AUDIT
    # ══════════════════════════════════════════════════════════
    log("\n==================================================")
    log("PHASE 5: ANALYTICS AUDIT")
    log("==================================================")
    
    try:
        # Import endpoints to test directly
        from api.routers.analytics import get_warehouse_stats, get_roi_analytics, get_clv_analytics, get_calibration_analytics, get_leagues_analytics, get_markets_analytics, get_model_bias
        
        success = True
        endpoints = [
            ("/analytics/warehouse", get_warehouse_stats),
            ("/analytics/roi", get_roi_analytics),
            ("/analytics/clv", get_clv_analytics),
            ("/analytics/calibration", get_calibration_analytics),
            ("/analytics/leagues", get_leagues_analytics),
            ("/analytics/markets", get_markets_analytics),
            ("/analytics/model-bias", get_model_bias),
        ]
        
        for name, func in endpoints:
            try:
                func(conn=conn)
                log(f"{name} -> OK")
            except Exception as e:
                log(f"{name} -> ERROR: {e}")
                success = False
                
        if success:
            log("\nResult: PASS")
            score_components.append(("Analytics", 100))
        else:
            log("\nResult: FAIL")
            score_components.append(("Analytics", 0))
    except Exception as e:
        log(f"Error testing analytics: {e}")
        score_components.append(("Analytics", 0))

    # ══════════════════════════════════════════════════════════
    # PHASE 6: BIAS AUDIT
    # ══════════════════════════════════════════════════════════
    log("\n==================================================")
    log("PHASE 6: BIAS AUDIT")
    log("==================================================")
    
    try:
        # Home Bias
        h_stats = conn.execute("SELECT AVG(predicted_prob), AVG(actual_outcome * 100.0) FROM prediction_log WHERE market = 'Home Win' AND actual_outcome IS NOT NULL").fetchone()
        
        # Over 2.5 Bias
        o_stats = conn.execute("SELECT AVG(predicted_prob), AVG(actual_outcome * 100.0) FROM prediction_log WHERE market = 'Over 2.5 Goals' AND actual_outcome IS NOT NULL").fetchone()
        
        # Favorite Bias
        f_stats = conn.execute("SELECT AVG(model_prob), AVG(CASE WHEN result='W' THEN 100.0 ELSE 0.0 END) FROM picks WHERE implied_prob >= 50.0 AND result IS NOT NULL").fetchone()
        
        def print_bias(name, stats):
            pred = stats[0] or 0.0 if stats else 0.0
            act = stats[1] or 0.0 if stats else 0.0
            bias = pred - act
            log(f"{name} Bias:")
            log(f"  Predicted: {pred:.1f}%")
            log(f"  Actual: {act:.1f}%")
            log(f"  Bias: {'+' if bias > 0 else ''}{bias:.1f}%\n")
            
        print_bias("Home Win", h_stats)
        print_bias("Over 2.5 Goals", o_stats)
        print_bias("Favorite", f_stats)
        
    except Exception as e:
        log(f"Error calculating bias: {e}")

    # ══════════════════════════════════════════════════════════
    # PHASE 7: DATA INTEGRITY
    # ══════════════════════════════════════════════════════════
    log("\n==================================================")
    log("PHASE 7: DATA INTEGRITY")
    log("==================================================")
    
    try:
        null_outcome = conn.execute("SELECT COUNT(*) FROM prediction_log WHERE actual_outcome IS NULL AND match_date < ?", (now.strftime("%Y-%m-%d"),)).fetchone()[0]
        null_closing = conn.execute("SELECT COUNT(*) FROM picks WHERE closing_odds IS NULL").fetchone()[0]
        null_pnl = conn.execute("SELECT COUNT(*) FROM picks WHERE pnl_units IS NULL AND result IS NOT NULL").fetchone()[0]
        
        dup_fixtures = conn.execute("SELECT COUNT(*) FROM (SELECT match_id FROM match_history GROUP BY match_id HAVING COUNT(*) > 1)").fetchone()[0]
        dup_preds = conn.execute("SELECT COUNT(*) FROM (SELECT match_id, market_type FROM prediction_log GROUP BY match_id, market_type HAVING COUNT(*) > 1)").fetchone()[0]
        
        orphan_records = conn.execute("SELECT COUNT(*) FROM picks WHERE match_id NOT IN (SELECT id FROM matches)").fetchone()[0]
        
        log(f"NULL actual_outcome (past matches): {null_outcome}")
        log(f"NULL closing_odds: {null_closing}")
        log(f"NULL pnl_units (settled picks): {null_pnl}")
        log(f"duplicate fixtures: {dup_fixtures}")
        log(f"duplicate predictions: {dup_preds}")
        log(f"orphan records: {orphan_records}")
        
        total_errors = null_outcome + null_closing + null_pnl + dup_fixtures + dup_preds + orphan_records
        if total_errors == 0:
            score_components.append(("Data Integrity", 100))
        else:
            score_components.append(("Data Integrity", max(0, 100 - total_errors * 5)))
    except Exception as e:
        log(f"Error checking data integrity: {e}")
        score_components.append(("Data Integrity", 0))

    # ══════════════════════════════════════════════════════════
    # FINAL VERDICT
    # ══════════════════════════════════════════════════════════
    log("\n==================================================")
    log("FINAL VERDICT")
    log("==================================================")
    
    total_score = sum(s[1] for s in score_components) / len(score_components) if score_components else 0
    
    log("Category Scores:")
    for cat, score in score_components:
        log(f"- {cat}: {score}/100")
        
    log(f"\nFINAL SCORE: {total_score:.1f}/100")
    
    if total_score >= 90:
        log("\nRESULT: SYSTEM IS PRODUCTION READY")
    else:
        log("\nRESULT: DO NOT DEPLOY — SYSTEM FAILS PRODUCTION STANDARDS")


if __name__ == "__main__":
    run_audit()

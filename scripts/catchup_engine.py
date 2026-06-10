#!/usr/bin/env python3
"""
Catch-Up Engine — Infrastructure Recovery Plan

Repairs historical missing data by resolving predictions that have no actual_outcome.
Batches requests by date to save API-Football quota.
"""

import sys
import argparse
import logging
from datetime import datetime, timezone, date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db.database import get_db
from api.main import fetcher

# Setup Logging
log_file = PROJECT_ROOT / "logs" / "catchup_engine.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("catchup_engine")

def parse_args():
    parser = argparse.ArgumentParser(description="Catch-Up Engine for unresolved predictions.")
    parser.add_argument("--dry-run", action="store_true", help="Report stats without fetching or saving data.")
    parser.add_argument("--force", action="store_true", help="Recalculate PnL even if it already exists.")
    return parser.parse_args()

def run_catchup():
    args = parse_args()
    conn = get_db()
    
    logger.info("==================================================")
    logger.info(f"STARTING CATCH-UP ENGINE {'[DRY RUN]' if args.dry_run else ''}")
    logger.info("==================================================")
    
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # 1. Find all predictions with NULL actual_outcome (historical only)
    # We group by date to minimize API requests
    rows = conn.execute(
        """SELECT match_date, COUNT(*) as missing_count, COUNT(DISTINCT match_id) as matches
           FROM prediction_log
           WHERE actual_outcome IS NULL AND match_date < ?
           GROUP BY match_date
           ORDER BY match_date DESC""",
        (today_str,)
    ).fetchall()
    
    total_unresolved = sum(row[1] for row in rows)
    affected_dates = [row[0] for row in rows]
    estimated_requests = len(affected_dates)
    # API-football charges 1 request per endpoint call. Fetching fixtures by date = 1 request.
    estimated_quota = estimated_requests 
    
    if args.dry_run:
        logger.info("DRY RUN REPORT:")
        logger.info(f"Unresolved predictions: {total_unresolved}")
        logger.info(f"Affected dates: {len(affected_dates)}")
        if len(affected_dates) > 0:
            logger.info(f"Date range: {affected_dates[-1]} to {affected_dates[0]}")
        logger.info(f"Estimated provider requests: {estimated_requests}")
        logger.info(f"Estimated quota usage: {estimated_quota}")
        logger.info("\nRun without --dry-run to execute catch-up.")
        return

    if total_unresolved == 0:
        logger.info("No unresolved historical predictions found. System is fully caught up.")
        return
        
    logger.info(f"Found {total_unresolved} unresolved predictions across {len(affected_dates)} dates.")
    
    settled_matches = 0
    settled_predictions = 0
    settled_picks = 0
    
    # 2. Process batch by date
    for match_date in affected_dates:
        logger.info(f"Fetching historical results for {match_date}...")
        
        try:
            fixtures = fetcher.fetch_fixtures(match_date)
        except Exception as e:
            err_str = str(e).lower()
            if "quota" in err_str or "limit" in err_str:
                logger.error("CRITICAL: API-Football quota exceeded. Stopping catch-up immediately.")
                break
            logger.error(f"Failed to fetch data for {match_date}: {e}")
            continue
            
        # Build dictionary of finished matches for this date
        finished_map = {}
        for fix in fixtures:
            status = fix.get("fixture", {}).get("status", {}).get("short", "")
            if status in ("FT", "AET", "PEN"):
                fix_id = str(fix["fixture"]["id"])
                finished_map[fix_id] = fix
                
        # Get pending predictions for this date
        pending_preds = conn.execute(
            """SELECT id, match_id, market, home_team, away_team
               FROM prediction_log
               WHERE actual_outcome IS NULL AND match_date = ?""",
            (match_date,)
        ).fetchall()
        
        for pred in pending_preds:
            log_id, match_id, market, home, away = pred
            
            if match_id not in finished_map:
                continue
                
            fix = finished_map[match_id]
            home_goals = fix.get("goals", {}).get("home")
            away_goals = fix.get("goals", {}).get("away")
            league_name = fix.get("league", {}).get("name", "Unknown")
            
            if home_goals is None or away_goals is None:
                continue
                
            # 3. Insert into match history (idempotent)
            conn.execute(
                """INSERT OR IGNORE INTO match_history 
                   (match_id, match_date, league, home_team, away_team, home_goals, away_goals)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (match_id, match_date, league_name, home, away, home_goals, away_goals)
            )
            
            # 4. Determine actual outcome
            total_goals = home_goals + away_goals
            outcome_map = {
                "Home Win": 1 if home_goals > away_goals else 0,
                "Draw": 1 if home_goals == away_goals else 0,
                "Away Win": 1 if home_goals < away_goals else 0,
                "Over 1.5 Goals": 1 if total_goals > 1.5 else 0,
                "Over 2.5 Goals": 1 if total_goals > 2.5 else 0,
                "Over 3.5 Goals": 1 if total_goals > 3.5 else 0,
                "Under 2.5 Goals": 1 if total_goals < 2.5 else 0,
                "Under 3.5 Goals": 1 if total_goals < 3.5 else 0,
                "BTTS - Yes": 1 if home_goals > 0 and away_goals > 0 else 0,
                "BTTS - No": 1 if home_goals == 0 or away_goals == 0 else 0,
            }
            
            actual_outcome = outcome_map.get(market)
            if actual_outcome is None:
                continue
                
            # Update prediction_log
            conn.execute(
                "UPDATE prediction_log SET actual_outcome = ? WHERE id = ?",
                (actual_outcome, log_id)
            )
            settled_predictions += 1
            
            # 5. Settle picks
            # Fetch picks that match this match_id and market
            # Idempotency check: only update if result IS NULL, unless --force
            where_clause = "WHERE match_id = ? AND market = ?"
            if not args.force:
                where_clause += " AND result IS NULL"
                
            picks = conn.execute(
                f"SELECT id, odds_at_pick FROM picks {where_clause}",
                (match_id, market)
            ).fetchall()
            
            result_str = 'W' if actual_outcome == 1 else 'L'
            
            for p in picks:
                pick_id = p[0]
                odds = p[1] or 2.0
                pnl = (odds - 1.0) if actual_outcome == 1 else -1.0
                
                conn.execute(
                    "UPDATE picks SET result = ?, pnl_units = ? WHERE id = ?",
                    (result_str, pnl, pick_id)
                )
                settled_picks += 1
                
        conn.commit()
        logger.info(f"Date {match_date} complete: {settled_predictions} predictions settled.")
        
    logger.info("==================================================")
    logger.info("CATCH-UP COMPLETE")
    logger.info(f"Predictions settled: {settled_predictions}")
    logger.info(f"Picks settled: {settled_picks}")
    logger.info("==================================================")

if __name__ == "__main__":
    run_catchup()

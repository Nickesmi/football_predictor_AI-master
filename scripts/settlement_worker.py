#!/usr/bin/env python3
"""
Settlement Worker — SofaScore First Architecture
Implements Double Confirmation: LIVE -> FT -> wait 60s -> check again -> settle.
"""

import sys
import time
import logging
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import logger
from src.db.database import get_db
from src.data.sofascore_provider import fetch_daily_matches

def run_settlement():
    logger.info("==================================================")
    logger.info("STARTING AUTOMATED SETTLEMENT (DB Double Confirm)")
    logger.info("==================================================")
    
    conn = get_db()
    cursor = conn.cursor()
    
    today = date.today()
    yesterday = today - timedelta(days=1)
    dates_to_check = [yesterday.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")]
    
    finished_fixtures = []
    
    for d in dates_to_check:
        matches, err = fetch_daily_matches(d)
        if err:
            logger.error(f"Settlement fetch failed for {d}: {err}")
            continue
            
        for fix in matches:
            if fix.get("status") == "FT":
                finished_fixtures.append(fix)
                
    now_ts = datetime.now(timezone.utc).timestamp()
    settled_count = 0
    
    for fix in finished_fixtures:
        event_id = fix["event_id"]
        home_goals = fix["home_score"]
        away_goals = fix["away_score"]
        
        if home_goals is None or away_goals is None:
            continue
            
        # Check pending_settlements
        cursor.execute("SELECT first_ft_seen FROM pending_settlements WHERE event_id = ?", (event_id,))
        row = cursor.fetchone()
        
        if not row:
            # First time seeing it as FT
            logger.info(f"Match {event_id} reached FT. Added to pending_settlements for 60s confirmation.")
            cursor.execute("""
                INSERT INTO pending_settlements (event_id, first_ft_seen, provider)
                VALUES (?, ?, ?)
            """, (event_id, datetime.now(timezone.utc).isoformat(), fix["provider"]))
            conn.commit()
            continue
            
        # Already pending
        first_ft_seen = datetime.fromisoformat(row[0]).timestamp()
        if (now_ts - first_ft_seen) >= 60:
            # Double confirmed!
            logger.info(f"Match {event_id} DOUBLE CONFIRMED FT. Settling now.")
            
            # 1. match_history
            cursor.execute("""
                INSERT OR IGNORE INTO match_history 
                (match_id, match_date, league, home_team, away_team, home_goals, away_goals)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (event_id, fix["date"], fix["league"], fix["home_team"], fix["away_team"], home_goals, away_goals))
            
            # 2. prediction_log
            total_goals = home_goals + away_goals
            outcomes = {
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
            
            for market, outcome in outcomes.items():
                cursor.execute("""
                    UPDATE prediction_log 
                    SET actual_outcome = ?
                    WHERE match_id = ? AND market = ? AND actual_outcome IS NULL
                """, (outcome, event_id, market))
                
                # 3. picks
                result_str = 'W' if outcome == 1 else 'L'
                picks = cursor.execute(
                    "SELECT id, odds_at_pick FROM picks WHERE match_id = ? AND market = ? AND result IS NULL",
                    (event_id, market)
                ).fetchall()
                
                for p in picks:
                    pick_id = p[0]
                    odds = p[1] or 2.0
                    pnl = (odds - 1.0) if outcome == 1 else -1.0
                    cursor.execute("""
                        UPDATE picks SET result = ?, pnl_units = ? WHERE id = ?
                    """, (result_str, pnl, pick_id))
            
            # Remove from pending
            cursor.execute("DELETE FROM pending_settlements WHERE event_id = ?", (event_id,))
            conn.commit()
            settled_count += 1
            
    logger.info("==================================================")
    logger.info(f"SETTLEMENT COMPLETE: {settled_count} matches double-confirmed and settled.")
    logger.info("==================================================")

if __name__ == "__main__":
    while True:
        try:
            run_settlement()
        except Exception as e:
            logger.error(f"Settlement worker crashed: {e}")
        time.sleep(300) # Run every 5 minutes

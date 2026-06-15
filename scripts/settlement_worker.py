#!/usr/bin/env python3
"""
Settlement Worker — SofaScore First Architecture
Implements Double Confirmation: LIVE -> FT -> wait 60s -> fetch_match(event_id) -> settle.
With robust retry mechanics.
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
from src.data.sofascore_provider import fetch_daily_matches, fetch_match, normalize_event

def run_settlement():
    logger.info("==================================================")
    logger.info("STARTING AUTOMATED SETTLEMENT (DB Double Confirm)")
    logger.info("==================================================")
    
    conn = get_db()
    cursor = conn.cursor()
    
    today = date.today()
    yesterday = today - timedelta(days=1)
    dates_to_check = [yesterday.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")]
    
    # 1. Detect new FT matches
    for d in dates_to_check:
        matches, err = fetch_daily_matches(d)
        if err:
            continue
            
        for fix in matches:
            if fix.get("status") == "FT":
                event_id = fix["event_id"]
                cursor.execute("SELECT first_ft_seen FROM pending_settlements WHERE event_id = ?", (event_id,))
                if not cursor.fetchone():
                    logger.info(f"Match {event_id} reached FT. Added to pending_settlements for 60s confirmation.")
                    cursor.execute("""
                        INSERT INTO pending_settlements (event_id, first_ft_seen, provider)
                        VALUES (?, ?, ?)
                    """, (event_id, datetime.now(timezone.utc).isoformat(), fix["provider"]))
                    conn.commit()
                
    now_ts = datetime.now(timezone.utc).timestamp()
    settled_count = 0
    
    # 2. Process pending settlements using direct fetch_match
    cursor.execute("SELECT event_id, first_ft_seen, attempts, last_check FROM pending_settlements")
    pending = cursor.fetchall()
    
    for row in pending:
        event_id = row[0]
        first_ft_seen = datetime.fromisoformat(row[1]).timestamp()
        attempts = row[2] or 0
        last_check_str = row[3]
        
        # Don't check if we haven't waited 60s from first seeing it
        if (now_ts - first_ft_seen) < 60:
            continue
            
        # If we failed before, don't spam. Wait 60s between retries.
        if last_check_str:
            last_check = datetime.fromisoformat(last_check_str).timestamp()
            if (now_ts - last_check) < 60:
                continue

        if attempts >= 5:
            logger.error(f"Match {event_id} failed settlement 5 times. Keeping pending but ignoring until manual intervention.")
            cursor.execute("""
                INSERT INTO provider_health_log (provider, endpoint, success, latency_ms, error_message)
                VALUES ('sofascore', 'settlement_retry_failure', 0, 0, ?)
            """, (f"CRITICAL: Match {event_id} failed settlement 5 times",))
            conn.commit()
            continue
            
        # Double confirm using fetch_match directly
        raw_match, err = fetch_match(event_id)
        if err or not raw_match:
            logger.warning(f"Failed to double-confirm {event_id}: {err}")
            cursor.execute("""
                UPDATE pending_settlements SET attempts = attempts + 1, last_check = ? WHERE event_id = ?
            """, (datetime.now(timezone.utc).isoformat(), event_id))
            conn.commit()
            continue
            
        fix = normalize_event(raw_match)
        if fix.get("status") != "FT":
            logger.warning(f"Match {event_id} is no longer FT! Status reverted to {fix.get('status')}. Deleting pending.")
            cursor.execute("DELETE FROM pending_settlements WHERE event_id = ?", (event_id,))
            conn.commit()
            continue
            
        home_goals = fix["home_score"]
        away_goals = fix["away_score"]
        
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

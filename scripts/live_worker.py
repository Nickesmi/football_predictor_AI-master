#!/usr/bin/env python3
"""
Live Worker — SofaScore First Architecture
Polls live football match states and updates the matches table.
"""

import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import logger
from src.db.database import get_db
from src.data.sofascore_provider import fetch_live_matches

def get_sleep_interval(conn) -> int:
    cursor = conn.cursor()
    # Check statuses of matches happening today
    cursor.execute("SELECT status FROM matches WHERE date = date('now') OR status IN ('LIVE', 'HT')")
    statuses = [r[0] for r in cursor.fetchall()]
    
    if "LIVE" in statuses:
        return 15
    if "HT" in statuses:
        return 30
    if "NS" in statuses:
        return 300 # 5 min
    return 900 # 15 min

def run_live_loop():
    logger.info("Starting Football Live Worker (SofaScore)")
    conn = get_db()
    
    while True:
        try:
            live_matches, err = fetch_live_matches()
            cursor = conn.cursor()
            
            now_ts = datetime.now(timezone.utc).timestamp()
            
            if err:
                logger.warning(f"SofaScore Live Error: {err}")
                # Mark active matches as STALE
                cursor.execute("""
                    UPDATE matches 
                    SET is_stale = 1, status = 'STALE', provider_error = ? 
                    WHERE status IN ('LIVE', 'HT')
                """, (err,))
                conn.commit()
            else:
                # Update matches
                updated_ids = set()
                for m in live_matches:
                    event_id = m["event_id"]
                    updated_ids.add(event_id)
                    cursor.execute("""
                        UPDATE matches SET
                            status = ?,
                            home_goals = ?,
                            away_goals = ?,
                            last_live_update = ?,
                            is_stale = 0,
                            provider_error = NULL
                        WHERE id = ?
                    """, (
                        m["status"], m["home_score"], m["away_score"], 
                        m["last_live_update"], event_id
                    ))
                
                # Check for matches that were LIVE but are now missing
                # and haven't been updated in 120 seconds -> STALE
                cursor.execute("SELECT id, last_live_update FROM matches WHERE status IN ('LIVE', 'HT')")
                for row in cursor.fetchall():
                    mid = row[0]
                    last_str = row[1]
                    if mid not in updated_ids and last_str:
                        last_dt = datetime.fromisoformat(last_str)
                        if (now_ts - last_dt.timestamp()) > 120:
                            cursor.execute("""
                                UPDATE matches 
                                SET is_stale = 1, status = 'STALE', provider_error = 'no_fresh_update'
                                WHERE id = ?
                            """, (mid,))
                conn.commit()
                
        except Exception as e:
            logger.error(f"Live worker crash: {e}")
            
        sleep_sec = get_sleep_interval(conn)
        time.sleep(sleep_sec)

if __name__ == "__main__":
    run_live_loop()

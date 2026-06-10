#!/usr/bin/env python3
"""
Odds Worker — Post-BILQE Data Warehouse Expansion

Automated odds collection script.
To be scheduled via cron (e.g., every hour or every 4 hours).
Captures:
- 4-hourly snapshots
- Kickoff -1h pre-match odds
- Kickoff closing odds
"""

import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import logger
from src.db.database import get_db
from src.db.match_repo import get_matches_by_date
from src.data.odds_fetcher import TheOddsAPIProvider, LEAGUE_TO_SPORT

def run_odds_collection():
    logger.info("==================================================")
    logger.info("STARTING AUTOMATED ODDS COLLECTION")
    logger.info("==================================================")
    
    conn = get_db()
    provider = TheOddsAPIProvider(conn)
    
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    matches = get_matches_by_date(conn, today_str)
    
    # Filter upcoming matches
    upcoming = [m for m in matches if m.get("status") in ("NS", "TBD")]
    logger.info(f"Found {len(upcoming)} upcoming matches today")
    
    now = datetime.now(timezone.utc)
    
    success_count = 0
    for match in upcoming:
        home_team = match["home_team"]
        away_team = match["away_team"]
        league_id = match.get("league_id")
        kickoff_str = match.get("kickoff")
        
        sport_key = LEAGUE_TO_SPORT.get(league_id)
        if not sport_key:
            continue
            
        # Determine if we should scan based on kickoff time
        should_scan = False
        scan_reason = "4-hourly snapshot"
        
        if kickoff_str:
            try:
                # Assuming kickoff is stored in ISO format
                kickoff = datetime.fromisoformat(kickoff_str)
                if kickoff.tzinfo is None:
                    kickoff = kickoff.replace(tzinfo=timezone.utc)
                
                time_to_kickoff = (kickoff - now).total_seconds() / 3600.0
                
                if 0 <= time_to_kickoff <= 0.25:
                    should_scan = True
                    scan_reason = "Closing odds (kickoff)"
                elif 0.75 <= time_to_kickoff <= 1.25:
                    should_scan = True
                    scan_reason = "Pre-match odds (kickoff -1h)"
                else:
                    # Every 4 hours roughly. This script should be run by cron.
                    # We will scan if it hasn't been scanned recently.
                    # (Simple proxy: we just scan if it's a generic run)
                    should_scan = True
            except Exception:
                should_scan = True # Fallback if time parsing fails
        else:
            should_scan = True
            
        if not should_scan:
            continue
            
        try:
            logger.info(f"Fetching odds for {home_team} vs {away_team} [{scan_reason}]")
            # This automatically inserts into `odds_snapshots`
            provider.get_normalized_odds_for_match(sport_key, home_team, away_team)
            success_count += 1
            # Sleep to respect rate limits
            time.sleep(1.0)
            
        except Exception as e:
            logger.error(f"Failed to fetch odds for {home_team} vs {away_team}: {e}")
            
    logger.info("==================================================")
    logger.info(f"ODDS COLLECTION COMPLETE: {success_count} matches scanned")
    logger.info("==================================================")

if __name__ == "__main__":
    run_odds_collection()

#!/usr/bin/env python3
"""
Settlement Worker — Post-BILQE Data Warehouse Expansion

Runs periodically to:
1. Fetch fixtures from today and yesterday.
2. Identify finished matches (FT/AET/PEN).
3. Ingest results into match_history.
4. Settle all associated predictions in prediction_log and picks.
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
from api.main import fetcher, TOP_LEAGUES

def run_settlement():
    logger.info("==================================================")
    logger.info("STARTING AUTOMATED SETTLEMENT")
    logger.info("==================================================")
    
    conn = get_db()
    
    today = date.today()
    yesterday = today - timedelta(days=1)
    dates_to_check = [yesterday.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")]
    
    finished_fixtures = []
    
    for d in dates_to_check:
        logger.info(f"Fetching fixtures for {d}")
        try:
            fixtures = fetcher.fetch_fixtures(d)
            for fix in fixtures:
                status = fix.get("fixture", {}).get("status", {}).get("short", "")
                if status in ("FT", "AET", "PEN"):
                    finished_fixtures.append(fix)
        except Exception as e:
            logger.error(f"Failed to fetch fixtures for {d}: {e}")
            
    logger.info(f"Found {len(finished_fixtures)} finished fixtures across {dates_to_check}")
    
    settled_matches = 0
    settled_picks = 0
    
    for fix in finished_fixtures:
        fixture_id = str(fix["fixture"]["id"])
        match_date = fix["fixture"]["date"][:10]
        home_team = fix["teams"]["home"]["name"]
        away_team = fix["teams"]["away"]["name"]
        league_id = fix["league"]["id"]
        league_name = TOP_LEAGUES.get(league_id, fix["league"]["name"])
        
        home_goals = fix.get("goals", {}).get("home")
        away_goals = fix.get("goals", {}).get("away")
        
        if home_goals is None or away_goals is None:
            continue
            
        try:
            # 1. Update Match History
            # We insert/update the match_history table
            conn.execute(
                """INSERT OR IGNORE INTO match_history 
                   (match_id, match_date, league, home_team, away_team, home_goals, away_goals)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (fixture_id, match_date, league_name, home_team, away_team, home_goals, away_goals)
            )
            
            # 2. Settle prediction_log
            # We need to map actual goals to outcomes
            # Home Win, Draw, Away Win, BTTS, Over 2.5 etc.
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
                conn.execute(
                    """UPDATE prediction_log 
                       SET actual_outcome = ?
                       WHERE match_id = ? AND market = ? AND actual_outcome IS NULL""",
                    (outcome, fixture_id, market)
                )
                
            # 3. Settle picks table
            for market, outcome in outcomes.items():
                # If hit -> W, miss -> L
                result_str = 'W' if outcome == 1 else 'L'
                
                # Fetch pending picks for this match/market
                picks = conn.execute(
                    "SELECT id, odds_at_pick FROM picks WHERE match_id = ? AND market = ? AND result IS NULL",
                    (fixture_id, market)
                ).fetchall()
                
                for p in picks:
                    pick_id = p[0]
                    odds = p[1] or 2.0
                    pnl = (odds - 1.0) if outcome == 1 else -1.0
                    
                    conn.execute(
                        """UPDATE picks 
                           SET result = ?, pnl_units = ?
                           WHERE id = ?""",
                        (result_str, pnl, pick_id)
                    )
                    settled_picks += 1
                    
            conn.commit()
            settled_matches += 1
            
        except Exception as e:
            logger.error(f"Failed to settle match {fixture_id}: {e}")
            
    logger.info("==================================================")
    logger.info(f"SETTLEMENT COMPLETE: {settled_matches} matches, {settled_picks} picks settled.")
    logger.info("==================================================")

if __name__ == "__main__":
    run_settlement()

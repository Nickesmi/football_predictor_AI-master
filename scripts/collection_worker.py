#!/usr/bin/env python3
"""
Collection Worker — Post-BILQE Data Warehouse Expansion

Runs daily at 00:00 to fetch fixtures, generate predictions, and store them in the prediction_log.
"""

import sys
import time
import logging
from datetime import datetime, date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import logger
from src.db.database import get_db
from api.main import fetcher, _compute_match_analysis, TOP_LEAGUES

def run_collection():
    logger.info("==================================================")
    logger.info("STARTING AUTOMATED PREDICTION COLLECTION")
    logger.info("==================================================")
    
    conn = get_db()
    today = date.today().strftime("%Y-%m-%d")
    
    try:
        fixtures = fetcher.fetch_fixtures(today)
    except Exception as e:
        logger.error(f"Failed to fetch fixtures: {e}")
        return
        
    logger.info(f"Fetched {len(fixtures)} fixtures for {today}")
    
    # Filter for tracked leagues to save quota and compute
    target_fixtures = [f for f in fixtures if f.get("league", {}).get("id") in TOP_LEAGUES]
    logger.info(f"Found {len(target_fixtures)} target fixtures in tracked leagues")
    
    success_count = 0
    for fix in target_fixtures:
        fixture_id = str(fix["fixture"]["id"])
        home = fix["teams"]["home"]["name"]
        away = fix["teams"]["away"]["name"]
        league_id = fix["league"]["id"]
        league_name = TOP_LEAGUES.get(league_id, fix["league"]["name"])
        
        try:
            logger.info(f"Generating prediction for {home} vs {away}...")
            # Compute match analysis
            analysis = _compute_match_analysis(home, away, league_name)
            
            # The _compute_match_analysis function internally evaluates picks 
            # via execution_engine, which logs them to `prediction_log` 
            # AND saves qualified executable picks to the `picks` table.
            
            success_count += 1
            # Sleep to respect rate limits if needed
            time.sleep(1.5)
            
        except Exception as e:
            logger.error(f"Failed analysis for {fixture_id} ({home} vs {away}): {e}")
            
    logger.info("==================================================")
    logger.info(f"COLLECTION COMPLETE: {success_count}/{len(target_fixtures)} succeeded")
    logger.info("==================================================")

if __name__ == "__main__":
    run_collection()

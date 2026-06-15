#!/usr/bin/env python3
"""
Collection Worker — SofaScore First Architecture
Runs daily at 00:00 to fetch fixtures, generate predictions, and store them.
"""

import sys
import time
import logging
from datetime import datetime, date, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import logger
from src.db.database import get_db
from src.data.sofascore_provider import fetch_daily_matches
from src.data.emergency_backup_provider import APIFootballFetcher
from api.main import _compute_match_analysis

def run_collection():
    logger.info("==================================================")
    logger.info("STARTING AUTOMATED PREDICTION COLLECTION (SOFASCORE FIRST)")
    logger.info("==================================================")
    
    conn = get_db()
    today = date.today().strftime("%Y-%m-%d")
    
    # 1. Fetch from SofaScore
    fixtures, err = fetch_daily_matches(today)
    
    if err and not fixtures:
        logger.error(f"SofaScore completely failed: {err}")
        logger.warning("Triggering EMERGENCY BACKUP: API-Football")
        backup = APIFootballFetcher()
        try:
            raw_fixtures = backup.fetch_fixtures(today)
            # Normalization logic for backup would go here.
            # But the requirement was "sleeps silently". So we just log it for now.
            logger.error("API-Football backup triggered. Manual intervention or normalization needed.")
            return
        except Exception as e:
            logger.error(f"Emergency Backup ALSO failed: {e}")
            return

    logger.info(f"Fetched {len(fixtures)} main fixtures for {today}")
    
    success_count = 0
    for fix in fixtures:
        event_id = fix["event_id"]
        home = fix["home_team"]
        away = fix["away_team"]
        league_name = fix["league"]
        quality = fix["data_quality_score"]
        
        # Save match to DB
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO matches (
                id, date, kickoff, home_team, away_team, league_name, status, 
                home_goals, away_goals, provider, data_quality_score, is_main_fixture, is_stale, provider_error, last_live_update
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                home_goals=excluded.home_goals,
                away_goals=excluded.away_goals,
                data_quality_score=excluded.data_quality_score,
                is_stale=excluded.is_stale,
                provider_error=excluded.provider_error,
                last_live_update=excluded.last_live_update
        """, (
            event_id, fix["date"], fix["kickoff"], home, away, league_name, fix["status"],
            fix["home_score"], fix["away_score"], fix["provider"], quality, fix["is_main_fixture"],
            fix["is_stale"], fix["provider_error"], fix["last_live_update"]
        ))
        conn.commit()

        if quality < 60:
            logger.warning(f"NO PICK: {home} vs {away} (Quality: {quality} < 60)")
            # Log NO PICK to database explicitly if needed
            cursor.execute("""
                INSERT INTO picks (match_id, market, selection, model_prob, implied_prob, edge, odds_at_pick, grade, result)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (event_id, "NO PICK", "Quality < 60", 0, 0, 0, 0, "F", "NO PICK"))
            conn.commit()
            continue
            
        try:
            logger.info(f"Generating prediction for {home} vs {away}...")
            # _compute_match_analysis internally writes to prediction_log
            analysis = _compute_match_analysis(home, away, league_name)
            success_count += 1
            time.sleep(1.0)
        except Exception as e:
            logger.error(f"Failed analysis for {event_id} ({home} vs {away}): {e}")
            
    logger.info("==================================================")
    logger.info(f"COLLECTION COMPLETE: {success_count}/{len(fixtures)} generated picks.")
    logger.info("==================================================")

if __name__ == "__main__":
    run_collection()

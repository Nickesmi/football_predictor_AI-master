#!/usr/bin/env python3
"""
tennis_catchup_engine.py
=========================
Catch-Up Engine for Tennis matches.
Fetches all recent active/finished matches from the provider and settles them,
writing them to tennis_results.
"""

import sys
import logging
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db.database import get_db
from src.tennis.db.tennis_schema import init_tennis_db
from src.tennis.data.tennis_provider import _fetch_espn_matches
from scripts.tennis_settlement_worker import _settle_match, run_settlement

# Setup Logging
log_file = PROJECT_ROOT / "logs" / "tennis_catchup_engine.log"
# Ensure logs dir exists
log_file.parent.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("tennis_catchup_engine")

def run_catchup():
    logger.info("==================================================")
    logger.info("STARTING TENNIS CATCH-UP ENGINE")
    logger.info("==================================================")

    conn = get_db()
    init_tennis_db(conn)

    # 1. Fetch recent matches from ESPN scoreboard
    logger.info("Fetching recent tournament matches from ESPN...")
    matches, err, latency = _fetch_espn_matches()

    if err:
        logger.error(f"Failed to fetch tennis matches: {err}")
        return

    logger.info(f"Fetched {len(matches)} matches in {latency}ms.")

    # 2. Filter for FT matches and update/insert them into tennis_matches
    ft_matches = [m for m in matches if m.get("status") == "FT"]
    logger.info(f"Found {len(ft_matches)} Finished (FT) matches.")

    new_ft = 0
    for m in ft_matches:
        try:
            # We insert or ignore to make sure they exist
            conn.execute(
                """
                INSERT OR IGNORE INTO tennis_matches
                  (match_id, date, start_time, tournament, surface,
                   player_1, player_2, rank_1, rank_2, status,
                   sets_1, sets_2, games_1, games_2, provider,
                   is_stale, last_live_update)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    m["match_id"], m.get("date"), m.get("start_time"),
                    m.get("tournament"), m.get("surface"),
                    m["player_1"], m["player_2"],
                    m.get("rank_1"), m.get("rank_2"),
                    m.get("status", "NS"),
                    m.get("sets_1", 0), m.get("sets_2", 0),
                    m.get("games_1", 0), m.get("games_2", 0),
                    m.get("provider"), 0, m.get("last_live_update"),
                )
            )
            # Update status to FT in case they already existed but were LIVE/NS
            conn.execute(
                """
                UPDATE tennis_matches
                SET status = 'FT', sets_1 = ?, sets_2 = ?, games_1 = ?, games_2 = ?, updated_at = datetime('now')
                WHERE match_id = ?
                """,
                (m.get("sets_1", 0), m.get("sets_2", 0), m.get("games_1", 0), m.get("games_2", 0), m["match_id"])
            )
        except Exception as exc:
            logger.warning(f"Failed to store FT match {m.get('match_id')}: {exc}")
    conn.commit()

    # 3. Settle all outstanding FT matches
    logger.info("Running settlement logic...")
    settled_count = run_settlement(conn)

    logger.info("==================================================")
    logger.info("TENNIS CATCH-UP COMPLETE")
    logger.info(f"Matches settled: {settled_count}")
    logger.info("==================================================")

if __name__ == "__main__":
    run_catchup()

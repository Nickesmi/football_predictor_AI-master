#!/usr/bin/env python3
"""
tennis_collection_worker.py
============================
Fetches daily tennis fixtures, generates predictions, and stores them.
Runs once per day (or manually triggered).

Settlement: NEVER uses live provider. Daily refresh is the ONLY settlement source.
"""

import sys
import os
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Project root on path ──────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("tennis_collection_worker")

from src.db.database import get_db
from src.tennis.db.tennis_schema import init_tennis_db
from src.tennis.data.tennis_provider import fetch_daily_matches
from src.tennis.engine.tennis_prediction_engine import predict_batch


def _store_matches(conn, matches: list[dict]) -> int:
    """Store matches to DB, return count of newly inserted."""
    count = 0
    for m in matches:
        try:
            cursor = conn.execute(
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
            if cursor.rowcount > 0:
                count += 1
        except Exception as exc:
            logger.warning(f"Failed to store match {m.get('match_id')}: {exc}")
    conn.commit()
    return count


def _log_provider_health(conn, success: bool, latency_ms: int, match_count: int, error: str = None):
    try:
        conn.execute(
            """
            INSERT INTO provider_health_log
              (provider, endpoint, success, latency_ms, fixture_count, error_message)
            VALUES ('rapidapi_tennis', '/matches/daily', ?, ?, ?, ?)
            """,
            (1 if success else 0, latency_ms, match_count, error)
        )
        conn.commit()
    except Exception:
        pass


def run_collection(date_str: str = None):
    """Main collection loop for one day."""
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    logger.info(f"[TENNIS COLLECTION] Starting collection for {date_str}")
    conn = get_db()
    init_tennis_db(conn)

    # ── Fetch daily fixtures ──────────────────────────────────────────────────
    matches, err, latency = fetch_daily_matches(date_str)
    _log_provider_health(conn, err is None, latency, len(matches), err)

    if err:
        logger.error(f"[TENNIS COLLECTION] Provider failed: {err}")
        return

    if not matches:
        logger.info(f"[TENNIS COLLECTION] No matches returned for {date_str}")
        return

    # ── Store to DB ───────────────────────────────────────────────────────────
    inserted = _store_matches(conn, matches)
    logger.info(f"[TENNIS COLLECTION] Stored {inserted}/{len(matches)} new matches")

    # ── Generate predictions ──────────────────────────────────────────────────
    # Only predict NS matches (not yet started)
    ns_matches = [m for m in matches if m.get("status") == "NS"]
    predictions = predict_batch(conn, ns_matches)
    logger.info(f"[TENNIS COLLECTION] {len(predictions)} predictions generated")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="Date in YYYY-MM-DD format")
    args = parser.parse_args()

    run_collection(args.date)

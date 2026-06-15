#!/usr/bin/env python3
"""
tennis_live_worker.py
=====================
Polls live tennis match states and updates the tennis_matches table.
Live data is used for UI display ONLY.

GOVERNANCE RULE:
  Live provider data is NEVER used for settlement.
  Settlement happens only via tennis_settlement_worker.py (daily refresh).
"""

import sys
import os
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("tennis_live_worker")

POLL_INTERVAL_SECONDS = 30


def _get_adaptive_poll_interval(conn, date_str: str) -> int:
    """Calculate adaptive poll interval based on today's match statuses."""
    try:
        rows = conn.execute(
            "SELECT status FROM tennis_matches WHERE date=?",
            (date_str,)
        ).fetchall()
        
        if not rows:
            return 900  # No matches today, sleep 15 mins
            
        statuses = [r[0].upper() for r in rows if r[0]]
        has_live = any(s == 'LIVE' or s.startswith('SET') or s.isdigit() for s in statuses)
        has_ns = any(s in ('NS', 'TBD', 'DELAYED') for s in statuses)
        
        if has_live:
            return 15
        if has_ns:
            return 300  # 5 minutes
        return 900  # All finished, check every 15 mins
    except Exception:
        return 30


def _log_health(conn, success: bool, latency_ms: int, match_count: int, error: str = None):
    try:
        conn.execute(
            """
            INSERT INTO provider_health_log
              (provider, endpoint, success, latency_ms, fixture_count, error_message)
            VALUES ('rapidapi_tennis_live', '/matches/live', ?, ?, ?, ?)
            """,
            (1 if success else 0, latency_ms, match_count, error)
        )
        conn.commit()
    except Exception:
        pass


def run_once(conn, date_str: str):
    """Fetch live matches and update UI state. Never touches settlement."""
    from src.tennis.data.tennis_provider import fetch_live_matches

    live_matches, err, latency = fetch_live_matches()
    _log_health(conn, err is None, latency, len(live_matches), err)

    if err:
        logger.warning(f"[TENNIS LIVE] Provider error: {err} — marking STALE")
        conn.execute(
            "UPDATE tennis_matches SET is_stale=1 WHERE date=? AND status='LIVE'",
            (date_str,)
        )
        conn.commit()
        return

    # ── Apply live overlay to DB (status, score, last_live_update only) ───────
    updated = 0
    for m in live_matches:
        mid = m.get("match_id")
        if not mid:
            continue
        try:
            conn.execute(
                """
                UPDATE tennis_matches
                SET status=?, sets_1=?, sets_2=?, games_1=?, games_2=?,
                    last_live_update=?, is_stale=0
                WHERE match_id=?
                """,
                (
                    m.get("status", "LIVE"),
                    m.get("sets_1", 0), m.get("sets_2", 0),
                    m.get("games_1", 0), m.get("games_2", 0),
                    m.get("last_live_update"),
                    mid,
                )
            )
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                updated += 1
        except Exception as exc:
            logger.warning(f"[TENNIS LIVE] Failed update for {mid}: {exc}")

    conn.commit()
    logger.info(f"[TENNIS LIVE] {updated}/{len(live_matches)} matches updated")


def main():
    from src.db.database import get_db
    from src.tennis.db.tennis_schema import init_tennis_db

    logger.info("[TENNIS LIVE WORKER] Starting adaptive polling")
    conn = get_db()
    init_tennis_db(conn)

    while True:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            run_once(conn, date_str)
        except Exception as exc:
            logger.error(f"[TENNIS LIVE] Unexpected error: {exc}", exc_info=True)
            
        sleep_sec = _get_adaptive_poll_interval(conn, date_str)
        time.sleep(sleep_sec)


if __name__ == "__main__":
    main()

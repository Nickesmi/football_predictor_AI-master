#!/usr/bin/env python3
"""
tennis_settlement_worker.py
============================
Settles finished tennis matches by reading ONLY from the daily official refresh.

GOVERNANCE RULE (user-approved):
  RapidAPI SofaScore → LIVE UI ONLY
  ════════════════════════════════
  Daily Official Refresh → FT status → Settlement

This worker NEVER uses live provider data for settlement.
It reads from tennis_matches (populated by daily refresh via collection worker),
confirms FT status, and writes to tennis_results.
"""

import sys
import os
import logging
import time
from datetime import datetime, timezone, timedelta
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
logger = logging.getLogger("tennis_settlement_worker")

POLL_INTERVAL_SECONDS = 300   # 5 minutes — matches settle slowly


def _settle_match(conn, match: dict) -> bool:
    """
    Settle a single match from the daily refresh data.
    Returns True if settlement was written.
    Settlement source is always 'daily_refresh'.
    """
    mid      = match["match_id"]
    sets_1   = match.get("sets_1", 0)
    sets_2   = match.get("sets_2", 0)

    # Determine winner from sets
    if sets_1 > sets_2:
        winner = match["player_1"]
    elif sets_2 > sets_1:
        winner = match["player_2"]
    else:
        logger.warning(f"[TENNIS SETTLEMENT] Cannot determine winner for {mid}: {sets_1}-{sets_2}")
        return False

    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO tennis_results
              (match_id, winner, sets_1, sets_2, settled_at, settlement_source)
            VALUES (?, ?, ?, ?, ?, 'daily_refresh')
            """,
            (mid, winner, sets_1, sets_2, datetime.now(timezone.utc).isoformat())
        )

        # Update prediction results
        conn.execute(
            """
            UPDATE tennis_predictions
            SET result = CASE
              WHEN selection = ? THEN 1
              ELSE 0
            END
            WHERE match_id = ?
              AND market_type = 'match_winner'
              AND result IS NULL
            """,
            (winner, mid)
        )

        # Mark match as settled
        conn.execute(
            "UPDATE tennis_matches SET status='FT', updated_at=? WHERE match_id=?",
            (datetime.now(timezone.utc).isoformat(), mid)
        )

        conn.commit()

        # Update Elo ratings
        _update_elo_ratings(conn, match, winner)

        logger.info(f"[TENNIS SETTLEMENT] Settled: {mid} → Winner: {winner} ({sets_1}-{sets_2})")
        return True

    except Exception as exc:
        logger.error(f"[TENNIS SETTLEMENT] Failed to settle {mid}: {exc}", exc_info=True)
        return False


def _update_elo_ratings(conn, match: dict, winner: str):
    """Update surface Elo ratings after settlement."""
    from src.tennis.ml.tennis_model import update_elo

    p1 = match["player_1"]
    p2 = match["player_2"]
    surface = match.get("surface", "hard")
    is_best_of_5 = match.get("best_of", 3) == 5

    surfaces_to_update = [surface, "overall"]

    for surf in surfaces_to_update:
        try:
            row_1 = conn.execute(
                "SELECT elo, matches_played FROM tennis_player_state WHERE player_name=? AND surface=?",
                (p1, surf)
            ).fetchone()
            row_2 = conn.execute(
                "SELECT elo, matches_played FROM tennis_player_state WHERE player_name=? AND surface=?",
                (p2, surf)
            ).fetchone()

            elo_1 = float(row_1[0]) if row_1 else 1500.0
            elo_2 = float(row_2[0]) if row_2 else 1500.0
            mp_1  = int(row_1[1]) if row_1 else 0
            mp_2  = int(row_2[1]) if row_2 else 0

            if winner == p1:
                new_elo_1, new_elo_2 = update_elo(elo_1, elo_2, is_best_of_5=is_best_of_5)
            else:
                new_elo_2, new_elo_1 = update_elo(elo_2, elo_1, is_best_of_5=is_best_of_5)

            now = datetime.now(timezone.utc).isoformat()
            for player, new_elo, mp in [(p1, new_elo_1, mp_1 + 1), (p2, new_elo_2, mp_2 + 1)]:
                conn.execute(
                    """
                    INSERT INTO tennis_player_state (player_name, surface, elo, matches_played, last_match_date, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(player_name, surface) DO UPDATE SET
                      elo=excluded.elo,
                      matches_played=excluded.matches_played,
                      last_match_date=excluded.last_match_date,
                      updated_at=excluded.updated_at
                    """,
                    (player, surf, new_elo, mp, match.get("date"), now)
                )

            conn.commit()

        except Exception as exc:
            logger.warning(f"[TENNIS SETTLEMENT] Elo update failed for {surf}: {exc}")


def run_settlement(conn):
    """Find FT matches from daily refresh and settle them."""
    # Only settle FT matches not already in tennis_results
    rows = conn.execute(
        """
        SELECT m.*
        FROM tennis_matches m
        LEFT JOIN tennis_results r ON m.match_id = r.match_id
        WHERE m.status = 'FT'
          AND r.match_id IS NULL
          AND m.player_1 IS NOT NULL
          AND m.player_2 IS NOT NULL
          AND (m.sets_1 > 0 OR m.sets_2 > 0)
        """
    ).fetchall()

    if not rows:
        return 0

    settled = 0
    for row in rows:
        match = dict(row)
        if _settle_match(conn, match):
            settled += 1

    logger.info(f"[TENNIS SETTLEMENT] Settled {settled}/{len(rows)} FT matches")

    # Log calibration metrics after settlement
    if settled > 0:
        from src.tennis.ml.tennis_calibration import log_metrics
        log_metrics(conn)

    return settled


def main():
    from src.db.database import get_db
    from src.tennis.db.tennis_schema import init_tennis_db

    logger.info("[TENNIS SETTLEMENT WORKER] Starting — source: daily_refresh ONLY")
    conn = get_db()
    init_tennis_db(conn)

    while True:
        try:
            run_settlement(conn)
        except Exception as exc:
            logger.error(f"[TENNIS SETTLEMENT] Unexpected error: {exc}", exc_info=True)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()

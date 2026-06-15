"""
tennis_calibration.py
======================
Calibration and performance tracking for the tennis prediction engine.

GOVERNANCE RULE:
  No automatic recalibration before 500 settled tennis predictions.
  Before that threshold: LOG ONLY.

Tracked metrics:
  - Brier Score
  - Expected Calibration Error (ECE)
  - Hit Rate
  - ROI (when bookmaker odds are stored)

These metrics are read-only observations until the governance threshold is met.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from typing import Optional

logger = logging.getLogger("football_predictor.tennis")

# ── Governance threshold ──────────────────────────────────────────────────────
CALIBRATION_THRESHOLD = 500   # settled predictions required before recalibration


def get_settled_count(conn: sqlite3.Connection) -> int:
    """Return total number of settled tennis predictions."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM tennis_predictions WHERE result IS NOT NULL"
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def is_calibration_authorized(conn: sqlite3.Connection) -> bool:
    """
    Returns True only if settled prediction count >= CALIBRATION_THRESHOLD.
    Until then, calibration is NOT AUTHORIZED.
    """
    count = get_settled_count(conn)
    authorized = count >= CALIBRATION_THRESHOLD
    if not authorized:
        logger.info(
            f"[TENNIS CALIBRATION] NOT AUTHORIZED. "
            f"Settled: {count}/{CALIBRATION_THRESHOLD}"
        )
    return authorized


def compute_brier_score(conn: sqlite3.Connection) -> Optional[float]:
    """
    Compute Brier Score across all settled predictions.
    BS = (1/N) * sum((predicted_prob - outcome)^2)
    Lower is better. Perfect = 0.0, Random = 0.25.
    """
    try:
        rows = conn.execute(
            """
            SELECT predicted_probability, result
            FROM tennis_predictions
            WHERE result IS NOT NULL
              AND market_type = 'match_winner'
            """
        ).fetchall()

        if not rows:
            return None

        bs = sum((float(r[0]) - float(r[1])) ** 2 for r in rows) / len(rows)
        return round(bs, 4)
    except Exception as e:
        logger.warning(f"[TENNIS CALIBRATION] Brier Score error: {e}")
        return None


def compute_ece(conn: sqlite3.Connection, n_bins: int = 10) -> Optional[float]:
    """
    Expected Calibration Error (ECE).
    Bins predictions by probability, measures average calibration gap.
    Lower is better. Perfect = 0.0.
    """
    try:
        rows = conn.execute(
            """
            SELECT predicted_probability, result
            FROM tennis_predictions
            WHERE result IS NOT NULL
              AND market_type = 'match_winner'
            ORDER BY predicted_probability
            """
        ).fetchall()

        if len(rows) < 20:
            return None  # too few samples for meaningful ECE

        bins = [[] for _ in range(n_bins)]
        for prob, outcome in rows:
            p = float(prob)
            o = float(outcome)
            bin_idx = min(int(p * n_bins), n_bins - 1)
            bins[bin_idx].append((p, o))

        ece = 0.0
        n = len(rows)
        for b in bins:
            if not b:
                continue
            avg_prob = sum(p for p, _ in b) / len(b)
            avg_outcome = sum(o for _, o in b) / len(b)
            ece += (len(b) / n) * abs(avg_prob - avg_outcome)

        return round(ece, 4)
    except Exception as e:
        logger.warning(f"[TENNIS CALIBRATION] ECE error: {e}")
        return None


def compute_hit_rate(conn: sqlite3.Connection, min_confidence: str = "MEDIUM") -> Optional[float]:
    """
    Hit rate (accuracy) on match winner predictions that met confidence threshold.
    """
    try:
        rows = conn.execute(
            """
            SELECT result
            FROM tennis_predictions
            WHERE result IS NOT NULL
              AND market_type = 'match_winner'
              AND confidence_score >= ?
            """,
            (0.55 if min_confidence == "MEDIUM" else 0.65,)
        ).fetchall()

        if not rows:
            return None

        hits = sum(int(r[0]) for r in rows)
        return round(hits / len(rows), 4)
    except Exception as e:
        logger.warning(f"[TENNIS CALIBRATION] Hit rate error: {e}")
        return None


def compute_roi(conn: sqlite3.Connection) -> Optional[float]:
    """
    ROI based on settling picks at fair odds.
    Only meaningful when odds snapshots exist.
    ROI = (total_return - total_staked) / total_staked
    """
    try:
        rows = conn.execute(
            """
            SELECT p.result, p.predicted_probability,
                   COALESCE(o.odds, 1.0 / p.predicted_probability) as odds
            FROM tennis_predictions p
            LEFT JOIN tennis_odds_snapshots o
              ON p.match_id = o.match_id AND p.market_type = o.market
            WHERE p.result IS NOT NULL
              AND p.market_type = 'match_winner'
              AND p.confidence_score >= 0.55
            """
        ).fetchall()

        if not rows:
            return None

        staked = len(rows)
        returns = sum(float(r[2]) if int(r[0]) == 1 else 0.0 for r in rows)
        roi = (returns - staked) / staked
        return round(roi * 100, 2)  # as percentage
    except Exception as e:
        logger.warning(f"[TENNIS CALIBRATION] ROI error: {e}")
        return None


def get_baseline_metrics(conn: sqlite3.Connection) -> dict:
    """
    Return all calibration metrics in one call.
    Governance status is always included.
    """
    settled = get_settled_count(conn)
    authorized = settled >= CALIBRATION_THRESHOLD

    metrics = {
        "settled_predictions": settled,
        "calibration_threshold": CALIBRATION_THRESHOLD,
        "calibration_authorized": authorized,
        "calibration_status": "AUTHORIZED" if authorized else "NOT AUTHORIZED",
        "brier_score":  compute_brier_score(conn),
        "ece":          compute_ece(conn),
        "hit_rate":     compute_hit_rate(conn),
        "roi_pct":      compute_roi(conn),
    }

    if not authorized:
        metrics["message"] = (
            f"Recalibration locked. "
            f"{settled}/{CALIBRATION_THRESHOLD} settled predictions collected."
        )

    return metrics


def log_metrics(conn: sqlite3.Connection) -> None:
    """Log current metrics to the tennis baseline contract table."""
    metrics = get_baseline_metrics(conn)
    try:
        conn.execute(
            """
            INSERT INTO tennis_baseline_contract (event, detail, settled_count)
            VALUES ('calibration_log', ?, ?)
            """,
            (str(metrics), metrics["settled_predictions"])
        )
        conn.commit()
        logger.info(
            f"[TENNIS CALIBRATION] Metrics logged — "
            f"Settled: {metrics['settled_predictions']}, "
            f"Brier: {metrics['brier_score']}, "
            f"ECE: {metrics['ece']}, "
            f"Hit Rate: {metrics['hit_rate']}, "
            f"ROI: {metrics['roi_pct']}%"
        )
    except Exception as e:
        logger.warning(f"[TENNIS CALIBRATION] Failed to log metrics: {e}")

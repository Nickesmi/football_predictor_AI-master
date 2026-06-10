"""
Probability Calibration Module

Calibrates raw model probabilities using historical performance data.
Implements Platt scaling and isotonic regression.

Why this matters:
  - Raw model says 70% → but actually wins 58% of the time
  - Calibration corrects this so 70% means ~70%
  - This is critical for edge calculation accuracy
"""

import logging
import math
import sqlite3
from collections import defaultdict

logger = logging.getLogger("football_predictor")


class ProbabilityCalibrator:
    """
    Calibrates raw model probabilities using historical performance.

    Uses binned calibration (simple, robust, works with small samples):
    - Group predictions into probability buckets
    - Track actual win rate per bucket
    - Create a mapping: raw_prob → calibrated_prob
    """

    def __init__(self, n_bins: int = 10):
        self.n_bins = n_bins
        # bin_edges[i] to bin_edges[i+1] → (total, wins)
        self.bin_width = 1.0 / n_bins
        self.bins: dict[int, dict] = {}
        self.group_bins: dict[tuple[str, str], dict[int, dict]] = {}
        self.group_counts: dict[tuple[str, str], int] = {}
        self._fitted = False

    def fit_from_db(self, conn: sqlite3.Connection) -> None:
        """
        Build calibration map from historical picks.
        Only uses settled picks (won/lost).
        """
        rows = conn.execute(
            """SELECT market, selection, model_prob, result FROM picks
               WHERE result IN ('won', 'lost')
               ORDER BY created_at"""
        ).fetchall()

        if len(rows) < 20:
            logger.info(
                f"Calibrator: only {len(rows)} settled picks, "
                "need ≥20 for calibration. Using identity."
            )
            self._fitted = False
            return

        # Reset bins
        self.bins = {}
        self.group_bins = {}
        self.group_counts = {}
        for i in range(self.n_bins):
            self.bins[i] = {"total": 0, "wins": 0}

        for row in rows:
            prob = row["model_prob"]
            won = 1 if row["result"] == "won" else 0
            bin_idx = min(int(prob / self.bin_width), self.n_bins - 1)
            self.bins[bin_idx]["total"] += 1
            self.bins[bin_idx]["wins"] += won

            for key in (
                (row["market"] or "", row["selection"] or ""),
                (row["market"] or "", "*"),
            ):
                if key not in self.group_bins:
                    self.group_bins[key] = {
                        i: {"total": 0, "wins": 0} for i in range(self.n_bins)
                    }
                    self.group_counts[key] = 0
                self.group_bins[key][bin_idx]["total"] += 1
                self.group_bins[key][bin_idx]["wins"] += won
                self.group_counts[key] += 1

        self._fitted = True
        logger.info(
            f"Calibrator fitted on {len(rows)} picks across {self.n_bins} bins"
        )

    def calibrate(
        self,
        raw_prob: float,
        market: str | None = None,
        selection: str | None = None,
    ) -> float:
        """
        Calibrate a raw probability.

        If not fitted (not enough data), applies mild shrinkage toward 50%:
            calibrated = 0.85 * raw + 0.15 * 0.5
        This is conservative but prevents overconfidence.
        """
        if not self._fitted:
            # Shrinkage toward 50% when uncalibrated
            return 0.85 * raw_prob + 0.15 * 0.5

        bin_idx = min(int(raw_prob / self.bin_width), self.n_bins - 1)
        bins = self.bins
        if market:
            exact_key = (market, selection or "")
            market_key = (market, "*")
            if self.group_counts.get(exact_key, 0) >= 30:
                bins = self.group_bins[exact_key]
            elif self.group_counts.get(market_key, 0) >= 30:
                bins = self.group_bins[market_key]

        bin_data = bins.get(bin_idx, {"total": 0, "wins": 0})

        if bin_data["total"] < 5:
            # Not enough samples in this bin — use shrinkage
            return 0.85 * raw_prob + 0.15 * 0.5

        actual_rate = bin_data["wins"] / bin_data["total"]

        # Blend: 70% actual rate + 30% raw model (smoothing)
        return 0.7 * actual_rate + 0.3 * raw_prob

    def get_calibration_report(self, conn: sqlite3.Connection) -> list[dict]:
        """
        Generate a calibration report showing predicted vs actual rates.

        This answers: "When we say 60%, does it really happen 60%?"
        """
        if not self._fitted:
            self.fit_from_db(conn)

        report = []
        for i in range(self.n_bins):
            bin_data = self.bins.get(i, {"total": 0, "wins": 0})
            low = round(i * self.bin_width, 2)
            high = round((i + 1) * self.bin_width, 2)
            total = bin_data["total"]
            wins = bin_data["wins"]
            actual = round(wins / total, 3) if total > 0 else None
            expected = round((low + high) / 2, 3)

            report.append({
                "bin": f"{int(low*100)}-{int(high*100)}%",
                "predicted_avg": expected,
                "actual_rate": actual,
                "samples": total,
                "gap": round(actual - expected, 3) if actual is not None else None,
            })

        return report


class ConfidenceBucketer:
    """
    Analyzes model performance by confidence bucket.

    Shows where the model is actually strong vs where it's noise.
    Critical for bet selection: only bet in buckets where model performs.
    """

    def analyze(self, conn: sqlite3.Connection) -> list[dict]:
        """
        Group settled picks by confidence bucket and compute stats.

        Returns performance per bucket:
            bucket, picks, wins, hit_rate, avg_edge, roi, avg_odds
        """
        rows = conn.execute(
            """SELECT model_prob, edge, odds_at_pick, stake_units,
                      result, pnl_units
               FROM picks
               WHERE result IN ('won', 'lost')
               ORDER BY model_prob"""
        ).fetchall()

        if not rows:
            return []

        buckets = defaultdict(lambda: {
            "picks": 0, "wins": 0, "total_pnl": 0.0,
            "total_staked": 0.0, "edges": [], "odds": [],
        })

        for row in rows:
            prob = row["model_prob"]
            bucket_key = f"{int(prob * 20) * 5}-{int(prob * 20) * 5 + 5}%"

            b = buckets[bucket_key]
            b["picks"] += 1
            if row["result"] == "won":
                b["wins"] += 1
            b["total_pnl"] += row["pnl_units"] or 0
            b["total_staked"] += row["stake_units"] or 0
            b["edges"].append(row["edge"])
            b["odds"].append(row["odds_at_pick"])

        result = []
        for bucket_name, b in sorted(buckets.items()):
            staked = b["total_staked"] if b["total_staked"] > 0 else 1
            result.append({
                "bucket": bucket_name,
                "picks": b["picks"],
                "wins": b["wins"],
                "hit_rate": round(b["wins"] / max(b["picks"], 1) * 100, 1),
                "avg_edge": round(
                    sum(b["edges"]) / len(b["edges"]) * 100, 1
                ) if b["edges"] else 0,
                "avg_odds": round(
                    sum(b["odds"]) / len(b["odds"]), 2
                ) if b["odds"] else 0,
                "roi": round(b["total_pnl"] / staked * 100, 1),
                "pnl": round(b["total_pnl"], 2),
            })

        return result

"""
Isotonic Calibration Engine — Phase 3 Priority #1

Fits a monotonic calibration function per market type using sklearn
IsotonicRegression on **binned hit rates** (not raw binary outcomes).

Why binning matters:
    Raw isotonic on binary (0/1) outcomes creates a noisy step function
    that memorizes training data — mapping high probabilities to 100%.
    By pre-binning into 2.5% buckets and fitting on bin-level hit rates
    (with Laplace smoothing), we get a smooth, generalizable curve.

Architecture:
    1. Loads (predicted_prob, actual_outcome) pairs from prediction_log
    2. Bins into 2.5% buckets, computes smoothed hit rate per bucket
    3. Fits IsotonicRegression on (bin_center, smoothed_hit_rate, weight)
    4. Clips output to [2%, 95%] — nothing in sports is certain
    5. Stores fitted models as JSON in calibration_models table

Minimum samples: 200 per market type (below this, uses shrinkage fallback)

Key design decisions:
    - Separate model per market type (goals, result, btts, etc.)
    - Probabilities stored as 0-100 scale throughout
    - Thread-safe: models are immutable once fitted
    - Fallback: mild shrinkage toward 50% when insufficient data
    - Laplace smoothing: 2 pseudocounts per bin (prevents 0%/100%)
    - Hard ceiling: 95% max output (no sports event is certain)
"""

import json
import sqlite3
import logging
import numpy as np
from datetime import datetime
from typing import Optional

logger = logging.getLogger("football_predictor")

# Minimum samples required for isotonic fit (below this, use shrinkage)
MIN_SAMPLES_FOR_FIT = 200
# Ideal samples for high-quality calibration
IDEAL_SAMPLES = 500

# ── Output bounds ──────────────────────────────────────────────────
# Nothing in sports is 0% or 100%.  Cap outputs to prevent
# downstream Kelly blowups and false certainty.
CALIBRATED_FLOOR = 2.0   # minimum calibrated probability (%)
CALIBRATED_CEILING = 85.0  # maximum calibrated probability (%) - reduced to reduce overconfidence

# Per-market-type ceilings. Handicap +spread markets are near-certain by
# Poisson math but are NOT that reliable in practice — cap them more tightly.
# This prevents the calibration model's X-axis truncation from outputting 95%
# for everything above ~84%.
MARKET_CEILINGS: dict[str, float] = {
    "handicap": 82.0,
}

# ── Binning parameters ────────────────────────────────────────────
BIN_WIDTH = 2.5   # percentage points per bin
MIN_BIN_SAMPLES = 5  # bins with fewer samples are excluded from fit
LAPLACE_ALPHA = 2  # pseudocounts for Laplace smoothing (higher = more conservative)


def _bin_predictions(probs: np.ndarray, outcomes: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bin predictions into fixed-width buckets and compute smoothed hit rates.

    Returns:
        bin_centers: (K,) array of bin center probabilities (0-1 scale)
        hit_rates:   (K,) array of Laplace-smoothed hit rates per bin
        weights:     (K,) array of sample counts per bin (for weighted fit)
    """
    bin_edges = np.arange(0, 100 + BIN_WIDTH, BIN_WIDTH)
    n_bins = len(bin_edges) - 1

    centers = []
    rates = []
    sample_weights = []

    for i in range(n_bins):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        mask = (probs >= lo) & (probs < hi)
        if i == n_bins - 1:  # last bin includes upper edge
            mask = (probs >= lo) & (probs <= hi)

        n = int(mask.sum())
        if n < MIN_BIN_SAMPLES:
            continue

        wins = float(outcomes[mask].sum())

        # Laplace smoothing: (wins + α) / (n + 2α)
        # This pulls extreme bins (all wins / all losses) toward the center
        smoothed_rate = (wins + LAPLACE_ALPHA) / (n + 2 * LAPLACE_ALPHA)

        centers.append((lo + hi) / 2.0 / 100.0)  # convert to 0-1
        rates.append(smoothed_rate)
        sample_weights.append(n)

    return np.array(centers), np.array(rates), np.array(sample_weights)


class IsotonicCalibrator:
    """Per-market-type isotonic calibration engine.

    Usage:
        cal = IsotonicCalibrator()
        cal.fit_all(conn)                          # fit from prediction_log
        calibrated = cal.calibrate(82.5, "goals")  # raw → calibrated
    """

    def __init__(self):
        # market_type → fitted IsotonicRegression model
        self._models: dict = {}
        # market_type → {"samples": int, "fitted_at": str}
        self._metadata: dict = {}
        self._loaded = False

    def fit_all(self, conn: sqlite3.Connection) -> dict:
        """Fit isotonic models for all market types with enough data.

        Returns summary dict: {market_type: {samples, fitted, gap_before, gap_after}}
        """
        from sklearn.isotonic import IsotonicRegression

        # Get all settled predictions grouped by market type, ordered chronologically
        rows = conn.execute(
            """SELECT market_type, predicted_prob, actual_outcome
               FROM prediction_log
               WHERE actual_outcome IS NOT NULL
               ORDER BY market_type, id"""
        ).fetchall()

        if not rows:
            logger.info("IsotonicCalibrator: no settled predictions yet")
            return {}

        # Group by market type
        groups: dict[str, list] = {}
        for row in rows:
            mt = row[0]
            if mt not in groups:
                groups[mt] = []
            groups[mt].append((row[1], row[2]))  # (predicted_prob, actual_outcome)

        summary = {}
        for market_type, data in groups.items():
            n = len(data)
            
            if n < MIN_SAMPLES_FOR_FIT:
                summary[market_type] = {
                    "samples": n,
                    "fitted": False,
                    "reason": f"need {MIN_SAMPLES_FOR_FIT}, have {n}",
                    "gap_before": 0.0,
                }
                continue

            # ── TIME-SERIES SPLIT (Out-Of-Sample Evaluation) ──
            # Train on the first 80%, test on the most recent 20%
            split_idx = int(n * 0.8)
            train_data = data[:split_idx]
            test_data = data[split_idx:]
            
            train_probs = np.array([d[0] for d in train_data])
            train_outcomes = np.array([d[1] for d in train_data])
            
            test_probs = np.array([d[0] for d in test_data])
            test_outcomes = np.array([d[1] for d in test_data])

            # Pre-calibration gap (on test set)
            avg_pred_test = float(np.mean(test_probs)) if len(test_probs) > 0 else 0.0
            avg_actual_test = float(np.mean(test_outcomes)) * 100 if len(test_outcomes) > 0 else 0.0
            gap_before = round(avg_pred_test - avg_actual_test, 1)

            # ── Evaluate existing (old) model on test set for baseline ──
            old_brier = 999.0
            old_log_loss = 999.0
            old_gap_after = 999.0
            if market_type in self._models:
                old_iso = self._models[market_type]
                X_test = test_probs / 100.0
                old_cal_test = np.clip(old_iso.predict(X_test) * 100, CALIBRATED_FLOOR, CALIBRATED_CEILING)
                old_avg_cal = float(np.mean(old_cal_test)) if len(old_cal_test) > 0 else 0.0
                old_gap_after = round(old_avg_cal - avg_actual_test, 1)

                brier = 0.0
                log_loss_val = 0.0
                eps = 1e-7
                for p_val, y_val in zip(old_cal_test / 100.0, test_outcomes):
                    p_val = max(eps, min(1 - eps, float(p_val)))
                    y_val = float(y_val)
                    brier += (p_val - y_val) ** 2
                    log_loss_val += -(y_val * np.log(p_val) + (1 - y_val) * np.log(1 - p_val))
                old_brier = round(brier / len(test_data), 4) if len(test_data) > 0 else 999.0
                old_log_loss = round(log_loss_val / len(test_data), 4) if len(test_data) > 0 else 999.0

            # ── Pre-bin training data to prevent overfitting ──
            # We aggregate into 2.5% bins and fit on smoothed hit rates.
            bin_centers, hit_rates, weights = _bin_predictions(train_probs, train_outcomes)

            if len(bin_centers) < 3:
                summary[market_type] = {
                    "samples": n,
                    "fitted": False,
                    "reason": f"only {len(bin_centers)} bins with enough samples in train set",
                    "gap_before": gap_before,
                }
                continue

            iso = IsotonicRegression(
                y_min=CALIBRATED_FLOOR / 100.0,
                y_max=CALIBRATED_CEILING / 100.0,
                increasing=True,
                out_of_bounds="clip",
            )
            # Weighted fit: bins with more samples get more influence
            iso.fit(bin_centers, hit_rates, sample_weight=weights)

            # Post-calibration gap (evaluate out-of-sample on test set)
            X_test = test_probs / 100.0
            calibrated_test = np.clip(iso.predict(X_test) * 100, CALIBRATED_FLOOR, CALIBRATED_CEILING)
            avg_calibrated_test = float(np.mean(calibrated_test)) if len(calibrated_test) > 0 else 0.0
            gap_after = round(avg_calibrated_test - avg_actual_test, 1)

            # Brier and Log Loss for the calibrated probabilities (out-of-sample)
            brier = 0.0
            log_loss_val = 0.0
            eps = 1e-7
            for p_val, y_val in zip(calibrated_test / 100.0, test_outcomes):
                p_val = max(eps, min(1 - eps, float(p_val)))
                y_val = float(y_val)
                brier += (p_val - y_val) ** 2
                log_loss_val += -(y_val * np.log(p_val) + (1 - y_val) * np.log(1 - p_val))

            test_n = len(test_data)
            brier_score = round(brier / test_n, 4) if test_n > 0 else 0.0
            log_loss_score = round(log_loss_val / test_n, 4) if test_n > 0 else 0.0

            # ── SAFETY RULE: Only activate if performance improves or no prior model exists ──
            is_safe = True
            reason = ""
            if old_brier != 999.0:
                if brier_score > old_brier:
                    is_safe = False
                    reason = f"Brier worsened ({old_brier:.4f} -> {brier_score:.4f})"
                elif abs(gap_after) > abs(old_gap_after):
                    is_safe = False
                    reason = f"Gap worsened ({old_gap_after:+.1f}% -> {gap_after:+.1f}%)"
                elif log_loss_score > old_log_loss:
                    is_safe = False
                    reason = f"Log Loss worsened ({old_log_loss:.4f} -> {log_loss_score:.4f})"

            if is_safe:
                self._models[market_type] = iso
                self._metadata[market_type] = {
                    "samples": n,
                    "train_samples": len(train_data),
                    "test_samples": len(test_data),
                    "bins_used": len(bin_centers),
                    "fitted_at": datetime.utcnow().isoformat(),
                }
                # Store the fitted model as JSON for persistence
                self._store_model(conn, market_type, iso, n, brier_score, log_loss_score)
                logger.info(
                    f"Isotonic fit [{market_type}] ACCEPTED: {n} samples ({len(bin_centers)} bins), "
                    f"gap {gap_before:+.1f}% → {gap_after:+.1f}%, Brier={brier_score:.4f}"
                )
            else:
                logger.warning(
                    f"Isotonic fit [{market_type}] REJECTED: {reason}. Keeping old model."
                )

            summary[market_type] = {
                "samples": n,
                "train_samples": len(train_data),
                "test_samples": test_n,
                "bins_used": len(bin_centers),
                "fitted": is_safe,
                "gap_before": gap_before,
                "gap_after": gap_after,
                "old_gap_after": old_gap_after if old_gap_after != 999.0 else gap_before,
                "improvement": round(abs(gap_before) - abs(gap_after), 1),
                "brier_score": brier_score,
                "old_brier": old_brier if old_brier != 999.0 else None,
                "log_loss": log_loss_score,
                "old_log_loss": old_log_loss if old_log_loss != 999.0 else None,
                "reason": reason if not is_safe else "Accepted",
            }

            logger.info(
                f"Isotonic fit [{market_type}]: {n} samples ({len(bin_centers)} bins), "
                f"gap {gap_before:+.1f}% → {gap_after:+.1f}%, "
                f"Brier={brier_score:.4f}"
            )

        self._loaded = True
        return summary

    def _store_model(
        self, conn: sqlite3.Connection, market_type: str,
        iso, n: int, brier_score: float, log_loss: float
    ) -> None:
        """Persist fitted model breakpoints to DB."""
        # Extract the piecewise-linear breakpoints
        model_data = {
            "X_thresholds": iso.X_thresholds_.tolist() if hasattr(iso, 'X_thresholds_') else [],
            "y_thresholds": iso.y_thresholds_.tolist() if hasattr(iso, 'y_thresholds_') else [],
            "X_min": float(iso.X_min_) if hasattr(iso, 'X_min_') else 0.0,
            "X_max": float(iso.X_max_) if hasattr(iso, 'X_max_') else 1.0,
        }

        conn.execute(
            """INSERT INTO calibration_models
               (market_type, fitted_json, samples, brier_score, log_loss, created_at)
               VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (market_type, json.dumps(model_data), n, brier_score, log_loss),
        )
        conn.commit()

    def load_from_db(self, conn: sqlite3.Connection) -> int:
        """Load previously fitted models from DB.

        Returns number of models loaded.
        """
        from sklearn.isotonic import IsotonicRegression

        rows = conn.execute(
            """SELECT market_type, fitted_json, samples, created_at
               FROM calibration_models
               WHERE id IN (
                   SELECT MAX(id) FROM calibration_models GROUP BY market_type
               )"""
        ).fetchall()

        count = 0
        for row in rows:
            try:
                market_type = row[0]
                model_data = json.loads(row[1])
                samples = row[2]

                X_t = np.array(model_data["X_thresholds"])
                y_t = np.array(model_data["y_thresholds"])

                if len(X_t) < 2:
                    continue

                # Reconstruct isotonic model from breakpoints
                iso = IsotonicRegression(
                    y_min=CALIBRATED_FLOOR / 100.0,
                    y_max=CALIBRATED_CEILING / 100.0,
                    increasing=True,
                    out_of_bounds="clip",
                )
                # Fit on the stored breakpoints (reconstructs the piecewise function)
                iso.fit(X_t, y_t)

                self._models[market_type] = iso
                self._metadata[market_type] = {
                    "samples": samples,
                    "fitted_at": row[3],
                }
                count += 1
            except Exception as e:
                logger.warning(f"Failed to load calibration model for {row[0]}: {e}")

        self._loaded = count > 0
        logger.info(f"Loaded {count} isotonic calibration models from DB")
        return count

    def calibrate(self, raw_prob: float, market_type: str) -> float:
        """Calibrate a raw probability (0-100 scale) using fitted isotonic model.

        Falls back to mild shrinkage if no model is available.
        Output is always clamped to [CALIBRATED_FLOOR, market-type ceiling].

        Args:
            raw_prob: model probability in 0-100 scale
            market_type: one of goals, result, btts, cs, combo, handicap, etc.

        Returns:
            calibrated probability in 0-100 scale
        """
        ceiling = MARKET_CEILINGS.get(market_type, CALIBRATED_CEILING)

        if market_type in self._models:
            iso = self._models[market_type]
            # Apply pre-shrinkage for handicap markets so raw values above the
            # model's training X-range don't saturate at the ceiling.
            input_prob = raw_prob
            if market_type == "handicap":
                # Shrink from edges toward centre before entering the model
                p = raw_prob / 100.0
                input_prob = round((0.5 + (p - 0.5) * 0.78) * 100.0, 1)
            # Convert to 0-1, predict, convert back to 0-100
            calibrated = float(iso.predict(np.array([[input_prob / 100.0]]))[0]) * 100
            return round(max(CALIBRATED_FLOOR, min(ceiling, calibrated)), 1)

        # Fallback: mild shrinkage toward 50%
        # This is conservative but prevents overconfidence when uncalibrated
        shrunk = 0.85 * raw_prob + 0.15 * 50
        return round(max(CALIBRATED_FLOOR, min(ceiling, shrunk)), 1)

    def get_status(self) -> dict:
        """Return calibration status per market type."""
        status = {}
        for mt, meta in self._metadata.items():
            has_model = mt in self._models
            status[mt] = {
                "fitted": has_model,
                "samples": meta["samples"],
                "bins_used": meta.get("bins_used"),
                "fitted_at": meta["fitted_at"],
            }
        return status

    def get_calibration_curve(self, market_type: str, steps: int = 20) -> list[dict]:
        """Generate the calibration mapping curve for visualization.

        Returns list of {raw, calibrated} pairs showing the transform.
        """
        if market_type not in self._models:
            return [{"raw": r, "calibrated": r} for r in range(0, 101, 5)]

        iso = self._models[market_type]
        curve = []
        for raw in np.linspace(0, 100, steps + 1):
            cal = float(iso.predict(np.array([[raw / 100]]))[0]) * 100
            cal = max(CALIBRATED_FLOOR, min(CALIBRATED_CEILING, cal))
            curve.append({
                "raw": round(raw, 1),
                "calibrated": round(cal, 1),
            })
        return curve


# ── Singleton instance ──
_calibrator_instance: Optional[IsotonicCalibrator] = None


def get_isotonic_calibrator(conn: sqlite3.Connection = None) -> IsotonicCalibrator:
    """Get or create the singleton isotonic calibrator.

    On first call, attempts to load fitted models from DB.
    """
    global _calibrator_instance
    if _calibrator_instance is None:
        _calibrator_instance = IsotonicCalibrator()
        if conn is not None:
            _calibrator_instance.load_from_db(conn)
    return _calibrator_instance

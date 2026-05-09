"""
Isotonic Calibration Engine — Phase 3 Priority #1

Fits a monotonic calibration function per market type using sklearn
IsotonicRegression. This corrects the systematic overconfidence bias
(+8% gap) discovered in the prediction log.

Architecture:
    1. Loads (predicted_prob, actual_outcome) pairs from prediction_log
    2. Fits IsotonicRegression per market_type
    3. Stores fitted models as JSON in calibration_models table
    4. Provides calibrate(raw_prob, market_type) → calibrated_prob

Minimum samples: 200 per market type (below this, uses shrinkage fallback)
Refit trigger: every 50 new settled predictions per market type

Key design decisions:
    - Separate model per market type (goals, result, btts, etc.)
    - Probabilities stored as 0-100 scale throughout
    - Thread-safe: models are immutable once fitted
    - Fallback: mild shrinkage toward 50% when insufficient data
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

        # Get all settled predictions grouped by market type
        rows = conn.execute(
            """SELECT market_type, predicted_prob, actual_outcome
               FROM prediction_log
               WHERE actual_outcome IS NOT NULL
               ORDER BY market_type, predicted_prob"""
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
            probs = np.array([d[0] for d in data])
            outcomes = np.array([d[1] for d in data])

            # Pre-calibration gap
            avg_pred = float(np.mean(probs))
            avg_actual = float(np.mean(outcomes)) * 100
            gap_before = round(avg_pred - avg_actual, 1)

            if n < MIN_SAMPLES_FOR_FIT:
                summary[market_type] = {
                    "samples": n,
                    "fitted": False,
                    "reason": f"need {MIN_SAMPLES_FOR_FIT}, have {n}",
                    "gap_before": gap_before,
                }
                continue

            # Fit isotonic regression
            # Convert probs from 0-100 to 0-1 for fitting
            X = probs / 100.0
            y = outcomes.astype(float)

            iso = IsotonicRegression(
                y_min=0.0, y_max=1.0,
                increasing=True,
                out_of_bounds="clip",
            )
            iso.fit(X, y)

            self._models[market_type] = iso
            self._metadata[market_type] = {
                "samples": n,
                "fitted_at": datetime.utcnow().isoformat(),
            }

            # Post-calibration gap
            calibrated = iso.predict(X) * 100
            avg_calibrated = float(np.mean(calibrated))
            gap_after = round(avg_calibrated - avg_actual, 1)

            # Brier and Log Loss for the calibrated probabilities
            brier = 0.0
            log_loss = 0.0
            eps = 1e-7
            for p_val, y_val in zip(calibrated / 100.0, outcomes):
                p_val = max(eps, min(1 - eps, p_val))
                y_val = float(y_val)
                brier += (p_val - y_val) ** 2
                log_loss += -(y_val * np.log(p_val) + (1 - y_val) * np.log(1 - p_val))
            
            brier_score = round(brier / n, 4) if n > 0 else 0.0
            log_loss_score = round(log_loss / n, 4) if n > 0 else 0.0

            summary[market_type] = {
                "samples": n,
                "fitted": True,
                "gap_before": gap_before,
                "gap_after": gap_after,
                "improvement": round(abs(gap_before) - abs(gap_after), 1),
                "brier_score": brier_score,
                "log_loss": log_loss_score,
            }

            # Store the fitted model as JSON for persistence
            self._store_model(conn, market_type, iso, n, brier_score, log_loss_score)

            logger.info(
                f"Isotonic fit [{market_type}]: {n} samples, "
                f"gap {gap_before:+.1f}% → {gap_after:+.1f}%"
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
                    y_min=0.0, y_max=1.0,
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

        Args:
            raw_prob: model probability in 0-100 scale
            market_type: one of goals, result, btts, cs, combo, handicap, etc.

        Returns:
            calibrated probability in 0-100 scale
        """
        if market_type in self._models:
            iso = self._models[market_type]
            # Convert to 0-1, predict, convert back to 0-100
            calibrated = float(iso.predict(np.array([[raw_prob / 100.0]]))[0]) * 100
            return round(max(0, min(100, calibrated)), 1)

        # Fallback: mild shrinkage toward 50%
        # This is conservative but prevents overconfidence when uncalibrated
        shrunk = 0.85 * raw_prob + 0.15 * 50
        return round(max(0, min(100, shrunk)), 1)

    def get_status(self) -> dict:
        """Return calibration status per market type."""
        status = {}
        for mt, meta in self._metadata.items():
            has_model = mt in self._models
            status[mt] = {
                "fitted": has_model,
                "samples": meta["samples"],
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

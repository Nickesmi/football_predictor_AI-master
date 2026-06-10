"""Runtime gates that prevent weak markets from being promoted as picks.

The prediction engine can still calculate every market for analysis, but the
frontend's "confident picks" should be treated more like a shortlist. This
module checks that a market family has enough settled local history and that
the league has not been flagged as unreliable before a pick is promoted.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str = ""
    min_probability: float = 75.0


@dataclass(frozen=True)
class MarketRule:
    min_samples: int
    min_hit_rate: float
    max_brier: float
    min_probability: float
    disabled_reason: str = ""


MARKET_RULES: dict[str, MarketRule] = {
    "goals": MarketRule(150, 65.0, 0.20, 75.0),
    "team_goals": MarketRule(120, 65.0, 0.22, 77.0),
    "handicap": MarketRule(100, 67.0, 0.20, 77.0),
    "half": MarketRule(100, 66.0, 0.21, 78.0),
    "result": MarketRule(150, 68.0, 0.21, 80.0),
    "btts": MarketRule(200, 60.0, 0.23, 82.0, "BTTS has weak recent calibration"),
    "corners": MarketRule(200, 65.0, 0.22, 85.0, "corner data is too noisy for bankroll picks"),
    "cards": MarketRule(200, 65.0, 0.22, 85.0, "card data is too noisy for bankroll picks"),
    "combo": MarketRule(9999, 99.0, 0.01, 99.0, "combo markets are correlated and high variance"),
    "cs": MarketRule(9999, 99.0, 0.01, 99.0, "correct-score markets are high variance"),
}

UNKNOWN_MARKET_RULE = MarketRule(
    9999,
    99.0,
    0.01,
    99.0,
    "unknown market type cannot be promoted safely",
)


def _row_value(row, key: str, index: int):
    if row is None:
        return None
    try:
        return row[key]
    except (TypeError, IndexError, KeyError):
        return row[index]


def load_market_performance(conn: sqlite3.Connection) -> dict[str, dict]:
    """Return settled performance by market type from prediction_log."""
    rows = conn.execute(
        """SELECT market_type,
                  COUNT(*) AS samples,
                  AVG(actual_outcome) * 100.0 AS hit_rate,
                  AVG(predicted_prob) AS avg_probability,
                  AVG((predicted_prob / 100.0 - actual_outcome)
                      * (predicted_prob / 100.0 - actual_outcome)) AS brier
           FROM prediction_log
           WHERE actual_outcome IS NOT NULL
           GROUP BY market_type"""
    ).fetchall()

    performance: dict[str, dict] = {}
    for row in rows:
        market_type = _row_value(row, "market_type", 0)
        if not market_type:
            continue
        performance[str(market_type)] = {
            "samples": int(_row_value(row, "samples", 1) or 0),
            "hit_rate": float(_row_value(row, "hit_rate", 2) or 0.0),
            "avg_probability": float(_row_value(row, "avg_probability", 3) or 0.0),
            "brier": float(_row_value(row, "brier", 4) or 1.0),
        }
    return performance


def load_league_reliability(
    conn: sqlite3.Connection,
    league_names: Iterable[str],
) -> dict | None:
    """Return the strongest available league reliability row."""
    candidates = [name for name in dict.fromkeys(league_names) if name]
    if not candidates:
        return None

    placeholders = ",".join("?" for _ in candidates)
    rows = conn.execute(
        f"""SELECT league_name, sample_count, actual_hit_rate,
                   reliability_score, status
            FROM model_confidence_adjustments
            WHERE league_name IN ({placeholders})
            ORDER BY sample_count DESC, reliability_score DESC
            LIMIT 1""",
        candidates,
    ).fetchall()
    if not rows:
        return None

    row = rows[0]
    return {
        "league_name": _row_value(row, "league_name", 0),
        "sample_count": int(_row_value(row, "sample_count", 1) or 0),
        "actual_hit_rate": _row_value(row, "actual_hit_rate", 2),
        "reliability_score": float(_row_value(row, "reliability_score", 3) or 0.0),
        "status": _row_value(row, "status", 4),
    }


class RuntimePickGate:
    """Stateful gate built once per match analysis."""

    def __init__(
        self,
        market_performance: dict[str, dict],
        league_reliability: dict | None,
        data_quality: float,
    ):
        self.market_performance = market_performance
        self.league_reliability = league_reliability
        self.data_quality = data_quality

    def evaluate(self, market_type: str, probability: float) -> GateDecision:
        if self.data_quality < 55.0:
            return GateDecision(False, "data quality below bankroll-pick floor")

        rule = MARKET_RULES.get(market_type, UNKNOWN_MARKET_RULE)
        if rule.disabled_reason:
            return GateDecision(False, rule.disabled_reason, rule.min_probability)
        if probability < rule.min_probability:
            return GateDecision(False, "probability below runtime gate", rule.min_probability)

        league = self.league_reliability
        if league:
            samples = int(league.get("sample_count") or 0)
            accuracy = league.get("actual_hit_rate")
            reliability = float(league.get("reliability_score") or 0.0)
            if samples >= 20 and accuracy is not None and float(accuracy) < 50.0:
                return GateDecision(False, "league learning shows sub-50 hit rate", rule.min_probability)
            if samples >= 20 and reliability < 35.0:
                return GateDecision(False, "league reliability is too low", rule.min_probability)
        elif self.data_quality < 70.0 and probability < rule.min_probability + 5.0:
            return GateDecision(False, "unknown league needs extra confidence", rule.min_probability + 5.0)

        perf = self.market_performance.get(market_type)
        if not perf:
            return GateDecision(False, "no settled market history", rule.min_probability)
        if perf["samples"] < rule.min_samples:
            return GateDecision(False, "insufficient settled market sample", rule.min_probability)
        if perf["hit_rate"] < rule.min_hit_rate:
            return GateDecision(False, "market family under hit-rate floor", rule.min_probability)
        if perf["brier"] > rule.max_brier:
            return GateDecision(False, "market family has poor Brier score", rule.min_probability)

        return GateDecision(True, "", rule.min_probability)


def build_runtime_pick_gate(
    conn: sqlite3.Connection,
    league_names: Iterable[str],
    data_quality: float,
) -> RuntimePickGate:
    return RuntimePickGate(
        market_performance=load_market_performance(conn),
        league_reliability=load_league_reliability(conn, league_names),
        data_quality=data_quality,
    )

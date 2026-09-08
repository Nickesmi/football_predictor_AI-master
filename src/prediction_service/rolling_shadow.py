"""
Rolling / paper-shadow mode (Phase 4 §16).

Emulates a continuously running production deployment: for each simulated
day, ingest only what's knowable as of that day, generate the REAL
champion prediction (frozen, versioned, exactly like replay_engine.py),
and ALSO generate a challenger's shadow prediction alongside it — without
the challenger ever influencing what's actually returned or recommended
(src.prediction_service.shadow_mode.predict_with_shadow already enforces
that; this module just runs it on a rolling clock instead of a single
match). Old predictions are never mutated when the clock advances — only
new versions/new rows are ever added (Phase 4 §12).

This is deliberately built ON TOP of shadow_mode.py and the same
ReplayProvider chronology as replay_engine.py, not a third re-implementation.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

import pandas as pd

from src.prediction_service import snapshot_db
from src.prediction_service.prediction_pipeline import PredictionRefused
from src.prediction_service.providers.replay_provider import ReplayProvider
from src.prediction_service.replay_engine import _market_outcome, DEFAULT_PREDICTION_HOUR_UTC, DEFAULT_KICKOFF_HOUR_UTC
from src.prediction_service.shadow_mode import predict_with_shadow, evaluate_shadow_predictions, shadow_mode_summary


@dataclass
class RollingShadowRun:
    market: str
    challenger_model: str
    start_date: str
    end_date: str
    matches_processed: int = 0
    shadow_predictions_generated: int = 0
    refusals: int = 0
    errors: int = 0
    error_details: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def run_rolling_shadow(
    historical_matches: pd.DataFrame,
    market: str,
    challenger_model: str,
    start_date: str,
    end_date: str,
    conn: Optional[sqlite3.Connection] = None,
) -> RollingShadowRun:
    conn = conn or snapshot_db.get_connection()
    provider = ReplayProvider(historical_matches)
    run = RollingShadowRun(market=market, challenger_model=challenger_model, start_date=start_date, end_date=end_date)

    dates = pd.date_range(start_date, end_date, freq="D")
    pending_match_ids: set[str] = set()
    match_result_cache: dict[str, tuple[int, int]] = {}

    for current_date in dates:
        as_of_date_str = current_date.strftime("%Y-%m-%d")

        # ── Advance clock: reveal results for matches now old enough,
        # settle every pending shadow prediction for them in one pass. ──
        newly_known = {}
        for match_id in list(pending_match_ids):
            result = provider.fetch_result(match_id, as_of=as_of_date_str)
            if result is not None:
                match_result_cache[match_id] = (result.home_goals, result.away_goals)
                pending_match_ids.discard(match_id)
                newly_known[match_id] = _market_outcome(market, result.home_goals, result.away_goals)
        if newly_known:
            evaluate_shadow_predictions(conn, newly_known)

        # ── Generate today's champion + shadow predictions ──
        fixtures = provider.fetch_fixtures(as_of=as_of_date_str)
        for fixture in fixtures:
            run.matches_processed += 1
            pred_ts = f"{as_of_date_str}T{DEFAULT_PREDICTION_HOUR_UTC:02d}:00:00Z"
            kickoff_ts = f"{as_of_date_str}T{DEFAULT_KICKOFF_HOUR_UTC:02d}:00:00Z"
            try:
                predict_with_shadow(
                    fixture.home_team, fixture.away_team, fixture.competition, market,
                    prediction_timestamp=pred_ts, kickoff_timestamp=kickoff_ts,
                    historical_matches=historical_matches, challenger_model=challenger_model,
                    match_id=fixture.fixture_id, conn=conn,
                )
                run.shadow_predictions_generated += 1
                pending_match_ids.add(fixture.fixture_id)
            except PredictionRefused as e:
                run.refusals += 1
            except Exception as e:
                run.errors += 1
                run.error_details.append(f"{fixture.fixture_id}: {e}")

    # ── Final settlement pass, one day after the window closes. ──
    final_as_of = (dates[-1] + timedelta(days=1)).strftime("%Y-%m-%d") if len(dates) else end_date
    newly_known = {}
    for match_id in list(pending_match_ids):
        result = provider.fetch_result(match_id, as_of=final_as_of)
        if result is not None:
            newly_known[match_id] = _market_outcome(market, result.home_goals, result.away_goals)
    if newly_known:
        evaluate_shadow_predictions(conn, newly_known)

    run.summary = shadow_mode_summary(conn, challenger_model)
    return run

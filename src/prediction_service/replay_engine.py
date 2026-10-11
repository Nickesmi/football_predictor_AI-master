"""
Replay engine (Phase 4 §15/§16/§21).

A deterministic simulation of what the production system would have
known and done at each historical prediction timestamp, run through the
ACTUAL production prediction service (prediction_pipeline.predict()) —
not a separate re-implementation. This is what makes it a genuine parity
proof rather than a second, potentially-drifting code path:

    real historical fixtures (ReplayProvider)
        -> chronological day-by-day clock
        -> prediction_pipeline.predict(data_mode=REPLAY) for each fixture/market
        -> frozen snapshot (snapshot_db, versioned, never mutated)
        -> clock advances
        -> ONLY THEN: settle predictions whose match has since finished
        -> post_match_eval scores them
        -> repeat

Blindness guarantee: a fixture's result is never fetched until the
simulated clock has moved to a date AFTER that fixture's own date —
enforced by ReplayProvider.fetch_result() itself (§15/§19), and re-checked
here by never calling fetch_result with as_of <= the fixture's date.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

import pandas as pd

from src.prediction_service import snapshot_db
from src.prediction_service.data_contract import DataMode
from src.prediction_service.post_match_eval import evaluate_prediction
from src.prediction_service.prediction_pipeline import predict, PredictionRefused
from src.prediction_service.providers.replay_provider import ReplayProvider

DEFAULT_MARKETS = ["home_win", "draw", "away_win", "over_1_5", "over_2_5", "over_3_5", "btts"]

# A fixture's real kickoff time isn't always known to day-granularity data,
# so predictions are made "as of" this time on the fixture's own date, and
# kickoff is treated as later the same day — consistent with this
# dataset's real precision (see REAL_DATA_BACKTEST_REPORT.md §1).
DEFAULT_PREDICTION_HOUR_UTC = 8    # morning of matchday
DEFAULT_KICKOFF_HOUR_UTC = 15      # afternoon kickoff, same day


@dataclass
class PendingSettlement:
    prediction_id: str
    fixture_id: str
    market: str
    home_team: str
    away_team: str


@dataclass
class ReplayRun:
    start_date: str
    end_date: str
    matches_processed: int = 0
    predictions_generated: int = 0
    refusals: int = 0
    errors: int = 0
    settled: int = 0
    started_at: str = ""
    finished_at: str = ""
    refusal_reasons: dict = field(default_factory=dict)
    error_details: list = field(default_factory=list)


def _market_outcome(market: str, home_goals: int, away_goals: int) -> int:
    total = home_goals + away_goals
    if market == "home_win":
        return int(home_goals > away_goals)
    if market == "draw":
        return int(home_goals == away_goals)
    if market == "away_win":
        return int(away_goals > home_goals)
    if market == "over_1_5":
        return int(total > 1)
    if market == "over_2_5":
        return int(total > 2)
    if market == "over_3_5":
        return int(total > 3)
    if market == "btts":
        return int(home_goals > 0 and away_goals > 0)
    raise ValueError(f"unsupported market for settlement: {market}")


def run_replay(
    historical_matches: pd.DataFrame,
    start_date: str,
    end_date: str,
    markets: Optional[list[str]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> ReplayRun:
    """Runs a chronological, blind replay over [start_date, end_date]
    (inclusive), predicting every real fixture in that window for every
    requested market through the actual production pipeline."""
    markets = markets or DEFAULT_MARKETS
    conn = conn or snapshot_db.get_connection()
    provider = ReplayProvider(historical_matches)

    run = ReplayRun(start_date=start_date, end_date=end_date)
    run.started_at = pd.Timestamp.now('UTC').isoformat()

    dates = pd.date_range(start_date, end_date, freq="D")
    pending: list[PendingSettlement] = []

    for current_date in dates:
        as_of_date_str = current_date.strftime("%Y-%m-%d")

        # ── Settle anything whose match has now finished (a date strictly
        # after the fixture's own date has been reached) — BEFORE
        # generating today's new predictions, so settlement always lags
        # prediction by at least one full day, never the other way around. ──
        still_pending = []
        for item in pending:
            result = provider.fetch_result(item.fixture_id, as_of=as_of_date_str)
            if result is None:
                still_pending.append(item)
                continue
            try:
                outcome = _market_outcome(item.market, result.home_goals, result.away_goals)
                evaluate_prediction(conn, item.prediction_id, outcome, evaluated_at=f"{as_of_date_str}T00:00:00Z")
                run.settled += 1
            except Exception as e:
                run.errors += 1
                run.error_details.append(f"settlement failed for {item.prediction_id}: {e}")
        pending = still_pending

        # ── Generate today's predictions (fixtures scheduled for today,
        # WITHOUT results — ReplayProvider.fetch_fixtures never reveals them) ──
        fixtures = provider.fetch_fixtures(as_of=as_of_date_str)
        for fixture in fixtures:
            run.matches_processed += 1
            pred_ts = f"{as_of_date_str}T{DEFAULT_PREDICTION_HOUR_UTC:02d}:00:00Z"
            kickoff_ts = f"{as_of_date_str}T{DEFAULT_KICKOFF_HOUR_UTC:02d}:00:00Z"

            for market in markets:
                try:
                    result = predict(
                        fixture.home_team, fixture.away_team, fixture.competition, market,
                        prediction_timestamp=pred_ts, kickoff_timestamp=kickoff_ts,
                        historical_matches=historical_matches, match_id=fixture.fixture_id,
                        conn=conn, data_mode=DataMode.REPLAY,
                    )
                    run.predictions_generated += 1
                    pending.append(PendingSettlement(
                        prediction_id=result.prediction_id, fixture_id=fixture.fixture_id, market=market,
                        home_team=fixture.home_team, away_team=fixture.away_team,
                    ))
                except PredictionRefused as e:
                    run.refusals += 1
                    run.refusal_reasons[e.stage_failed] = run.refusal_reasons.get(e.stage_failed, 0) + 1
                except Exception as e:
                    run.errors += 1
                    run.error_details.append(f"prediction failed for {fixture.fixture_id}/{market}: {e}")

    # ── Final settlement pass: give the last processed day's matches one
    # more day to be revealed (day-granularity data — "the day after" is
    # the earliest honest point to consider a same-day match finished). ──
    final_as_of = (dates[-1] + timedelta(days=1)).strftime("%Y-%m-%d") if len(dates) else end_date
    for item in pending:
        result = provider.fetch_result(item.fixture_id, as_of=final_as_of)
        if result is None:
            continue
        try:
            outcome = _market_outcome(item.market, result.home_goals, result.away_goals)
            evaluate_prediction(conn, item.prediction_id, outcome, evaluated_at=f"{final_as_of}T00:00:00Z")
            run.settled += 1
        except Exception as e:
            run.errors += 1
            run.error_details.append(f"final settlement failed for {item.prediction_id}: {e}")

    run.finished_at = pd.Timestamp.now('UTC').isoformat()
    return run

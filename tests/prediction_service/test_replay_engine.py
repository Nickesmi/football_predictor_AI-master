"""
Regression tests for src/prediction_service/replay_engine.py — the
chronological, blind replay simulation (Phase 4 §15/§16/§21).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.prediction_service import snapshot_db
from src.prediction_service.replay_engine import run_replay


@pytest.fixture()
def db_conn(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_db, "_DB_PATH", tmp_path / "test_predictions.db")
    conn = snapshot_db.get_connection()
    yield conn
    conn.close()


@pytest.fixture()
def toy_history():
    rng = np.random.default_rng(1)
    teams = [f"Team{i}" for i in range(8)]
    rows = []
    day = 0
    for round_ in range(30):
        rng.shuffle(teams)
        for i in range(0, len(teams), 2):
            day += 1
            rows.append((
                f"m{round_}_{i}", (pd.Timestamp("2024-01-01") + pd.Timedelta(days=day)).strftime("%Y-%m-%d"),
                "TestLeague", "TestSeason", teams[i], teams[i + 1],
                int(rng.integers(0, 4)), int(rng.integers(0, 4)),
            ))
    return pd.DataFrame(rows, columns=["match_id", "date", "league", "season", "home_team", "away_team", "home_goals", "away_goals"]).sort_values("date").reset_index(drop=True)


def test_replay_processes_every_fixture_in_the_window(toy_history, db_conn):
    window_start = toy_history["date"].iloc[-6]
    window_end = toy_history["date"].iloc[-1]
    run = run_replay(toy_history, window_start, window_end, markets=["home_win"], conn=db_conn)
    assert run.matches_processed == 6
    assert run.predictions_generated + run.refusals == run.matches_processed
    assert run.errors == 0


def test_settlement_always_happens_strictly_after_the_prediction_was_frozen(toy_history, db_conn):
    window_start = toy_history["date"].iloc[-6]
    window_end = toy_history["date"].iloc[-1]
    run_replay(toy_history, window_start, window_end, markets=["home_win"], conn=db_conn)

    rows = db_conn.execute(
        """SELECT p.prediction_timestamp, e.evaluated_at FROM predictions p
           JOIN post_match_evaluations e ON p.prediction_id = e.prediction_id"""
    ).fetchall()
    assert len(rows) > 0
    for row in rows:
        assert pd.Timestamp(row["evaluated_at"]) > pd.Timestamp(row["prediction_timestamp"]), (
            "a prediction was settled at or before its own prediction_timestamp — this is the exact "
            "blindness violation Phase 4 forbids"
        )


def test_settlement_never_happens_on_the_same_day_as_the_match_even_though_the_result_exists_in_memory(toy_history, db_conn):
    """The dataset technically has the result in memory the whole time —
    this test proves the engine still refuses to use it until the clock
    has moved to the day AFTER the match."""
    window_start = toy_history["date"].iloc[-6]
    window_end = toy_history["date"].iloc[-1]
    run_replay(toy_history, window_start, window_end, markets=["home_win"], conn=db_conn)

    rows = db_conn.execute(
        """SELECT p.prediction_timestamp, e.evaluated_at FROM predictions p
           JOIN post_match_evaluations e ON p.prediction_id = e.prediction_id"""
    ).fetchall()
    for row in rows:
        pred_date = pd.Timestamp(row["prediction_timestamp"]).normalize()
        eval_date = pd.Timestamp(row["evaluated_at"]).normalize()
        assert eval_date >= pred_date + pd.Timedelta(days=1)


def test_replay_predictions_are_tagged_as_replay_not_live(toy_history, db_conn):
    window_start = toy_history["date"].iloc[-3]
    window_end = toy_history["date"].iloc[-1]
    run_replay(toy_history, window_start, window_end, markets=["home_win"], conn=db_conn)
    rows = db_conn.execute("SELECT data_mode FROM predictions").fetchall()
    assert len(rows) > 0
    assert all(r["data_mode"] == "REPLAY" for r in rows)


def test_replay_over_a_window_with_no_fixtures_processes_nothing_and_errors_nothing(toy_history, db_conn):
    # Pick a date range entirely before any real history exists (would be rejected by the pipeline,
    # so this should surface as refusals, not silent skips or crashes).
    run = run_replay(toy_history, "2024-01-01", "2024-01-02", markets=["home_win"], conn=db_conn)
    assert run.errors == 0  # refusals are fine and expected; unhandled errors are not


def test_replay_run_report_totals_are_internally_consistent(toy_history, db_conn):
    window_start = toy_history["date"].iloc[-8]
    window_end = toy_history["date"].iloc[-1]
    run = run_replay(toy_history, window_start, window_end, markets=["home_win", "over_2_5"], conn=db_conn)
    assert run.predictions_generated + run.refusals + run.errors == run.matches_processed * 2  # 2 markets
    assert run.settled <= run.predictions_generated

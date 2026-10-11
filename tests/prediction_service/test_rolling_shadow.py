"""
Regression tests for src/prediction_service/rolling_shadow.py (Phase 4 §16).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.prediction_service import snapshot_db
from src.prediction_service.rolling_shadow import run_rolling_shadow


@pytest.fixture()
def db_conn(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_db, "_DB_PATH", tmp_path / "test_predictions.db")
    conn = snapshot_db.get_connection()
    yield conn
    conn.close()


@pytest.fixture()
def toy_history():
    rng = np.random.default_rng(3)
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


def test_rolling_shadow_never_lets_the_challenger_change_the_production_prediction(toy_history, db_conn):
    from src.prediction_service.champion_registry import get_champion

    window_start = toy_history["date"].iloc[-6]
    window_end = toy_history["date"].iloc[-1]
    run_rolling_shadow(toy_history, "home_win", "xgboost_real_data_calibrated", window_start, window_end, conn=db_conn)

    # Every production prediction row's champion_model must be whatever
    # champion_registry actually selected for this market — never silently
    # replaced by the challenger being shadow-tested alongside it.
    real_champion = get_champion("home_win")
    rows = db_conn.execute("SELECT champion_model FROM predictions WHERE is_latest = 1").fetchall()
    assert len(rows) > 0
    for row in rows:
        assert row["champion_model"] == real_champion

    # Shadow rows exist separately, one per production prediction, never conflated with it.
    shadow_rows = db_conn.execute("SELECT * FROM shadow_predictions").fetchall()
    assert len(shadow_rows) == len(rows)
    for row in shadow_rows:
        assert row["challenger_model"] == "xgboost_real_data_calibrated"
        assert row["champion_model"] == real_champion


def test_rolling_shadow_settles_only_after_the_clock_passes_the_match(toy_history, db_conn):
    window_start = toy_history["date"].iloc[-6]
    window_end = toy_history["date"].iloc[-1]
    run_rolling_shadow(toy_history, "home_win", "xgboost_real_data_calibrated", window_start, window_end, conn=db_conn)

    rows = db_conn.execute(
        "SELECT prediction_timestamp, evaluated_at FROM shadow_predictions WHERE evaluated_at IS NOT NULL"
    ).fetchall()
    assert len(rows) > 0
    for row in rows:
        assert pd.Timestamp(row["evaluated_at"]) > pd.Timestamp(row["prediction_timestamp"])


def test_rolling_shadow_summary_reports_real_evaluated_count(toy_history, db_conn):
    window_start = toy_history["date"].iloc[-8]
    window_end = toy_history["date"].iloc[-1]
    run = run_rolling_shadow(toy_history, "home_win", "xgboost_real_data_calibrated", window_start, window_end, conn=db_conn)
    assert run.summary["n_evaluated"] == run.shadow_predictions_generated


def test_rolling_shadow_report_totals_are_consistent(toy_history, db_conn):
    window_start = toy_history["date"].iloc[-6]
    window_end = toy_history["date"].iloc[-1]
    run = run_rolling_shadow(toy_history, "home_win", "xgboost_real_data_calibrated", window_start, window_end, conn=db_conn)
    assert run.shadow_predictions_generated + run.refusals + run.errors == run.matches_processed

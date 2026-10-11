"""
Adversarial test suite (Phase 4 §28/§29 §26 list): temporal/result
leakage through the FULL pipeline (not just the isolated feature engine),
stale-data refusal, and cross-references to where every other required
adversarial scenario is already covered.

This file's job is specifically the scenarios not already exercised
end-to-end elsewhere:
  - result leakage through the whole predict() call, not just generate_features()
  - stale LIVE data refusal (new in this pass — see data_freshness.py)
  - future-dated information rejected by the full pipeline, not just the
    feature engine unit tests

Existing adversarial coverage this file does NOT duplicate (see the
referenced files for the actual tests):
  - temporal/result leakage at the feature-engine level:
    tests/prediction_service/test_feature_engine.py,
    tests/real_data/test_point_in_time_leakage.py
  - same-day fixture ordering determinism:
    tests/prediction_service/test_historical_simulation_parity.py
  - cold-start / insufficient-history refusal:
    tests/prediction_service/test_prediction_pipeline.py
    ::test_true_cold_start_team_is_refused_not_low_confidence
  - provider failure fail-closed (network/auth/rate-limit/timeout/malformed):
    tests/prediction_service/providers/test_live_provider.py
  - duplicate/conflicting fixture quarantine:
    tests/prediction_service/providers/test_replay_and_manual_provider.py
  - timestamp anomaly / future-dated info rejection:
    tests/prediction_service/test_prediction_pipeline.py
    ::test_prediction_timestamp_at_or_after_kickoff_is_refused
  - model/feature-engine failure fail-closed:
    tests/prediction_service/test_prediction_pipeline.py (whole file)
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.prediction_service import snapshot_db
from src.prediction_service.data_contract import DataMode
from src.prediction_service.prediction_pipeline import predict, PredictionRefused


@pytest.fixture()
def real_matches():
    return pd.read_csv("data/real_historical/matches.csv")


@pytest.fixture()
def db_conn(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_db, "_DB_PATH", tmp_path / "test_predictions.db")
    conn = snapshot_db.get_connection()
    yield conn
    conn.close()


# ── Result leakage through the FULL pipeline ────────────────────────

def test_full_pipeline_prediction_is_unaffected_by_a_result_planted_in_the_future(real_matches, db_conn):
    """The raw DataFrame technically CONTAINS results for matches after
    the prediction timestamp — this proves predict() end-to-end (not just
    generate_features in isolation) never lets them influence the output,
    even when an adversarial/corrupted future row is injected."""
    r1 = predict(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-01-01T10:00:00Z", kickoff_timestamp="2025-01-01T15:00:00Z",
        historical_matches=real_matches, conn=db_conn,
    )

    corrupted = real_matches.copy()
    future_mask = pd.to_datetime(corrupted["date"]) >= pd.Timestamp("2025-01-02")
    corrupted.loc[future_mask, "home_goals"] = 99
    corrupted.loc[future_mask, "away_goals"] = 0

    r2 = predict(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-01-01T10:00:00Z", kickoff_timestamp="2025-01-01T15:00:00Z",
        historical_matches=corrupted, conn=db_conn,
    )
    assert r1.raw_probability == pytest.approx(r2.raw_probability)
    assert r1.calibrated_probability == pytest.approx(r2.calibrated_probability)


def test_full_pipeline_rejects_a_dataset_containing_only_future_matches(real_matches):
    only_future = real_matches[pd.to_datetime(real_matches["date"]) >= pd.Timestamp("2025-05-01")].copy()
    with pytest.raises(PredictionRefused) as exc_info:
        predict(
            "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
            prediction_timestamp="2020-01-01T10:00:00Z", kickoff_timestamp="2020-01-01T15:00:00Z",
            historical_matches=only_future,
        )
    assert exc_info.value.stage_failed == "feature_generation"


# ── Stale LIVE data refusal (Phase 4 §10) ───────────────────────────

def test_live_mode_refuses_prediction_on_stale_dataset(real_matches, db_conn):
    """The real dataset's latest match is 2025-05-25. Asking for a LIVE
    prediction dated months later must refuse — the underlying data can't
    honestly claim to represent 'now'."""
    with pytest.raises(PredictionRefused) as exc_info:
        predict(
            "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
            prediction_timestamp="2025-12-01T10:00:00Z", kickoff_timestamp="2025-12-01T15:00:00Z",
            historical_matches=real_matches, conn=db_conn, data_mode=DataMode.LIVE,
        )
    assert exc_info.value.stage_failed == "stale_data"
    assert "STALE_DATA" in exc_info.value.reason


def test_replay_mode_does_not_apply_the_staleness_check(real_matches, db_conn):
    """The exact same 'far future relative to dataset' timestamp that
    triggers stale_data under LIVE must NOT be refused for staleness under
    REPLAY — replaying a fixed historical snapshot is not a staleness
    problem, it's the entire point of replay mode. (It may succeed, or
    refuse for a wholly different reason — this only asserts stale_data
    specifically never fires here.)"""
    try:
        predict(
            "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
            prediction_timestamp="2025-12-01T10:00:00Z", kickoff_timestamp="2025-12-01T15:00:00Z",
            historical_matches=real_matches, conn=db_conn, data_mode=DataMode.REPLAY,
        )
    except PredictionRefused as e:
        assert e.stage_failed != "stale_data"


def test_live_mode_accepts_a_prediction_right_at_the_dataset_freshness_edge(real_matches, db_conn):
    """One day after the dataset's latest match is well within the
    default 3-day staleness threshold and must NOT be refused for
    staleness (it may still be refused for other reasons, e.g. no
    fixture that day — this test only asserts stale_data isn't the cause)."""
    try:
        predict(
            "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
            prediction_timestamp="2025-05-26T10:00:00Z", kickoff_timestamp="2025-05-26T15:00:00Z",
            historical_matches=real_matches, conn=db_conn, data_mode=DataMode.LIVE,
        )
    except PredictionRefused as e:
        assert e.stage_failed != "stale_data"

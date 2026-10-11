"""
Regression tests for prediction snapshot immutability/versioning
(Phase 4 §11/§12/§27).
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.prediction_service import snapshot_db
from src.prediction_service.prediction_pipeline import predict
from src.prediction_service.post_match_eval import evaluate_prediction
from src.prediction_service.data_contract import DataMode


@pytest.fixture()
def real_matches():
    return pd.read_csv("data/real_historical/matches.csv")


@pytest.fixture()
def db_conn(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_db, "_DB_PATH", tmp_path / "test_predictions.db")
    conn = snapshot_db.get_connection()
    yield conn
    conn.close()


def test_regenerating_a_prediction_creates_a_new_version_not_an_overwrite(real_matches, db_conn):
    r1 = predict(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, conn=db_conn,
    )
    assert r1.version == 1

    r2 = predict(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, conn=db_conn,
    )
    assert r2.version == 2
    assert r1.prediction_id == r2.prediction_id  # same logical prediction, different version

    versions = snapshot_db.get_all_prediction_versions(db_conn, r1.prediction_id)
    assert len(versions) == 2
    assert versions[0]["version"] == 1 and versions[0]["is_latest"] == 0
    assert versions[1]["version"] == 2 and versions[1]["is_latest"] == 1


def test_original_version_content_is_never_mutated_by_a_later_regeneration(real_matches, db_conn):
    r1 = predict(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, conn=db_conn,
    )
    v1_before = snapshot_db.get_prediction_version(db_conn, r1.prediction_id, 1)

    predict(  # regenerate
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, conn=db_conn,
    )
    v1_after = snapshot_db.get_prediction_version(db_conn, r1.prediction_id, 1)

    # Every content column of version 1 must be byte-identical — only
    # is_latest (a marker, not content) is allowed to have changed.
    for key in v1_before:
        if key == "is_latest":
            continue
        assert v1_before[key] == v1_after[key], f"version 1's {key!r} was mutated — immutability broken"
    assert v1_before["is_latest"] == 1 and v1_after["is_latest"] == 0


def test_get_prediction_returns_the_latest_version_by_default(real_matches, db_conn):
    r1 = predict(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, conn=db_conn,
    )
    r2 = predict(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, conn=db_conn,
    )
    latest = snapshot_db.get_prediction(db_conn, r1.prediction_id)
    assert latest["version"] == 2


def test_predictions_are_tagged_with_the_correct_data_mode(real_matches, db_conn):
    result = predict(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, conn=db_conn, data_mode=DataMode.REPLAY,
    )
    assert result.data_mode == DataMode.REPLAY
    saved = snapshot_db.get_prediction(db_conn, result.prediction_id)
    assert saved["data_mode"] == "REPLAY"


def test_default_data_mode_is_replay_never_silently_live(real_matches, db_conn):
    """A caller that forgets to pass data_mode must never accidentally get
    a prediction labeled LIVE — the honest default in this environment is
    REPLAY (see prediction_pipeline.py's docstring)."""
    result = predict(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, conn=db_conn,
    )
    assert result.data_mode == DataMode.REPLAY


def test_evaluating_a_prediction_before_its_own_prediction_timestamp_is_rejected(real_matches, db_conn):
    result = predict(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, conn=db_conn,
    )
    with pytest.raises(ValueError, match="BEFORE this prediction's own prediction_timestamp"):
        evaluate_prediction(db_conn, result.prediction_id, actual_outcome=1, evaluated_at="2025-05-25T09:00:00Z")


def test_evaluation_records_which_version_was_scored(real_matches, db_conn):
    r1 = predict(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, conn=db_conn,
    )
    predict(  # regenerate to version 2 before evaluating
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, conn=db_conn,
    )
    record = evaluate_prediction(db_conn, r1.prediction_id, actual_outcome=1)
    assert record["version_evaluated"] == 2

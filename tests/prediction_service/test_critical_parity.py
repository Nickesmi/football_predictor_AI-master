"""
THE critical replay/production parity test (Phase 4 §29).

Formalizes scripts/historical_simulation.py's proof as a real pytest test
that fails the suite (and therefore CI) on any mismatch, rather than a
standalone script whose output a human has to remember to check.

Compares, for real 2024-25 matches replayed through replay_engine.py
against an INDEPENDENT direct call to prediction_pipeline.predict() for
the exact same inputs:
    - every FEATURE_COLUMNS value (via the reference build_point_in_time_features())
    - the champion model selected
    - raw probability
    - calibrated probability
    - confidence score
    - OOD severity
    - the meets_confidence_threshold decision

Any mismatch here means the "one canonical feature/prediction path" claim
in PRODUCTION_ARCHITECTURE_REPORT.md is false — this test is what keeps
that claim honest going forward, not just true on the day it was written.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.ml.point_in_time import build_point_in_time_features, FEATURE_COLUMNS
from src.prediction_service import snapshot_db
from src.prediction_service.prediction_pipeline import predict, PredictionRefused
from src.prediction_service.replay_engine import run_replay

MARKETS = ["home_win", "over_2_5"]
# A small, real, recent window from the frozen 2024-25 holdout — kept
# short so this runs fast enough for every CI run, not just nightly.
REPLAY_START = "2025-05-18"
REPLAY_END = "2025-05-20"


@pytest.fixture()
def real_matches():
    return pd.read_csv("data/real_historical/matches.csv").sort_values("date").reset_index(drop=True)


@pytest.fixture()
def db_conn(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_db, "_DB_PATH", tmp_path / "test_predictions.db")
    conn = snapshot_db.get_connection()
    yield conn
    conn.close()


def test_replay_predictions_exactly_match_independent_direct_predict_calls(real_matches, db_conn):
    run = run_replay(real_matches, REPLAY_START, REPLAY_END, markets=MARKETS, conn=db_conn)
    assert run.errors == 0, f"replay run hit unexpected errors: {run.error_details}"
    assert run.predictions_generated > 0, "test window produced zero predictions — widen REPLAY_START/END"

    rows = db_conn.execute(
        "SELECT * FROM predictions WHERE is_latest = 1 ORDER BY prediction_id"
    ).fetchall()
    assert len(rows) == run.predictions_generated

    checked = 0
    for row in rows:
        direct_conn = snapshot_db.get_connection()  # separate physical connection, same test DB file
        try:
            direct_result = predict(
                row["home_team"], row["away_team"], row["league"], row["market"],
                prediction_timestamp=row["prediction_timestamp"],
                kickoff_timestamp=pd.Timestamp(row["prediction_timestamp"]) + pd.Timedelta(hours=5),
                historical_matches=real_matches, match_id=row["match_id"], conn=direct_conn,
            )
        except PredictionRefused as e:
            pytest.fail(f"direct predict() refused for a match replay succeeded on: {e.stage_failed} — {e.reason}")

        assert direct_result.champion_model == row["champion_model"]
        assert direct_result.raw_probability == pytest.approx(row["raw_probability"], abs=1e-9)
        assert direct_result.calibrated_probability == pytest.approx(row["calibrated_probability"], abs=1e-9)
        assert direct_result.confidence.score == pytest.approx(row["confidence_score"], abs=1e-9)
        assert direct_result.ood.severity == row["ood_severity"]
        assert int(direct_result.meets_confidence_threshold) == row["meets_confidence_threshold"]
        checked += 1

    assert checked == len(rows)


def test_replay_feature_values_exactly_match_the_reference_batch_computation(real_matches, db_conn):
    """Same claim, at the feature level — every value the replay engine's
    predictions were built from must equal build_point_in_time_features()'s
    output for that exact match, not an approximation of it."""
    import json

    run_replay(real_matches, REPLAY_START, REPLAY_END, markets=["home_win"], conn=db_conn)
    rows = db_conn.execute(
        "SELECT match_id, feature_snapshot_json FROM predictions WHERE is_latest = 1 AND market = 'home_win'"
    ).fetchall()
    assert len(rows) > 0

    reference = build_point_in_time_features(real_matches)

    checked = 0
    for row in rows:
        ref_row = reference[reference["match_id"] == row["match_id"]]
        if len(ref_row) == 0:
            continue  # a synthetic/live-only match_id, not applicable here (shouldn't happen in replay mode)
        ref_row = ref_row.iloc[0]
        stored_features = json.loads(row["feature_snapshot_json"])
        for col in FEATURE_COLUMNS:
            assert stored_features[col] == pytest.approx(float(ref_row[col]), abs=1e-6), (
                f"match {row['match_id']} feature {col}: stored={stored_features[col]} reference={ref_row[col]}"
            )
        checked += 1
    assert checked > 0

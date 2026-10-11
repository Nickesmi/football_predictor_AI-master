"""
Regression tests for src/prediction_service/observability.py (Phase 4 §26)
and its wiring into the pipeline/providers/shadow/monitoring call sites.
"""

from __future__ import annotations

import json
import logging

import pandas as pd
import pytest

from src.prediction_service import observability, snapshot_db
from src.prediction_service.prediction_pipeline import predict, PredictionRefused


def test_emit_rejects_unknown_event_type():
    with pytest.raises(ValueError, match="unknown observability event_type"):
        observability._emit("not_a_real_event_type", foo="bar")


def test_every_public_log_function_only_emits_registered_event_types(caplog):
    caplog.set_level(logging.INFO, logger="prediction_service")
    observability.log_prediction_generated("p1", "home_win", "elo", "v1", "fe1", "REPLAY")
    observability.log_prediction_refused("r1", "timestamp_validation", "bad timestamp")
    observability.log_provider_health("test_provider", "AVAILABLE", 12.3, "ok")
    observability.log_shadow_prediction("s1", "home_win", "elo", "xgboost_real_data_calibrated")
    observability.log_settlement("p1", "home_win", True, 0.04)
    observability.log_drift_detected("home_win", 0.05, 0.03)

    assert len(caplog.records) == 6
    for record in caplog.records:
        payload = json.loads(record.message)
        assert payload["event_type"] in observability.EVENT_TYPES
        assert "timestamp" in payload


def test_prediction_generation_emits_a_structured_event(caplog, tmp_path, monkeypatch):
    caplog.set_level(logging.INFO, logger="prediction_service")
    monkeypatch.setattr(snapshot_db, "_DB_PATH", tmp_path / "test_predictions.db")
    conn = snapshot_db.get_connection()
    real_matches = pd.read_csv("data/real_historical/matches.csv")

    predict(
        "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
        prediction_timestamp="2025-05-25T10:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
        historical_matches=real_matches, conn=conn,
    )

    events = [json.loads(r.message) for r in caplog.records if r.name == "prediction_service"]
    generated = [e for e in events if e["event_type"] == "prediction_generated"]
    assert len(generated) == 1
    assert generated[0]["market"] == "home_win"
    assert generated[0]["data_mode"] == "REPLAY"


def test_prediction_refusal_emits_a_structured_event(caplog, tmp_path, monkeypatch):
    caplog.set_level(logging.INFO, logger="prediction_service")
    monkeypatch.setattr(snapshot_db, "_DB_PATH", tmp_path / "test_predictions.db")
    conn = snapshot_db.get_connection()
    real_matches = pd.read_csv("data/real_historical/matches.csv")

    with pytest.raises(PredictionRefused):
        predict(
            "Arsenal FC", "Chelsea FC", "English Premier League", "home_win",
            prediction_timestamp="2025-05-25T16:00:00Z", kickoff_timestamp="2025-05-25T15:00:00Z",
            historical_matches=real_matches, conn=conn,
        )

    events = [json.loads(r.message) for r in caplog.records if r.name == "prediction_service"]
    refused = [e for e in events if e["event_type"] == "prediction_refused"]
    assert len(refused) == 1
    assert refused[0]["stage_failed"] == "timestamp_validation"

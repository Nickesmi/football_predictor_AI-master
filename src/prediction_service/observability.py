"""
Structured observability (Phase 4 §26).

A thin wrapper over the standard `logging` module (integrates with
whatever log aggregation a real deployment already has) that emits
structured, machine-parseable events for every stage Phase 4 asks for.
Every event is a single JSON-serializable dict under the `event` extra
field, with a stable `event_type` so downstream tooling can filter/alert
on specific event types without regex-parsing free-text log messages.

This module defines the complete event vocabulary Phase 4 §26 asks for.
Not every event type is wired into a call site yet in this pass — see
PHASE_4_LIVE_SHADOW_REPORT.md's "Observability" section for exactly which
ones are actually emitted today (prediction generation/refusal, provider
health, shadow predictions, settlement) versus defined-but-not-yet-wired
(e.g. per-record ingestion counts for a live provider, which cannot be
exercised for real in this environment — see AUDIT_REPORT.md).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("prediction_service")

EVENT_TYPES = {
    "ingestion_attempt", "ingestion_success", "ingestion_failure",
    "provider_latency", "provider_freshness",
    "records_received", "records_rejected", "records_duplicate",
    "schema_failure", "timestamp_failure",
    "prediction_generated", "prediction_refused",
    "shadow_prediction", "settlement", "calibration_event", "drift_detected", "ood_rate",
}


def _emit(event_type: str, **fields: Any) -> None:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown observability event_type {event_type!r} — add it to EVENT_TYPES first")
    event = {"event_type": event_type, "timestamp": datetime.now(timezone.utc).isoformat(), **fields}
    logger.info(json.dumps(event, default=str), extra={"event": event})


def log_prediction_generated(prediction_id: str, market: str, champion_model: str,
                              model_version: str, feature_engine_version: str, data_mode: str) -> None:
    _emit("prediction_generated", prediction_id=prediction_id, market=market, champion_model=champion_model,
          model_version=model_version, feature_engine_version=feature_engine_version, data_mode=data_mode)


def log_prediction_refused(request_id: str, stage_failed: str, reason: str, market: str = "") -> None:
    _emit("prediction_refused", request_id=request_id, stage_failed=stage_failed, reason=reason, market=market)


def log_provider_health(provider_name: str, status: str, latency_ms: float | None, detail: str) -> None:
    _emit("provider_latency", provider_name=provider_name, status=status, latency_ms=latency_ms, detail=detail)


def log_provider_freshness(provider_name: str, age_seconds: float, is_stale: bool) -> None:
    _emit("provider_freshness", provider_name=provider_name, age_seconds=age_seconds, is_stale=is_stale)


def log_records_received(source: str, count: int) -> None:
    _emit("records_received", source=source, count=count)


def log_records_rejected(source: str, count: int, reasons: dict) -> None:
    _emit("records_rejected", source=source, count=count, reasons=reasons)


def log_records_duplicate(source: str, count: int) -> None:
    _emit("records_duplicate", source=source, count=count)


def log_schema_failure(source: str, detail: str) -> None:
    _emit("schema_failure", source=source, detail=detail)


def log_timestamp_failure(source: str, detail: str) -> None:
    _emit("timestamp_failure", source=source, detail=detail)


def log_shadow_prediction(shadow_id: str, market: str, champion_model: str, challenger_model: str) -> None:
    _emit("shadow_prediction", shadow_id=shadow_id, market=market, champion_model=champion_model, challenger_model=challenger_model)


def log_settlement(prediction_id: str, market: str, correct: bool, brier_contribution: float) -> None:
    _emit("settlement", prediction_id=prediction_id, market=market, correct=correct, brier_contribution=brier_contribution)


def log_drift_detected(market: str, brier_delta: float, threshold: float) -> None:
    _emit("drift_detected", market=market, brier_delta=brier_delta, threshold=threshold)


def log_ood_rate(market: str, window_n: int, ood_count: int) -> None:
    _emit("ood_rate", market=market, window_n=window_n, ood_count=ood_count, rate=round(ood_count / window_n, 4) if window_n else None)

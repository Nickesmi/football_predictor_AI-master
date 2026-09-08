"""
Real-time prediction pipeline orchestrator (Phase 3 §4/§13/§14/§19).

    DATA AVAILABILITY
        -> TIMESTAMP VALIDATION
        -> CHAMPION LOOKUP + PROVENANCE GATE
        -> POINT-IN-TIME FEATURE GENERATION
        -> MODEL PREDICTION (champion + all available, for agreement)
        -> CALIBRATION
        -> MODEL AGREEMENT
        -> OUT-OF-DISTRIBUTION CHECK
        -> LINEUP UNCERTAINTY
        -> CONFIDENCE ENGINE
        -> MARKET THRESHOLD CHECK
        -> NO-BET ENGINE
        -> SNAPSHOT PERSISTED
        -> PredictionResult

Every stage can raise PredictionRefused. There is no code path that
substitutes a default/fabricated value for a failed stage — a refusal is
always a hard stop, always logged (snapshot_db.log_refusal), and always
propagated to the caller as "NO PREDICTION — INSUFFICIENT RELIABLE
INFORMATION" plus the specific reason.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from src.ml.model_provenance import real_data_model_provenance
from src.prediction_service import champion_registry, live_models, model_agreement, ood_detection
from src.prediction_service import confidence_engine as confidence_engine_mod
from src.prediction_service import thresholds as thresholds_mod
from src.prediction_service import snapshot_db
from src.prediction_service.feature_engine import generate_features, FeatureGenerationError, FeatureSnapshot
from src.prediction_service.lineup_info import LineupInfo, compute_lineup_uncertainty_penalty
from src.prediction_service.no_bet_engine import evaluate_bet, BettingRecommendation
from src.prediction_service.model_agreement import AgreementResult
from src.prediction_service.ood_detection import OODResult
from src.prediction_service.confidence_engine import ConfidenceResult

PIPELINE_VERSION = "phase3-v1"

# Maps a champion_registry model name to the key live_models.predict_all_available_models() returns.
_CHAMPION_TO_LIVE_KEY = {
    "frequency": "frequency", "home_baseline": "home_baseline", "elo": "elo",
    "poisson_real_data": "poisson_real_data", "dixon_coles": "dixon_coles",
    "xgboost_real_data_calibrated": "xgboost_real_data",
}


class PredictionRefused(Exception):
    """Raised for every NO-PREDICTION outcome. Always carries a NoPrediction
    with a specific stage and reason — never a generic failure."""

    def __init__(self, stage_failed: str, reason: str):
        self.stage_failed = stage_failed
        self.reason = reason
        super().__init__(f"NO PREDICTION — INSUFFICIENT RELIABLE INFORMATION [{stage_failed}]: {reason}")


@dataclass
class PredictionResult:
    prediction_id: str
    match_id: str
    home_team: str
    away_team: str
    league: str
    market: str
    prediction_timestamp: str
    kickoff_timestamp: str
    feature_snapshot_timestamp: str
    champion_model: str
    model_provenance: str
    calibration_version: str
    raw_probability: float
    calibrated_probability: float
    confidence: ConfidenceResult
    data_sufficiency: int
    agreement: AgreementResult
    ood: OODResult
    meets_confidence_threshold: bool
    threshold_used: Optional[float]
    betting_recommendation: BettingRecommendation
    pipeline_version: str = PIPELINE_VERSION


def _deterministic_prediction_id(match_id: str, market: str, prediction_timestamp: pd.Timestamp) -> str:
    key = f"{match_id}|{market}|{prediction_timestamp.isoformat()}"
    return "pred_" + hashlib.sha1(key.encode()).hexdigest()[:16]


def predict(
    home_team: str,
    away_team: str,
    league: str,
    market: str,
    prediction_timestamp,
    kickoff_timestamp,
    historical_matches: pd.DataFrame,
    match_id: Optional[str] = None,
    odds: Optional[float] = None,
    odds_timestamp: Optional[str] = None,
    lineup_info: Optional[LineupInfo] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> PredictionResult:
    """The real-time prediction service entrypoint (Phase 3 §13). Raises
    PredictionRefused on any stage failure — callers MUST catch it and
    treat it as "no prediction", never substitute a default."""
    conn = conn or snapshot_db.get_connection()
    pred_ts_str = str(prediction_timestamp)

    def refuse(stage: str, reason: str):
        snapshot_db.log_refusal(conn, {
            "request_id": _deterministic_prediction_id(match_id or f"{home_team}|{away_team}", market, pd.Timestamp(prediction_timestamp)),
            "home_team": home_team, "away_team": away_team, "league": league, "market": market,
            "prediction_timestamp": pred_ts_str, "stage_failed": stage, "reason": reason,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        raise PredictionRefused(stage, reason)

    # ── STAGE 1: absolute timestamp rule (§14) ──
    if kickoff_timestamp is None:
        refuse("timestamp_validation", "kickoff_timestamp is required and was not provided")
    pred_ts = pd.Timestamp(prediction_timestamp)
    kickoff_ts = pd.Timestamp(kickoff_timestamp)
    if not (pred_ts < kickoff_ts):
        refuse("timestamp_validation",
               f"INVALID_PREDICTION_TIME: prediction_timestamp ({pred_ts.isoformat()}) must be strictly "
               f"before kickoff_timestamp ({kickoff_ts.isoformat()})")

    # ── STAGE 2: data availability ──
    if historical_matches is None or len(historical_matches) == 0:
        refuse("data_availability", "no historical match data available")

    # ── STAGE 3: champion lookup + provenance gate ──
    try:
        champion = champion_registry.get_champion(market)
    except FileNotFoundError as e:
        refuse("provenance_gate", f"champion registry unavailable: {e}")
    if champion is None:
        refuse("champion_selection", f"no validated champion model exists for market '{market}'")

    if champion.startswith("xgboost"):
        prov = real_data_model_provenance(market)
        if not prov.get("production_validated"):
            refuse("provenance_gate",
                   f"champion '{champion}' failed the provenance re-check for market '{market}' "
                   "(production_validated=False) — refusing even though it was recorded as champion")

    live_key = _CHAMPION_TO_LIVE_KEY.get(champion)
    if live_key is None:
        refuse("provenance_gate", f"champion '{champion}' has no live model wiring (unknown model name)")

    # ── STAGE 4: point-in-time feature generation ──
    try:
        snapshot: FeatureSnapshot = generate_features(
            home_team, away_team, prediction_timestamp, league, historical_matches, match_id=match_id,
        )
    except FeatureGenerationError as e:
        refuse("feature_generation", str(e))

    # ── STAGE 5: model prediction (champion + all available for agreement) ──
    try:
        all_preds = live_models.predict_all_available_models(snapshot.raw_row, historical_matches)
    except Exception as e:
        refuse("model_prediction", f"model prediction failed: {e}")

    champion_probs = all_preds.get(live_key)
    raw_p = model_agreement.extract_market_probability(champion_probs, market) if champion_probs else None
    if raw_p is None:
        refuse("model_prediction", f"champion model '{champion}' does not cover market '{market}' for this match")

    # ── STAGE 6: calibration ──
    # Only the XGBoost artifacts went through an explicit isotonic fit
    # (src/ml/real_data_trainer.py::calibrate(), fit on the 2023-24
    # validation season — see REAL_DATA_BACKTEST_REPORT.md). The baseline
    # models' probabilities ARE the model output; there is no separate
    # calibration step to apply, and their own backtest ECE numbers (see
    # champion_registry evidence) are the honest measure of how well
    # calibrated they already are.
    if live_key == "xgboost_real_data":
        calibrated_p = raw_p  # isotonic calibration already applied inside live_models.predict_xgboost
        calibration_version = "isotonic_v1_fit_on_2023-24_validation_season"
    else:
        calibrated_p = raw_p
        calibration_version = "none_direct_model_output"

    # ── STAGE 7: model agreement ──
    # Compare only substantive, match-aware models (see
    # model_agreement.AGREEMENT_COMPARISON_MODELS) — the naive
    # frequency/home_baseline reference points would otherwise make every
    # match look like "low agreement" regardless of how well the real
    # models actually agree.
    agreement = model_agreement.compute_agreement(model_agreement.filter_for_agreement(all_preds), market)

    # ── STAGE 8: out-of-distribution check ──
    historical_features = live_models.get_full_history_features(historical_matches)
    ood = ood_detection.check_ood(
        snapshot.raw_row, historical_features,
        snapshot.home_is_cold_start, snapshot.away_is_cold_start, snapshot.data_sufficiency,
    )
    if ood.severity == "severe":
        refuse("ood_check", f"severe out-of-distribution match: {'; '.join(ood.reasons)}")

    # ── STAGE 9: lineup uncertainty ──
    lineup_penalty, lineup_reasons = compute_lineup_uncertainty_penalty(lineup_info)

    # ── STAGE 10: confidence engine ──
    champion_evidence = champion_registry.get_champion_evidence(market)
    champion_metrics = champion_registry.get_champion_holdout_metrics(market)
    confidence = confidence_engine_mod.compute_confidence(
        champion_evidence, champion_metrics, agreement, ood, snapshot.data_sufficiency, lineup_penalty,
    )

    # ── STAGE 11: market-specific threshold ──
    try:
        threshold = thresholds_mod.get_threshold(market)
    except FileNotFoundError:
        threshold = None
    meets_threshold = threshold is not None and calibrated_p >= threshold

    # ── STAGE 12: no-bet engine (prediction vs. betting recommendation, §15) ──
    betting = evaluate_bet(calibrated_p, prediction_timestamp, odds=odds, odds_timestamp=odds_timestamp)
    if betting.status == "BET" and not meets_threshold:
        betting = BettingRecommendation(
            status="NO_BET",
            reason="raw expected value was positive, but the calibrated probability did not clear the "
                   "evidence-backed market threshold (or none exists) — see thresholds.py; a prediction "
                   "can still be reported, just not recommended as a bet",
            expected_value=betting.expected_value, implied_probability=betting.implied_probability,
            edge=betting.edge, odds_used=betting.odds_used, odds_timestamp=betting.odds_timestamp,
        )

    prediction_id = _deterministic_prediction_id(snapshot.match_id, market, pred_ts)
    result = PredictionResult(
        prediction_id=prediction_id, match_id=snapshot.match_id,
        home_team=home_team, away_team=away_team, league=league, market=market,
        prediction_timestamp=snapshot.prediction_timestamp, kickoff_timestamp=kickoff_ts.isoformat(),
        feature_snapshot_timestamp=snapshot.feature_snapshot_timestamp,
        champion_model=champion, model_provenance=("REAL_DATA_TRAINED" if live_key == "xgboost_real_data" else "BASELINE_MODEL"),
        calibration_version=calibration_version,
        raw_probability=round(raw_p, 4), calibrated_probability=round(calibrated_p, 4),
        confidence=confidence, data_sufficiency=snapshot.data_sufficiency,
        agreement=agreement, ood=ood,
        meets_confidence_threshold=meets_threshold, threshold_used=threshold,
        betting_recommendation=betting,
    )

    snapshot_db.save_prediction(conn, {
        "prediction_id": prediction_id, "match_id": snapshot.match_id,
        "home_team": home_team, "away_team": away_team, "league": league, "market": market,
        "prediction_timestamp": snapshot.prediction_timestamp,
        "feature_snapshot_timestamp": snapshot.feature_snapshot_timestamp,
        "feature_snapshot_json": _json_dumps(snapshot.features),
        "champion_model": champion, "model_provenance": result.model_provenance,
        "calibration_version": calibration_version,
        "raw_probability": result.raw_probability, "calibrated_probability": result.calibrated_probability,
        "confidence_score": confidence.score, "confidence_tier": confidence.tier,
        "confidence_components_json": _json_dumps(confidence.components),
        "data_sufficiency": snapshot.data_sufficiency,
        "model_agreement_level": agreement.agreement_level, "model_agreement_spread": agreement.max_pairwise_spread,
        "ood_severity": ood.severity, "ood_reasons_json": _json_dumps(ood.reasons),
        "meets_confidence_threshold": int(meets_threshold), "threshold_used": threshold,
        "betting_status": betting.status, "betting_reason": betting.reason,
        "pipeline_version": PIPELINE_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    return result


def _json_dumps(obj) -> str:
    import json
    return json.dumps(obj, default=str)

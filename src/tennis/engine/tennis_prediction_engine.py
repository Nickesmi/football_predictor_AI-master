"""
tennis_prediction_engine.py
============================
Orchestrates the full tennis prediction pipeline:
  features → model → predictions → database storage

This is the only module that writes to tennis_predictions.
It never touches football tables.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from src.tennis.ml.tennis_feature_builder import build_features
from src.tennis.ml.tennis_model import predict_match

logger = logging.getLogger("football_predictor.tennis")

MODEL_VERSION = "v1.0-elo"


def _insert_prediction_row(
    conn: sqlite3.Connection,
    match_id: str,
    now: str,
    market_type: str,
    selection: str,
    probability: float,
    fair_odds: float,
    confidence_score: float,
    model_version: str,
    features_json: str,
) -> None:
    conn.execute(
        """
        INSERT INTO tennis_predictions
          (match_id, prediction_time, market_type, selection,
           predicted_probability, fair_odds, confidence_score,
           model_version, features_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            match_id,
            now,
            market_type,
            selection,
            probability,
            fair_odds,
            confidence_score,
            model_version,
            features_json,
        )
    )


def _store_predictions(
    conn: sqlite3.Connection,
    match_id: str,
    prediction: dict,
    features: dict,
) -> None:
    """Write prediction markets to tennis_predictions table."""
    now = datetime.now(timezone.utc).isoformat()
    features_json = json.dumps({
        k: v for k, v in features.items()
        if k not in ("missing_features", "h2h_recent3")
    })

    conn.execute("DELETE FROM tennis_predictions WHERE match_id = ?", (match_id,))

    # ── No pick governance row ────────────────────────────────────────────────
    mw = prediction["match_winner"]
    if mw is None:
        _insert_prediction_row(
            conn, match_id, now, "match_winner", "NO PICK",
            0.0, 0.0, 0.0, prediction.get("model_version", MODEL_VERSION), features_json
        )
        conn.commit()
        return

    for pick in prediction.get("all_picks", []):
        probability = float(pick.get("probability", 0.0)) / 100.0
        _insert_prediction_row(
            conn=conn,
            match_id=match_id,
            now=now,
            market_type=pick.get("market_type") or pick.get("market") or "unknown",
            selection=pick.get("selection") or "Unknown",
            probability=probability,
            fair_odds=float(pick.get("fair_odds") or 0.0),
            confidence_score=float(pick.get("confidence_score") or probability),
            model_version=prediction.get("model_version", MODEL_VERSION),
            features_json=features_json,
        )

    conn.commit()


def predict_and_store(
    conn: sqlite3.Connection,
    match: dict,
) -> Optional[dict]:
    """
    Full pipeline for one match:
      1. Build features
      2. Run model
      3. Store predictions to DB
      4. Return prediction dict

    Returns None if critical data is missing.
    """
    match_id  = match.get("match_id")
    player_1  = match.get("player_1")
    player_2  = match.get("player_2")
    surface   = match.get("surface", "hard")
    tournament = match.get("tournament")
    rank_1    = match.get("rank_1")
    rank_2    = match.get("rank_2")

    if not match_id or not player_1 or not player_2:
        logger.warning("[TENNIS ENGINE] Skipping match with missing player data")
        return None

    logger.info(f"[TENNIS ENGINE] {player_1} vs {player_2} [{surface}]")

    try:
        features   = build_features(
            conn, player_1, player_2, surface, tournament, rank_1, rank_2
        )
        features["player_1"] = player_1
        features["player_2"] = player_2
        features["allow_low_quality_markets"] = True
        prediction = predict_match(features)

        _store_predictions(conn, match_id, prediction, features)

        return {
            "match_id":    match_id,
            "player_1":    player_1,
            "player_2":    player_2,
            "tournament":  tournament,
            "surface":     surface,
            "predictions": {
                "match_winner": prediction["match_winner"],
                "sets_markets": prediction["sets_markets"],
                "market_groups": prediction.get("market_groups", {}),
            },
            "all_picks":     prediction.get("all_picks", []),
            "top_picks":     prediction["top_picks"],
            "data_quality":  prediction["data_quality"],
            "model_version": MODEL_VERSION,
            "warnings":      prediction["warnings"],
        }

    except Exception as exc:
        logger.error(f"[TENNIS ENGINE] Prediction failed for {match_id}: {exc}", exc_info=True)
        return None


def predict_batch(conn: sqlite3.Connection, matches: list[dict]) -> list[dict]:
    """Run predictions for a list of matches. Skips failures silently."""
    results = []
    for match in matches:
        result = predict_and_store(conn, match)
        if result:
            results.append(result)
    logger.info(f"[TENNIS ENGINE] Batch complete: {len(results)}/{len(matches)} predictions generated")
    return results

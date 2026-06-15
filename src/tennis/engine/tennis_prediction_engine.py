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

    # ── Match Winner ──────────────────────────────────────────────────────────
    mw = prediction["match_winner"]
    for player_key, prob_key, odds_key in [
        ("Player 1", "player_1_win", "fair_odds_p1"),
        ("Player 2", "player_2_win", "fair_odds_p2"),
    ]:
        prob = float(mw[prob_key]) / 100.0
        conf = prob if player_key == "Player 1" else 1.0 - prob + prob  # raw prob as score
        conn.execute(
            """
            INSERT OR IGNORE INTO tennis_predictions
              (match_id, prediction_time, market_type, selection,
               predicted_probability, fair_odds, confidence_score,
               model_version, features_json)
            VALUES (?, ?, 'match_winner', ?, ?, ?, ?, ?, ?)
            """,
            (
                match_id, now, player_key,
                prob,
                float(mw[odds_key]),
                prob,
                MODEL_VERSION,
                features_json,
            )
        )

    # ── Sets Handicap (if generated) ──────────────────────────────────────────
    sets_h = prediction.get("sets_markets", {}).get("favourite_minus_1_5_sets")
    if sets_h:
        prob = float(sets_h["probability"]) / 100.0
        conn.execute(
            """
            INSERT OR IGNORE INTO tennis_predictions
              (match_id, prediction_time, market_type, selection,
               predicted_probability, fair_odds, confidence_score,
               model_version, features_json)
            VALUES (?, ?, 'sets_handicap', ?, ?, ?, ?, ?, ?)
            """,
            (
                match_id, now,
                sets_h["selection"],
                prob,
                float(sets_h["fair_odds"]),
                prob,
                MODEL_VERSION,
                features_json,
            )
        )

    # ── First Set Winner ──────────────────────────────────────────────────────
    fsw = prediction.get("sets_markets", {}).get("first_set_winner")
    if fsw:
        for player_key, prob_key, odds_key in [
            ("Player 1", "player_1_win", "fair_odds_p1"),
            ("Player 2", "player_2_win", "fair_odds_p2"),
        ]:
            prob = float(fsw[prob_key]) / 100.0
            conn.execute(
                """
                INSERT OR IGNORE INTO tennis_predictions
                  (match_id, prediction_time, market_type, selection,
                   predicted_probability, fair_odds, confidence_score,
                   model_version, features_json)
                VALUES (?, ?, 'first_set_winner', ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id, now, player_key,
                    prob,
                    float(fsw[odds_key]),
                    prob,
                    MODEL_VERSION,
                    features_json,
                )
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
            },
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

"""
tennis_routes.py
================
FastAPI router for the tennis prediction engine.
Prefix: /api/tennis

Endpoints:
  GET /api/tennis/matches?date=YYYY-MM-DD         → Daily tennis matches
  GET /api/tennis/live?date=YYYY-MM-DD            → Live tennis matches only
  GET /api/tennis/predict/{match_id}              → Full prediction card
  GET /api/tennis/analytics/baseline              → Calibration & governance
  GET /api/tennis/debug/provider-status           → Provider health

Football routes are completely separate. No football table is read or written.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from src.db.database import get_db
from src.tennis.db.tennis_schema import init_tennis_db
from src.tennis.data.tennis_provider import (
    fetch_daily_matches,
    fetch_live_matches,
    apply_live_overlay,
    mark_all_stale,
)
from src.tennis.engine.tennis_prediction_engine import predict_and_store, predict_batch
from src.tennis.ml.tennis_calibration import get_baseline_metrics

logger = logging.getLogger("football_predictor.tennis")

router = APIRouter(prefix="/api/tennis", tags=["tennis"])

# ── Ensure tennis tables exist on first request ───────────────────────────────
_schema_initialized = False

def _ensure_schema():
    global _schema_initialized
    if not _schema_initialized:
        conn = get_db()
        init_tennis_db(conn)
        _schema_initialized = True


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/matches")
def get_tennis_matches(date: str = None):
    """
    Return daily tennis matches for a given date, with live overlay applied.
    Base fixtures come from the daily warehouse refresh.
    Live status overlay comes from RapidAPI (UI only — not for settlement).
    """
    _ensure_schema()

    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ── Daily base fixtures ───────────────────────────────────────────────────
    conn = get_db()
    try:
        stored = conn.execute(
            "SELECT * FROM tennis_matches WHERE date = ? ORDER BY start_time",
            (date,)
        ).fetchall()
        base_matches = [dict(r) for r in stored]
    except Exception:
        base_matches = []

    # If nothing in DB yet, try to fetch from provider
    if not base_matches:
        fetched, err, _ = fetch_daily_matches(date)
        if err:
            return {"matches": [], "message": f"Provider error: {err}", "date": date}
        # Store fetched matches
        _store_matches(conn, fetched)
        base_matches = fetched

    if not base_matches:
        return {"matches": [], "message": "No tennis matches found for this date.", "date": date}

    # ── Live overlay (UI only) ────────────────────────────────────────────────
    live_matches, live_err, _ = fetch_live_matches()
    if live_err:
        # Mark stale but return base data
        base_matches = mark_all_stale(base_matches, live_err)
    else:
        base_matches = apply_live_overlay(base_matches, live_matches)

    return {"matches": base_matches, "count": len(base_matches), "date": date}


@router.get("/live")
def get_tennis_live(date: str = None):
    """Return only live tennis matches (status=LIVE)."""
    _ensure_schema()
    result = get_tennis_matches(date)
    live = [m for m in result.get("matches", []) if m.get("status") == "LIVE"]
    return {"matches": live, "count": len(live)}


@router.get("/predict/{match_id}")
def get_tennis_prediction(match_id: str):
    """
    Return the full prediction card for a tennis match.
    Generates a fresh prediction on-demand so old cached market rows do not
    hide newly supported handicap, total, and player total picks.
    """
    _ensure_schema()
    conn = get_db()

    # Fetch match record
    match_row = conn.execute(
        "SELECT * FROM tennis_matches WHERE match_id = ?",
        (match_id,)
    ).fetchone()

    if not match_row:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found")

    match = dict(match_row)

    result = predict_and_store(conn, match)
    if not result:
        raise HTTPException(status_code=422, detail="Prediction failed — insufficient data")
    return result


@router.get("/analytics/baseline")
def get_tennis_baseline():
    """
    Return calibration metrics and governance status.
    Recalibration is locked until 500 settled predictions.
    """
    _ensure_schema()
    conn = get_db()
    metrics = get_baseline_metrics(conn)
    return metrics


@router.get("/results")
def get_tennis_results(date: str = None):
    """
    Return all settled matches for a specific date with their resolved predictions.
    """
    _ensure_schema()
    conn = get_db()
    
    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
    # Fetch FT matches for the date
    matches_rows = conn.execute(
        "SELECT * FROM tennis_matches WHERE date = ? AND status = 'FT' ORDER BY start_time",
        (date,)
    ).fetchall()
    
    if not matches_rows:
        return {
            "date": date,
            "matches": [],
            "summary": {"total_matches": 0, "accuracy_pct": 0, "total_correct": 0, "total_wrong": 0}
        }
        
    match_ids = [m["match_id"] for m in matches_rows]
    
    # Fetch results for those matches
    results_rows = conn.execute(
        f"SELECT * FROM tennis_results WHERE match_id IN ({','.join(['?']*len(match_ids))})",
        match_ids
    ).fetchall()
    
    results_by_id = {r["match_id"]: dict(r) for r in results_rows}
    
    # Fetch predictions for those matches
    preds_rows = conn.execute(
        f"SELECT * FROM tennis_predictions WHERE match_id IN ({','.join(['?']*len(match_ids))})",
        match_ids
    ).fetchall()
    
    preds_by_match = {}
    total_correct = 0
    total_wrong = 0
    
    for p in preds_rows:
        mid = p["match_id"]
        if mid not in preds_by_match:
            preds_by_match[mid] = []
        pred_dict = dict(p)
        preds_by_match[mid].append(pred_dict)
        if pred_dict["result"] == 1:
            total_correct += 1
        elif pred_dict["result"] == 0:
            total_wrong += 1
            
    clean_results = []
    
    for row in matches_rows:
        match_dict = dict(row)
        mid = match_dict["match_id"]
        res = results_by_id.get(mid)
        preds = preds_by_match.get(mid, [])
        
        match_correct = sum(1 for p in preds if p["result"] == 1)
        match_wrong = sum(1 for p in preds if p["result"] == 0)
        
        clean_results.append({
            "fixture": match_dict,
            "result": res,
            "picks": [
                {
                    "market": p["market_type"],
                    "selection": p["selection"],
                    "probability": round(float(p["predicted_probability"]) * 100, 1) if p["predicted_probability"] else 0,
                    "result": True if p["result"] == 1 else (False if p["result"] == 0 else None),
                    "isSettled": p["result"] is not None
                }
                for p in preds
            ],
            "summary": {
                "correct": match_correct,
                "wrong": match_wrong,
                "total": len(preds)
            }
        })
        
    total_settled_picks = total_correct + total_wrong
    accuracy = round((total_correct / total_settled_picks * 100), 1) if total_settled_picks > 0 else 0.0

    return {
        "date": date,
        "matches": clean_results,
        "summary": {
            "total_matches": len(clean_results),
            "total_picks": total_settled_picks,
            "total_correct": total_correct,
            "total_wrong": total_wrong,
            "accuracy_pct": accuracy,
        }
    }


@router.get("/debug/provider-status")
def get_tennis_provider_status():
    """Return the last 20 provider health entries for tennis."""
    _ensure_schema()
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT provider, success, latency_ms, fixture_count, error_message, created_at
            FROM provider_health_log
            WHERE provider LIKE 'rapidapi_tennis%'
            ORDER BY created_at DESC
            LIMIT 20
            """
        ).fetchall()
        entries = [dict(r) for r in rows]
        success_rate = (
            sum(1 for e in entries if e["success"]) / len(entries) * 100
            if entries else None
        )
        return {
            "provider":     "rapidapi_tennis",
            "host":         __import__("os").getenv("RAPIDAPI_TENNIS_HOST", "not_configured"),
            "success_rate": round(success_rate, 1) if success_rate is not None else None,
            "last_20_requests": entries,
        }
    except Exception as e:
        return {"error": str(e)}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _store_matches(conn, matches: list[dict]) -> None:
    """Insert matches into tennis_matches table. Skips duplicates."""
    for m in matches:
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO tennis_matches
                  (match_id, date, start_time, tournament, surface,
                   player_1, player_2, rank_1, rank_2, status,
                   sets_1, sets_2, games_1, games_2, provider,
                   is_stale, last_live_update)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    m["match_id"], m.get("date"), m.get("start_time"),
                    m.get("tournament"), m.get("surface"),
                    m["player_1"], m["player_2"],
                    m.get("rank_1"), m.get("rank_2"),
                    m.get("status", "NS"),
                    m.get("sets_1", 0), m.get("sets_2", 0),
                    m.get("games_1", 0), m.get("games_2", 0),
                    m.get("provider"), 0, m.get("last_live_update"),
                )
            )
        except Exception as exc:
            logger.warning(f"[TENNIS ROUTES] Failed to store match {m.get('match_id')}: {exc}")
    conn.commit()


def _confidence_from_score(score: float) -> str:
    if score >= 0.65:
        return "HIGH"
    if score >= 0.55:
        return "MEDIUM"
    return "LOW"

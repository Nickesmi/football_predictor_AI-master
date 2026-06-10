"""
Prediction Error Intelligence — Phase 5 + League-Specific Learning

Transforms the platform from a predictor into a self-improving engine.

Architecture:
  store_prediction_record()   — called on every analysis computation
  settle_prediction()         — called on every finished match
  get_league_adjustment()     — called during prediction to apply runtime corrections
  get_model_health()          — full health report for /api/debug/model-health
  rebuild_confidence_adjustments() — recomputes all league profiles

League-Specific Learning:
  Computes sample-weighted reliability and confidence adjustments.
  Status categories: trusted, developing, insufficient_data
"""

from __future__ import annotations

import math
import sqlite3
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("football_predictor")

# ── Constants ─────────────────────────────────────────────────────────────────
_MIN_LEAGUE_SAMPLES         = 5    # minimum to appear in health reports
_MIN_BUCKET_SAMPLES         = 3    # minimum to appear in bucket report
_MIN_SAMPLES_FOR_ADJUSTMENT = 50   # gate before applying runtime corrections (50 min to get 0.25 weight)
_EQUAL_PROB                 = 33.333  # perfect uncertainty baseline for shrinkage
_MAX_PENALTY_PCT            = 15.0    # max confidence reduction per prediction
_MAX_BOOST_PCT              = 10.0    # max confidence increase per prediction


def _get_sample_weight(n: int) -> float:
    """Determine statistical weight based on sample size."""
    if n < 50:
        return 0.0
    elif n < 100:
        return 0.25
    elif n < 250:
        return 0.50
    elif n < 500:
        return 0.75
    else:
        return 1.00


def _get_league_status(n: int, reliability: float) -> str:
    """Categorize league based on sample size and reliability."""
    if n < 100:
        return "insufficient_data"
    elif reliability >= 60.0:
        return "trusted"
    else:
        return "developing"


# ── Reliability Score ──────────────────────────────────────────────────────────

def _reliability_score(sample_count: int, accuracy: float, cal_gap: float, brier_score: float) -> float:
    """
    Compute a 0-100 reliability score for a league based on:
    sample size, accuracy, calibration gap, and brier score.
    """
    # Volume: reaches 25 at 500+ samples
    volume_score = min(sample_count / 500.0, 1.0) * 25.0

    # Accuracy: 0 at random baseline (33.3%), maxes at 35 for 70%+
    acc_above_random = max(0.0, accuracy - 33.3)
    accuracy_score = min(acc_above_random / 36.7, 1.0) * 35.0

    # Calibration: 20 at gap=0, 0 at gap≥20
    calibration_score = max(0.0, 1.0 - abs(cal_gap) / 20.0) * 20.0

    # Brier: 20 at perfect (0.0), 0 at poor (0.35+)
    brier_score_component = max(0.0, 1.0 - brier_score / 0.35) * 20.0

    total = volume_score + accuracy_score + calibration_score + brier_score_component
    return round(min(100.0, max(0.0, total)), 1)


# ── Confidence bucket helper ───────────────────────────────────────────────────

def _bucket(confidence: float) -> str:
    """Map a confidence % (0-100) to a display bucket string, e.g. '75-80%'."""
    lo = int(confidence // 5) * 5
    lo = max(0, min(lo, 95))
    return f"{lo}-{lo + 5}%"


# ── Competition type classifier ───────────────────────────────────────────────

def _comp_type(league_name: str, country: str) -> str:
    n = league_name.lower()
    if any(w in n for w in ("women", "female", "ladies", "wsl", "nwsl", "liga f")):
        return "women"
    if any(w in n for w in ("u17", "u18", "u19", "u20", "u21", "u23", "youth", "under-")):
        return "youth"
    if any(w in n for w in ("friendly", "friendlies")):
        return "friendly"
    if country in ("World", "Europe", "South America", "Asia", "Africa",
                   "North America", "Oceania", "CONMEBOL", "UEFA", "AFC", "CAF", "CONCACAF"):
        return "international"
    return "men"


# ── Runtime adjustment lookup ──────────────────────────────────────────────────

def get_league_adjustment(conn: sqlite3.Connection, league_name: str) -> dict:
    """Look up the learned adjustment parameters for a league."""
    row = conn.execute(
        """SELECT sample_count, avg_confidence, actual_hit_rate,
                  adjustment_factor, overconfidence, brier_score, reliability_score, status
           FROM model_confidence_adjustments
           WHERE league_name = ?""",
        (league_name,),
    ).fetchone()

    if not row:
        return {
            "active": False,
            "shrink_factor": 1.0,
            "reliability": 0.0,
            "sample_count": 0,
            "accuracy": None,
            "cal_gap": None,
            "status": "insufficient_data",
            "description": f"No data yet for '{league_name}'. Raw probabilities used.",
        }

    n = row["sample_count"]
    weight = _get_sample_weight(n)
    if weight == 0.0:
        return {
            "active": False,
            "shrink_factor": 1.0,
            "reliability": row["reliability_score"] or 0.0,
            "sample_count": n,
            "accuracy": row["actual_hit_rate"],
            "cal_gap": row["overconfidence"],
            "status": row["status"] or "insufficient_data",
            "description": (
                f"Learning in progress ({n} predictions). "
                "Raw probabilities used."
            ),
        }

    adj = row["adjustment_factor"]
    rel = row["reliability_score"] or 0.0
    gap = row["overconfidence"]

    if abs(gap) < 3.0:
        desc = "Well calibrated — minimal adjustment applied."
    elif gap > 0:
        desc = f"Overconfident by {gap:+.1f}pp — shrinking probabilities toward equal."
    else:
        desc = f"Underconfident by {abs(gap):.1f}pp — expanding probabilities from equal."

    return {
        "active": True,
        "shrink_factor": float(adj),
        "reliability": rel,
        "sample_count": n,
        "accuracy": row["actual_hit_rate"],
        "cal_gap": gap,
        "status": row["status"] or "insufficient_data",
        "description": desc,
    }


def apply_league_adjustment(
    adj: dict,
    home_win: float,
    draw: float,
    away_win: float,
) -> tuple[float, float, float]:
    """
    Apply the learned league adjustment to raw 1X2 probabilities.
    """
    if not adj.get("active"):
        return home_win, draw, away_win

    sf = adj["shrink_factor"]

    adj_h = _EQUAL_PROB + (home_win - _EQUAL_PROB) * sf
    adj_d = _EQUAL_PROB + (draw    - _EQUAL_PROB) * sf
    adj_a = _EQUAL_PROB + (away_win - _EQUAL_PROB) * sf

    # Clamp to [1%, 99%]
    adj_h = max(1.0, min(99.0, adj_h))
    adj_d = max(1.0, min(99.0, adj_d))
    adj_a = max(1.0, min(99.0, adj_a))

    # Renormalize
    total = adj_h + adj_d + adj_a
    if total > 0:
        adj_h = round(adj_h / total * 100, 1)
        adj_d = round(adj_d / total * 100, 1)
        adj_a = round(100.0 - adj_h - adj_d, 1)

    return adj_h, adj_d, adj_a


# ── Store prediction record ────────────────────────────────────────────────────

def store_prediction_record(
    conn: sqlite3.Connection,
    fixture_id: str,
    match_date: str,
    league_name: str,
    country: str,
    home_team: str,
    away_team: str,
    home_win_pct: float,
    draw_pct: float,
    away_win_pct: float,
) -> bool:
    """Store a 1X2 prediction for a fixture."""
    confidence = max(home_win_pct, draw_pct, away_win_pct)
    if home_win_pct >= draw_pct and home_win_pct >= away_win_pct:
        predicted_result = "home"
    elif draw_pct >= home_win_pct and draw_pct >= away_win_pct:
        predicted_result = "draw"
    else:
        predicted_result = "away"

    comp_type = _comp_type(league_name, country)

    try:
        conn.execute(
            """INSERT OR IGNORE INTO prediction_errors
               (fixture_id, match_date, league_name, country, competition_type,
                home_team, away_team,
                predicted_result, home_win_pct, draw_pct, away_win_pct,
                confidence, confidence_bucket)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fixture_id, match_date, league_name, country, comp_type,
                home_team, away_team,
                predicted_result, home_win_pct, draw_pct, away_win_pct,
                confidence, _bucket(confidence),
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"store_prediction_record failed for {fixture_id}: {e}")
        return False


def store_scoreline_predictions(
    conn: sqlite3.Connection,
    fixture_id: str,
    poisson_data: dict
) -> bool:
    """Store the full scoreline matrix and predicted xG for later settlement."""
    import json
    try:
        all_scorelines = poisson_data.get("all_scorelines", [])
        xg = poisson_data.get("xg", {})
        
        conn.execute(
            """INSERT OR REPLACE INTO scoreline_predictions
               (fixture_id, matrix_json, predicted_home_xg, predicted_away_xg)
               VALUES (?, ?, ?, ?)""",
            (
                fixture_id,
                json.dumps(all_scorelines),
                xg.get("home", 0.0),
                xg.get("away", 0.0)
            )
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"store_scoreline_predictions failed for {fixture_id}: {e}")
        return False


# ── Settle a prediction ────────────────────────────────────────────────────────

def settle_prediction(
    conn: sqlite3.Connection,
    fixture_id: str,
    home_goals: int,
    away_goals: int,
    predicted_home_goals: Optional[float] = None,
    predicted_away_goals: Optional[float] = None,
) -> Optional[dict]:
    """Settle a stored prediction with the actual result."""
    row = conn.execute(
        """SELECT fixture_id, league_name, predicted_result,
                  home_win_pct, draw_pct, away_win_pct, confidence
           FROM prediction_errors
           WHERE fixture_id = ? AND correct IS NULL""",
        (fixture_id,),
    ).fetchone()

    if not row:
        return None

    fid           = row["fixture_id"]
    league_name   = row["league_name"]
    predicted_result = row["predicted_result"]
    h_pct, d_pct, a_pct = row["home_win_pct"], row["draw_pct"], row["away_win_pct"]
    confidence    = row["confidence"]

    # Actual result
    if home_goals > away_goals:
        actual_result = "home"
    elif home_goals < away_goals:
        actual_result = "away"
    else:
        actual_result = "draw"

    correct = 1 if predicted_result == actual_result else 0

    # Confidence error
    confidence_error = abs(confidence - (100.0 if correct else 0.0))

    # Brier score contribution
    p_map = {"home": h_pct / 100.0, "draw": d_pct / 100.0, "away": a_pct / 100.0}
    p = max(1e-7, min(1 - 1e-7, p_map[predicted_result]))
    y = float(correct)
    probability_error = round((p - y) ** 2, 6)

    # Score error
    score_error = None
    if predicted_home_goals is not None and predicted_away_goals is not None:
        score_error = round(abs((predicted_home_goals + predicted_away_goals) -
                               (home_goals + away_goals)), 2)

    now = datetime.utcnow().isoformat()

    conn.execute(
        """UPDATE prediction_errors
           SET actual_result    = ?,
               home_goals       = ?,
               away_goals       = ?,
               correct          = ?,
               confidence_error   = ?,
               probability_error  = ?,
               score_error        = ?,
               settled_at         = ?
           WHERE fixture_id = ?""",
        (actual_result, home_goals, away_goals,
         correct, round(confidence_error, 2), probability_error, score_error, now, fid),
    )
    
    # Phase 5: Scoreline Intelligence Reform
    try:
        scoreline_row = conn.execute(
            "SELECT matrix_json, predicted_home_xg, predicted_away_xg FROM scoreline_predictions WHERE fixture_id = ?", 
            (fid,)
        ).fetchone()
        
        if scoreline_row:
            import json
            matrix = json.loads(scoreline_row["matrix_json"])
            
            # Basic info
            match_date = conn.execute("SELECT match_date FROM prediction_errors WHERE fixture_id = ?", (fid,)).fetchone()[0]
            confidence_bucket = "Unknown"
            if "confidence_bucket" in row.keys():
                confidence_bucket = row["confidence_bucket"]
            
            # Top Score predicted
            pred_top_score = matrix[0]["score"] if matrix else "Unknown"
            pred_top_prob = matrix[0]["probability"] if matrix else 0.0
            
            # Actual score details
            actual_score_str = f"{home_goals}-{away_goals}"
            actual_rank = None
            actual_prob = 0.0
            
            for score_entry in matrix:
                if score_entry["score"] == actual_score_str:
                    actual_rank = score_entry["rank"]
                    actual_prob = score_entry["probability"]
                    break
                    
            if actual_rank is None:
                # If it wasn't even in the generated matrix!
                actual_rank = 999
            
            # Hits
            top1_hit = actual_rank == 1
            top3_hit = actual_rank is not None and actual_rank <= 3
            top5_hit = actual_rank is not None and actual_rank <= 5
            top10_hit = actual_rank is not None and actual_rank <= 10
            
            # xG Errors
            pred_h_xg = scoreline_row["predicted_home_xg"]
            pred_a_xg = scoreline_row["predicted_away_xg"]
            
            h_err = round(home_goals - pred_h_xg, 2)
            a_err = round(away_goals - pred_a_xg, 2)
            tot_err = round((home_goals + away_goals) - (pred_h_xg + pred_a_xg), 2)
            
            conn.execute(
                """INSERT OR IGNORE INTO scoreline_learning_log
                   (fixture_id, league_name, match_date,
                    predicted_top_score, predicted_top_probability,
                    actual_score, actual_rank, actual_probability,
                    top1_hit, top3_hit, top5_hit, top10_hit,
                    predicted_home_xg, predicted_away_xg,
                    actual_home_goals, actual_away_goals,
                    home_goal_error, away_goal_error, total_goal_error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (fid, league_name, match_date,
                 pred_top_score, pred_top_prob,
                 actual_score_str, actual_rank, actual_prob,
                 top1_hit, top3_hit, top5_hit, top10_hit,
                 pred_h_xg, pred_a_xg, home_goals, away_goals,
                 h_err, a_err, tot_err)
            )
    except Exception as e:
        logger.warning(f"Failed to process scoreline learning log for {fid}: {e}")
        
    conn.commit()

    # Continuously update this league's profile
    _update_league_profile(conn, league_name)

    return {
        "fixture_id":       fid,
        "predicted_result": predicted_result,
        "actual_result":    actual_result,
        "correct":          correct,
        "confidence_error": round(confidence_error, 2),
        "probability_error": probability_error,
        "score_error":      score_error,
    }


# ── League profile update ──────────────────────────────────────────────────────

def _update_league_profile(conn: sqlite3.Connection, league_name: str) -> None:
    """Recompute and upsert the full league profile including reliability score."""
    rows = conn.execute(
        """SELECT confidence, correct
           FROM prediction_errors
           WHERE league_name = ? AND correct IS NOT NULL""",
        (league_name,),
    ).fetchall()

    n = len(rows)
    if n < _MIN_LEAGUE_SAMPLES:
        return

    avg_conf = sum(r[0] for r in rows) / n
    hit_rate = sum(r[1] for r in rows) / n * 100.0
    overconf = round(avg_conf - hit_rate, 2)

    brier = sum(
        (max(1e-7, min(1 - 1e-7, r[0] / 100.0)) - float(r[1])) ** 2
        for r in rows
    ) / n

    # Base raw adjustment (how much to scale probabilities)
    raw_adj = (hit_rate / avg_conf) if avg_conf > 0 else 1.0

    # Sample-size weighting
    weight = _get_sample_weight(n)
    
    # Weighted adjustment formula:
    # Interpolate between 1.0 (no adjustment) and raw_adj based on sample weight
    adj = 1.0 + (raw_adj - 1.0) * weight
    adj = round(max(0.5, min(1.5, adj)), 4)

    # Reliability score (0-100)
    rel = _reliability_score(n, hit_rate, overconf, brier)

    # Category status
    status = _get_league_status(n, rel)

    # Confidence penalty / boost with safety limits
    if weight > 0.0:
        gap = overconf
        # Apply sample weight to the gap
        weighted_gap = gap * weight
        if weighted_gap > 0:
            # Penalty (overconfident)
            penalty = round(min(weighted_gap, _MAX_PENALTY_PCT), 1)
            boost = 0.0
        else:
            # Boost (underconfident)
            boost = round(min(abs(weighted_gap), _MAX_BOOST_PCT), 1)
            penalty = 0.0
    else:
        penalty = 0.0
        boost = 0.0

    country = conn.execute(
        "SELECT country FROM prediction_errors WHERE league_name = ? LIMIT 1",
        (league_name,),
    ).fetchone()
    country_val = country[0] if country else ""

    conn.execute(
        """INSERT INTO model_confidence_adjustments
               (league_name, country, sample_count, avg_confidence, actual_hit_rate,
                adjustment_factor, overconfidence, brier_score, reliability_score,
                confidence_penalty, confidence_boost, status, last_updated)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(league_name) DO UPDATE SET
               country            = excluded.country,
               sample_count       = excluded.sample_count,
               avg_confidence     = excluded.avg_confidence,
               actual_hit_rate    = excluded.actual_hit_rate,
               adjustment_factor  = excluded.adjustment_factor,
               overconfidence     = excluded.overconfidence,
               brier_score        = excluded.brier_score,
               reliability_score  = excluded.reliability_score,
               confidence_penalty = excluded.confidence_penalty,
               confidence_boost   = excluded.confidence_boost,
               status             = excluded.status,
               last_updated       = CURRENT_TIMESTAMP""",
        (league_name, country_val, n,
         round(avg_conf, 2), round(hit_rate, 2),
         adj, overconf, round(brier, 4), rel,
         penalty, boost, status),
    )
    conn.commit()


# ── Batch settle ───────────────────────────────────────────────────────────────

def settle_date(conn: sqlite3.Connection, date_str: str, finished_fixtures: list[dict]) -> dict:
    """Settle all predictions for a given date."""
    settled = 0
    skipped = 0
    for f in finished_fixtures:
        if f.get("home_goals") is None or f.get("away_goals") is None:
            skipped += 1
            continue
        result = settle_prediction(conn, str(f["id"]), f["home_goals"], f["away_goals"])
        if result:
            settled += 1
        else:
            skipped += 1
    return {"date": date_str, "settled": settled, "skipped": skipped}


# ── Bulk rebuild ───────────────────────────────────────────────────────────────

def rebuild_confidence_adjustments(conn: sqlite3.Connection) -> int:
    """Recompute league profiles for every league in the database."""
    leagues = conn.execute(
        "SELECT DISTINCT league_name FROM prediction_errors WHERE correct IS NOT NULL"
    ).fetchall()
    for row in leagues:
        _update_league_profile(conn, row[0])
    n = len(leagues)
    logger.info(f"[LEAGUE LEARNING] Rebuilt profiles for {n} leagues")
    return n


# ── League profiles list ───────────────────────────────────────────────────────

def get_league_profiles(conn: sqlite3.Connection, min_samples: int = 1) -> list[dict]:
    """Return all league profiles sorted by reliability score descending."""
    rows = conn.execute(
        """SELECT league_name, country, sample_count, avg_confidence,
                  actual_hit_rate, adjustment_factor, overconfidence,
                  brier_score, reliability_score,
                  confidence_penalty, confidence_boost, status, last_updated
           FROM model_confidence_adjustments
           WHERE sample_count >= ?
           ORDER BY reliability_score DESC""",
        (min_samples,),
    ).fetchall()

    profiles = []
    for r in rows:
        n     = r["sample_count"]
        acc   = r["actual_hit_rate"]
        gap   = r["overconfidence"]
        rel   = r["reliability_score"] or 0.0
        adj   = r["adjustment_factor"]
        status = r["status"] or "insufficient_data"
        weight = _get_sample_weight(n)
        active = weight > 0.0

        if active:
            if abs(gap) < 3.0:
                desc = "Well calibrated"
            elif gap > 0:
                desc = f"Overconfident ({gap:+.1f}pp) — reducing confidence (weight={weight})"
            else:
                desc = f"Underconfident ({gap:+.1f}pp) — boosting confidence (weight={weight})"
        else:
            desc = f"Learning ({n} predictions)"

        profiles.append({
            "league":             r["league_name"],
            "country":            r["country"],
            "predictions":        n,
            "accuracy":           round(acc, 1),
            "avg_confidence":     round(r["avg_confidence"], 1),
            "calibration_gap":    round(gap, 1),
            "brier_score":        round(r["brier_score"], 4),
            "reliability_score":  round(rel, 1),
            "adjustment_factor":  round(adj, 4),
            "confidence_penalty": r["confidence_penalty"] or 0.0,
            "confidence_boost":   r["confidence_boost"] or 0.0,
            "adjustment_active":  active,
            "status":             status,
            "description":        desc,
            "last_updated":       r["last_updated"],
        })

    return profiles


# ── Model health report ────────────────────────────────────────────────────────

def get_model_health(conn: sqlite3.Connection) -> dict:
    """Full model health report for GET /api/debug/model-health."""
    rows = conn.execute(
        """SELECT confidence, correct
           FROM prediction_errors WHERE correct IS NOT NULL"""
    ).fetchall()

    total = len(rows)
    if total == 0:
        return {
            "total_stored_predictions": 0,
            "settled_predictions": 0,
            "accuracy": 0.0,
            "brier_score": 0.0,
            "calibration_gap": 0.0,
            "message": (
                "No settled predictions yet. "
                "Call GET /api/debug/settle-date/{YYYY-MM-DD} for completed dates."
            ),
            "trusted_leagues": [],
            "developing_leagues": [],
            "insufficient_data": [],
        }

    total_stored = conn.execute("SELECT COUNT(*) FROM prediction_errors").fetchone()[0]
    correct_total = sum(r[1] for r in rows)
    accuracy  = round(correct_total / total * 100, 2)
    avg_conf  = sum(r[0] for r in rows) / total
    act_rate  = correct_total / total * 100.0
    brier     = round(sum((max(1e-7, min(1-1e-7, r[0]/100)) - float(r[1]))**2 for r in rows) / total, 4)
    cal_gap   = round(avg_conf - act_rate, 2)

    # ── By League ────────────────────────────────────────────────────────
    league_rows = conn.execute(
        """SELECT league_name, country,
                  COUNT(*) as n, SUM(correct) as hits, AVG(confidence) as avg_conf
           FROM prediction_errors WHERE correct IS NOT NULL
           GROUP BY league_name HAVING n >= ? ORDER BY n DESC""",
        (_MIN_LEAGUE_SAMPLES,),
    ).fetchall()

    by_league = []
    for r in league_rows:
        n = r["n"]; hits = r["hits"]
        hit_rate = round(hits / n * 100, 1)
        avg_c    = round(r["avg_conf"], 1)
        by_league.append({
            "league": r["league_name"], "country": r["country"],
            "predictions": n, "accuracy": hit_rate,
            "avg_confidence": avg_c,
            "overconfidence": round(avg_c - hit_rate, 1),
        })

    # ── By Country ───────────────────────────────────────────────────────
    country_rows = conn.execute(
        """SELECT country, COUNT(*) as n, SUM(correct) as hits, AVG(confidence) as avg_conf
           FROM prediction_errors WHERE correct IS NOT NULL AND country != ''
           GROUP BY country HAVING n >= ? ORDER BY n DESC""",
        (_MIN_LEAGUE_SAMPLES,),
    ).fetchall()

    by_country = []
    for r in country_rows:
        n = r["n"]; hits = r["hits"]
        hit_rate = round(hits / n * 100, 1)
        avg_c    = round(r["avg_conf"], 1)
        by_country.append({
            "country": r["country"], "predictions": n, "accuracy": hit_rate,
            "avg_confidence": avg_c, "overconfidence": round(avg_c - hit_rate, 1),
        })

    # ── By Competition Type ──────────────────────────────────────────────
    comp_rows = conn.execute(
        """SELECT competition_type, COUNT(*) as n, SUM(correct) as hits, AVG(confidence) as avg_conf
           FROM prediction_errors WHERE correct IS NOT NULL
           GROUP BY competition_type ORDER BY n DESC"""
    ).fetchall()

    by_comp_type = []
    for r in comp_rows:
        n = r["n"]; hits = r["hits"]
        hit_rate = round(hits / n * 100, 1)
        avg_c    = round(r["avg_conf"], 1)
        by_comp_type.append({
            "competition_type": r["competition_type"], "predictions": n,
            "accuracy": hit_rate, "avg_confidence": avg_c,
            "overconfidence": round(avg_c - hit_rate, 1),
        })

    # ── By Confidence Bucket ─────────────────────────────────────────────
    bucket_rows = conn.execute(
        """SELECT confidence_bucket, COUNT(*) as n, SUM(correct) as hits, AVG(confidence) as avg_conf
           FROM prediction_errors WHERE correct IS NOT NULL
           GROUP BY confidence_bucket HAVING n >= ? ORDER BY MIN(confidence)""",
        (_MIN_BUCKET_SAMPLES,),
    ).fetchall()

    by_bucket = []
    for r in bucket_rows:
        n = r["n"]; hits = r["hits"]
        hit_rate = round(hits / n * 100, 1)
        avg_c    = round(r["avg_conf"], 1)
        by_bucket.append({
            "bucket": r["confidence_bucket"], "predictions": n,
            "claimed_confidence": avg_c, "actual_hit_rate": hit_rate,
            "overconfidence": round(avg_c - hit_rate, 1),
            "well_calibrated": abs(avg_c - hit_rate) <= 5.0,
        })

    # ── League profiles with categories ───────────────────────────────────
    all_profiles = get_league_profiles(conn, min_samples=1)
    
    trusted_leagues = [p for p in all_profiles if p["status"] == "trusted"]
    developing_leagues = [p for p in all_profiles if p["status"] == "developing"]
    insufficient_data = [p for p in all_profiles if p["status"] == "insufficient_data"]

    # Sort each appropriately
    trusted_leagues.sort(key=lambda x: -x["reliability_score"])
    developing_leagues.sort(key=lambda x: -x["reliability_score"])
    insufficient_data.sort(key=lambda x: -x["predictions"])

    # ── Classic ranked lists ─────────────────────────────────────────────
    best_leagues   = sorted(by_league, key=lambda x: -x["accuracy"])[:10]
    worst_leagues  = sorted(by_league, key=lambda x:  x["accuracy"])[:10]
    most_overconf  = sorted(by_league, key=lambda x: -x["overconfidence"])[:10]

    return {
        "total_stored_predictions": total_stored,
        "settled_predictions":      total,
        "accuracy":                 accuracy,
        "brier_score":              brier,
        "calibration_gap":          cal_gap,
        "interpretation":           _interpret_health(accuracy, brier, cal_gap),

        # Breakdowns
        "by_league":           by_league,
        "by_country":          by_country,
        "by_competition_type": by_comp_type,
        "by_confidence_bucket": by_bucket,

        # Classic ranking
        "best_leagues":              best_leagues,
        "worst_leagues":             worst_leagues,
        "most_overconfident_leagues": most_overconf,

        # Categories
        "trusted_leagues":    trusted_leagues,
        "developing_leagues": developing_leagues,
        "insufficient_data":  insufficient_data,
        
        # League profiles dump
        "league_profiles": all_profiles,
    }


def _interpret_health(accuracy: float, brier: float, cal_gap: float) -> dict:
    return {
        "accuracy": (
            "Excellent (>65%)"  if accuracy > 65 else
            "Good (55-65%)"     if accuracy > 55 else
            "Fair (45-55%)"     if accuracy > 45 else
            "Poor (<45%)"
        ),
        "brier_score": (
            "Excellent (<0.20)" if brier < 0.20 else
            "Good (0.20-0.25)"  if brier < 0.25 else
            "Fair (0.25-0.30)"  if brier < 0.30 else
            "Poor (>0.30)"
        ),
        "calibration": (
            "Well calibrated (gap <5)"      if abs(cal_gap) < 5  else
            "Slightly overconfident (5-15)" if 0 <= cal_gap < 15 else
            "Overconfident (>15)"           if cal_gap >= 15     else
            "Underconfident"
        ),
    }

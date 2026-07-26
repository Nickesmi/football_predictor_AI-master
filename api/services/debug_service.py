"""
Debug & Admin Service.

Provides business logic for all debug, diagnostic, and admin reporting endpoints.
"""

import json
import math
import os
import re
import ssl
import certifi
import time
import unicodedata
import urllib.request
import asyncio
import requests
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Union, Any

from fastapi import HTTPException, BackgroundTasks, Depends
from src.config import logger, APIFOOTBALL_API_KEY, APIFOOTBALL_HOST, TOP_LEAGUES, ADMIN_API_KEY
from src.db.database import get_db
from src.processing.pattern_analyzer import PatternAnalyzer
from src.processing.factor_analyzer import FactorAnalyzer
from src.reporting.report_formatter import ReportFormatter
from src.processing.value_detector import ValueDetector
from src.ml.predictor import XGBoostPredictor
from src.ml.poisson_model import PoissonGoalModel
from src.ml.team_stats_db import get_team_stats
from src.ml.feature_builder import TeamProfile
from src.engine.odds_scanner import scan_live_odds
from src.db.competition_tracker import upsert_competition, get_competition_stats, list_competitions

from api.services.match_analysis_service import _compute_match_analysis, _ANALYSIS_CACHE
from api.services.fixtures_service import (
    _read_fixture_cache,
    _write_fixture_cache,
    _fetch_api_football_fixtures,
    _fetch_sofascore_fixtures,
    _get_istanbul_today,
    _PREDICTION_STATUS,
    _precompute_predictions_for_date,
    _categorize_competition,
    _sofascore_to_fixture,
)
from api.services.logo_service import resolve_team_logo

# ── Endpoints ──────────────────────────

from fastapi.responses import Response


def get_model_health_report_service():
    """
    Full model health report — accuracy, calibration, and league intelligence.

    GET /api/debug/model-health

    Returns aggregated statistics from the prediction_errors table.
    Data is populated automatically when GET /api/results/{date} is called
    for any date with finished matches.

    Reports:
      - Overall accuracy, Brier score, calibration gap
      - Accuracy by League (best / worst / most overconfident)
      - Accuracy by Country
      - Accuracy by Competition Type (men / women / youth / friendly / international)
      - Calibration Curve by Confidence Bucket
      - Confidence Adjustment Factors per league
    """
    try:
        from src.db.database import get_db
        from src.db.error_intelligence import get_model_health
        conn = get_db()
        return get_model_health(conn)
    except Exception as e:
        logger.error(f"Model health report failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))



def get_team_rating_endpoint_service(team: str):
    """
    Get dynamic team learning state (rating, momentum) and history.
    """
    try:
        from src.db.database import get_db
        from src.db.team_intelligence import get_team_diagnostics
        conn = get_db()
        return get_team_diagnostics(conn, team)
    except Exception as e:
        logger.error(f"Team rating debug failed for {team}: {e}")
        raise HTTPException(status_code=500, detail=str(e))



def get_calibration_report_debug_service(market_type: str = "result"):
    """
    Get full calibration report and reliability diagram.
    """
    try:
        from src.db.database import get_db
        from src.db.prediction_logger import get_calibration_data, get_competition_type_analysis, get_backtest_summary
        
        conn = get_db()
        
        # Task 1 & 2: Reliability Diagram (buckets)
        cal_data = get_calibration_data(conn, market_type, min_samples=10)
        
        # Task 6: Brier Score (from backtest summary)
        backtest = get_backtest_summary(conn, market_type)
        
        # Task 7: Competition Type Analysis
        comp_analysis = get_competition_type_analysis(conn)
        
        return {
            "market_type": market_type,
            "total_predictions": backtest.get("total_predictions", 0),
            "brier_score": backtest.get("brier_score", 0),
            "log_loss": backtest.get("log_loss", 0),
            "accuracy_pct": backtest.get("accuracy_pct", 0),
            "calibration_gap": backtest.get("calibration_gap", 0),
            "reliability_diagram": cal_data.get("buckets", []),
            "competition_analysis": comp_analysis
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



def settle_date_endpoint_service(date_str: str):
    """
    Manually settle all predictions for a completed date.

    GET /api/debug/settle-date/YYYY-MM-DD

    This is a convenience endpoint for backfilling historical accuracy data.
    It:
      1. Loads all finished fixtures from the cache for that date
      2. Runs the model to compute 1X2 predictions
      3. Stores them in prediction_errors
      4. Settles them against the actual results
      5. Updates confidence adjustment factors per league
    """
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date_str!r}")

    try:
        from src.db.database import get_db
        from src.db.error_intelligence import store_prediction_record, settle_prediction, rebuild_confidence_adjustments

        cached = _read_fixture_cache(date_str) or []
        finished = [f for f in cached if f.get("status") in ("FT", "AET", "PEN")
                    and f.get("home_goals") is not None
                    and f.get("away_goals") is not None]

        if not finished:
            return {
                "date": date_str,
                "status": "no_finished_fixtures",
                "settled": 0,
                "message": "No finished fixtures found in cache for this date. Load the date first."
            }

        conn = get_db()
        settled = 0
        errors = 0

        for f in finished:
            try:
                fid = str(f["id"])
                home = f["home_team"]["name"]
                away = f["away_team"]["name"]
                league = f["league"]["name"]
                country = f["league"].get("country", "")
                home_goals = f["home_goals"]
                away_goals = f["away_goals"]

                # Compute fresh prediction for this match
                analysis = _compute_match_analysis(home, away, league, shuffle_tiers=False)
                result_mkt = analysis.get("poisson", {}).get("result", {})
                h_pct = float(result_mkt.get("home_win", 33.0))
                d_pct = float(result_mkt.get("draw", 33.0))
                a_pct = float(result_mkt.get("away_win", 33.0))

                store_prediction_record(conn, fid, date_str, league, country,
                                        home, away, h_pct, d_pct, a_pct)
                pred_h = analysis.get("score_prediction", {}).get("expected_goals", {}).get("home")
                pred_a = analysis.get("score_prediction", {}).get("expected_goals", {}).get("away")
                settle_prediction(conn, fid, home_goals, away_goals,
                                  float(pred_h) if pred_h is not None else None,
                                  float(pred_a) if pred_a is not None else None)
                                  
                # ── Dynamic Team Learning System Update ──
                from src.db.team_intelligence import update_team_ratings
                update_team_ratings(conn, fid, date_str, league,
                                    home, away, h_pct, d_pct, a_pct,
                                    home_goals, away_goals)
                                    
                settled += 1
            except Exception as fe:
                logger.debug(f"settle-date: fixture {f.get('id')} failed: {fe}")
                errors += 1

        leagues_updated = rebuild_confidence_adjustments(conn)

        return {
            "date": date_str,
            "status": "ok",
            "total_finished": len(finished),
            "settled": settled,
            "errors": errors,
            "leagues_updated": leagues_updated,
        }
    except Exception as e:
        logger.error(f"settle-date failed for {date_str}: {e}")
        raise HTTPException(status_code=500, detail=str(e))



def debug_coverage_service(date: str):
    """
    Full coverage report for a given date.
    GET /api/debug/coverage?date=YYYY-MM-DD

    Returns:
      - provider_count: how many events SofaScore returned
      - stored_count: how many were saved to cache (after date filter)
      - rendered_count: same as stored (all stored fixtures are rendered)
      - predicted_ready: how many have prediction pre-warmed
      - coverage_pct: stored / provider * 100
      - leagues, countries, category breakdown
    """
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date!r}")

    # Stored/rendered count
    cached = _read_fixture_cache(date) or []
    stored = [f for f in cached if f.get("date") == date]

    # Provider count (live SofaScore query — may use cache indirectly)
    try:
        raw_sofa = _fetch_sofascore_fixtures(date)
        provider_total = len(raw_sofa)
        # How many of those pass the date filter?
        provider_matching = sum(
            1 for ev in raw_sofa
            if _sofascore_to_fixture(ev, date).get("date") == date
        )
    except Exception:
        provider_total = 0
        provider_matching = len(stored)

    # Prediction status
    predicted_ready = sum(
        1 for f in stored
        if _PREDICTION_STATUS.get(f["id"], {}).get("status") == "ready"
    )
    predicted_pending = sum(
        1 for f in stored
        if _PREDICTION_STATUS.get(f["id"], {}).get("status") == "pending"
    )

    # League/country breakdowns
    leagues_seen: dict = {}
    countries: set = set()
    for f in stored:
        lg = f["league"]
        lid = lg["id"]
        if lid not in leagues_seen:
            leagues_seen[lid] = {
                "id": lid,
                "name": lg["name"],
                "country": lg["country"],
                "category": _categorize_competition(lg["name"], lg["country"]),
                "count": 0,
            }
        leagues_seen[lid]["count"] += 1
        countries.add(lg["country"])

    by_category: dict = {}
    for lg in leagues_seen.values():
        cat = lg["category"]
        by_category[cat] = by_category.get(cat, 0) + lg["count"]

    coverage_pct = round(len(stored) / provider_matching * 100, 1) if provider_matching > 0 else 100.0

    return {
        "provider_matches": provider_total,
        "stored_matches": len(stored),
        "rendered_matches": len(stored),
        "countries": len(countries),
        "competitions": len(leagues_seen)
    }



def debug_coverage_report_service(date: str):
    """
    Mathematical proof of 100% fixture coverage for a given date.

    GET /api/debug/coverage-report?date=YYYY-MM-DD

    Pipeline:
      1. provider_raw           — total events returned by SofaScore
      2. provider_after_timezone — events that actually belong to this date (Istanbul time)
      3. stored                 — events saved to the fixture cache
      4. rendered               — events served to the UI (must equal stored)
      5. coverage_pct           = rendered / provider_after_timezone * 100

    Target: coverage_pct == 100.0
    """
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date!r}. Use YYYY-MM-DD.")

    import datetime as dt_module
    import zoneinfo as zi_module
    from collections import defaultdict

    TZ = zi_module.ZoneInfo("Europe/Istanbul")

    # ── Step 1: Raw provider count ──────────────────────────────────────────
    try:
        raw_events = _fetch_sofascore_fixtures(date)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SofaScore fetch failed: {e}")

    provider_raw = len(raw_events)

    # ── Step 2: After timezone filter ──────────────────────────────────────
    # Exactly mirrors the pipeline in get_fixtures_by_date
    tz_matched = []
    tz_rejected = []
    for ev in raw_events:
        ts = ev.get("startTimestamp")
        if ts:
            try:
                ev_date = dt_module.datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d")
            except Exception:
                ev_date = date  # fallback — keep it
        else:
            ev_date = date  # no timestamp, assume correct date
        if ev_date == date:
            tz_matched.append(ev)
        else:
            tz_rejected.append({"id": ev.get("id"), "actual_date": ev_date})

    provider_after_timezone = len(tz_matched)

    # ── Step 3 & 4: Stored / rendered ──────────────────────────────────────
    cached = _read_fixture_cache(date) or []
    stored = [f for f in cached if f.get("date") == date]
    rendered = len(stored)

    # ── Step 5: Coverage % ─────────────────────────────────────────────────
    if provider_after_timezone > 0:
        coverage_pct = round(rendered / provider_after_timezone * 100, 2)
    else:
        coverage_pct = 100.0

    # ── Fixture loss audit (stored vs after-tz) ────────────────────────────
    stored_ids = {f["id"] for f in stored}
    tz_matched_ids = {str(ev.get("id", "")) for ev in tz_matched}
    missing_ids = tz_matched_ids - stored_ids
    extra_ids   = stored_ids - tz_matched_ids  # should always be 0

    # ── League breakdown ───────────────────────────────────────────────────
    league_counts: dict = defaultdict(int)
    league_meta: dict = {}
    for f in stored:
        lg = f.get("league", {})
        lid = lg.get("id", "unknown")
        league_counts[lid] += 1
        if lid not in league_meta:
            league_meta[lid] = {
                "league_id": lid,
                "league": lg.get("name", ""),
                "country": lg.get("country", ""),
            }

    league_breakdown = sorted(
        [
            {
                "league_id": lid,
                "league": league_meta[lid]["league"],
                "country": league_meta[lid]["country"],
                "matches": cnt,
            }
            for lid, cnt in league_counts.items()
        ],
        key=lambda x: -x["matches"],
    )

    # ── Country breakdown ──────────────────────────────────────────────────
    country_counts: dict = defaultdict(int)
    for f in stored:
        country = f.get("league", {}).get("country", "Unknown")
        country_counts[country] += 1

    country_breakdown = sorted(
        [{"country": c, "matches": n} for c, n in country_counts.items()],
        key=lambda x: -x["matches"],
    )

    # ── Pipeline integrity checks ──────────────────────────────────────────
    checks = {
        "stored_equals_rendered": rendered == len(stored),
        "no_extra_fixtures":      len(extra_ids) == 0,
        "no_missing_fixtures":    len(missing_ids) == 0,
        "full_coverage":          coverage_pct >= 100.0,
    }
    all_checks_passed = all(checks.values())

    return {
        # Core numbers
        "date":                    date,
        "provider_raw":            provider_raw,
        "provider_after_timezone": provider_after_timezone,
        "timezone_filtered_out":   provider_raw - provider_after_timezone,
        "stored":                  len(stored),
        "rendered":                rendered,
        "coverage_pct":            coverage_pct,

        # Integrity
        "checks":           checks,
        "all_checks_passed": all_checks_passed,
        "missing_from_cache": sorted(missing_ids),
        "extra_in_cache":     sorted(extra_ids),

        # Breakdowns
        "total_leagues":  len(league_breakdown),
        "total_countries": len(country_breakdown),
        "league_breakdown":  league_breakdown,
        "country_breakdown": country_breakdown,
    }



def debug_date_trace_service(date: str):
    """
    Full pipeline trace for a given date.
    GET /api/debug/date-trace?date=YYYY-MM-DD
    """
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date!r}. Use YYYY-MM-DD.")

    trace: dict = {
        "requested_date": date,
        "cache_key": f"fixtures-{date}.json",
        "cache_hit": False,
        "cache_count": 0,
        "cache_wrong_date_count": 0,
        "provider_count": 0,
        "provider": None,
        "response_count": 0,
        "response_date_rejected": 0,
        "first_fixture": None,
        "all_fixture_dates_match": None,
    }

    # Cache check
    cached = _read_fixture_cache(date)
    if cached:
        trace["cache_hit"] = True
        trace["cache_count"] = len(cached)
        trace["cache_wrong_date_count"] = sum(1 for f in cached if f.get("date") != date)
        valid = [f for f in cached if f.get("date") == date]
        if valid:
            f0 = valid[0]
            trace["first_fixture"] = {"home": f0["home_team"]["name"], "away": f0["away_team"]["name"], "date": f0["date"], "source": f0.get("source")}
        trace["response_count"] = len(valid)
        trace["all_fixture_dates_match"] = trace["cache_wrong_date_count"] == 0
        return trace

    # SofaScore
    raw_sofa = _fetch_sofascore_fixtures(date)
    trace["provider"] = "sofascore"
    trace["provider_count"] = len(raw_sofa)
    matched = []
    rejected = 0
    for ev in raw_sofa:
        f = _sofascore_to_fixture(ev, date)
        if f["date"] == date:
            matched.append(f)
        else:
            rejected += 1
    trace["response_count"] = len(matched)
    trace["response_date_rejected"] = rejected
    trace["all_fixture_dates_match"] = rejected == 0
    if matched:
        f0 = matched[0]
        trace["first_fixture"] = {"home": f0["home_team"]["name"], "away": f0["away_team"]["name"], "date": f0["date"], "source": f0.get("source")}
        return trace

    # API-Football
    raw_api = _fetch_api_football_fixtures(date)
    trace["provider"] = "apifootball"
    trace["provider_count"] = len(raw_api)
    matched = []
    rejected = 0
    for raw in raw_api:
        f = _api_football_to_fixture(raw)
        if f["date"] == date:
            matched.append(f)
        else:
            rejected += 1
    trace["response_count"] = len(matched)
    trace["response_date_rejected"] = rejected
    trace["all_fixture_dates_match"] = rejected == 0
    if matched:
        f0 = matched[0]
        trace["first_fixture"] = {"home": f0["home_team"]["name"], "away": f0["away_team"]["name"], "date": f0["date"], "source": f0.get("source")}

    return trace



def debug_date_validation_service(date: str):
    """
    Debug endpoint — validate fixture date consistency for a given date.

    Returns per-source counts and flags any fixture whose date != the requested date.
    Use: GET /api/debug/date-validation?date=YYYY-MM-DD
    """
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date!r}. Use YYYY-MM-DD.")

    report = {
        "selected_date": date,
        "cache": {"hit": False, "total": 0, "matching_date": 0, "wrong_date": [], "fixtures": []},
        "sofascore": {"total_events": 0, "league_filtered": 0, "date_matched": 0, "date_rejected": [], "fixtures": []},
        "apifootball": {"total": 0, "date_matched": 0, "date_rejected": [], "fixtures": []},
    }

    # Cache
    cached = _read_fixture_cache(date)
    if cached:
        report["cache"]["hit"] = True
        report["cache"]["total"] = len(cached)
        for f in cached:
            if f.get("date") == date:
                report["cache"]["matching_date"] += 1
                report["cache"]["fixtures"].append({"id": f["id"], "date": f["date"], "teams": f"{f['home_team']['name']} vs {f['away_team']['name']}"})
            else:
                report["cache"]["wrong_date"].append({"id": f["id"], "actual_date": f.get("date"), "teams": f"{f['home_team']['name']} vs {f['away_team']['name']}"})

    # SofaScore
    raw_sofa = _fetch_sofascore_fixtures(date)
    report["sofascore"]["total_events"] = len(raw_sofa)
    for ev in raw_sofa:
        report["sofascore"]["total_events"] += 1
        f = _sofascore_to_fixture(ev, date)
        entry = {"id": f["id"], "fixture_date": f["date"], "teams": f"{f['home_team']['name']} vs {f['away_team']['name']}"}
        if f["date"] == date:
            report["sofascore"]["date_matched"] += 1
            report["sofascore"]["fixtures"].append(entry)
        else:
            report["sofascore"]["date_rejected"].append(entry)

    # API-Football
    raw_api = _fetch_api_football_fixtures(date)
    report["apifootball"]["total"] = len(raw_api)
    for raw in raw_api:
        f = _api_football_to_fixture(raw)
        entry = {"id": f["id"], "fixture_date": f["date"], "teams": f"{f['home_team']['name']} vs {f['away_team']['name']}"}
        if f["date"] == date:
            report["apifootball"]["date_matched"] += 1
            report["apifootball"]["fixtures"].append(entry)
        else:
            report["apifootball"]["date_rejected"].append(entry)

    return report


# /api/analysis/match/{fixture_id} extracted to analysis_router



# ═══════════════════════════════════════════════════════════════════════
# PHASE 4: LIVE ADAPTIVE PIPELINE — API Endpoints
# ═══════════════════════════════════════════════════════════════════════


def get_provider_health_service():
    """Returns the health status of all data providers."""
    from src.engine.audit_engine import audit_provider_health
    return audit_provider_health()



def debug_realtime_health_service(date: str = None):
    """Real-time freshness audit for fixtures, odds, live scores, and settlement."""
    from src.engine.audit_engine import audit_realtime_freshness
    if not date:
        date = _get_istanbul_today()
    return audit_realtime_freshness(date)



def debug_odds_integrity_service(date: str = None):
    """Audit the odds pipeline for data availability and integrity."""
    from src.engine.audit_engine import audit_odds_integrity
    if not date:
        date = _get_istanbul_today()
    return audit_odds_integrity(date)



def debug_probability_integrity_service(home: str = "Arsenal", away: str = "Chelsea", league: str = "Premier League"):
    """Verify all market probabilities are mathematically consistent."""
    from src.engine.audit_engine import audit_probability_integrity
    return audit_probability_integrity(home, away, league)



def debug_model_validation_service():
    """Run backtest on settled predictions only."""
    from src.engine.audit_engine import audit_model_validation
    return audit_model_validation()



def debug_warehouse_stats_service():
    """Verify warehouse completeness and coverage."""
    from src.engine.audit_engine import audit_warehouse
    return audit_warehouse()



def debug_production_readiness_service(date: str = None):
    """Master production readiness audit — aggregates all checks."""
    from src.engine.audit_engine import audit_production_readiness
    if not date:
        date = _get_istanbul_today()
    return audit_production_readiness(date)

# ── Phase 5: Scoreline & xG Diagnostics ──


def get_scoreline_performance_service(
    league: str = None,
    season: str = None,
    date_from: str = None,
    date_to: str = None,
    confidence_bucket: str = None
):
    import sqlite3
    from src.db.database import get_db
    conn = get_db()
    conn.row_factory = sqlite3.Row
    
    query = "SELECT * FROM scoreline_learning_log WHERE 1=1"
    params = []
    
    if league:
        query += " AND league_name = ?"
        params.append(league)
    if season:
        query += " AND season = ?"
        params.append(season)
    if date_from:
        query += " AND match_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND match_date <= ?"
        params.append(date_to)
    if confidence_bucket:
        query += " AND confidence_bucket = ?"
        params.append(confidence_bucket)
        
    rows = conn.execute(query, params).fetchall()
    
    if not rows:
        return {"count": 0}
        
    total = len(rows)
    top1 = sum(1 for r in rows if r["top1_hit"])
    top3 = sum(1 for r in rows if r["top3_hit"])
    top5 = sum(1 for r in rows if r["top5_hit"])
    top10 = sum(1 for r in rows if r["top10_hit"])
    
    avg_rank = sum(r["actual_rank"] for r in rows) / total
    avg_prob = sum(r["actual_probability"] for r in rows) / total
    
    return {
        "count": total,
        "top1_hit_rate": round(top1 / total * 100, 1),
        "top3_hit_rate": round(top3 / total * 100, 1),
        "top5_hit_rate": round(top5 / total * 100, 1),
        "top10_hit_rate": round(top10 / total * 100, 1),
        "avg_actual_rank": round(avg_rank, 1),
        "avg_actual_probability": round(avg_prob, 2)
    }


def get_xg_performance_service():
    import sqlite3
    from src.db.database import get_db
    conn = get_db()
    conn.row_factory = sqlite3.Row
    
    rows = conn.execute("SELECT * FROM scoreline_learning_log").fetchall()
    if not rows:
        return {"count": 0}
        
    total = len(rows)
    avg_h = sum(r["home_goal_error"] for r in rows) / total
    avg_a = sum(r["away_goal_error"] for r in rows) / total
    avg_tot = sum(r["total_goal_error"] for r in rows) / total
    
    # Leagues breakdown
    league_map = {}
    for r in rows:
        ln = r["league_name"] or "Unknown"
        if ln not in league_map:
            league_map[ln] = {"count": 0, "sum_err": 0}
        league_map[ln]["count"] += 1
        league_map[ln]["sum_err"] += r["total_goal_error"]
        
    league_list = []
    for ln, data in league_map.items():
        if data["count"] >= 5:
            league_list.append({
                "league": ln,
                "count": data["count"],
                "avg_error": round(data["sum_err"] / data["count"], 2)
            })
            
    league_list.sort(key=lambda x: abs(x["avg_error"]))
    best_leagues = league_list[:5]
    worst_leagues = league_list[-5:]
    worst_leagues.reverse()
    
    return {
        "count": total,
        "avg_home_goal_error": round(avg_h, 2),
        "avg_away_goal_error": round(avg_a, 2),
        "avg_total_goal_error": round(avg_tot, 2),
        "best_leagues": best_leagues,
        "worst_leagues": worst_leagues
    }


def get_model_comparison_service():
    """
    Returns the strict mathematical backtesting comparison of all Deep Learning
    and Tree-based models (Brier Score, ECE, ROI, Log Loss, Accuracy).
    """
    from src.ml.model_benchmark import run_full_benchmark
    benchmark_data = run_full_benchmark()
    return benchmark_data["metrics"]


def get_model_rankings_service():
    """
    Returns the final ranking of all predictive models, ordered by
    Brier Score + Calibration Error + ROI.
    """
    from src.ml.model_benchmark import run_full_benchmark
    benchmark_data = run_full_benchmark()
    return benchmark_data["rankings"]




def run_feature_backtest_service(limit: int = 50):
    """
    Feature Contribution Backtesting Framework.
    Evaluates configurations progressively to measure feature impact.
    """
    from src.db.database import get_db
    conn = get_db()
    
    # Fetch last N matches from match_history
    matches = conn.execute(
        "SELECT home_team, away_team, league, home_goals, away_goals FROM match_history ORDER BY match_date DESC, id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    
    if not matches:
        return {"error": "No matches in history."}
        
    configs = [
        {"name": "Baseline", "flags": {"USE_TEAM_RATINGS": False, "USE_MOMENTUM": False, "USE_HOME_ADVANTAGE": False, "USE_VOLATILITY": False, "USE_LEAGUE_RELIABILITY": False}},
        {"name": "+ Team Ratings", "flags": {"USE_TEAM_RATINGS": True, "USE_MOMENTUM": False, "USE_HOME_ADVANTAGE": False, "USE_VOLATILITY": False, "USE_LEAGUE_RELIABILITY": False}},
        {"name": "+ Momentum", "flags": {"USE_TEAM_RATINGS": True, "USE_MOMENTUM": True, "USE_HOME_ADVANTAGE": False, "USE_VOLATILITY": False, "USE_LEAGUE_RELIABILITY": False}},
        {"name": "+ Home Advantage", "flags": {"USE_TEAM_RATINGS": True, "USE_MOMENTUM": True, "USE_HOME_ADVANTAGE": True, "USE_VOLATILITY": False, "USE_LEAGUE_RELIABILITY": False}},
        {"name": "+ Volatility", "flags": {"USE_TEAM_RATINGS": True, "USE_MOMENTUM": True, "USE_HOME_ADVANTAGE": True, "USE_VOLATILITY": True, "USE_LEAGUE_RELIABILITY": False}},
        {"name": "All Features", "flags": {"USE_TEAM_RATINGS": True, "USE_MOMENTUM": True, "USE_HOME_ADVANTAGE": True, "USE_VOLATILITY": True, "USE_LEAGUE_RELIABILITY": True}}
    ]
    
    results = []
    
    for cfg in configs:
        correct = 0
        brier_sum = 0.0
        conf_sum = 0.0
        
        for m in matches:
            home_team, away_team, league, h_goals, a_goals = m
            
            # Actual result
            if h_goals > a_goals: act_h, act_d, act_a = 1.0, 0.0, 0.0
            elif h_goals == a_goals: act_h, act_d, act_a = 0.0, 1.0, 0.0
            else: act_h, act_d, act_a = 0.0, 0.0, 1.0
                
            try:
                pred = _compute_match_analysis(home_team, away_team, league, feature_flags=cfg["flags"])
                # Extract 1X2 probabilities
                h_pct = pred["poisson"]["result"]["home_win"] / 100.0
                d_pct = pred["poisson"]["result"]["draw"] / 100.0
                a_pct = pred["poisson"]["result"]["away_win"] / 100.0
            except Exception:
                h_pct, d_pct, a_pct = 0.33, 0.33, 0.33
                
            # Highest prob outcome
            best_p = max(h_pct, d_pct, a_pct)
            conf_sum += best_p
            
            if best_p == h_pct and act_h == 1.0: correct += 1
            elif best_p == d_pct and act_d == 1.0: correct += 1
            elif best_p == a_pct and act_a == 1.0: correct += 1
                
            brier_sum += ((h_pct - act_h)**2 + (d_pct - act_d)**2 + (a_pct - act_a)**2)
            
        N = len(matches)
        acc = correct / N
        avg_conf = conf_sum / N
        cal_gap = abs(avg_conf - acc)
        brier = brier_sum / N
        
        results.append({
            "configuration": cfg["name"],
            "accuracy": round(acc * 100, 2),
            "brier_score": round(brier, 4),
            "calibration_gap": round(cal_gap * 100, 2)
        })
        
    deltas = {}
    for i in range(1, len(results)):
        prev = results[i-1]
        curr = results[i]
        diff = round(curr["accuracy"] - prev["accuracy"], 2)
        
        # Mapping config name to feature name
        feature_name = curr["configuration"].replace("+ ", "")
        if feature_name == "All Features": feature_name = "League Reliability"
        
        deltas[feature_name] = diff
        
    # Sort leaderboard by brier score ascending
    leaderboard = sorted(results, key=lambda x: x["brier_score"])
    
    return {
        "matches_tested": len(matches),
        "leaderboard": leaderboard,
        "feature_deltas_accuracy": deltas
    }

def debug_provider_comparison_service():
    """
    Returns a 30-day side-by-side comparison of API-Football vs SofaScore reliability.
    """
    from src.db.database import get_db
    conn = get_db()
    
    # 30-day query
    # 30-day query
    query = """
        SELECT provider,
               COUNT(*) as total_requests,
               SUM(CASE WHEN success THEN 1 ELSE 0 END) as successful_requests,
               AVG(latency_ms) as avg_latency_ms,
               SUM(fixture_count) as total_fixtures,
               SUM(odds_count) as total_odds,
               SUM(statistics_count) as total_stats,
               SUM(lineups_count) as total_lineups,
               SUM(live_updates) as total_live_updates
        FROM provider_health_log
        WHERE created_at >= datetime('now', '-30 days')
        GROUP BY provider
    """
    rows = conn.execute(query).fetchall()
    
    stats = {}
    for row in rows:
        provider = row["provider"]
        total = row["total_requests"]
        successes = row["successful_requests"]
        failure_rate = ((total - successes) / total * 100) if total > 0 else 0
        
        stats[provider] = {
            "total_requests": total,
            "success_rate": f"{(successes / total * 100):.1f}%" if total > 0 else "0.0%",
            "failure_rate": f"{failure_rate:.1f}%",
            "avg_latency_ms": round(row["avg_latency_ms"] or 0),
            "fixtures_provided": row["total_fixtures"] or 0,
            "odds_provided": row["total_odds"] or 0,
            "statistics_provided": row["total_stats"] or 0,
            "lineups_provided": row["total_lineups"] or 0,
            "live_updates": row["total_live_updates"] or 0
        }
        
    api_football_fixtures = stats.get("api-football", {}).get("fixtures_provided", 0)
    sofascore_fixtures = stats.get("sofascore", {}).get("fixtures_provided", 0)
    
    # SofaScore now runs in the background alongside API-Football,
    # so we measure the absolute difference in fixtures found.
    net_gain = sofascore_fixtures - api_football_fixtures
    
    return {
        "api_football": stats.get("api-football", {}),
        "sofascore": stats.get("sofascore", {}),
        "coverage_difference": {
            "additional_fixtures_from_sofascore": net_gain
        },
        "recommendation": "Pending 30-day evaluation. Do not replace API-Football until failure rates drop and coverage gain is substantial."
    }


def debug_provider_history_service():
    """
    Returns an executive summary of provider reliability and data yields 
    over the last 7 days to drive architectural decisions.
    """
    from src.db.database import get_db
    conn = get_db()
    
    # 7-day query
    query = """
        SELECT provider,
               COUNT(*) as total_requests,
               SUM(CASE WHEN success THEN 1 ELSE 0 END) as successful_requests,
               AVG(latency_ms) as avg_latency_ms,
               SUM(fixture_count) as total_fixtures
        FROM provider_health_log
        WHERE created_at >= datetime('now', '-7 days')
        GROUP BY provider
    """
    rows = conn.execute(query).fetchall()
    
    stats = {}
    for row in rows:
        provider_id = row["provider"].replace("-", "_")
        total = row["total_requests"]
        successes = row["successful_requests"]
        uptime = (successes / total * 100) if total > 0 else 0
        
        stats[provider_id] = {
            "uptime": round(uptime, 1),
            "avg_latency": round(row["avg_latency_ms"] or 0),
            "fixtures": row["total_fixtures"] or 0
        }
        
    return {
        "last_7_days": stats
    }



def debug_api_football_status_service():
    from src.config import APIFOOTBALL_API_KEY, APIFOOTBALL_HOST
    import requests
    import os
    
    key_source = "missing"
    if "APIFOOTBALL_API_KEY" in os.environ or "API_FOOTBALL_KEY" in os.environ:
        key_source = "env"
    elif APIFOOTBALL_API_KEY:
        key_source = ".env"
    
    status = {
        "key_loaded": bool(APIFOOTBALL_API_KEY),
        "key_source": key_source,
        "connected": False,
        "quota_exceeded": False,
        "requests_remaining": 0,
        "daily_limit": 0,
        "last_success": None,
        "last_failure": None,
        "error": None
    }
    
    if not APIFOOTBALL_API_KEY:
        status["error"] = "No API key configured"
        return status
        
    try:
        url = f"https://{APIFOOTBALL_HOST}/status"
        headers = {"x-apisports-key": APIFOOTBALL_API_KEY}
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        
        status["connected"] = True
        
        errors = data.get("errors", {})
        if errors and isinstance(errors, dict):
            if "access" in errors:
                status["error"] = "API_FOOTBALL_ACCESS_ERROR"
                status["last_failure"] = errors["access"]
            elif "requests" in errors:
                status["quota_exceeded"] = True
                status["error"] = "API_FOOTBALL_QUOTA_EXCEEDED"
                status["last_failure"] = errors["requests"]
            elif errors:
                status["error"] = "API_FOOTBALL_ERROR"
                status["last_failure"] = errors
            
        response_data = data.get("response", {})
        if response_data and isinstance(response_data, dict):
            reqs = response_data.get("requests", {})
            status["daily_limit"] = reqs.get("limit_day", 0)
            status["requests_remaining"] = status["daily_limit"] - reqs.get("current", status["daily_limit"])
            
            if not status["quota_exceeded"] and status["requests_remaining"] <= 0:
                status["quota_exceeded"] = True
                status["error"] = "API_FOOTBALL_QUOTA_EXCEEDED"
                
    except Exception as e:
        status["error"] = str(e)
        status["connected"] = False
        
    return status


# ── DEBUG API ROUTES ─────────────────────────────────────────────────────────



def debug_sofascore_status_service():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT success, latency_ms, created_at 
        FROM provider_health_log 
        WHERE provider = 'sofascore' 
        ORDER BY id DESC LIMIT 100
    """)
    rows = cursor.fetchall()
    
    if not rows:
        return {"connected": False, "avg_latency_ms": 0, "failure_rate_24h": 0.0}
        
    success_count = sum(1 for r in rows if r[0] == 1)
    fail_count = len(rows) - success_count
    latencies = [r[1] for r in rows if r[1] is not None]
    
    last_success = next((r[2] for r in rows if r[0] == 1), None)
    last_failure = next((r[2] for r in rows if r[0] == 0), None)
    
    return {
        "connected": success_count > 0 and rows[0][0] == 1,
        "avg_latency_ms": sum(latencies)/len(latencies) if latencies else 0,
        "failure_rate_24h": fail_count / len(rows),
        "last_success": last_success,
        "last_failure": last_failure
    }


def debug_main_fixtures_service():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT is_main_fixture, COUNT(*) FROM matches WHERE date = date('now') GROUP BY is_main_fixture")
    counts = dict(cursor.fetchall())
    main_c = counts.get(1, 0)
    rejected_c = counts.get(0, 0)
    return {
        "raw_count": main_c + rejected_c,
        "main_count": main_c,
        "rejected": rejected_c
    }


def debug_live_quality_service():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT is_stale, COUNT(*) FROM matches WHERE status IN ('LIVE', 'HT', 'STALE') GROUP BY is_stale")
    counts = dict(cursor.fetchall())
    fresh = counts.get(0, 0)
    stale = counts.get(1, 0)
    total = fresh + stale
    return {
        "live_matches": total,
        "fresh": fresh,
        "stale": stale,
        "coverage_pct": (fresh / total * 100) if total > 0 else 100
    }



def debug_pending_settlements_service():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT event_id, attempts, provider, first_ft_seen, last_check 
        FROM pending_settlements 
        ORDER BY first_ft_seen ASC
    """)
    rows = cursor.fetchall()
    
    matches = []
    now_ts = datetime.now(timezone.utc).timestamp()
    oldest_minutes = 0
    
    for r in rows:
        first_ft_dt = datetime.fromisoformat(r[3])
        minutes_pending = int((now_ts - first_ft_dt.timestamp()) / 60)
        if minutes_pending > oldest_minutes:
            oldest_minutes = minutes_pending
            
        matches.append({
            "event_id": r[0],
            "attempts": r[1],
            "provider": r[2],
            "first_ft_seen": r[3],
            "last_check": r[4]
        })
        
    return {
        "pending_count": len(matches),
        "oldest_pending_minutes": oldest_minutes,
        "matches": matches
    }


def debug_dashboard_service():
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. Health
    cursor.execute("""
        SELECT success FROM provider_health_log 
        WHERE provider = 'sofascore' 
        ORDER BY id DESC LIMIT 10
    """)
    rows = cursor.fetchall()
    health_status = "unknown"
    if rows:
        recent_successes = sum(1 for r in rows if r[0] == 1)
        health_status = "healthy" if recent_successes >= 8 else ("failing" if recent_successes < 3 else "degraded")
        
    # 2. Live Quality
    cursor.execute("SELECT is_stale, COUNT(*) FROM matches WHERE status IN ('LIVE', 'HT', 'STALE') GROUP BY is_stale")
    counts = dict(cursor.fetchall())
    fresh = counts.get(0, 0)
    stale = counts.get(1, 0)
    total_live = fresh + stale
    coverage = round((fresh / total_live * 100), 1) if total_live > 0 else 100.0
    
    # 3. Pending & Settled
    cursor.execute("SELECT COUNT(*) FROM pending_settlements")
    pending_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM match_history WHERE match_date = date('now')")
    settled_today = cursor.fetchone()[0]
    
    # 4. Predictions / NO PICK
    cursor.execute("SELECT market, selection, grade, edge FROM picks WHERE match_id IN (SELECT id FROM matches WHERE date = date('now'))")
    picks = cursor.fetchall()
    no_pick = 0
    high_c = 0
    med_c = 0
    low_c = 0
    for p in picks:
        if p[1] == "NO PICK" or p[0] == "NO PICK":
            no_pick += 1
            continue
        grade = p[2]
        edge = p[3] or 0
        if grade in ("A", "A+", "A++"):
            high_c += 1
        elif grade in ("B", "B+", "B-"):
            med_c += 1
        else:
            low_c += 1
            
    return {
        "provider": "sofascore",
        "sofascore_health": health_status,
        "live_matches": total_live,
        "stale_matches": stale,
        "coverage_pct": coverage,
        "pending_settlements": pending_count,
        "settled_today": settled_today,
        "high_confidence": high_c,
        "medium_confidence": med_c,
        "low_confidence": low_c,
        "no_pick": no_pick
    }

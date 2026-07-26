"""
Debug & Admin Router.

Exposes endpoints for system diagnostics, calibration inspection, provider health,
and data warehouse validation.
Strictly delegates business logic to debug_service.
"""

from typing import Optional
from fastapi import APIRouter, Depends
from api.dependencies import verify_admin_key
from api.services import debug_service

router = APIRouter(tags=["Debug & Admin"])


@router.get("/api/debug/model-health", dependencies=[Depends(verify_admin_key)])
def get_model_health_report():
    return debug_service.get_model_health_report_service()

@router.get("/api/debug/team-rating", dependencies=[Depends(verify_admin_key)])
def get_team_rating_endpoint(team: str):
    return debug_service.get_team_rating_endpoint_service(team=team)

@router.get("/api/debug/calibration", dependencies=[Depends(verify_admin_key)])
def get_calibration_report_debug(market_type: str = "result"):
    return debug_service.get_calibration_report_debug_service(market_type=market_type)

@router.get("/api/debug/settle-date/{date_str}", dependencies=[Depends(verify_admin_key)])
def settle_date_endpoint(date_str: str):
    return debug_service.settle_date_endpoint_service(date_str=date_str)

@router.get("/api/debug/coverage", dependencies=[Depends(verify_admin_key)])
def debug_coverage(date: str):
    return debug_service.debug_coverage_service(date=date)

@router.get("/api/debug/coverage-report", dependencies=[Depends(verify_admin_key)])
def debug_coverage_report(date: str):
    return debug_service.debug_coverage_report_service(date=date)

@router.get("/api/debug/date-trace", dependencies=[Depends(verify_admin_key)])
def debug_date_trace(date: str):
    return debug_service.debug_date_trace_service(date=date)

@router.get("/api/debug/date-validation", dependencies=[Depends(verify_admin_key)])
def debug_date_validation(date: str):
    return debug_service.debug_date_validation_service(date=date)

@router.get("/api/debug/provider-health", dependencies=[Depends(verify_admin_key)])
def get_provider_health():
    return debug_service.get_provider_health_service()

@router.get("/api/debug/realtime-health", dependencies=[Depends(verify_admin_key)])
def debug_realtime_health(date: str = None):
    return debug_service.debug_realtime_health_service(date=date)

@router.get("/api/debug/odds-integrity", dependencies=[Depends(verify_admin_key)])
def debug_odds_integrity(date: str = None):
    return debug_service.debug_odds_integrity_service(date=date)

@router.get("/api/debug/probability-integrity", dependencies=[Depends(verify_admin_key)])
def debug_probability_integrity(home: str = "Arsenal", away: str = "Chelsea", league: str = "Premier League"):
    return debug_service.debug_probability_integrity_service(home=home, away=away, league=league)

@router.get("/api/debug/model-validation", dependencies=[Depends(verify_admin_key)])
def debug_model_validation():
    return debug_service.debug_model_validation_service()

@router.get("/api/debug/warehouse-stats", dependencies=[Depends(verify_admin_key)])
def debug_warehouse_stats():
    return debug_service.debug_warehouse_stats_service()

@router.get("/api/debug/production-readiness", dependencies=[Depends(verify_admin_key)])
def debug_production_readiness(date: str = None):
    return debug_service.debug_production_readiness_service(date=date)

@router.get("/api/debug/scoreline-performance", dependencies=[Depends(verify_admin_key)])
def get_scoreline_performance(
    league: str = None,
    season: str = None,
    date_from: str = None,
    date_to: str = None,
    confidence_bucket: str = None
):
    return debug_service.get_scoreline_performance_service(league=league, season=season, date_from=date_from, date_to=date_to, confidence_bucket=confidence_bucket)

@router.get("/api/debug/xg-performance", dependencies=[Depends(verify_admin_key)])
def get_xg_performance():
    return debug_service.get_xg_performance_service()

@router.get("/api/debug/model-comparison", dependencies=[Depends(verify_admin_key)])
def get_model_comparison():
    return debug_service.get_model_comparison_service()

@router.get("/api/debug/model-rankings", dependencies=[Depends(verify_admin_key)])
def get_model_rankings():
    return debug_service.get_model_rankings_service()

@router.get("/api/debug/backtest-features", dependencies=[Depends(verify_admin_key)])
def run_feature_backtest(limit: int = 50):
    return debug_service.run_feature_backtest_service(limit=limit)

@router.get("/api/debug/provider-comparison")
def debug_provider_comparison():
    return debug_service.debug_provider_comparison_service()

@router.get("/api/debug/provider-history")
def debug_provider_history():
    return debug_service.debug_provider_history_service()

@router.get("/api/debug/api-football-status")
def debug_api_football_status():
    return debug_service.debug_api_football_status_service()

@router.get("/api/debug/sofascore-status")
def debug_sofascore_status():
    return debug_service.debug_sofascore_status_service()

@router.get("/api/debug/main-fixtures")
def debug_main_fixtures():
    return debug_service.debug_main_fixtures_service()

@router.get("/api/debug/live-quality")
def debug_live_quality():
    return debug_service.debug_live_quality_service()

@router.get("/api/debug/pending-settlements")
def debug_pending_settlements():
    return debug_service.debug_pending_settlements_service()

@router.get("/api/debug/dashboard")
def debug_dashboard():
    return debug_service.debug_dashboard_service()
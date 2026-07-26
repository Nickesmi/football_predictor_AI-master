"""
Fixtures Router.

Handles endpoints for daily fixtures, date-based fixtures, prediction precomputation,
and prediction precomputation status. Strictly delegates business logic to fixtures_service.
"""

from fastapi import APIRouter, BackgroundTasks
from api.services import fixtures_service

router = APIRouter(tags=["Fixtures & Precomputation"])


@router.get("/api/fixtures/today")
def get_today_fixtures(background_tasks: BackgroundTasks):
    return fixtures_service.get_today_fixtures_service(background_tasks)


@router.get("/api/precompute-predictions")
def precompute_predictions(date_str: str, background_tasks: BackgroundTasks):
    return fixtures_service.precompute_predictions_service(date_str, background_tasks)


@router.get("/api/prediction-status/{fixture_id}")
def get_prediction_status(fixture_id: str):
    return fixtures_service.get_prediction_status_service(fixture_id)


@router.get("/api/fixtures/{date_str}")
def get_fixtures_by_date(date_str: str, background_tasks: BackgroundTasks, force_refresh: bool = False):
    return fixtures_service.get_fixtures_by_date_service(date_str, background_tasks, force_refresh=force_refresh)

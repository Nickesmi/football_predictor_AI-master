"""
Results & Pipeline Router.

Handles endpoints for result verification and running the investment pipeline.
Strictly delegates business logic to results_service.
"""

from fastapi import APIRouter, BackgroundTasks
from api.services import results_service

router = APIRouter(tags=["Results & Pipeline"])


@router.get("/api/results/{date_str}")
def get_results_verification(date_str: str, background_tasks: BackgroundTasks):
    return results_service.get_results_verification_service(date_str, background_tasks)


@router.get("/api/pipeline/run/{date_str}")
def run_investment_pipeline(date_str: str):
    return results_service.run_investment_pipeline_service(date_str)

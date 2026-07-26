"""
Router for match analysis and prediction endpoints.
"""
from typing import Any
from fastapi import APIRouter
from api.services.match_analysis_service import get_match_analysis

router = APIRouter(prefix="/api/analysis", tags=["Match Analysis"])


@router.get("/match/{fixture_id}")
def analyze_match_endpoint(
    fixture_id: str,
    home: str = "",
    away: str = "",
    league: str = "Premier League",
    status: str = "",
    start_time: str = "",
) -> dict[str, Any]:
    """
    Per-match prediction endpoint.
    Returns unique probabilities and analysis across all betting markets.
    """
    return get_match_analysis(
        fixture_id=fixture_id,
        home=home,
        away=away,
        league=league,
        status=status,
        start_time=start_time,
    )

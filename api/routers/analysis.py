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
    provider: str | None = None,
    league_id: str | None = None,
    league_code: str | None = None,
    country: str = "",
    home_id: str | None = None,
    away_id: str | None = None,
    kickoff_utc: str | None = None,
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
        provider=provider,
        league_id=league_id,
        league_code=league_code,
        country=country,
        home_id=home_id,
        away_id=away_id,
        kickoff_utc=kickoff_utc,
    )

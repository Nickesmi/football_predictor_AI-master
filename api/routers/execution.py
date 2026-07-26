"""
Execution Router.

Exposes endpoints for finding executable betting opportunities, retrieving betting rules,
and simulating bet EV/edge/Kelly calculations.
Strictly delegates business logic to execution_service.
"""

from typing import Any, Dict
from fastapi import APIRouter
from api.services import execution_service

router = APIRouter(prefix="/api/execution", tags=["Execution Engine"])


@router.get("/rules")
def get_execution_rules() -> Dict[str, Any]:
    """Return current execution rules and tradable market whitelist."""
    return execution_service.get_execution_rules()


@router.get("/simulate")
def simulate_execution(
    calibrated_prob: float = 75.0,
    odds: float = 1.55,
) -> Dict[str, Any]:
    """Test EV/Edge/Kelly computation on a single hypothetical bet."""
    return execution_service.simulate_execution(calibrated_prob=calibrated_prob, odds=odds)


@router.get("/opportunities/{home}/{away}/{league}")
def get_execution_opportunities(
    home: str, away: str, league: str, use_live_odds: bool = False
) -> Dict[str, Any]:
    """Find executable, positive-EV betting opportunities for a match."""
    return execution_service.get_execution_opportunities(
        home=home, away=away, league=league, use_live_odds=use_live_odds
    )

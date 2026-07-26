"""
Portfolio Router.

Handles API endpoints for bankroll management, betting portfolio tracking, pick placement,
automatic match result settlement, closing line value (CLV) calculation, and odds fetching.
Strictly delegates business logic to portfolio_service.
"""

import sqlite3
from typing import Any, Dict
from fastapi import APIRouter, Depends
from api.services import portfolio_service
from api.services.portfolio_service import PickCreate, ClvUpdate

router = APIRouter(tags=["Portfolio & Picks"])


def _get_db() -> sqlite3.Connection:
    """Dependency to retrieve database connection."""
    from src.db.database import get_db

    return get_db()


@router.get("/api/portfolio/summary")
def get_portfolio(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """Bankroll state: total P&L, ROI, hit rate, CLV."""
    return portfolio_service.get_portfolio(conn)


@router.post("/api/picks/settle")
def auto_settle_picks(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """Auto-settle picks where match results are available."""
    return portfolio_service.auto_settle_picks(conn)


@router.post("/api/picks/place")
def place_pick(
    pick: PickCreate, conn: sqlite3.Connection = Depends(_get_db)
) -> Dict[str, Any]:
    """Place a pick into the tracking portfolio."""
    return portfolio_service.place_pick(conn, pick.dict())


@router.post("/api/picks/update_clv")
def update_pick_clv(
    payload: ClvUpdate, conn: sqlite3.Connection = Depends(_get_db)
) -> Dict[str, Any]:
    """Update a pick with closing odds and calculate CLV."""
    return portfolio_service.update_pick_clv(
        conn, pick_id=payload.pick_id, closing_odds=payload.closing_odds
    )


@router.post("/api/odds/fetch")
def trigger_odds_fetch(conn: sqlite3.Connection = Depends(_get_db)) -> Dict[str, Any]:
    """Manually trigger odds fetch from The Odds API."""
    return portfolio_service.trigger_odds_fetch(conn)


@router.get("/api/picks/{date_str}")
def get_picks_for_date(
    date_str: str, conn: sqlite3.Connection = Depends(_get_db)
) -> Dict[str, Any]:
    """Get all stored picks for a date (from DB)."""
    return portfolio_service.get_picks_for_date(conn, date_str=date_str)

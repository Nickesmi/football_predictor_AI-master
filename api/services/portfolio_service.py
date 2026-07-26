"""
Portfolio Service Module.

Provides business logic and data access for bankroll management, pick placement,
automatic settlement against match results, CLV (Closing Line Value) calculation,
and triggering external odds fetching.
"""

import logging
import sqlite3
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from src.db.picks_repo import (
    get_picks_by_date,
    get_portfolio_summary,
    get_unsettled_picks,
    settle_pick,
    insert_pick,
    update_closing_odds,
)
from src.data.odds_fetcher import TheOddsAPIProvider, get_api_key as get_odds_key, LEAGUE_TO_SPORT

logger = logging.getLogger("football_predictor")


class PickCreate(BaseModel):
    """Schema for creating a new pick in the portfolio."""

    match_id: str
    market: str
    selection: str
    model_prob: float
    implied_prob: float
    edge: float
    odds_at_pick: float
    confidence: float
    league_reliability: float
    grade: str
    stake_units: float


class ClvUpdate(BaseModel):
    """Schema for updating closing line value on a placed pick."""

    pick_id: int
    closing_odds: float


def _evaluate_pick_result(
    market: str, selection: str, home_goals: int, away_goals: int
) -> Optional[str]:
    """
    Determine if a pick won or lost based on final match score.

    Args:
        market: Betting market (e.g., '1X2', 'O/U 2.5', 'BTTS').
        selection: Chosen selection (e.g., 'home', 'over', 'yes').
        home_goals: Final home team goals scored.
        away_goals: Final away team goals scored.

    Returns:
        'won', 'lost', or None if market/selection cannot be evaluated.
    """
    total_goals = home_goals + away_goals

    if market == "1X2":
        if selection == "home":
            return "won" if home_goals > away_goals else "lost"
        elif selection == "draw":
            return "won" if home_goals == away_goals else "lost"
        elif selection == "away":
            return "won" if away_goals > home_goals else "lost"

    elif market == "O/U 2.5":
        if selection == "over":
            return "won" if total_goals > 2.5 else "lost"
        elif selection == "under":
            return "won" if total_goals < 2.5 else "lost"

    elif market == "BTTS":
        both_scored = home_goals > 0 and away_goals > 0
        if selection == "yes":
            return "won" if both_scored else "lost"
        elif selection == "no":
            return "won" if not both_scored else "lost"

    return None


def get_picks_for_date(conn: sqlite3.Connection, date_str: str) -> Dict[str, Any]:
    """
    Retrieve all stored portfolio picks for a specific date.

    Args:
        conn: Active SQLite database connection.
        date_str: ISO format date string (YYYY-MM-DD).

    Returns:
        Dictionary containing date, list of picks, and pick count.
    """
    picks = get_picks_by_date(conn, date_str)
    return {"date": date_str, "picks": picks, "count": len(picks)}


def get_portfolio(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Retrieve current bankroll summary including total P&L, ROI, hit rate, and average CLV.

    Args:
        conn: Active SQLite database connection.

    Returns:
        Dictionary detailing bankroll and performance metrics.
    """
    summary = get_portfolio_summary(conn)
    return summary


def auto_settle_picks(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Automatically evaluate and settle open picks where match results are ingested.

    Args:
        conn: Active SQLite database connection.

    Returns:
        Dictionary indicating count of settled picks and remaining unsettled picks.
    """
    unsettled = get_unsettled_picks(conn)
    settled_count = 0

    for pick in unsettled:
        home_goals = pick.get("home_goals")
        away_goals = pick.get("away_goals")
        if home_goals is None or away_goals is None:
            continue

        result = _evaluate_pick_result(pick["market"], pick["selection"], home_goals, away_goals)
        if result is None:
            continue

        if result == "won":
            pnl = round(pick["stake_units"] * (pick["odds_at_pick"] - 1), 3)
        elif result == "lost":
            pnl = round(-pick["stake_units"], 3)
        else:
            pnl = 0.0

        settle_pick(conn, pick["id"], result, pnl)
        settled_count += 1

    return {"settled": settled_count, "remaining": len(unsettled) - settled_count}


def place_pick(conn: sqlite3.Connection, pick_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Insert a new pick into the portfolio ledger.

    Args:
        conn: Active SQLite database connection.
        pick_data: Dictionary representation of PickCreate schema.

    Returns:
        Dictionary with status and new pick ID or error message.
    """
    try:
        pick_id = insert_pick(conn, pick_data)
        return {"status": "ok", "pick_id": pick_id}
    except Exception as e:
        logger.error(f"Failed to insert pick: {e}")
        return {"status": "error", "message": str(e)}


def update_pick_clv(
    conn: sqlite3.Connection, pick_id: int, closing_odds: float
) -> Dict[str, Any]:
    """
    Update a placed pick with closing odds and compute closing line value (CLV).

    Args:
        conn: Active SQLite database connection.
        pick_id: Unique identifier of the pick.
        closing_odds: Final closing decimal odds.

    Returns:
        Dictionary with status, pick ID, CLV percentage, and closing line verdict.
    """
    pick = conn.execute("SELECT odds_at_pick FROM picks WHERE id = ?", (pick_id,)).fetchone()
    if not pick:
        return {"status": "error", "message": "Pick not found"}

    entry_odds = pick["odds_at_pick"]

    if closing_odds <= 1.0:
        return {"status": "error", "message": "Invalid closing odds"}

    entry_implied = 100.0 / entry_odds
    closing_implied = 100.0 / closing_odds
    clv_pct = closing_implied - entry_implied

    update_closing_odds(conn, pick_id, closing_odds, round(clv_pct, 2))

    return {
        "status": "ok",
        "pick_id": pick_id,
        "clv_pct": round(clv_pct, 2),
        "beat_closing_line": clv_pct > 0,
    }


def trigger_odds_fetch(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Manually trigger odds fetch across all configured leagues from The Odds API.

    Args:
        conn: Active SQLite database connection.

    Returns:
        Dictionary detailing fetch status, leagues fetched count, and total odds rows in DB.
    """
    if not get_odds_key():
        return {"error": "ODDS_API_KEY not set in .env", "status": "failed"}

    provider = TheOddsAPIProvider(conn)
    fetched = 0
    for sport_key in LEAGUE_TO_SPORT.keys():
        events = provider.fetch_events(sport_key)
        if events:
            fetched += 1

    count = conn.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone()[0]
    return {
        "status": "ok",
        "leagues_fetched": fetched,
        "total_odds_in_db": count,
    }

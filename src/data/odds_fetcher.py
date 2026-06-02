"""
The Odds API integration — fetches real bookmaker odds.

Free tier: 500 requests/month
Docs: https://the-odds-api.com/liveapi/guides/v4/

Maps our SofaScore league IDs to The Odds API sport keys.
"""

import json
import os
import logging
import urllib.request
import sqlite3
from datetime import datetime
from typing import Optional
from src.db.odds_repo import insert_odds

logger = logging.getLogger("football_predictor")

# The Odds API sport keys for football leagues
SPORT_KEYS = {
    "soccer_epl":              17,   # Premier League
    "soccer_spain_la_liga":    8,    # LaLiga
    "soccer_italy_serie_a":   23,   # Serie A
    "soccer_germany_bundesliga": 35, # Bundesliga
    "soccer_france_ligue_one": 34,   # Ligue 1
    "soccer_efl_champ":       18,   # Championship
    "soccer_netherlands_eredivisie": 37,  # Eredivisie
    "soccer_portugal_primeira_liga": 238, # Primeira Liga
    "soccer_belgium_first_div": 38,  # Belgian Pro League
    "soccer_turkey_super_league": 52, # Süper Lig
    "soccer_uefa_champs_league": 7,  # Champions League
    "soccer_uefa_europa_league": 679, # Europa League
    "soccer_uefa_europa_conference_league": 17015,  # Conference League
}

# Reverse: league_id → sport_key
LEAGUE_TO_SPORT = {v: k for k, v in SPORT_KEYS.items()}

API_BASE = "https://api.the-odds-api.com/v4"


def get_api_key() -> str:
    """Get the Odds API key from environment."""
    return os.getenv("ODDS_API_KEY", "")


def fetch_odds_for_sport(
    sport_key: str,
    regions: str = "eu",
    markets: str = "h2h,totals",
    odds_format: str = "decimal",
) -> list[dict]:
    """
    Fetch odds from The Odds API for a given sport.

    Args:
        sport_key: e.g. "soccer_epl"
        regions: "eu", "uk", "us"
        markets: "h2h" (1X2), "totals" (O/U), "spreads" (handicap)
        odds_format: "decimal" or "american"

    Returns list of event dicts with bookmaker odds.
    """
    api_key = get_api_key()
    if not api_key:
        logger.warning("ODDS_API_KEY not set — cannot fetch real odds")
        return []

    url = (
        f"{API_BASE}/sports/{sport_key}/odds"
        f"?apiKey={api_key}"
        f"&regions={regions}"
        f"&markets={markets}"
        f"&oddsFormat={odds_format}"
    )

    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            remaining = resp.headers.get("x-requests-remaining", "?")
            logger.info(
                f"Odds API: {len(data)} events for {sport_key} "
                f"(requests remaining: {remaining})"
            )
            return data
    except Exception as e:
        logger.error(f"Odds API error for {sport_key}: {e}")
        return []


def fetch_and_store_odds(
    conn: sqlite3.Connection,
    league_ids: Optional[list[int]] = None,
) -> dict:
    """
    Fetch odds for all tracked leagues and store in DB.
    Matches events to our matches table by team name fuzzy matching.

    Returns {league: events_matched}
    """
    if league_ids is None:
        league_ids = list(LEAGUE_TO_SPORT.keys())

    results = {}
    total_stored = 0

    for league_id in league_ids:
        sport_key = LEAGUE_TO_SPORT.get(league_id)
        if not sport_key:
            continue

        events = fetch_odds_for_sport(sport_key)
        if not events:
            continue

        matched = 0
        for event in events:
            home_team = event.get("home_team", "")
            away_team = event.get("away_team", "")

            # Find matching match in our DB
            match_id = _find_match(conn, home_team, away_team, league_id)
            if not match_id:
                continue

            # Extract and store odds from each bookmaker
            for bookmaker in event.get("bookmakers", []):
                bk_name = bookmaker.get("key", "unknown")
                for market in bookmaker.get("markets", []):
                    market_key = _map_market(market.get("key", ""))
                    if not market_key:
                        continue

                    for outcome in market.get("outcomes", []):
                        selection = _map_selection(
                            market_key, outcome.get("name", ""), home_team
                        )
                        if not selection:
                            continue

                        odds_val = outcome.get("price", 0)
                        if odds_val <= 1.0:
                            continue

                        insert_odds(conn, {
                            "match_id": match_id,
                            "market": market_key,
                            "selection": selection,
                            "odds": round(odds_val, 3),
                            "bookmaker": bk_name,
                            "is_opening": False,
                        })
                        total_stored += 1

            matched += 1

        results[sport_key] = matched
        logger.info(f"Odds: {sport_key} → {matched} matches linked, odds stored")

    logger.info(f"Total odds snapshots stored: {total_stored}")
    return results


def _find_match(
    conn: sqlite3.Connection, home_team: str, away_team: str, league_id: int
) -> Optional[str]:
    """Find a match in our DB by fuzzy team name matching."""
    # Try exact-ish match first
    home_lower = home_team.lower()
    away_lower = away_team.lower()

    rows = conn.execute(
        """SELECT id, home_team, away_team FROM matches
           WHERE league_id = ? AND status = 'NS'""",
        (league_id,),
    ).fetchall()

    for row in rows:
        db_home = row["home_team"].lower()
        db_away = row["away_team"].lower()

        # Check if names overlap significantly
        if (_fuzzy_match(home_lower, db_home) and
                _fuzzy_match(away_lower, db_away)):
            return row["id"]

    return None


def _fuzzy_match(name1: str, name2: str) -> bool:
    """Simple fuzzy match: one name contains the other, or significant word overlap."""
    if name1 in name2 or name2 in name1:
        return True

    words1 = set(name1.split())
    words2 = set(name2.split())
    # Remove common words
    stop = {"fc", "sc", "cf", "ac", "afc", "as", "ss", "us", "1.", "de", "cd"}
    words1 -= stop
    words2 -= stop

    if not words1 or not words2:
        return name1[:4] == name2[:4]

    overlap = words1 & words2
    return len(overlap) >= 1


def _map_market(api_market: str) -> str:
    """Map The Odds API market key to our internal market names."""
    return {
        "h2h": "1X2",
        "totals": "O/U 2.5",
        "spreads": "AH",
    }.get(api_market, "")


def _map_selection(
    market: str, outcome_name: str, home_team: str
) -> str:
    """Map The Odds API outcome name to our selection keys."""
    name = outcome_name.lower()

    if market == "1X2":
        if name == home_team.lower() or "home" in name:
            return "home"
        elif name == "draw":
            return "draw"
        else:
            return "away"

    elif market == "O/U 2.5":
        if "over" in name:
            return "over"
        elif "under" in name:
            return "under"

    elif market == "AH":
        return name

    return ""

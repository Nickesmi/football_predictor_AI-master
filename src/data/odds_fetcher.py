import json
import os
import logging
import urllib.request
import sqlite3
from datetime import datetime, timezone
from typing import Optional, List, Dict
from src.db.odds_repo import insert_odds, insert_odds_batch
from src.data.odds_provider import OddsProvider

logger = logging.getLogger("football_predictor")

SPORT_KEYS = {
    "soccer_epl":              17,
    "soccer_spain_la_liga":    8,
    "soccer_italy_serie_a":   23,
    "soccer_germany_bundesliga": 35,
    "soccer_france_ligue_one": 34,
    "soccer_efl_champ":       18,
    "soccer_netherlands_eredivisie": 37,
    "soccer_portugal_primeira_liga": 238,
    "soccer_belgium_first_div": 38,
    "soccer_turkey_super_league": 52,
    "soccer_uefa_champs_league": 7,
    "soccer_uefa_europa_league": 679,
    "soccer_uefa_europa_conference_league": 17015,
}

LEAGUE_TO_SPORT = {v: k for k, v in SPORT_KEYS.items()}
API_BASE = "https://api.the-odds-api.com/v4"
REQUESTED_MARKETS = "h2h,totals,spreads,btts,double_chance,totals_h1,draw_no_bet"

def get_api_key() -> str:
    return os.getenv("ODDS_API_KEY", "")

def _fuzzy_match(s1: str, s2: str) -> bool:
    """Helper for basic team matching."""
    if not s1 or not s2: return False
    s1, s2 = s1.lower(), s2.lower()
    return s1 in s2 or s2 in s1 or s1.replace(" ", "") == s2.replace(" ", "")

class TheOddsAPIProvider(OddsProvider):
    def __init__(self, db_conn: sqlite3.Connection):
        self.db_conn = db_conn
        self.last_update = None

    def fetch_events(self, sport_key: str) -> List[Dict]:
        return self._fetch_from_api(sport_key)

    def fetch_odds(self, event_id: str, markets: str = None) -> List[Dict]:
        """TheOddsAPI fetches odds per sport efficiently in free tier."""
        pass 

    def normalize_market(self, raw_market: str) -> Optional[str]:
        from src.engine.execution_engine import normalize_bookmaker_market
        return normalize_bookmaker_market(raw_market, "theoddsapi")

    def get_last_update(self) -> Optional[datetime]:
        return self.last_update

    def _fetch_from_api(self, sport_key: str, regions: str = "eu", markets: str = None, odds_format: str = "decimal") -> List[Dict]:
        if markets is None:
            markets = REQUESTED_MARKETS

        api_key = get_api_key()
        if not api_key:
            logger.warning("ODDS_API_KEY not set — cannot fetch real odds")
            return []

        url = f"{API_BASE}/sports/{sport_key}/odds?apiKey={api_key}&regions={regions}&markets={markets}&oddsFormat={odds_format}"

        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                self.last_update = datetime.now(timezone.utc)
                remaining = resp.headers.get("x-requests-remaining", "?")
                logger.info(f"Odds API: {len(data)} events for {sport_key} (requests remaining: {remaining})")
                
                self._store_snapshots(data)
                return data
        except Exception as e:
            logger.error(f"Odds API error for {sport_key}: {e}")
            return []

    def _store_snapshots(self, events: List[Dict]):
        """Parse raw events and store in odds_snapshots table."""
        snapshots = []
        for event in events:
            match_id = event.get("id")
            for bookmaker in event.get("bookmakers", []):
                bm_key = bookmaker.get("key")
                timestamp_str = bookmaker.get("last_update")
                try:
                    ts = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).isoformat()
                except:
                    ts = datetime.now(timezone.utc).isoformat()
                    
                for market in bookmaker.get("markets", []):
                    market_key = market.get("key")
                    for outcome in market.get("outcomes", []):
                        selection = outcome.get("name")
                        odds = outcome.get("price")
                        if odds and odds > 1.0:
                            implied_prob = 1.0 / odds
                            
                            snapshot = {
                                "match_id": match_id,
                                "market": market_key,
                                "selection": selection,
                                "odds": odds,
                                "bookmaker": bm_key,
                                "is_opening": 0,
                                "implied_probability": implied_prob,
                                "timestamp": ts
                            }
                            snapshots.append(snapshot)
        if snapshots:
            insert_odds_batch(self.db_conn, snapshots)


    def get_normalized_odds_for_match(self, sport_key: str, home_team: str, away_team: str, preferred_bookmakers: list[str] = None) -> list[dict]:
        if preferred_bookmakers is None:
            preferred_bookmakers = ["pinnacle", "bet365", "unibet", "betfair"]

        events = self._fetch_from_api(sport_key)
        if not events:
            return []

        target_event = None
        for event in events:
            if (_fuzzy_match(home_team.lower(), event.get("home_team", "").lower()) and
                _fuzzy_match(away_team.lower(), event.get("away_team", "").lower())):
                target_event = event
                break

        if not target_event:
            logger.info(f"Match {home_team} vs {away_team} not found in Odds API")
            return []

        best_odds: dict[str, dict] = {}
        bookmakers = sorted(
            target_event.get("bookmakers", []),
            key=lambda b: preferred_bookmakers.index(b["key"]) if b["key"] in preferred_bookmakers else 999
        )

        api_home = target_event.get("home_team", "")
        api_away = target_event.get("away_team", "")

        for bm in bookmakers:
            bm_key = bm.get("key")
            for market in bm.get("markets", []):
                mk = market.get("key")
                for outcome in market.get("outcomes", []):
                    name = outcome.get("name")
                    price = outcome.get("price")

                    norm_market = None
                    if mk == "h2h":
                        if name == api_home: norm_market = "Home Win"
                        elif name == api_away: norm_market = "Away Win"
                        elif name == "Draw": norm_market = "Draw"
                    elif mk == "btts":
                        if name == "Yes": norm_market = "BTTS - Yes"
                        elif name == "No": norm_market = "BTTS - No"
                    elif mk == "totals":
                        point = outcome.get("point")
                        if point == 2.5:
                            norm_market = f"{name} 2.5 Goals"

                    if norm_market:
                        if norm_market not in best_odds or bm_key in preferred_bookmakers:
                            if norm_market not in best_odds or price > best_odds[norm_market]["odds"]:
                                best_odds[norm_market] = {"odds": price, "bookmaker": bm_key}

        return [{"market": k, "odds": v["odds"], "bookmaker": v["bookmaker"]} for k, v in best_odds.items()]


def fetch_normalized_odds_for_match(
    sport_key: str, home_team: str, away_team: str, preferred_bookmakers: list[str] = None
) -> list[dict]:
    """
    Helper function to instantiate TheOddsAPIProvider and fetch normalized odds for a match.
    """
    from src.db.database import get_db

    conn = get_db()
    provider = TheOddsAPIProvider(conn)
    return provider.get_normalized_odds_for_match(
        sport_key, home_team, away_team, preferred_bookmakers
    )

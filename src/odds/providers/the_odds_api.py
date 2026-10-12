"""The Odds API (v4) — primary real-odds provider.

One GET /v4/sports/{sport}/odds call returns every upcoming event of a
competition with all bookmakers in the requested regions. Its quota cost is
(number of markets) x (number of regions); the response headers report the
actual cost (x-requests-last) and the remaining quota.

Only the featured markets h2h, totals and spreads are requested from the
bulk endpoint (ODDS_API_MARKETS). btts and draw_no_bet are parsed when present
but, per the provider documentation, are only served by the per-event
endpoint, so they are not requested by default. Outcome names are matched
exactly: an outcome that is neither team nor 'Draw' is skipped, never guessed.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from urllib.parse import urlencode

from src.odds import quota
from src.odds.identity import ProviderEvent
from src.odds.model import InvalidQuote, Quote, decimal_odds, format_line, iso_utc

PROVIDER = "the_odds_api"
API_BASE = "https://api.the-odds-api.com/v4"

# Tracked league id (SofaScore unique tournament id) -> The Odds API sport key.
SPORT_KEYS = {
    17: "soccer_epl",
    8: "soccer_spain_la_liga",
    23: "soccer_italy_serie_a",
    35: "soccer_germany_bundesliga",
    34: "soccer_france_ligue_one",
    18: "soccer_efl_champ",
    37: "soccer_netherlands_eredivisie",
    238: "soccer_portugal_primeira_liga",
    38: "soccer_belgium_first_div",
    52: "soccer_turkey_super_league",
    36: "soccer_spl",
    7: "soccer_uefa_champs_league",
    679: "soccer_uefa_europa_league",
    17015: "soccer_uefa_europa_conference_league",
}

# The same competitions under API-Football league ids (used by fixtures stored
# with provider 'api_football' or 'matches_db'). The two id spaces overlap, so
# the provider decides which table applies.
API_FOOTBALL_SPORT_KEYS = {
    39: "soccer_epl",
    140: "soccer_spain_la_liga",
    135: "soccer_italy_serie_a",
    78: "soccer_germany_bundesliga",
    61: "soccer_france_ligue_one",
    40: "soccer_efl_champ",
    88: "soccer_netherlands_eredivisie",
    94: "soccer_portugal_primeira_liga",
    144: "soccer_belgium_first_div",
    203: "soccer_turkey_super_league",
    179: "soccer_spl",
    2: "soccer_uefa_champs_league",
    3: "soccer_uefa_europa_league",
    848: "soccer_uefa_europa_conference_league",
}


def sport_for(fixture: dict) -> str | None:
    table = API_FOOTBALL_SPORT_KEYS if fixture.get("provider") in ("api_football", "matches_db") else SPORT_KEYS
    return table.get(fixture.get("league_id"))


def requested_markets() -> list[str]:
    return [m for m in os.getenv("ODDS_API_MARKETS", "h2h,totals,spreads").split(",") if m.strip()]


def requested_regions() -> list[str]:
    return [r for r in os.getenv("ODDS_API_REGIONS", "eu").split(",") if r.strip()]


def request_cost(markets: list[str] | None = None, regions: list[str] | None = None) -> int:
    return len(markets or requested_markets()) * len(regions or requested_regions())


def _selection(market_key: str, name: str, home: str, away: str) -> tuple[str, str] | None:
    if market_key in ("h2h", "draw_no_bet", "spreads"):
        side = "home" if name == home else "away" if name == away else None
        if market_key == "h2h" and name == "Draw":
            side = "draw"
        if side is None:
            return None
        return {"h2h": "1X2", "draw_no_bet": "DNB", "spreads": "AH"}[market_key], side
    if market_key == "totals" and name in ("Over", "Under"):
        return "TOTALS", name.lower()
    if market_key == "btts" and name in ("Yes", "No"):
        return "BTTS", name.lower()
    return None


def parse_events(payload: list) -> tuple[list[tuple[ProviderEvent, list[Quote]]], list[str]]:
    """Parse an /odds response into (event, quotes) pairs plus a list of skipped-value notes."""
    parsed, skipped = [], []
    for raw in payload or []:
        try:
            event = ProviderEvent(PROVIDER, str(raw["id"]), raw["home_team"], raw["away_team"],
                                  iso_utc(raw["commence_time"]))
        except (KeyError, TypeError, InvalidQuote) as exc:
            skipped.append(f"event without identity: {exc}")
            continue
        quotes = []
        for book in raw.get("bookmakers") or []:
            bookmaker = str(book.get("key") or "").strip().lower()
            for market in book.get("markets") or []:
                key = market.get("key", "")
                observed = market.get("last_update") or book.get("last_update")
                for outcome in market.get("outcomes") or []:
                    name = outcome.get("name", "")
                    mapped = _selection(key, name, event.home_team, event.away_team)
                    if mapped is None:
                        skipped.append(f"{event.event_id}/{bookmaker}: unsupported {key} outcome {name!r}")
                        continue
                    canonical, selection = mapped
                    try:
                        line = format_line(outcome.get("point")) if canonical in ("TOTALS", "AH") else ""
                        quotes.append(Quote(
                            provider=PROVIDER, provider_event_id=event.event_id, bookmaker=bookmaker,
                            market=canonical, selection=selection, line=line,
                            decimal_odds=decimal_odds(outcome.get("price")),
                            observed_at=iso_utc(observed), commence_time=event.commence_time,
                            raw_market=key, raw_selection=name,
                        ))
                    except InvalidQuote as exc:
                        skipped.append(f"{event.event_id}/{bookmaker}/{key}/{name}: {exc}")
        parsed.append((event, quotes))
    return parsed, skipped


def fetch_competition(conn: sqlite3.Connection, sport_key: str, api_key: str,
                      transport: quota.Transport | None = None, now: datetime | None = None) -> list:
    """One budgeted request for every upcoming event of a competition."""
    if not api_key:
        raise quota.ProviderUnavailable("ODDS_API_KEY is not configured")
    markets, regions = requested_markets(), requested_regions()
    query = {"regions": ",".join(regions), "markets": ",".join(markets),
             "oddsFormat": "decimal", "dateFormat": "iso"}
    endpoint = f"/sports/{sport_key}/odds?{urlencode(query)}"
    return quota.fetch_json(
        conn, PROVIDER, endpoint, f"{API_BASE}{endpoint}&{urlencode({'apiKey': api_key})}", {},
        cost=request_cost(markets, regions), remaining_header="x-requests-remaining",
        used_header="x-requests-used", cost_header="x-requests-last", transport=transport, now=now,
    )

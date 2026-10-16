"""API-Football (v3) /odds — alternative real-odds provider.

GET /odds?fixture={id} returns pre-match prices per bookmaker for one
fixture (one request each; the daily limit is shared with fixture feeds).
The odds response carries no team names. A quote is attached only through
a fixture identity: either the fixture is stored with the same API-Football
id (matches.provider = 'api_football'), or GET /fixtures?date= (one request
per date, team names and kickoff included) mapped the API-Football id onto a
stored fixture with the strict rules in src/odds/identity.py. Prices are
decimal strings. Bet names are matched exactly; unknown bets and values are
skipped. /odds serves pre-match prices only (in-play is /odds/live, never used).
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime

from src.odds import quota
from src.odds.identity import ProviderEvent
from src.odds.model import InvalidQuote, Quote, decimal_odds, format_line, iso_utc

PROVIDER = "api_football"
REQUEST_COST = 1

_SIMPLE = {
    "Match Winner": ("1X2", {"Home": "home", "Draw": "draw", "Away": "away"}),
    "Both Teams Score": ("BTTS", {"Yes": "yes", "No": "no"}),
    "Double Chance": ("DC", {"Home/Draw": "home_draw", "Home/Away": "home_away", "Draw/Away": "draw_away"}),
}
_LINE = {
    "Goals Over/Under": ("TOTALS", re.compile(r"^(Over|Under) (\d+(?:\.\d+)?)$")),
    "Asian Handicap": ("AH", re.compile(r"^(Home|Away) ([+-]?\d+(?:\.\d+)?)$")),
}


def _selection(bet_name: str, value: str) -> tuple[str, str, str] | None:
    if bet_name in _SIMPLE:
        market, values = _SIMPLE[bet_name]
        return (market, values[value], "") if value in values else None
    if bet_name in _LINE:
        market, pattern = _LINE[bet_name]
        found = pattern.match(value.strip())
        if not found:
            return None
        return market, found.group(1).lower(), format_line(found.group(2))
    return None


def parse_fixture_odds(payload: dict, home_team: str, away_team: str) -> tuple[list[tuple[ProviderEvent, list[Quote]]], list[str]]:
    """Parse an /odds response; team names come from the stored fixture with this provider id."""
    parsed, skipped = [], []
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if errors:
        return [], [f"provider error: {errors}"]
    for item in (payload or {}).get("response") or []:
        fixture = item.get("fixture") or {}
        try:
            event = ProviderEvent(PROVIDER, str(fixture["id"]), home_team, away_team, iso_utc(fixture["date"]))
            observed = iso_utc(item["update"])
        except (KeyError, TypeError, InvalidQuote) as exc:
            skipped.append(f"fixture without identity or update time: {exc}")
            continue
        quotes = []
        for book in item.get("bookmakers") or []:
            bookmaker = str(book.get("name") or "").strip().lower()
            for bet in book.get("bets") or []:
                for value in bet.get("values") or []:
                    raw_value = str(value.get("value", ""))
                    try:
                        mapped = _selection(bet.get("name", ""), raw_value)
                        if mapped is None:
                            skipped.append(f"{event.event_id}/{bookmaker}: unsupported {bet.get('name')!r} {raw_value!r}")
                            continue
                        market, selection, line = mapped
                        quotes.append(Quote(
                            provider=PROVIDER, provider_event_id=event.event_id, bookmaker=bookmaker,
                            market=market, selection=selection, line=line,
                            decimal_odds=decimal_odds(value.get("odd")), observed_at=observed,
                            commence_time=event.commence_time, raw_market=bet.get("name", ""),
                            raw_selection=raw_value,
                        ))
                    except InvalidQuote as exc:
                        skipped.append(f"{event.event_id}/{bookmaker}/{bet.get('name')}/{raw_value}: {exc}")
        parsed.append((event, quotes))
    return parsed, skipped


def fetch_fixture(conn: sqlite3.Connection, fixture_id: str, api_key: str, host: str,
                  transport: quota.Transport | None = None, now: datetime | None = None) -> dict:
    if not api_key:
        raise quota.ProviderUnavailable("API-Football key is not configured")
    endpoint = f"/odds?fixture={int(fixture_id)}"
    return quota.fetch_json(
        conn, PROVIDER, endpoint, f"https://{host}{endpoint}", {"x-apisports-key": api_key},
        cost=REQUEST_COST, remaining_header="x-ratelimit-requests-remaining",
        transport=transport, now=now,
    )


def parse_fixture_list(payload: dict) -> tuple[list[ProviderEvent], list[str]]:
    """Parse GET /fixtures?date= into provider events (id, team names, UTC kickoff)."""
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if errors:
        return [], [f"provider error: {errors}"]
    events, skipped = [], []
    for item in (payload or {}).get("response") or []:
        try:
            fixture, teams = item["fixture"], item["teams"]
            home, away = str(teams["home"]["name"]).strip(), str(teams["away"]["name"]).strip()
            if not home or not away:
                raise KeyError("team name")
            events.append(ProviderEvent(PROVIDER, str(int(fixture["id"])), home, away, iso_utc(fixture["date"])))
        except (KeyError, TypeError, ValueError, InvalidQuote) as exc:
            skipped.append(f"fixture without identity: {exc}")
    return events, skipped


def fetch_fixtures_by_date(conn: sqlite3.Connection, date: str, api_key: str, host: str,
                           transport: quota.Transport | None = None, now: datetime | None = None) -> dict:
    if not api_key:
        raise quota.ProviderUnavailable("API-Football key is not configured")
    datetime.strptime(date, "%Y-%m-%d")
    endpoint = f"/fixtures?date={date}"
    return quota.fetch_json(
        conn, PROVIDER, endpoint, f"https://{host}{endpoint}", {"x-apisports-key": api_key},
        cost=REQUEST_COST, remaining_header="x-ratelimit-requests-remaining",
        transport=transport, now=now,
    )

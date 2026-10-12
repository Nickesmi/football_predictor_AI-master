"""Offline odds-provider fakes.

`odds_api_event` builds a response in The Odds API v4 shape and
`ingest_odds_api` pushes it through the real budget guard, parser, fixture
mapping and storage with a fake transport, so tests exercise the production
ingestion path without any network. Bookmaker names are obviously fake.
Prices from here only ever reach the per-test temporary database.

`synthetic_quote` builds an explicitly labelled SYNTHETIC_TEST quote for
tests that need a price without pretending a provider sent it.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from src.odds import collector
from src.odds.model import OddsType, Quote, iso_utc

FAKE_BOOKMAKER = "fakebook"


def odds_api_event(event_id, home, away, commence, h2h=None, totals=None, observed=None, bookmaker=FAKE_BOOKMAKER):
    """h2h=(home, draw, away) prices; totals={line: (over, under)}; observed=ISO time of the prices."""
    markets = []
    if h2h:
        markets.append({"key": "h2h", "last_update": observed, "outcomes": [
            {"name": home, "price": h2h[0]}, {"name": "Draw", "price": h2h[1]}, {"name": away, "price": h2h[2]},
        ]})
    for line, (over, under) in (totals or {}).items():
        markets.append({"key": "totals", "last_update": observed, "outcomes": [
            {"name": "Over", "price": over, "point": line}, {"name": "Under", "price": under, "point": line},
        ]})
    return {"id": event_id, "sport_key": "soccer_epl", "commence_time": commence,
            "home_team": home, "away_team": away,
            "bookmakers": [{"key": bookmaker, "title": bookmaker, "last_update": observed, "markets": markets}]}


def transport_returning(payload, status=200, headers=None):
    calls = []

    def transport(url, request_headers, timeout):
        calls.append(url)
        return status, {"x-requests-remaining": "480", "x-requests-used": "20", "x-requests-last": "1",
                        **(headers or {})}, json.dumps(payload).encode()

    transport.calls = calls
    return transport


def ingest_odds_api(conn, monkeypatch, events, league_id=17, now=None):
    """Store fake provider events for fixtures in `league_id`; returns the collector report."""
    monkeypatch.setenv("ODDS_REQUEST_BUDGET", "10")
    monkeypatch.setenv("ODDS_API_MARKETS", "h2h")
    return collector.collect_the_odds_api(
        conn, [{"league_id": league_id}], api_key="test-key",
        transport=transport_returning(events), now=now or datetime.now(timezone.utc),
    )


def recent(minutes=5) -> str:
    return iso_utc(datetime.now(timezone.utc) - timedelta(minutes=minutes))


def synthetic_quote(market, selection, price, commence, line="", observed=None):
    return Quote(provider="synthetic_test", provider_event_id="synthetic", bookmaker="synthetic_test",
                 market=market, selection=selection, line=line, decimal_odds=price,
                 observed_at=observed or recent(), commence_time=commence, odds_type=OddsType.SYNTHETIC_TEST)

"""Collect real bookmaker odds for stored fixtures, within the approved request budget.

Each competition costs one The Odds API request regardless of how many of its
fixtures are tracked. Every outcome is reported: mapped and unmapped events,
new and duplicate quotes, skipped provider values, refusals and failures.
A refused or failed request produces no quotes; nothing is ever filled in.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

from src.odds import identity, quota, store
from src.odds.model import iso_utc
from src.odds.providers import api_football, the_odds_api


def empty_report(provider: str) -> dict:
    return {"provider": provider, "requests_ok": 0, "requests_refused": 0, "requests_failed": 0,
            "events": 0, "mapped": 0, "unmapped": [], "quotes_new": 0, "quotes_duplicate": 0,
            "values_skipped": 0, "notes": []}


def store_parsed(conn: sqlite3.Connection, parsed, skipped, report: dict, fetched_at: str) -> None:
    report["values_skipped"] += len(skipped)
    for event, quotes in parsed:
        report["events"] += 1
        mapping = identity.resolve_fixture(conn, event)
        if mapping.fixture_id is None:
            report["unmapped"].append(f"{event.home_team} v {event.away_team} {event.commence_time}: {mapping.reason}")
            continue
        report["mapped"] += 1
        identity.remember(conn, event, mapping, fetched_at)
        new = store.store_quotes(conn, mapping.fixture_id, quotes, fetched_at)
        report["quotes_new"] += new
        report["quotes_duplicate"] += len(quotes) - new
    conn.commit()


def collect_the_odds_api(conn: sqlite3.Connection, fixtures: list[dict], api_key: str,
                         transport: quota.Transport | None = None, now: datetime | None = None) -> dict:
    report = empty_report(the_odds_api.PROVIDER)
    sports = defaultdict(int)
    for fixture in fixtures:
        sport = the_odds_api.SPORT_KEYS.get(fixture.get("league_id"))
        if sport:
            sports[sport] += 1
        else:
            report["notes"].append(f"no sport key for league {fixture.get('league_name')!r}")
    for sport in sorted(sports):
        moment = now or datetime.now(timezone.utc)
        try:
            payload = the_odds_api.fetch_competition(conn, sport, api_key, transport, moment)
        except quota.BudgetExceeded as exc:
            report["requests_refused"] += 1
            report["notes"].append(f"{sport}: refused, {exc}")
            break
        except quota.ProviderUnavailable as exc:
            report["requests_failed"] += 1
            report["notes"].append(f"{sport}: {exc}")
            continue
        report["requests_ok"] += 1
        parsed, skipped = the_odds_api.parse_events(payload)
        store_parsed(conn, parsed, skipped, report, iso_utc(moment))
    return report


def collect_api_football(conn: sqlite3.Connection, fixtures: list[dict], api_key: str, host: str,
                         transport: quota.Transport | None = None, now: datetime | None = None) -> dict:
    report = empty_report(api_football.PROVIDER)
    for fixture in fixtures:
        if fixture.get("provider") != "api_football" or not str(fixture.get("id", "")).isdigit():
            continue
        moment = now or datetime.now(timezone.utc)
        try:
            payload = api_football.fetch_fixture(conn, fixture["id"], api_key, host, transport, moment)
        except quota.BudgetExceeded as exc:
            report["requests_refused"] += 1
            report["notes"].append(f"{fixture['id']}: refused, {exc}")
            break
        except quota.ProviderUnavailable as exc:
            report["requests_failed"] += 1
            report["notes"].append(f"{fixture['id']}: {exc}")
            continue
        report["requests_ok"] += 1
        parsed, skipped = api_football.parse_fixture_odds(payload, fixture["home_team"], fixture["away_team"])
        # The provider id is the stored fixture id: record that identity directly.
        for event, _ in parsed:
            if event.event_id == str(fixture["id"]):
                identity.remember(conn, event, identity.Mapping(str(fixture["id"]), "provider_id"), iso_utc(moment))
        store_parsed(conn, parsed, skipped, report, iso_utc(moment))
    return report

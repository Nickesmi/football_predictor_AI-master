"""Collect real bookmaker odds for stored fixtures, within the approved request budget.

Each competition costs one The Odds API request regardless of how many of its
fixtures are tracked. Every outcome is reported: mapped and unmapped events,
new and duplicate quotes, skipped provider values, refusals and failures.
A refused or failed request produces no quotes; nothing is ever filled in.
"""
from __future__ import annotations

import os
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from src.odds import identity, quota, store
from src.odds.model import iso_utc
from src.odds.providers import api_football, the_odds_api


def empty_report(provider: str) -> dict:
    return {"provider": provider, "requests_ok": 0, "requests_refused": 0, "requests_failed": 0,
            "events": 0, "mapped": 0, "unmapped": [], "rejected_events": [], "quotes_new": 0,
            "quotes_duplicate": 0, "quotes_after_kickoff": 0, "values_skipped": 0, "notes": []}


def store_parsed(conn: sqlite3.Connection, parsed, skipped, report: dict, fetched_at: str) -> None:
    report["values_skipped"] += len(skipped)
    for event, quotes in parsed:
        report["events"] += 1
        mapping = identity.resolve_fixture(conn, event)
        if not mapping.mapped:
            report["unmapped"].append(f"{event.home_team} v {event.away_team} {event.commence_time}: {mapping.reason}")
            continue
        report["mapped"] += 1
        identity.remember(conn, event, mapping, fetched_at)
        # A price observed at or after kickoff is in-play (or late), never a pre-match quote.
        pre_match = [q for q in quotes if iso_utc(q.observed_at) < iso_utc(q.commence_time)]
        report["quotes_after_kickoff"] += len(quotes) - len(pre_match)
        quotes = pre_match
        for fixture_id in mapping.fixture_ids:
            new = store.store_quotes(conn, fixture_id, quotes, fetched_at)
            report["quotes_new"] += new
            report["quotes_duplicate"] += len(quotes) - new
    conn.commit()


def collect_the_odds_api(conn: sqlite3.Connection, fixtures: list[dict], api_key: str,
                         transport: quota.Transport | None = None, now: datetime | None = None) -> dict:
    report = empty_report(the_odds_api.PROVIDER)
    sports = defaultdict(int)
    for fixture in fixtures:
        sport = the_odds_api.sport_for(fixture)
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


def api_football_interval() -> float:
    """Seconds between API-Football requests (free plan: about 10 per minute; HTTP 429 above that)."""
    try:
        return max(0.0, float(os.getenv("API_FOOTBALL_MIN_INTERVAL_SECONDS", "7")))
    except ValueError:
        return 7.0


def _throttle(index: int, sleep: Callable[[float], None]) -> None:
    if index > 0:
        sleep(api_football_interval())


def map_api_football_fixtures(conn: sqlite3.Connection, dates: list[str], api_key: str, host: str,
                              transport: quota.Transport | None = None, now: datetime | None = None,
                              sleep: Callable[[float], None] = time.sleep) -> dict:
    """Map API-Football fixture ids onto stored fixtures via GET /fixtures?date= (one request per date).

    Uses identity.resolve_fixture (normalised names + kickoff, at most one
    fixture per stored provider). Ambiguous or unmatched events stay unmapped.
    """
    report = {"provider": api_football.PROVIDER, "requests_ok": 0, "requests_refused": 0,
              "requests_failed": 0, "events": 0, "mapped": 0, "already_mapped": 0,
              "unmapped": 0, "ambiguous": [], "skipped": 0, "notes": []}
    for index, date in enumerate(dates):
        _throttle(index, sleep)
        moment = now or datetime.now(timezone.utc)
        try:
            payload = api_football.fetch_fixtures_by_date(conn, date, api_key, host, transport, moment)
        except quota.BudgetExceeded as exc:
            report["requests_refused"] += 1
            report["notes"].append(f"{date}: refused, {exc}")
            break
        except quota.ProviderUnavailable as exc:
            report["requests_failed"] += 1
            report["notes"].append(f"{date}: {exc}")
            continue
        report["requests_ok"] += 1
        events, skipped = api_football.parse_fixture_list(payload)
        report["skipped"] += len(skipped)
        for event in events:
            report["events"] += 1
            mapping = identity.resolve_fixture(conn, event)
            if mapping.method == "stored":
                report["already_mapped"] += 1
            elif mapping.mapped:
                identity.remember(conn, event, mapping, iso_utc(moment))
                report["mapped"] += 1
            else:
                report["unmapped"] += 1
                if mapping.reason.startswith("ambiguous"):
                    report["ambiguous"].append(f"{event.home_team} v {event.away_team} {event.commence_time}")
        conn.commit()
    return report


def api_football_targets(conn: sqlite3.Connection, fixtures: list[dict]) -> dict[str, dict]:
    """API-Football event id -> {"fixture_ids", "home", "away", "commence"} for the given fixtures.

    A fixture qualifies when it is stored with its API-Football id, or when a
    fixture_identity row maps an API-Football event onto it.
    """
    targets: dict[str, dict] = {}
    for fixture in fixtures:
        fid = str(fixture.get("id", ""))
        if fixture.get("provider") == "api_football" and fid.isdigit():
            t = targets.setdefault(fid, {"fixture_ids": set(), "home": fixture["home_team"],
                                         "away": fixture["away_team"], "commence": None})
            t["fixture_ids"].add(fid)
        rows = conn.execute(
            "SELECT provider_event_id, provider_home, provider_away, commence_time FROM fixture_identity "
            "WHERE provider = ? AND fixture_id = ?", (api_football.PROVIDER, fid),
        ).fetchall()
        for event_id, home, away, commence in rows:
            t = targets.setdefault(str(event_id), {"fixture_ids": set(), "home": home or fixture["home_team"],
                                                   "away": away or fixture["away_team"], "commence": None})
            t["fixture_ids"].add(fid)
            t["commence"] = commence
    return targets


def collect_api_football(conn: sqlite3.Connection, fixtures: list[dict], api_key: str, host: str,
                         transport: quota.Transport | None = None, now: datetime | None = None,
                         sleep: Callable[[float], None] = time.sleep) -> dict:
    report = empty_report(api_football.PROVIDER)
    for index, (event_id, target) in enumerate(sorted(api_football_targets(conn, fixtures).items())):
        _throttle(index, sleep)
        moment = now or datetime.now(timezone.utc)
        try:
            payload = api_football.fetch_fixture(conn, event_id, api_key, host, transport, moment)
        except quota.BudgetExceeded as exc:
            report["requests_refused"] += 1
            report["notes"].append(f"{event_id}: refused, {exc}")
            break
        except quota.ProviderUnavailable as exc:
            report["requests_failed"] += 1
            report["notes"].append(f"{event_id}: {exc}")
            continue
        report["requests_ok"] += 1
        parsed, skipped = api_football.parse_fixture_odds(payload, target["home"], target["away"])
        accepted = []
        for event, quotes in parsed:
            # The response must describe the fixture we asked for; the team names
            # we attach are that fixture's, so any other id would be mislabelled.
            if event.event_id != event_id:
                report["rejected_events"].append(f"{event_id}: response carried fixture {event.event_id}")
                continue
            if target["commence"] and abs(
                datetime.fromisoformat(iso_utc(event.commence_time))
                - datetime.fromisoformat(iso_utc(target["commence"]))
            ) > identity.KICKOFF_TOLERANCE:
                report["rejected_events"].append(
                    f"{event_id}: kickoff moved {target['commence']} -> {event.commence_time}; remap required")
                continue
            mapping = identity.Mapping(tuple(sorted(target["fixture_ids"])), "provider_id")
            identity.remember(conn, event, mapping, iso_utc(moment))
            accepted.append((event, quotes))
        store_parsed(conn, accepted, skipped, report, iso_utc(moment))
    return report

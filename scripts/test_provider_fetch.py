#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests
from src.config import APIFOOTBALL_API_KEY, APIFOOTBALL_HOST
from src.data.api_client import APIFootballClient
from src.data.fixtures_fetcher import _fetch_sofascore, normalize_apifootball_fixture


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test provider fetch for historical fixtures.")
    parser.add_argument("--provider", choices=["sofascore", "apifootball"], required=True)
    parser.add_argument("--date", required=True, help="Date to fetch (YYYY-MM-DD)")
    parser.add_argument("--league", help="Optional league ID for API-Football.")
    parser.add_argument("--season", help="Optional season year for API-Football.")
    return parser.parse_args()


def mask_headers(headers: dict[str, str]) -> dict[str, str]:
    masked = {}
    for key, value in headers.items():
        if key.lower().startswith("x-apisports-key") or key.lower().startswith("x-rapidapi-key"):
            masked[key] = "***"
        else:
            masked[key] = value
    return masked


def test_apifootball(date_str: str, league: Optional[str], season: Optional[str]) -> None:
    client = APIFootballClient()
    endpoint = "fixtures"
    url = f"{client._base_url}/{endpoint}"
    params: dict[str, Any] = {"date": date_str}
    if league:
        params["league"] = league
    if season:
        params["season"] = season

    headers = client._build_headers()
    print(f"endpoint: {url}")
    print(f"params: {params}")
    print(f"headers: {mask_headers(headers)}")

    try:
        resp = client._session.get(url, headers=headers, params=params, timeout=30)
    except requests.RequestException as exc:
        print(f"request failed: {exc}")
        return

    print(f"status: {resp.status_code}")
    print(f"request_url: {resp.url}")

    body_text = resp.text
    if resp.headers.get("Content-Type", "").startswith("application/json"):
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            payload = None
    else:
        payload = None

    if payload is not None:
        print(f"errors: {payload.get('errors')}")
        print(f"results: {payload.get('results')}")
        fixtures = []
        for item in payload.get("response", []):
            normalized = normalize_apifootball_fixture(item)
            if normalized is not None:
                fixtures.append(normalized)
        print(f"fixture_count: {len(fixtures)}")
        print("first_3_fixtures:")
        print(json.dumps(fixtures[:3], indent=2, ensure_ascii=False))
    else:
        print("response_body:")
        print(body_text[:2000])


def test_sofascore(date_str: str) -> None:
    print(f"provider: sofascore")
    fixtures = _fetch_sofascore(date_str)
    print(f"fixture_count: {len(fixtures)}")
    print("first_3_fixtures:")
    print(json.dumps(fixtures[:3], indent=2, ensure_ascii=False))


def main() -> None:
    args = parse_args()
    if args.provider == "apifootball":
        test_apifootball(args.date, args.league, args.season)
    else:
        test_sofascore(args.date)


if __name__ == "__main__":
    main()

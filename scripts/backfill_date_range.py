#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import CACHE_DIR, logger
from src.data.fixtures_fetcher import _fetch_sofascore, _persist_fixtures_snapshot, fetch_apifootball
from src.db.database import get_db
from src.db.match_repo import upsert_match

FINISHED_STATUSES = {"FT", "AET", "PEN"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill fixture coverage into matches and match_history."
    )
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--provider",
        choices=["sofascore", "apifootball"],
        default="sofascore",
        help="Provider used to fetch historical fixtures.",
    )
    parser.add_argument(
        "--league",
        help="Optional league ID for provider filtering (API-Football only).",
    )
    parser.add_argument(
        "--season",
        help="Optional season year for API-Football (e.g. 2025).",
    )
    return parser.parse_args()


def format_time(iso: Optional[str]) -> str:
    if not iso:
        return "TBD"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%H:%M")
    except Exception:
        return "TBD"


def create_match_history_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS match_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT,
            match_date DATE,
            league TEXT,
            home_team TEXT,
            away_team TEXT,
            home_goals INTEGER,
            away_goals INTEGER,
            home_xg REAL,
            away_xg REAL,
            home_corners INTEGER,
            away_corners INTEGER,
            home_cards INTEGER,
            away_cards INTEGER,
            home_elo_before REAL,
            away_elo_before REAL,
            home_elo_after REAL,
            away_elo_after REAL,
            ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()


def has_history_row(
    conn: sqlite3.Connection,
    match_id: Optional[str],
    match_date: str,
    home_team: str,
    away_team: str,
) -> bool:
    if match_id:
        row = conn.execute(
            "SELECT 1 FROM match_history WHERE match_id = ? LIMIT 1",
            (match_id,),
        ).fetchone()
        if row:
            return True
    row = conn.execute(
        "SELECT 1 FROM match_history WHERE match_date = ? AND home_team = ? AND away_team = ? LIMIT 1",
        (match_date, home_team, away_team),
    ).fetchone()
    return bool(row)


def insert_history_row(conn: sqlite3.Connection, fixture: Dict[str, Any]) -> bool:
    if not fixture.get("id") and not fixture.get("home_team"):
        return False

    match_id = str(fixture.get("id", ""))
    home_name = str(fixture.get("home_team", {}).get("name", "") or "")
    away_name = str(fixture.get("away_team", {}).get("name", "") or "")
    match_date = fixture.get("date") or ""

    if has_history_row(conn, match_id, match_date, home_name, away_name):
        return False

    conn.execute(
        """INSERT INTO match_history (
            match_id, match_date, league, home_team, away_team,
            home_goals, away_goals, home_xg, away_xg,
            home_corners, away_corners, home_cards, away_cards,
            home_elo_before, away_elo_before, home_elo_after, away_elo_after
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            match_id,
            match_date,
            str(fixture.get("league", {}).get("name", "") or ""),
            home_name,
            away_name,
            fixture.get("home_goals"),
            fixture.get("away_goals"),
            fixture.get("home_xg"),
            fixture.get("away_xg"),
            fixture.get("home_corners"),
            fixture.get("away_corners"),
            fixture.get("home_cards"),
            fixture.get("away_cards"),
            fixture.get("home_elo_before"),
            fixture.get("away_elo_before"),
            fixture.get("home_elo_after"),
            fixture.get("away_elo_after"),
        ),
    )
    conn.commit()
    return True


def call_on_match_finished_if_available(conn: sqlite3.Connection, fixture: Dict[str, Any]) -> bool:
    try:
        from src.engine.live_updater import on_match_finished
    except Exception:
        logger.warning("on_match_finished unavailable; skipping team_state update.")
        return False

    if not fixture.get("id"):
        return False

    try:
        on_match_finished(
            conn=conn,
            match_id=str(fixture.get("id")),
            match_date=str(fixture.get("date")),
            league=str(fixture.get("league", {}).get("name", "") or ""),
            home_team=str(fixture.get("home_team", {}).get("name", "") or ""),
            away_team=str(fixture.get("away_team", {}).get("name", "") or ""),
            home_goals=int(fixture.get("home_goals")) if fixture.get("home_goals") is not None else 0,
            away_goals=int(fixture.get("away_goals")) if fixture.get("away_goals") is not None else 0,
        )
        return True
    except Exception as exc:
        logger.warning("on_match_finished call failed for %s: %s", fixture.get("id"), exc)
        return False


def normalize_match_for_db(fixture: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(fixture.get("id", "")),
        "date": fixture.get("date") or "",
        "kickoff": str(fixture.get("kickoff") or ""),
        "home_team": fixture.get("home_team", {}).get("name") or "",
        "away_team": fixture.get("away_team", {}).get("name") or "",
        "league_name": fixture.get("league", {}).get("name") or "",
        "league_id": int(fixture.get("league", {}).get("id") or 0),
        "status": fixture.get("status") or "NS",
        "home_goals": fixture.get("home_goals"),
        "away_goals": fixture.get("away_goals"),
    }


def process_date(
    conn: sqlite3.Connection,
    date_str: str,
    provider: str,
    league: Optional[str],
    season: Optional[str],
) -> None:
    logger.info("Backfill date=%s provider=%s league=%s season=%s", date_str, provider, league, season)
    if provider == "sofascore":
        fixtures = _fetch_sofascore(date_str)
    else:
        fixtures = fetch_apifootball(date_str, league, season)

    _persist_fixtures_snapshot(date_str, fixtures, provider)

    inserted_matches = 0
    skipped_matches = 0
    inserted_history = 0
    skipped_history = 0
    errors = 0

    for fixture in fixtures:
        try:
            if not fixture.get("id"):
                skipped_matches += 1
                continue

            upsert_match(conn, normalize_match_for_db(fixture))
            inserted_matches += 1

            status = (fixture.get("status") or "").upper()
            if status in FINISHED_STATUSES and fixture.get("home_goals") is not None and fixture.get("away_goals") is not None:
                created = insert_history_row(conn, fixture)
                if created:
                    inserted_history += 1
                    call_on_match_finished_if_available(conn, fixture)
                else:
                    skipped_history += 1
        except Exception as exc:
            errors += 1
            logger.warning("Backfill error for %s: %s", fixture.get("id"), exc)

    logger.info(
        "Backfill result: date=%s fixtures=%d inserted_matches=%d skipped_matches=%d inserted_history=%d skipped_history=%d errors=%d",
        date_str,
        len(fixtures),
        inserted_matches,
        skipped_matches,
        inserted_history,
        skipped_history,
        errors,
    )
    return inserted_matches, inserted_history


def make_date_range(start_date: date, end_date: date) -> List[str]:
    dates: List[str] = []
    current = start_date
    while current <= end_date:
        dates.append(current.isoformat())
        current += timedelta(days=1)
    return dates


def main() -> None:
    args = parse_args()
    try:
        start_date = date.fromisoformat(args.start)
        end_date = date.fromisoformat(args.end)
    except ValueError as exc:
        raise SystemExit(f"Invalid date format: {exc}")

    if end_date < start_date:
        raise SystemExit("end date must be on or after start date")

    conn = get_db()
    create_match_history_table(conn)

    total_inserted_matches = 0
    total_inserted_history = 0
    total_dates = 0

    for current_date in make_date_range(start_date, end_date):
        total_dates += 1
        try:
            inserted_matches, inserted_history = process_date(
                conn,
                current_date,
                args.provider,
                args.league,
                args.season,
            )
            total_inserted_matches += inserted_matches
            total_inserted_history += inserted_history
        except Exception as exc:
            logger.warning("Failed to backfill %s: %s", current_date, exc)

    if total_dates and total_inserted_matches == 0 and total_inserted_history == 0:
        print(
            "No provider data available. Use CSV import:"
            " python scripts/import_football_data_csv.py --file path/to/E0.csv --league 'Premier League'"
        )


if __name__ == "__main__":
    main()

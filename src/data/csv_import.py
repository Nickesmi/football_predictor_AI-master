from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Optional

from src.db.match_repo import upsert_match


def parse_date(value: str) -> Optional[str]:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d.%m.%Y", "%d-%m-%Y"]:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_int(value: Any) -> Optional[int]:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return None


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


def _import_csv_row(conn: sqlite3.Connection, row: dict[str, str], league_override: Optional[str]) -> tuple[bool, bool]:
    date_str = parse_date(row.get("Date", "") or row.get("date", ""))
    if not date_str:
        return False, False

    home_team = (row.get("HomeTeam", "") or row.get("home_team", "")).strip()
    away_team = (row.get("AwayTeam", "") or row.get("away_team", "")).strip()
    if not home_team or not away_team:
        return False, False

    home_goals = normalize_int(row.get("FTHG", ""))
    away_goals = normalize_int(row.get("FTAG", ""))
    if home_goals is None or away_goals is None:
        return False, False

    league_name = league_override or row.get("Div") or row.get("League") or "Football Data"
    league_name = league_name.strip() if league_name else "Football Data"
    match_id = row.get("MatchID") or f"csv:{date_str}:{home_team}:{away_team}"

    upsert_match(conn, {
        "id": str(match_id),
        "date": date_str,
        "kickoff": "",
        "home_team": home_team,
        "away_team": away_team,
        "league_name": league_name,
        "league_id": 0,
        "status": "FT",
        "home_goals": home_goals,
        "away_goals": away_goals,
    })

    exists = conn.execute(
        "SELECT 1 FROM match_history WHERE match_id = ? LIMIT 1",
        (str(match_id),),
    ).fetchone()
    if exists:
        return True, False

    conn.execute(
        """INSERT INTO match_history (
            match_id, match_date, league, home_team, away_team,
            home_goals, away_goals, home_xg, away_xg,
            home_corners, away_corners, home_cards, away_cards,
            home_elo_before, away_elo_before, home_elo_after, away_elo_after
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(match_id),
            date_str,
            league_name,
            home_team,
            away_team,
            home_goals,
            away_goals,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ),
    )
    conn.commit()
    return True, True


def import_csv_stream(conn: sqlite3.Connection, stream: BinaryIO, league_override: Optional[str] = None) -> dict[str, int]:
    wrapped = io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")
    reader = csv.DictReader(wrapped)
    if not reader.fieldnames:
        raise ValueError("CSV has no header row.")

    inserted_matches = 0
    inserted_history = 0
    skipped_rows = 0

    for row in reader:
        inserted, history_added = _import_csv_row(conn, row, league_override)
        if not inserted and not history_added:
            skipped_rows += 1
            continue
        if inserted:
            inserted_matches += 1
        if history_added:
            inserted_history += 1

    return {
        "inserted_matches": inserted_matches,
        "inserted_history": inserted_history,
        "skipped_rows": skipped_rows,
    }


def import_csv_file(conn: sqlite3.Connection, file_path: Path, league_override: Optional[str] = None) -> dict[str, int]:
    with file_path.open("rb") as stream:
        return import_csv_stream(conn, stream, league_override=league_override)

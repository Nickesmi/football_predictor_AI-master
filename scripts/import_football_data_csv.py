#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.db.database import get_db
from src.data.csv_import import create_match_history_table, import_csv_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import historical fixtures from football-data.co.uk CSV files."
    )
    parser.add_argument("--file", required=True, help="Path to the CSV file.")
    parser.add_argument(
        "--league",
        help="Optional league name override (e.g. 'Premier League').",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    file_path = Path(args.file)
    if not file_path.exists():
        raise SystemExit(f"CSV file not found: {file_path}")

    conn = get_db()
    create_match_history_table(conn)
    result = import_csv_file(conn, file_path, league_override=args.league)

    print(
        f"Imported CSV rows: {result['inserted_matches']} matches, "
        f"{result['inserted_history']} match_history rows, {result['skipped_rows']} skipped."
    )
    print("If results look too low, verify the CSV has Date, HomeTeam, AwayTeam, FTHG, FTAG, and Div columns.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Build the real historical dataset from the downloaded openfootball .txt
files (data/real_historical/raw/*.txt) into validated CSVs.

This does NOT hit the network — the raw files must already exist (fetched
once from https://github.com/openfootball, real public-domain match
results, real teams, real scores; no synthetic data). Run this whenever
raw files change.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.openfootball_parser import parse_directory

LEAGUE_MAP = {
    "england": "Premier League",
    "deutschland": "Bundesliga",
    "italy": "Serie A",
}

RAW_DIR = PROJECT_ROOT / "data" / "real_historical" / "raw"
OUT_DIR = PROJECT_ROOT / "data" / "real_historical"


def main():
    matches_df, quarantined_df, count_report = parse_directory(RAW_DIR, LEAGUE_MAP)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    matches_df.to_csv(OUT_DIR / "matches.csv", index=False)
    quarantined_df.to_csv(OUT_DIR / "quarantined.csv", index=False)

    print(f"Parsed {len(matches_df)} real matches, quarantined {len(quarantined_df)} rows.")
    print(f"Leagues: {sorted(matches_df['league'].unique().tolist())}")
    print(f"Seasons: {sorted(matches_df['season'].unique().tolist())}")
    print(f"Date range: {matches_df['date'].min()} .. {matches_df['date'].max()}")

    mismatches = [r for r in count_report if not r["count_matches_header"]]
    print(f"\nPer-file expected-vs-parsed count check: {len(count_report) - len(mismatches)}/{len(count_report)} files match header count exactly.")
    if mismatches:
        print("Files where parsed+quarantined != header '# Matches N':")
        for r in mismatches:
            print(f"  {r['file']}: header={r['expected_matches']} parsed={r['parsed_matches']} quarantined={r['quarantined']}")

    if len(quarantined_df) > 0:
        print("\nQuarantine reasons breakdown:")
        print(quarantined_df["reason"].value_counts().to_string())

    return 0 if not mismatches else 1


if __name__ == "__main__":
    sys.exit(main())

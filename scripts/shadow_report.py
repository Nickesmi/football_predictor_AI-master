#!/usr/bin/env python3
"""
Shadow validation report (M3 Phase F): aggregate what the shadow cycles observed.

    python scripts/shadow_report.py --db <shadow db> --out shadow_report.json

Read-only. Reports the actual observation period; it never extrapolates.
"""

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--max-age", type=int, default=240)
    parser.add_argument("--check-stale-minutes", type=int, default=0,
                        help="exit 3 if the last cycle is older than this (monitoring)")
    args = parser.parse_args()
    os.environ["FOOTBALL_PREDICTOR_DB_PATH"] = str(args.db.resolve())
    sys.path.insert(0, str(PROJECT_ROOT))
    import sqlite3

    from src.shadow.report import build_report  # noqa: E402

    conn = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    report = build_report(conn, args.max_age)
    args.out.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: report[k] for k in ("period", "observations", "odds_coverage", "settlement", "results")},
                     indent=1, default=str))
    if args.check_stale_minutes:
        from datetime import datetime, timezone

        last = report["period"]["last_cycle"]
        age = None if last is None else (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 60
        if age is None or age > args.check_stale_minutes:
            print(f"STALE: last shadow cycle {last} ({age if age is None else round(age)} min ago)", file=sys.stderr)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

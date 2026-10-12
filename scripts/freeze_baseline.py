"""Create an immutable frozen football baseline (M3 activation step; never run implicitly).

Usage:
    python scripts/freeze_baseline.py --version football-v1.0 --approved-by "Owner Name" \
        [--db PATH] [--manifest-dir DIR]

Snapshots the current calibration models, league adjustments, gate statistics,
BILQE history inputs and pick calibrator into frozen tables, writes
<manifest-dir>/<version>/manifest.json and registers it in the database.
Refuses to overwrite an existing version. Activating the baseline is separate:
set BASELINE_FREEZE=<version> for every process (API, workers).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--db", default=None, help="SQLite path (default: FOOTBALL_PREDICTOR_DB_PATH / data/engine.db)")
    parser.add_argument("--manifest-dir", default=None, help="default: models/baseline")
    args = parser.parse_args(argv)

    from src import config
    from src.baseline.freeze import create_baseline
    from src.baseline.state import integrity_report
    from src.db.database import DB_PATH, init_db

    db_path = Path(args.db) if args.db else DB_PATH
    manifest_dir = Path(args.manifest_dir) if args.manifest_dir else config.BASELINE_MANIFEST_DIR
    if config.BASELINE_FREEZE is not None:
        print("Refusing: BASELINE_FREEZE is set in this process; freeze from an unfrozen shell.", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    manifest = create_baseline(conn, args.version, manifest_dir, args.approved_by)

    config.BASELINE_MANIFEST_DIR = manifest_dir
    report = integrity_report(conn, args.version)
    print(json.dumps({
        "baseline_version": manifest["baseline_version"],
        "model_version": manifest["model_version"],
        "calibration_version": manifest["calibration_version"],
        "selection_policy_version": manifest["selection_policy_version"],
        "runtime": manifest["runtime"],
        "manifest": str(manifest_dir / args.version / "manifest.json"),
        "integrity_ok": report["ok"],
        "problems": report["problems"],
    }, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

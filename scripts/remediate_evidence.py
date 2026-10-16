#!/usr/bin/env python3
"""
Evidence remediation (M3 Phase B) — runs only on an isolated copy.

    python scripts/remediate_evidence.py --source <backup.db> --out <empty dir>

Steps (see src/integrity/):
  1. online-backup the source (opened read-only) into <out>/engine_m3_candidate.db
  2. classify every prediction_errors / prediction_log row
  3. copy every non-verified row into evidence_quarantine (original PK + JSON)
  4. keep the original artifacts as *_pre_m3 tables
  5. remove quarantined rows from the copy (hash-checked) and rebuild
     model_confidence_adjustments and calibration_models with the production builders
  6. write rebuild_report.json (old vs corrected artifacts)

The live database is refused as --source and as output. Nothing here activates
the candidate artifacts.
"""

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = (PROJECT_ROOT / "data" / "engine.db").resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, type=Path, help="backup DB to read (never data/engine.db)")
    parser.add_argument("--out", required=True, type=Path, help="new or empty output directory")
    args = parser.parse_args()

    source = args.source.resolve()
    out = args.out.resolve()
    if source == LIVE_DB:
        print("refusing: --source is the live database; pass a verified backup", file=sys.stderr)
        return 2
    if out.exists() and any(out.iterdir()):
        print(f"refusing: {out} is not empty", file=sys.stderr)
        return 2
    if os.getenv("BASELINE_FREEZE"):
        print("refusing: unset BASELINE_FREEZE for an isolated rebuild", file=sys.stderr)
        return 2

    # Any incidental get_db() during the rebuild must hit the candidate, never the live DB.
    out.mkdir(parents=True, exist_ok=True)
    os.environ["FOOTBALL_PREDICTOR_DB_PATH"] = str(out / "engine_m3_candidate.db")
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.integrity.rebuild import rebuild  # noqa: E402

    report = rebuild(source, out)
    print(json.dumps({k: report[k] for k in (
        "classification", "quarantined_rows", "moved_rows", "counts_before", "counts_after",
        "leagues_rebuilt", "integrity_check")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

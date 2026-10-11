#!/usr/bin/env python3
"""
Real-data validation run for PHASE_4_LIVE_SHADOW_REPORT.md.

Runs a genuine (not toy-fixture) historical replay over a real window of
the frozen 2024-25 holdout, plus a rolling paper-shadow comparison for a
market where Phase 2/3 found XGBoost was actually statistically validated
(over_2_5), and prints every number the final report needs. This is a
one-off reporting script, not itself part of the test suite (the test
suite's own coverage — including the CI-failing parity test — already
proves correctness on a smaller sample; this exists purely to generate
real, larger-sample numbers to quote honestly in the report).
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.prediction_service import snapshot_db
from src.prediction_service.replay_engine import run_replay, DEFAULT_MARKETS
from src.prediction_service.rolling_shadow import run_rolling_shadow
from src.prediction_service.monitoring import compute_period_metrics

REPLAY_START = "2025-04-01"
REPLAY_END = "2025-04-30"


def main():
    matches = pd.read_csv(PROJECT_ROOT / "data" / "real_historical" / "matches.csv").sort_values("date").reset_index(drop=True)

    # ── Replay run (its own isolated DB) ──
    snapshot_db._DB_PATH = PROJECT_ROOT / "data" / "real_historical" / "phase4_replay_report.db"
    snapshot_db._DB_PATH.unlink(missing_ok=True)
    replay_conn = snapshot_db.get_connection()

    print(f"=== REPLAY: {REPLAY_START} .. {REPLAY_END}, markets={DEFAULT_MARKETS} ===")
    replay_run = run_replay(matches, REPLAY_START, REPLAY_END, markets=DEFAULT_MARKETS, conn=replay_conn)
    print(json.dumps({
        "matches_processed": replay_run.matches_processed,
        "predictions_generated": replay_run.predictions_generated,
        "refusals": replay_run.refusals,
        "refusal_reasons": replay_run.refusal_reasons,
        "errors": replay_run.errors,
        "error_details": replay_run.error_details[:10],
        "settled": replay_run.settled,
    }, indent=2))

    # Real metrics over the whole replay window from monitoring.py (uses REAL created_at timestamps).
    rows = replay_conn.execute("SELECT MIN(created_at) AS lo, MAX(created_at) AS hi FROM predictions").fetchone()
    if rows["lo"]:
        metrics = compute_period_metrics(replay_conn, rows["lo"], rows["hi"] + "Z" if not rows["hi"].endswith("Z") else rows["hi"])
        print("\n=== REPLAY WINDOW METRICS (monitoring.compute_period_metrics) ===")
        print(json.dumps(metrics, indent=2, default=str))

    # OOD frequency
    ood_counts = replay_conn.execute("SELECT ood_severity, COUNT(*) as n FROM predictions GROUP BY ood_severity").fetchall()
    print("\n=== OOD FREQUENCY ===")
    print(json.dumps({r["ood_severity"]: r["n"] for r in ood_counts}, indent=2))

    replay_conn.close()

    # ── Rolling shadow: champion vs XGBoost for over_2_5 (validated market) ──
    snapshot_db._DB_PATH = PROJECT_ROOT / "data" / "real_historical" / "phase4_shadow_report.db"
    snapshot_db._DB_PATH.unlink(missing_ok=True)
    shadow_conn = snapshot_db.get_connection()

    print(f"\n=== ROLLING SHADOW: over_2_5, champion vs xgboost_real_data_calibrated, {REPLAY_START} .. {REPLAY_END} ===")
    shadow_run = run_rolling_shadow(matches, "over_2_5", "xgboost_real_data_calibrated", REPLAY_START, REPLAY_END, conn=shadow_conn)
    print(json.dumps({
        "matches_processed": shadow_run.matches_processed,
        "shadow_predictions_generated": shadow_run.shadow_predictions_generated,
        "refusals": shadow_run.refusals,
        "errors": shadow_run.errors,
        "summary": shadow_run.summary,
    }, indent=2))
    shadow_conn.close()


if __name__ == "__main__":
    main()

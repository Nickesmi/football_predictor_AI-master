#!/usr/bin/env python3
"""
Real-time prediction service entrypoint (Phase 3 §13).

    python3 scripts/live_predict.py \
        --home "Arsenal FC" --away "Chelsea FC" --league "English Premier League" \
        --market home_win \
        --prediction-timestamp 2025-05-25T10:00:00Z \
        --kickoff-timestamp 2025-05-25T15:00:00Z

This environment has no live fixture/kickoff-schedule provider (see
AUDIT_REPORT.md — the network policy blocks the providers the legacy
system uses), so --kickoff-timestamp must be supplied by the caller
rather than looked up automatically; the pipeline still enforces
prediction_timestamp < kickoff_timestamp exactly as if it had been.

Exit code 0 + JSON prediction on success. Exit code 1 + JSON
{"status": "NO PREDICTION — INSUFFICIENT RELIABLE INFORMATION", ...} on
any refusal — never a fabricated fallback.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.prediction_service.prediction_pipeline import predict, PredictionRefused
from src.prediction_service.lineup_info import LineupInfo


def _load_historical_matches() -> pd.DataFrame:
    path = PROJECT_ROOT / "data" / "real_historical" / "matches.csv"
    if not path.exists():
        raise SystemExit(f"NO PREDICTION — INSUFFICIENT RELIABLE INFORMATION: {path} not found. "
                          f"Run scripts/build_real_dataset.py first.")
    return pd.read_csv(path)


def _result_to_json(result) -> dict:
    d = dataclasses.asdict(result)
    return d


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", required=True, dest="home_team")
    parser.add_argument("--away", required=True, dest="away_team")
    parser.add_argument("--league", required=True)
    parser.add_argument("--market", required=True,
                         help="e.g. home_win, draw, away_win, over_1_5, over_2_5, over_3_5, btts, "
                              "under_1_5, under_2_5, under_3_5, home_over_0_5, away_over_0_5")
    parser.add_argument("--prediction-timestamp", required=True, dest="prediction_timestamp")
    parser.add_argument("--kickoff-timestamp", required=True, dest="kickoff_timestamp")
    parser.add_argument("--match-id", default=None, dest="match_id")
    parser.add_argument("--odds", type=float, default=None)
    parser.add_argument("--odds-timestamp", default=None, dest="odds_timestamp")
    args = parser.parse_args()

    historical_matches = _load_historical_matches()

    try:
        result = predict(
            home_team=args.home_team, away_team=args.away_team, league=args.league,
            market=args.market, prediction_timestamp=args.prediction_timestamp,
            kickoff_timestamp=args.kickoff_timestamp, historical_matches=historical_matches,
            match_id=args.match_id, odds=args.odds, odds_timestamp=args.odds_timestamp,
        )
    except PredictionRefused as e:
        print(json.dumps({
            "status": "NO PREDICTION — INSUFFICIENT RELIABLE INFORMATION",
            "stage_failed": e.stage_failed, "reason": e.reason,
        }, indent=2))
        sys.exit(1)

    print(json.dumps(_result_to_json(result), indent=2, default=str))
    sys.exit(0)


if __name__ == "__main__":
    main()

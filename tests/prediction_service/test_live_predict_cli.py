"""
End-to-end tests for scripts/live_predict.py (Phase 3 §13/§14) — invoked
as a real subprocess, the way an operator or a cron job actually would.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = PROJECT_ROOT / "scripts" / "live_predict.py"


def _run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=60,
    )


def test_cli_successful_prediction_exits_zero_with_json():
    proc = _run(
        "--home", "Arsenal FC", "--away", "Chelsea FC", "--league", "English Premier League",
        "--market", "home_win",
        "--prediction-timestamp", "2025-05-25T10:00:00Z", "--kickoff-timestamp", "2025-05-25T15:00:00Z",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["champion_model"]
    assert 0.0 <= payload["calibrated_probability"] <= 1.0


def test_cli_invalid_timestamp_exits_nonzero_with_no_prediction_status():
    proc = _run(
        "--home", "Arsenal FC", "--away", "Chelsea FC", "--league", "English Premier League",
        "--market", "home_win",
        "--prediction-timestamp", "2025-05-25T16:00:00Z", "--kickoff-timestamp", "2025-05-25T15:00:00Z",
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["status"] == "NO PREDICTION — INSUFFICIENT RELIABLE INFORMATION"
    assert payload["stage_failed"] == "timestamp_validation"


def test_cli_missing_required_argument_fails_before_any_prediction_attempt():
    proc = _run("--home", "Arsenal FC", "--away", "Chelsea FC")  # missing --league, --market, timestamps
    assert proc.returncode != 0
    assert "required" in proc.stderr.lower()

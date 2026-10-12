"""Content fingerprints of everything that defines official prediction behaviour.

Versions are derived from file contents (not git), so they also hold on a
deployment without a .git directory. Any change to a listed file changes the
corresponding version and makes the frozen baseline's integrity check fail.
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# Model: probability mathematics, features, team-state/Elo updates (formulas and
# K-factors), and model artifacts. Team Elo/form VALUES are input data and may update.
MODEL_FILES = (
    "src/engine/probability_engine.py",
    "src/ml/poisson_model.py",
    "src/ml/team_stats_db.py",
    "src/ml/feature_builder.py",
    "src/ml/predictor.py",
    "src/ml/trainer.py",
    "src/ml/hybrid_predictor.py",
    "src/ml/model_provenance.py",
    "src/engine/live_updater.py",
    "src/db/team_state.py",
    "src/db/team_intelligence.py",
    "api/services/match_analysis_service.py",
    "models/training_metrics.json",
    "models/xgb_btts.pkl",
    "models/xgb_draw.pkl",
    "models/xgb_home_win.pkl",
    "models/xgb_ht_over_0_5.pkl",
    "models/xgb_over_1_5.pkl",
    "models/xgb_over_2_5.pkl",
    "models/xgb_over_3_5.pkl",
)

# Calibration code (the frozen calibration DATA is fingerprinted separately).
CALIBRATION_FILES = (
    "src/engine/calibration.py",
    "src/engine/isotonic_calibrator.py",
    "src/db/error_intelligence.py",
)

# Selection policy: value, risk, grading, staking, gates, BILQE, execution rules.
SELECTION_FILES = (
    "src/engine/pipeline.py",
    "src/engine/market_value.py",
    "src/engine/risk_control.py",
    "src/models/pick.py",
    "src/engine/performance_gate.py",
    "src/engine/qualification_engine.py",
    "src/engine/execution_engine.py",
    "src/engine/similar_match_engine.py",
    "src/engine/fixture_collector.py",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(obj) -> str:
    return sha256_bytes(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode())


def file_hashes(paths: tuple[str, ...]) -> dict[str, str]:
    return {p: sha256_bytes((ROOT / p).read_bytes()) for p in paths}


def runtime_fingerprint() -> dict:
    lock = ROOT / "requirements-lock.txt"
    return {
        "python": ".".join(platform.python_version_tuple()[:2]),
        "requirements_lock_sha256": sha256_bytes(lock.read_bytes()) if lock.exists() else None,
    }


def code_revision() -> str:
    """Git commit of the running code, with '+dirty' when tracked files are modified; 'unknown' without git."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=ROOT, capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
        return f"{sha}+dirty" if dirty else sha
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def version_tag(prefix: str, parts: dict) -> str:
    return f"{prefix}-{sha256_json(parts)[:12]}"

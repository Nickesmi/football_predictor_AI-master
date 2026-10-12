"""Create a frozen baseline: snapshot learned state into frozen tables/artifacts and write the manifest."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.baseline import fingerprint as fp
from src.baseline.tables import FROZEN_TABLES, ensure_baseline_tables

# Columns excluded from content hashes: row ids and bookkeeping timestamps carry no model content.
_VOLATILE_COLUMNS = {"id", "baseline_version", "last_updated", "created_at"}


def _rows(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def table_content_hash(conn: sqlite3.Connection, table_sql: str, order_by: str, params=()) -> str:
    rows = _rows(conn, f"SELECT * FROM {table_sql} ORDER BY {order_by}", params)
    return fp.sha256_json([{k: v for k, v in r.items() if k not in _VOLATILE_COLUMNS} for r in rows])


def frozen_table_hash(conn: sqlite3.Connection, table: str, version: str) -> str:
    order = "market_type" if table == "calibration_models" else "league_name"
    return table_content_hash(conn, f"{table}_frozen WHERE baseline_version = ?", order, (version,))


def league_profiles_hash(conn: sqlite3.Connection) -> str:
    return table_content_hash(conn, "league_profiles", "league_id")


def _snapshot_artifacts(conn: sqlite3.Connection) -> dict[str, object]:
    from src.engine.calibration import ProbabilityCalibrator
    from src.engine.performance_gate import _live_market_performance
    from src.engine.qualification_engine import load_pick_history

    calibrator = ProbabilityCalibrator(n_bins=10)
    calibrator.fit_from_db(conn)
    return {
        "pick_calibrator": calibrator.to_state(),
        "gate_market_stats": _live_market_performance(conn),
        "qualification_history": load_pick_history(conn),
    }


def create_baseline(
    conn: sqlite3.Connection,
    version: str,
    manifest_dir: Path,
    approved_by: str,
) -> dict:
    """Snapshot the current learned state as `version`. Refuses to overwrite an existing version."""
    from src import config

    if not re.match(config._BASELINE_VERSION_PATTERN, version):
        raise ValueError(f"invalid baseline version {version!r}")
    manifest_path = Path(manifest_dir) / version / "manifest.json"
    ensure_baseline_tables(conn)
    if manifest_path.exists() or conn.execute(
        "SELECT 1 FROM baseline_versions WHERE baseline_version = ?", (version,)
    ).fetchone():
        raise FileExistsError(f"baseline {version!r} already exists; versions are immutable")

    with conn:
        conn.execute(
            "INSERT INTO calibration_models_frozen SELECT ?, * FROM calibration_models "
            "WHERE id IN (SELECT MAX(id) FROM calibration_models GROUP BY market_type)",
            (version,),
        )
        conn.execute(
            "INSERT INTO model_confidence_adjustments_frozen SELECT ?, * FROM model_confidence_adjustments",
            (version,),
        )
        artifacts = _snapshot_artifacts(conn)
        artifact_hashes = {}
        for name, payload in artifacts.items():
            digest = fp.sha256_json(payload)
            artifact_hashes[name] = digest
            conn.execute(
                "INSERT INTO baseline_frozen_artifacts (baseline_version, artifact, payload_json, sha256) VALUES (?, ?, ?, ?)",
                (version, name, json.dumps(payload, sort_keys=True, default=str), digest),
            )
        for table in FROZEN_TABLES:
            artifact_hashes[f"{table}_frozen"] = frozen_table_hash(conn, table, version)
        artifact_hashes["league_profiles"] = league_profiles_hash(conn)

        manifest = build_manifest(version, artifact_hashes, approved_by)
        manifest_json = json.dumps(manifest, indent=2, sort_keys=True)
        conn.execute(
            "INSERT INTO baseline_versions (baseline_version, manifest_json, manifest_sha256) VALUES (?, ?, ?)",
            (version, manifest_json, fp.sha256_bytes(manifest_json.encode())),
        )

    manifest_path.parent.mkdir(parents=True, exist_ok=False)
    manifest_path.write_text(manifest_json + "\n")
    return manifest


def build_manifest(version: str, artifact_hashes: dict[str, str], approved_by: str) -> dict:
    model_files = fp.file_hashes(fp.MODEL_FILES)
    calibration_files = fp.file_hashes(fp.CALIBRATION_FILES)
    selection_files = fp.file_hashes(fp.SELECTION_FILES)
    calibration_artifacts = {k: artifact_hashes[k] for k in (
        "pick_calibrator", "calibration_models_frozen", "model_confidence_adjustments_frozen",
    )}
    selection_artifacts = {k: artifact_hashes[k] for k in (
        "gate_market_stats", "qualification_history", "league_profiles",
    )}
    return {
        "baseline_version": version,
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "approved_by": approved_by,
        "code_revision_at_freeze": fp.code_revision(),
        "model_version": fp.version_tag("model", model_files),
        "calibration_version": fp.version_tag("cal", {**calibration_files, **calibration_artifacts}),
        "selection_policy_version": fp.version_tag("sel", {**selection_files, **selection_artifacts}),
        "runtime": fp.runtime_fingerprint(),
        "files": {"model": model_files, "calibration": calibration_files, "selection": selection_files},
        "artifacts": artifact_hashes,
        "policy": {
            "inputs_may_update": ["team_state Elo and rolling form after verified finished matches", "fixtures", "odds", "results"],
            "frozen": ["model code, coefficients, features, Elo formulas/K", "calibration artifacts", "league adjustments",
                       "runtime gate statistics", "BILQE history inputs", "selection rules", "python minor + dependency lock"],
        },
    }

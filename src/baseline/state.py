"""Runtime state of the frozen baseline: active version, integrity check, provenance."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src import config
from src.baseline import fingerprint as fp


class BaselineFrozenError(RuntimeError):
    """A production model/calibration mutation was attempted while the baseline is frozen."""


class BaselineIntegrityError(RuntimeError):
    """Official predictions refused: the running system does not match the frozen manifest."""


def active_version() -> str | None:
    return config.BASELINE_FREEZE


def is_frozen() -> bool:
    return active_version() is not None


def assert_mutation_allowed(action: str) -> None:
    if is_frozen():
        raise BaselineFrozenError(
            f"{action} refused: football baseline {active_version()!r} is frozen "
            "(automatic and manual production model changes are disabled)"
        )


def manifest_path(version: str) -> Path:
    return Path(config.BASELINE_MANIFEST_DIR) / version / "manifest.json"


def load_manifest(version: str) -> dict:
    return json.loads(manifest_path(version).read_text())


def load_artifact(conn: sqlite3.Connection, name: str, version: str | None = None):
    version = version or active_version()
    row = conn.execute(
        "SELECT payload_json FROM baseline_frozen_artifacts WHERE baseline_version = ? AND artifact = ?",
        (version, name),
    ).fetchone()
    if row is None:
        raise BaselineIntegrityError(f"frozen artifact {name!r} missing for baseline {version!r}")
    return json.loads(row[0])


def integrity_report(conn: sqlite3.Connection, version: str | None = None) -> dict:
    """Compare the running code, runtime and frozen data with the manifest. Never raises."""
    from src.baseline.freeze import frozen_table_hash, league_profiles_hash
    from src.baseline.tables import FROZEN_TABLES

    version = version or active_version()
    report = {"baseline_version": version, "ok": False, "problems": [],
              "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if version is None:
        report["problems"].append("baseline not frozen (BASELINE_FREEZE unset)")
        return report
    problems = report["problems"]
    try:
        manifest = load_manifest(version)
    except (OSError, ValueError) as exc:
        problems.append(f"manifest unreadable: {exc}")
        return report

    db_row = conn.execute(
        "SELECT manifest_json FROM baseline_versions WHERE baseline_version = ?", (version,)
    ).fetchone()
    if db_row is None:
        problems.append("baseline not registered in this database")
    elif json.loads(db_row[0]) != manifest:
        problems.append("manifest file differs from the copy registered in the database")

    for group, paths in (("model", fp.MODEL_FILES), ("calibration", fp.CALIBRATION_FILES), ("selection", fp.SELECTION_FILES)):
        expected = manifest["files"][group]
        if set(paths) != set(expected):
            problems.append(f"{group} file list changed")
        for path, digest in fp.file_hashes(tuple(expected)).items():
            if digest != expected[path]:
                problems.append(f"{group} file modified: {path}")

    runtime = fp.runtime_fingerprint()
    if runtime != manifest["runtime"]:
        problems.append(f"runtime mismatch: running {runtime}, frozen {manifest['runtime']}")

    artifacts = manifest["artifacts"]
    for name in ("pick_calibrator", "gate_market_stats", "qualification_history"):
        try:
            if fp.sha256_json(load_artifact(conn, name, version)) != artifacts[name]:
                problems.append(f"frozen artifact modified: {name}")
        except BaselineIntegrityError as exc:
            problems.append(str(exc))
    for table in FROZEN_TABLES:
        if frozen_table_hash(conn, table, version) != artifacts[f"{table}_frozen"]:
            problems.append(f"frozen table modified: {table}_frozen")
    if league_profiles_hash(conn) != artifacts["league_profiles"]:
        problems.append("league_profiles modified")

    report["ok"] = not problems
    report["manifest"] = {k: manifest[k] for k in (
        "model_version", "calibration_version", "selection_policy_version", "runtime", "code_revision_at_freeze",
    )}
    return report


def require_official_integrity(conn: sqlite3.Connection) -> dict | None:
    """Manifest of the active baseline if official predictions may be produced; raises otherwise.

    Returns None when no baseline is frozen (non-official operation, behaviour unchanged).
    """
    if not is_frozen():
        return None
    report = integrity_report(conn)
    if not report["ok"]:
        raise BaselineIntegrityError("; ".join(report["problems"]))
    return load_manifest(active_version())


def provenance(manifest: dict | None, input_data_timestamp: str) -> dict:
    """Provenance columns for a stored prediction. Unfrozen runs record current content versions."""
    revision = fp.code_revision()
    if manifest is None:
        return {
            "baseline_version": None,
            "model_version": fp.version_tag("model", fp.file_hashes(fp.MODEL_FILES)),
            "calibration_version": "unfrozen",
            "selection_policy_version": fp.version_tag("sel", fp.file_hashes(fp.SELECTION_FILES)),
            "code_revision": revision,
            "runtime": json.dumps(fp.runtime_fingerprint(), sort_keys=True),
            "input_data_timestamp": input_data_timestamp,
        }
    return {
        "baseline_version": manifest["baseline_version"],
        "model_version": manifest["model_version"],
        "calibration_version": manifest["calibration_version"],
        "selection_policy_version": manifest["selection_policy_version"],
        "code_revision": revision,
        "runtime": json.dumps(manifest["runtime"], sort_keys=True),
        "input_data_timestamp": input_data_timestamp,
    }

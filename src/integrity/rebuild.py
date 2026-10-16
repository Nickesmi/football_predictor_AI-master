"""Rebuild calibration artifacts from verified evidence only, on an isolated database copy.

The production builders are reused unchanged (``rebuild_confidence_adjustments``
and ``IsotonicCalibrator.fit_all``) so the candidate artifacts are exactly what
the app would compute from the verified evidence. Original artifacts are kept
in ``<table>_pre_m3`` tables inside the candidate DB and in the untouched source
backup.

Conservative fallback when verified evidence is insufficient (explicit, not
invented confidence):

* League adjustment: no row, or a row below 50 settled samples, is inactive
  (``shrink_factor`` 1.0, raw probabilities). Leagues without verified evidence
  get no row at all.
* Isotonic calibration: with no fitted model the existing code paths apply
  their documented shrinkage (``IsotonicCalibrator.calibrate``:
  ``0.85*p + 0.15*50``; ``classic_prediction._calibrate_market_probability``:
  ``0.80*shrunk + 0.20*50``).
* BILQE: an inactive adjustment leaves league reliability at the "unknown"
  default. That default is a policy question (see BASELINE_POLICY_V1_PROPOSAL);
  this rebuild does not change it.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.integrity import evidence

ARTIFACT_TABLES = ("model_confidence_adjustments", "calibration_models")
REFERENCE_1X2 = ((55.0, 25.0, 20.0), (40.0, 30.0, 30.0), (25.0, 25.0, 50.0))
CALIBRATION_GRID = (20.0, 35.0, 50.0, 65.0, 80.0, 90.0)


def isolated_copy(source_db: Path, target_db: Path) -> None:
    """Online-backup ``source_db`` (opened read-only) into a fresh ``target_db``."""
    if target_db.exists():
        raise FileExistsError(f"{target_db} exists; refusing to overwrite")
    src = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    dst = sqlite3.connect(target_db)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _league_rows(conn: sqlite3.Connection, table: str) -> dict[str, dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT league_name, sample_count, avg_confidence, actual_hit_rate, adjustment_factor, "
        f"overconfidence, brier_score, reliability_score, confidence_penalty, confidence_boost, status "
        f"FROM {table}"
    ).fetchall()
    return {r["league_name"]: dict(r) for r in rows}


def _adjustment_dict(row: dict | None) -> dict:
    """Same activation rule as error_intelligence.get_league_adjustment."""
    from src.db.error_intelligence import _get_sample_weight

    if not row or _get_sample_weight(row["sample_count"]) == 0.0:
        return {"active": False, "shrink_factor": 1.0}
    return {"active": True, "shrink_factor": float(row["adjustment_factor"])}


def compare_league_adjustments(old: dict[str, dict], new: dict[str, dict]) -> list[dict]:
    from src.db.error_intelligence import apply_league_adjustment

    out = []
    for league in sorted(set(old) | set(new), key=lambda k: -(old.get(k, {}).get("sample_count") or 0)):
        o, n = old.get(league), new.get(league)
        oa, na = _adjustment_dict(o), _adjustment_dict(n)
        effects = []
        for ref in REFERENCE_1X2:
            effects.append({
                "raw": list(ref),
                "old": list(apply_league_adjustment(oa, *ref)),
                "new": list(apply_league_adjustment(na, *ref)),
            })
        out.append({
            "league": league,
            "old_samples": o["sample_count"] if o else 0,
            "new_samples": n["sample_count"] if n else 0,
            "old_factor": o["adjustment_factor"] if o else None,
            "new_factor": n["adjustment_factor"] if n else None,
            "old_active": oa["active"],
            "new_active": na["active"],
            "old_hit_rate": o["actual_hit_rate"] if o else None,
            "new_hit_rate": n["actual_hit_rate"] if n else None,
            "old_reliability": o["reliability_score"] if o else None,
            "new_reliability": n["reliability_score"] if n else None,
            "old_status": o["status"] if o else None,
            "new_status": n["status"] if n else None,
            "effects_1x2": effects,
        })
    return out


def _calibrator_for(conn: sqlite3.Connection, table: str):
    """Load an IsotonicCalibrator from ``table`` (a calibration_models-shaped table)."""
    from src.engine import isotonic_calibrator as iso_mod

    cal = iso_mod.IsotonicCalibrator()
    original = iso_mod.read_source
    iso_mod.read_source = lambda _t: table
    try:
        cal.load_from_db(conn)
    finally:
        iso_mod.read_source = original
    return cal


def compare_calibration(conn: sqlite3.Connection) -> dict:
    old = _calibrator_for(conn, "calibration_models_pre_m3")
    new = _calibrator_for(conn, "calibration_models")
    markets = sorted(set(old._models) | set(new._models))
    out = {}
    for m in markets:
        out[m] = {
            "old_fitted": m in old._models,
            "new_fitted": m in new._models,
            "old_samples": old._metadata.get(m, {}).get("samples"),
            "new_samples": new._metadata.get(m, {}).get("samples"),
            "grid": [{"raw": p, "old": old.calibrate(p, m), "new": new.calibrate(p, m)} for p in CALIBRATION_GRID],
        }
    return out


def rebuild(source_db: Path, work_dir: Path) -> dict:
    """Classify, quarantine, move, rebuild and compare on an isolated copy. Returns the report dict."""
    from src.db.error_intelligence import rebuild_confidence_adjustments
    from src.engine.isotonic_calibrator import IsotonicCalibrator
    from src.baseline import tables

    if tables._version() is not None:
        raise RuntimeError("BASELINE_FREEZE is set; the rebuild must run unfrozen on an isolated copy")

    work_dir.mkdir(parents=True, exist_ok=True)
    candidate = work_dir / "engine_m3_candidate.db"
    isolated_copy(source_db, candidate)

    conn = sqlite3.connect(candidate)
    conn.row_factory = sqlite3.Row
    before_counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                     for t in evidence.SOURCE_TABLES + ARTIFACT_TABLES}

    classified = evidence.classify_all(conn)
    evidence.record_classification(conn, classified)
    quarantined = evidence.quarantine(conn, classified)

    for table in ARTIFACT_TABLES:
        conn.execute(f"CREATE TABLE {table}_pre_m3 AS SELECT * FROM {table}")
    conn.commit()
    old_leagues = _league_rows(conn, "model_confidence_adjustments_pre_m3")

    moved = {t: evidence.move_quarantined(conn, t) for t in evidence.SOURCE_TABLES}

    with conn:
        conn.execute("DELETE FROM model_confidence_adjustments")
        conn.execute("DELETE FROM calibration_models")
    leagues_rebuilt = rebuild_confidence_adjustments(conn)
    fit_summary = IsotonicCalibrator(production=False).fit_all(conn)

    new_leagues = _league_rows(conn, "model_confidence_adjustments")
    after_counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in evidence.SOURCE_TABLES + ARTIFACT_TABLES}
    settled_by_league = [
        dict(r) for r in conn.execute(
            "SELECT league_name, COUNT(*) AS settled FROM prediction_errors "
            "WHERE correct IS NOT NULL GROUP BY league_name ORDER BY settled DESC"
        ).fetchall()
    ]
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]

    report = {
        "classifier_version": evidence.CLASSIFIER_VERSION,
        "source_db": str(source_db),
        "candidate_db": str(candidate),
        "classification": evidence.summarize(classified),
        "quarantined_rows": quarantined,
        "moved_rows": moved,
        "counts_before": before_counts,
        "counts_after": after_counts,
        "leagues_rebuilt": leagues_rebuilt,
        "verified_settled_by_league": settled_by_league,
        "isotonic_fit": fit_summary,
        "league_comparison": compare_league_adjustments(old_leagues, new_leagues),
        "calibration_comparison": compare_calibration(conn),
        "integrity_check": integrity,
    }
    conn.close()
    (work_dir / "rebuild_report.json").write_text(json.dumps(report, indent=1, default=str))
    return report

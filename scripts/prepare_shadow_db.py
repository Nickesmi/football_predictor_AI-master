#!/usr/bin/env python3
"""
Prepare a fresh, isolated shadow database and its configuration manifest (M3.4.1 Task 3).

    python scripts/prepare_shadow_db.py --source <remediated candidate db> --out <new empty dir>

* online-backup copy of --source (opened read-only) into <out>/engine_shadow.db; the
  production database is refused as source and as output;
* schema brought to the current code (additive migrations, K53 quote revisions);
* rh1 history rebuilt once to pin its content (the shadow cycle rebuilds it every cycle);
* <out>/shadow_manifest.json pins the git commit, identity resolver, registry, rh1 history,
  prediction inputs, profile, policies, odds schema and database fingerprint.

Forward-evidence rule recorded in the manifest: only shadow_observations rows written by a
cycle before kickoff are forward evidence; rebuilt history and pre-existing verified rows are
history, never forward observations. The database starts with zero observations.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = (PROJECT_ROOT / "data" / "engine.db").resolve()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _table_sha(conn, table: str, order: str) -> str:
    h = hashlib.sha256()
    for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}"):
        h.update(repr(tuple(row)).encode())
    return h.hexdigest()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args()
    src, out = a.source.resolve(), a.out.resolve()
    if src == LIVE_DB:
        print("refusing: the production database is never a shadow source", file=sys.stderr)
        return 2
    if out.exists() and any(out.iterdir()):
        print(f"refusing: {out} is not empty", file=sys.stderr)
        return 2
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=PROJECT_ROOT, capture_output=True, text=True).stdout
    if dirty.strip():
        print("refusing: the working tree is not clean; the manifest must pin an exact commit", file=sys.stderr)
        return 2
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True,
                            text=True).stdout.strip()
    out.mkdir(parents=True, exist_ok=True)
    db = out / "engine_shadow.db"
    os.environ["FOOTBALL_PREDICTOR_DB_PATH"] = str(db)
    os.environ.pop("PROBABILITY_INPUTS", None)
    os.environ.pop("BASELINE_FREEZE", None)
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.ops.workspace_guard import isolate

    import sqlite3

    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(db)
    s.backup(d)
    s.close()
    d.close()
    isolate(out / "workspace")

    from src.baseline import state as baseline
    from src.db.database import get_db
    from src.history.reconstruct import HISTORY_VERSION, reconstruct
    from src.identity import registry
    from src.identity.teams import TEAM_IDENTITY_VERSION
    from src.policy import baseline_v1, baseline_v1_1
    from src.shadow.cycle import PROFILES, _ensure

    conn = get_db()                         # additive migrations to the current schema
    _ensure(conn)
    history = reconstruct(conn)
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in (
        "matches", "prediction_errors", "odds_quotes", "shadow_observations", "shadow_fixtures", "picks",
        "team_history_rh1", "team_history_rh1_keys", "evidence_quarantine")}
    odds_cols = [r[1] for r in conn.execute("PRAGMA table_info(odds_quotes)")]
    odds_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'odds_quotes'").fetchone()[0]
    prov = baseline.provenance(None, datetime.now(timezone.utc).isoformat())
    reg = registry.load()
    medium = PROFILES["medium"]
    conn.commit()
    conn.close()

    manifest = {
        "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit,
        "source_db": str(src), "source_sha256": _sha(src),
        "shadow_db": str(db),
        "identity": {"resolver_version": TEAM_IDENTITY_VERSION, "registry_version": reg.version,
                     "registry_sha256": _sha(registry.REGISTRY_FILE),
                     "registry_entries_approved": sum((e.get("review") or {}).get("status") == "approved"
                                                      for e in reg.entries.values())},
        "history": {"version": HISTORY_VERSION, **history,
                    "team_history_rh1_sha256": _table_sha(sqlite3.connect(db), "team_history_rh1",
                                                          "competition_key, team_key, sequence"),
                    "rebuilt_every_cycle": True},
        "prediction": {"shadow_inputs": "identity-v1-rh1 (src/identity/inputs.resolve_probability_inputs_rh1)",
                       "production_default_inputs": "legacy (PROBABILITY_INPUTS unset; unchanged)",
                       "model_version": prov.get("model_version"),
                       "calibration_version": prov.get("calibration_version"),
                       "selection_policy_version": prov.get("selection_policy_version"),
                       "baseline_frozen": False},
        "profile": {"name": "medium", **{k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(medium).items()}},
        "policies": {"v1": baseline_v1.version(), "v1_1": baseline_v1_1.version(), "status": "research only"},
        "odds_schema": {"revision_column": "revision" in odds_cols,
                        "odds_quotes_sql_sha256": hashlib.sha256(odds_sql.encode()).hexdigest()},
        "quota": {"daily_budget_all_consumers": 70, "per_cycle_cap": medium.max_requests_per_cycle,
                  "attempts_per_fixture_window": medium.max_attempts_per_window,
                  "ledger_required": True, "provider_min_remaining": 20},
        "database": {"integrity_check": integrity, "counts": counts},
        "forward_evidence_rule": ("only shadow_observations written by a shadow cycle with decided_at < kickoff "
                                  "count as forward evidence; rh1 history, pre-existing verified prediction rows "
                                  "and any replay are history/retrospective and never counted"),
    }
    db_sha = _sha(db)
    manifest["database"]["sha256_at_preparation"] = db_sha
    (out / "shadow_manifest.json").write_text(json.dumps(manifest, indent=1, default=str))
    print(json.dumps({k: manifest[k] for k in ("git_commit", "identity", "history", "odds_schema", "database")},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

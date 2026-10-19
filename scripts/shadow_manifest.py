#!/usr/bin/env python3
"""
Write a new versioned shadow manifest (M3.5). Never overwrites: an existing --out is refused.

    python scripts/shadow_manifest.py --db <shadow dir>/engine_shadow.db \
        --supersedes <shadow dir>/shadow_manifest.json --out <shadow dir>/shadow_manifest.v2.json

Reads the shadow DB through a read-only in-memory copy (its file hash does not change), rebuilds rh1
in memory to prove the stored history is reproducible, and pins the code by content fingerprint.
"""
import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = (PROJECT_ROOT / "data" / "engine.db").resolve()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--supersedes", type=Path)
    p.add_argument("--profile", default="medium", choices=("low", "medium", "full"))
    p.add_argument("--max-requests-per-cycle", type=int, default=20)
    p.add_argument("--budget", type=int, default=70)
    a = p.parse_args()
    db = a.db.resolve()
    if db == LIVE_DB:
        print("refusing: the production database is never a shadow database", file=sys.stderr)
        return 2
    if a.out.exists():
        print(f"refusing: {a.out} exists; manifests are never rewritten (write a new version)", file=sys.stderr)
        return 2
    if not 0 < a.budget <= 70 or not 0 < a.max_requests_per_cycle <= 20:
        print("refusing: the approved ceilings are 70 per UTC day and 20 per cycle", file=sys.stderr)
        return 2
    sys.path.insert(0, str(PROJECT_ROOT))
    import os

    os.environ.pop("PROBABILITY_INPUTS", None)
    os.environ.pop("BASELINE_FREEZE", None)
    from src.shadow import manifest

    m = manifest.build(db, a.supersedes.resolve() if a.supersedes else None, a.profile,
                       a.max_requests_per_cycle, a.budget)
    if not m["git"]["working_tree_clean"]:
        print("refusing: the working tree is not clean; the manifest must pin an exact commit", file=sys.stderr)
        return 2
    bad = [k for k, ok in (("history.reproducible", m["history"]["reproducible"]),
                           ("odds_schema.revision_behaviour", m["odds_schema"]["revision_behaviour"]["ok"]),
                           ("odds_schema.revision_column", m["odds_schema"]["revision_column"]),
                           ("database.integrity_check", m["database"]["integrity_check"] == "ok"),
                           ("database.quarantine", m["database"]["quarantine"]["ok"]),
                           ("database.forward_evidence_at_creation", m["database"]["forward_evidence_at_creation"]["ok"]))
           if not ok]
    if bad:
        print(f"refusing: checks failed: {bad}", file=sys.stderr)
        return 2
    a.out.write_text(json.dumps(m, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: m[k] for k in ("manifest_id", "git", "code_fingerprint", "runtime", "identity", "prediction")},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

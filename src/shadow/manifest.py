"""Versioned shadow-experiment manifest (M3.5): what exactly the 14-day shadow runs, and a fail-closed check.

A manifest is never rewritten. Reconciliation writes a new version (shadow_manifest.v2.json, ...)
that names the file it supersedes and that file's sha256.

The code is pinned by content, not only by git: ``code_fingerprint`` hashes every runtime file
(src/, api/, models/, the shadow scripts, requirements-lock.txt), so the check also holds on a
host without .git, and later documentation-only commits do not change it.

``verify_runtime`` runs at the start of every deployed cycle. Any mismatch (code, Python, identity
registry, history version, profile, quota, odds schema, model or selection version) or missing
identity/history data refuses the cycle before a single request is made.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import subprocess
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_VERSION = "shadow-manifest-v2"
RUNTIME_DIRS = ("src", "api", "models")
RUNTIME_FILES = ("requirements-lock.txt", "scripts/shadow_cycle.py", "scripts/shadow_settle.py",
                 "scripts/shadow_report.py", "scripts/shadow_preflight.py", "scripts/shadow_manifest.py",
                 "scripts/prepare_shadow_db.py")
SKIP_PARTS = {"__pycache__", ".DS_Store"}
DAILY_BUDGET_MAX = 70
CYCLE_CAP_MAX = 20
PROVIDER_MIN_REMAINING = 20


class ManifestMismatch(RuntimeError):
    """The running code, data or settings differ from the pinned manifest: fail closed."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def runtime_files(root: Path = ROOT) -> list[str]:
    out = []
    for d in RUNTIME_DIRS:
        for p in sorted((root / d).rglob("*")):
            if p.is_file() and not (SKIP_PARTS & set(p.parts)) and p.suffix != ".pyc":
                out.append(p.relative_to(root).as_posix())
    out += [f for f in RUNTIME_FILES if (root / f).is_file()]
    return sorted(out)


def code_fingerprint(root: Path = ROOT) -> dict:
    files = runtime_files(root)
    h = hashlib.sha256()
    for f in files:
        h.update(f.encode() + b"\0" + sha256_file(root / f).encode() + b"\n")
    return {"sha256": h.hexdigest(), "files": len(files)}


def _git(*args) -> str | None:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10,
                              check=True).stdout.strip()
    except Exception:
        return None


def _memory_copy(db: Path) -> sqlite3.Connection:
    """Read-only online copy into memory, so building a manifest never writes the shadow DB."""
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    mem = sqlite3.connect(":memory:")
    src.backup(mem)
    src.close()
    mem.row_factory = sqlite3.Row
    return mem


def _table_sha(conn, table: str, order: str) -> str:
    """Content hash of a table, without its surrogate `id` (AUTOINCREMENT changes on every rebuild)."""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})") if r[1] != "id"]
    h = hashlib.sha256(",".join(cols).encode())
    for row in conn.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY {order}"):
        h.update(repr(tuple(row)).encode())
    return h.hexdigest()


def _odds_sql(conn) -> str:
    return conn.execute("SELECT sql FROM sqlite_master WHERE name = 'odds_quotes'").fetchone()[0]


def revision_selfcheck() -> dict:
    """K53 behaviour on a scratch DB: a changed price under an unchanged provider stamp is a new
    revision, and a decision sees only the revision collected by its own decision time."""
    from src.db.database import init_db
    from src.odds import store
    from src.odds.model import Quote, iso_utc

    now = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute("INSERT INTO matches (id, date, home_team, away_team, league_name, status) "
                 "VALUES ('fx', '2030-01-01', 'A', 'B', 'L', 'NS')")

    def q(price):
        return Quote(provider="api_football", provider_event_id="e", bookmaker="b", market="1X2",
                     selection="home", line="", decimal_odds=price, observed_at=iso_utc(now - timedelta(minutes=100)),
                     commence_time="2030-01-01T15:00:00+00:00")

    store.store_quotes(conn, "fx", [q(2.0)], iso_utc(now - timedelta(minutes=90)))
    store.store_quotes(conn, "fx", [q(1.8)], iso_utc(now - timedelta(minutes=20)))
    store.store_quotes(conn, "fx", [q(1.8)], iso_utc(now - timedelta(minutes=10)))   # duplicate: ignored
    revisions = [r[0] for r in conn.execute("SELECT revision FROM odds_quotes ORDER BY id")]
    early = [r["decimal_odds"] for r in store.actionable_quotes(conn, "fx", now - timedelta(minutes=60),
                                                                timedelta(minutes=240))]
    late = [r["decimal_odds"] for r in store.actionable_quotes(conn, "fx", now, timedelta(minutes=240))]
    ok = revisions == [0, 1] and early == [2.0] and late == [1.8]
    return {"ok": ok, "revisions": revisions, "decision_before_second_collection": early,
            "decision_after": late}


def _versions() -> dict:
    from src.baseline import fingerprint as fp
    from src.baseline import state as baseline
    from src.history.reconstruct import HISTORY_VERSION
    from src.identity import registry
    from src.identity.teams import TEAM_IDENTITY_VERSION
    from src.policy import baseline_v1, baseline_v1_1
    from src.shadow.legacy import LEGACY_POLICY_VERSION

    prov = baseline.provenance(None, datetime.now(timezone.utc).isoformat())
    reg = registry.load()
    return {
        "identity": {"resolver_version": TEAM_IDENTITY_VERSION, "registry_version": reg.version,
                     "registry_sha256": sha256_file(registry.REGISTRY_FILE)},
        "history_version": HISTORY_VERSION,
        "prediction": {"shadow_inputs": "identity-v1-rh1", "model_version": prov["model_version"],
                       "calibration_version": prov["calibration_version"],
                       "calibration_code": fp.version_tag("cal", fp.file_hashes(fp.CALIBRATION_FILES)),
                       "selection_policy_version": prov["selection_policy_version"],
                       "baseline_frozen": baseline.is_frozen()},
        "policies": {"legacy": LEGACY_POLICY_VERSION, "v1": baseline_v1.version(), "v1_1": baseline_v1_1.version(),
                     "status": "research only"},
    }


def _profile(name: str, cap: int) -> dict:
    from dataclasses import replace

    from src.shadow.cycle import PROFILES

    cfg = replace(PROFILES[name], max_requests_per_cycle=cap)
    return {"name": name, **{k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(cfg).items()}}


def build(db: Path, supersedes: Path | None, profile: str = "medium", cap: int = CYCLE_CAP_MAX,
          budget: int = DAILY_BUDGET_MAX) -> dict:
    from src.history.reconstruct import reconstruct
    from src.shadow.cycle import _ensure

    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain")
    mem = _memory_copy(db)
    stored_rh1 = _table_sha(mem, "team_history_rh1", "competition_key, team_key, sequence")
    rebuilt = reconstruct(mem)                       # in memory only: proves the stored history is reproducible
    rebuilt_rh1 = _table_sha(mem, "team_history_rh1", "competition_key, team_key, sequence")
    _ensure(mem)                                     # columns the first cycle will add
    obs_columns = [r[1] for r in mem.execute("PRAGMA table_info(shadow_observations)")]
    tables = {r[0] for r in mem.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    counts = {t: (mem.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] if t in tables else None) for t in (
        "matches", "prediction_errors", "odds_quotes", "shadow_observations", "shadow_fixtures", "shadow_cycles",
        "shadow_selection_results", "picks", "team_history_rh1", "team_history_rh1_keys", "evidence_quarantine")}
    integrity = mem.execute("PRAGMA integrity_check").fetchone()[0]
    quarantine = quarantine_check(mem)
    forward = forward_evidence_check(mem)
    odds_cols = [r[1] for r in mem.execute("PRAGMA table_info(odds_quotes)")]
    odds_sql = _odds_sql(mem)
    mem.close()
    goldens = {p.name: sha256_file(p) for p in sorted((ROOT / "tests" / "golden").glob("*.json"))}
    m = {
        "manifest_version": MANIFEST_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "supersedes": None if supersedes is None else {
            "path": str(supersedes), "sha256": sha256_file(supersedes),
            "git_commit": json.loads(supersedes.read_text()).get("git_commit"),
            "reason": "v1 pinned the commit of the preparation script; later validated commits changed runtime "
                      "code, so the final revision is pinned here. v1 is kept unchanged as history."},
        "git": {"commit": commit, "working_tree_clean": status == ""},
        "code_fingerprint": code_fingerprint(),
        "runtime": {"python": platform.python_version(),
                    "python_major_minor": ".".join(platform.python_version_tuple()[:2]),
                    "implementation": platform.python_implementation(),
                    "requirements_lock_sha256": sha256_file(ROOT / "requirements-lock.txt")},
        **_versions(),
        "history": {"version": rebuilt["history_version"], "fixtures_used": rebuilt["fixtures_used"],
                    "teams": rebuilt["teams"], "collisions": rebuilt["collisions"],
                    "state_rows": rebuilt["state_rows"], "by_competition": rebuilt["by_competition"],
                    "stored_table_sha256": stored_rh1, "rebuilt_table_sha256": rebuilt_rh1,
                    "reproducible": stored_rh1 == rebuilt_rh1, "rebuilt_every_cycle": True},
        "goldens": goldens,
        "odds_schema": {"revision_column": "revision" in odds_cols,
                        "odds_quotes_sql_sha256": hashlib.sha256(odds_sql.encode()).hexdigest(),
                        "revision_behaviour": revision_selfcheck()},
        "profile": _profile(profile, cap),
        "quota": {"daily_budget_all_consumers": budget, "per_cycle_cap": cap,
                  "attempts_per_fixture_window": _profile(profile, cap)["max_attempts_per_window"],
                  "ledger_required": True, "provider_min_remaining": PROVIDER_MIN_REMAINING,
                  "failed_requests_count": True},
        "database": {"path": str(db), "sha256": sha256_file(db), "integrity_check": integrity, "counts": counts,
                     "shadow_observation_columns": obs_columns, "quarantine": quarantine,
                     "forward_evidence_at_creation": forward},
        "forward_evidence_rule": (
            "An observation is forward evidence only if it was written by a shadow cycle running under this "
            "manifest (manifest_id column), with decided_at < kickoff, and its cycle_id is in shadow_cycles. "
            "rh1 history, the pre-existing verified prediction rows, replays and anything decided at or after "
            "kickoff are never forward evidence. Nothing counts toward the official baseline "
            "(counts_toward_baseline is constrained to 0)."),
    }
    m["manifest_id"] = "m35-" + hashlib.sha256(json.dumps(
        {k: v for k, v in m.items() if k != "created_at_utc"}, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return m


def problems_runtime(manifest: dict, conn: sqlite3.Connection, profile: dict, budget: int) -> list[str]:
    """Checks for every deployed cycle start. Returns the list of mismatches (empty = run)."""
    out = []
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        out.append(f"manifest version {manifest.get('manifest_version')!r} is not {MANIFEST_VERSION}")
        return out
    fpr = code_fingerprint()
    if fpr["sha256"] != manifest["code_fingerprint"]["sha256"]:
        out.append(f"code fingerprint {fpr['sha256'][:16]} != manifest {manifest['code_fingerprint']['sha256'][:16]}")
    mm = ".".join(platform.python_version_tuple()[:2])
    if mm != manifest["runtime"]["python_major_minor"]:
        out.append(f"python {mm} != manifest {manifest['runtime']['python_major_minor']}")
    try:
        v = _versions()
    except Exception as exc:  # e.g. the identity registry is missing or unreadable
        out.append(f"identity/version data unavailable: {type(exc).__name__}: {exc}")
        return out
    for key in ("identity", "history_version"):
        if v[key] != manifest[key]:
            out.append(f"{key} {v[key]} != manifest {manifest[key]}")
    for key in ("model_version", "selection_policy_version", "calibration_code", "calibration_version"):
        if v["prediction"][key] != manifest["prediction"][key]:
            out.append(f"{key} {v['prediction'][key]} != manifest {manifest['prediction'][key]}")
    if v["prediction"]["baseline_frozen"]:
        out.append("BASELINE_FREEZE is active; the shadow runs unfrozen")
    if os.getenv("PROBABILITY_INPUTS"):
        out.append("PROBABILITY_INPUTS is set; the shadow selects rh1 inputs itself")
    if profile != manifest["profile"]:
        diff = sorted(k for k in set(profile) | set(manifest["profile"]) if profile.get(k) != manifest["profile"].get(k))
        out.append(f"profile differs from manifest in {diff}")
    if not 0 < budget <= manifest["quota"]["daily_budget_all_consumers"]:
        out.append(f"daily budget {budget} is outside 1..{manifest['quota']['daily_budget_all_consumers']}")
    if os.getenv("API_FOOTBALL_REQUIRE_LEDGER") != "1" or not os.getenv("API_FOOTBALL_LEDGER"):
        out.append("API_FOOTBALL_REQUIRE_LEDGER=1 and API_FOOTBALL_LEDGER are required")
    try:
        sql = _odds_sql(conn)
        if hashlib.sha256(sql.encode()).hexdigest() != manifest["odds_schema"]["odds_quotes_sql_sha256"]:
            out.append("odds_quotes schema differs from manifest")
        from src.history.reconstruct import _finished_fixtures

        finished = len(_finished_fixtures(conn))
        if finished < manifest["history"]["fixtures_used"]:
            out.append(f"history source shrank: {finished} finished fixtures < manifest "
                       f"{manifest['history']['fixtures_used']} (identity/history data unavailable)")
        bad = conn.execute("SELECT COUNT(*) FROM shadow_observations WHERE counts_toward_baseline != 0").fetchone()[0]
        if bad:
            out.append(f"{bad} shadow observations marked as counted")
    except sqlite3.Error as exc:
        out.append(f"shadow DB unreadable: {exc}")
    return out


def quarantine_check(conn: sqlite3.Connection) -> dict:
    """Every quarantined row is out of its source table and its stored copy is byte-identical."""
    present = sha_bad = moved = 0
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not {"evidence_quarantine", "evidence_classification"} <= tables:
        return {"ok": True, "tables_present": False, "moved": 0, "still_in_source_table": 0,
                "copy_sha_mismatch": 0, "classifications": {}}
    for table, pk, row_json, sha in conn.execute(
            "SELECT source_table, original_pk, original_row_json, row_sha256 FROM evidence_quarantine "
            "WHERE moved_at IS NOT NULL AND restored_at IS NULL"):
        moved += 1
        sha_bad += hashlib.sha256(row_json.encode("utf-8")).hexdigest() != sha
        present += conn.execute(f"SELECT COUNT(*) FROM {table} WHERE id = ?", (pk,)).fetchone()[0]
    classes = {f"{t}:{c}": n for t, c, n in conn.execute(
        "SELECT source_table, classification, COUNT(*) FROM evidence_classification GROUP BY 1, 2")}
    return {"ok": present == 0 and sha_bad == 0, "tables_present": True, "moved": moved, "still_in_source_table": present,
            "copy_sha_mismatch": sha_bad, "classifications": classes}


def forward_evidence_check(conn: sqlite3.Connection, manifest_id: str | None = None) -> dict:
    """Shadow observations split by the forward rule. Nothing historical may look forward."""
    q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    total = q("SELECT COUNT(*) FROM shadow_observations")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(shadow_observations)")}
    at_or_after = q("SELECT COUNT(*) FROM shadow_observations WHERE decided_at >= kickoff")
    orphan = q("SELECT COUNT(*) FROM shadow_observations o WHERE NOT EXISTS "
               "(SELECT 1 FROM shadow_cycles c WHERE c.cycle_id = o.cycle_id)")
    other_manifest = 0
    if manifest_id is not None:
        other_manifest = total if "manifest_id" not in cols else q(
            "SELECT COUNT(*) FROM shadow_observations WHERE COALESCE(manifest_id, '') != ?", manifest_id)
    counted = q("SELECT COUNT(*) FROM shadow_observations WHERE counts_toward_baseline != 0") + \
        q("SELECT COUNT(*) FROM shadow_selection_results WHERE counts_toward_baseline != 0")
    picks = q("SELECT COUNT(*) FROM picks")
    return {"ok": at_or_after == 0 and orphan == 0 and other_manifest == 0 and counted == 0 and picks == 0,
            "observations": total, "decided_at_or_after_kickoff": at_or_after, "without_cycle": orphan,
            "from_another_manifest": other_manifest, "counted_toward_baseline": counted, "official_picks": picks,
            "forward": q("SELECT COUNT(*) FROM shadow_observations o WHERE decided_at < kickoff AND EXISTS "
                         "(SELECT 1 FROM shadow_cycles c WHERE c.cycle_id = o.cycle_id)"
                         + ("" if manifest_id is None or "manifest_id" not in cols else " AND manifest_id = ?"),
                         *(() if manifest_id is None or "manifest_id" not in cols else (manifest_id,)))}


def load(path: Path) -> dict:
    data = json.loads(Path(path).read_text())
    if data.get("manifest_version") != MANIFEST_VERSION:
        raise ManifestMismatch(f"{path} is not a {MANIFEST_VERSION} manifest")
    return data

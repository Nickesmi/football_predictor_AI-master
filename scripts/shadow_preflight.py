#!/usr/bin/env python3
"""
Shadow host preflight (M3.5). Run on the deployment host BEFORE any scheduler is enabled.

    python scripts/shadow_preflight.py --manifest /srv/shadow/shadow_manifest.v2.json \
        --db /srv/shadow/engine_shadow.db --env-file /etc/football-shadow/shadow.env \
        --env-file /etc/football-shadow/api_key.env --run-tests [--check-provider] --out preflight.json

Exit 0 only when every check passed. A check that did not run is not a pass.
The shadow DB is never written (read-only in-memory copy; scratch copies under a temp dir).
--check-provider sends ONE API-Football GET /status through the shared ledger (counted as
consumer "shadow_preflight"); without it no request is made.
"""
import argparse
import fcntl
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = (PROJECT_ROOT / "data" / "engine.db").resolve()
ALLOWED_CONSUMERS = {"shadow_cycle", "shadow_settle", "shadow_preflight"}
VOLATILE = ("/tmp", "/var/tmp", "/dev/shm", "/run")
TESTS = ["tests/test_shadow_validation.py", "tests/test_shadow_operations.py", "tests/test_shadow_candidate_pathway.py",
         "tests/test_shadow_manifest.py", "tests/test_team_history_rh1.py", "tests/test_team_identity.py",
         "tests/test_team_registry.py", "tests/test_odds_history_k53.py", "tests/test_odds_m3.py",
         "tests/test_odds_layer.py", "tests/test_workspace_isolation.py", "tests/test_api_guard.py",
         "tests/test_prepare_shadow_db.py", "tests/golden"]


def _check(results, _name, _passed, **detail):
    detail.pop("ok", None)
    results.append({"check": _name, "status": "pass" if _passed is True else ("not_run" if _passed is None else "FAIL"),
                    **detail})


def _env_file(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--env-file", action="append", type=Path, default=[])
    p.add_argument("--run-tests", action="store_true", help="run the shadow, quota and golden tests on this host")
    p.add_argument("--check-provider", action="store_true", help="one counted GET /status (needs the key)")
    p.add_argument("--offline", action="store_true",
                   help="validation only: exit 0 when all but the provider check pass (still reported not_run)")
    p.add_argument("--out", type=Path)
    a = p.parse_args()
    # isolate() below changes the working directory: resolve every path first
    a.manifest, a.env_file = a.manifest.resolve(), [f.resolve() for f in a.env_file]
    a.out = a.out.resolve() if a.out else None
    db = a.db.resolve()
    if db == LIVE_DB:
        print("refusing: the production database is never a shadow database", file=sys.stderr)
        return 2
    results: list[dict] = []
    files_env = {}
    for f in a.env_file:
        files_env.update(_env_file(f))
    for k, v in files_env.items():
        os.environ.setdefault(k, v)
    sys.path.insert(0, str(PROJECT_ROOT))
    os.environ["FOOTBALL_PREDICTOR_DB_PATH"] = str(db)
    from src.ops.workspace_guard import isolate

    scratch = Path(tempfile.mkdtemp(prefix="shadow_preflight_"))
    isolate(scratch / "workspace")
    from src.shadow import manifest as sm

    # 1. Linux tests on this host
    if a.run_tests:
        r = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-q", "-p", "no:cacheprovider"], cwd=PROJECT_ROOT,
                           capture_output=True, text=True,
                           env={**os.environ, "API_FOOTBALL_KEY": "", "APIFOOTBALL_API_KEY": "",
                                "API_FOOTBALL_REQUIRE_LEDGER": "0", "API_FOOTBALL_LEDGER": ""})
        _check(results, "1_linux_tests_on_host", r.returncode == 0, platform=sys.platform,
               summary=(r.stdout.strip().splitlines() or [""])[-1])
    else:
        _check(results, "1_linux_tests_on_host", None, note="run with --run-tests")

    # 2. Credentials configured securely
    key = os.getenv("API_FOOTBALL_KEY") or os.getenv("APIFOOTBALL_API_KEY") or ""
    perms = {str(f): oct(stat.S_IMODE(f.stat().st_mode)) for f in a.env_file}
    loose = [f for f in a.env_file if stat.S_IMODE(f.stat().st_mode) & 0o027]
    in_repo = bool(key) and any(key in (PROJECT_ROOT / f).read_text(errors="ignore")
                                for f in sm.runtime_files() if (PROJECT_ROOT / f).suffix in (".py", ".json", ".txt"))
    key_files = [f for f in a.env_file if "API_FOOTBALL_KEY" in _env_file(f) or "APIFOOTBALL_API_KEY" in _env_file(f)]
    _check(results, "2_credentials", bool(key) and not loose and not in_repo and bool(key_files),
           key_present=bool(key), key_length=len(key), env_file_modes=perms,
           group_writable_or_world_readable=[str(f) for f in loose], key_found_in_code=in_repo,
           key_from_env_file=bool(key_files))

    # 4. Shared ledger for every consumer (persistent, writable, required)
    from src.odds import ledger
    from src.ops import api_guard

    lp = ledger.path()
    problems = []
    if os.getenv("API_FOOTBALL_REQUIRE_LEDGER") != "1":
        problems.append("API_FOOTBALL_REQUIRE_LEDGER is not 1")
    try:
        api_guard.verify_ledger_writable()
    except api_guard.LedgerUnavailable as exc:
        problems.append(str(exc))
    if lp is not None and str(lp.resolve()).startswith(VOLATILE):
        problems.append(f"ledger {lp} is on volatile storage")
    consumers, used = {}, None
    if lp is not None and lp.exists():
        c = sqlite3.connect(f"file:{lp}?mode=ro", uri=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        consumers = dict(c.execute("SELECT consumer, SUM(cost) FROM ledger WHERE provider = 'api_football' AND day = ? "
                                   "GROUP BY 1", (day,)).fetchall())
        c.close()
        used = sum(consumers.values())
    unknown = sorted(set(consumers) - ALLOWED_CONSUMERS)
    pinned = None
    try:
        pinned = sm.load(a.manifest)
    except Exception as exc:
        problems.append(f"manifest unusable: {exc}")
    budget = int(os.getenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "0") or 0)
    if pinned and used is not None and used > pinned["quota"]["daily_budget_all_consumers"]:
        problems.append(f"ledger day total {used} above the daily ceiling")
    _check(results, "4_shared_ledger_all_consumers", not problems and not unknown, ledger=str(lp),
           consumers_today=consumers, unknown_consumers=unknown, problems=problems)
    if pinned is None:
        _write(a.out, results)
        return 1

    # 7/8. Pinned code, identity, rh1, odds schema, profile, quota; quarantine and provenance
    mem = sm._memory_copy(db)
    from dataclasses import asdict, replace

    from src.shadow.cycle import PROFILES

    cfg = replace(PROFILES[pinned["profile"]["name"]], max_requests_per_cycle=pinned["quota"]["per_cycle_cap"])
    profile = {"name": pinned["profile"]["name"], **{k: (list(v) if isinstance(v, tuple) else v)
                                                     for k, v in asdict(cfg).items()}}
    problems = sm.problems_runtime(pinned, mem, profile, budget)
    goldens = {f.name: sm.sha256_file(f) for f in sorted((PROJECT_ROOT / "tests" / "golden").glob("*.json"))}
    if goldens != pinned["goldens"]:
        problems.append("golden reference files differ from the manifest")
    git = None
    if shutil.which("git") and (PROJECT_ROOT / ".git").exists():
        git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True).stdout.strip()
    _check(results, "7_pinned_rh1_identity_configuration", not problems, problems=problems,
           manifest_id=pinned["manifest_id"], code_fingerprint=sm.code_fingerprint()["sha256"],
           git_commit_here=git, git_commit_manifest=pinned["git"]["commit"],
           history_version=pinned["history_version"], profile_history=profile["history"],
           shadow_inputs=pinned["prediction"]["shadow_inputs"])
    from src.history.reconstruct import reconstruct

    stored = sm._table_sha(mem, "team_history_rh1", "competition_key, team_key, sequence")
    rebuilt = reconstruct(mem)
    rebuilt_sha = sm._table_sha(mem, "team_history_rh1", "competition_key, team_key, sequence")
    _check(results, "7b_rh1_rebuild", rebuilt["fixtures_used"] >= pinned["history"]["fixtures_used"]
           and rebuilt["collisions"] == 0 and (rebuilt_sha == stored or rebuilt["fixtures_used"] >
                                               pinned["history"]["fixtures_used"]),
           fixtures_used=rebuilt["fixtures_used"], manifest_fixtures=pinned["history"]["fixtures_used"],
           collisions=rebuilt["collisions"], reproduces_stored_table=rebuilt_sha == stored)
    cycles = mem.execute("SELECT COUNT(*) FROM shadow_cycles").fetchone()[0]
    db_sha = sm.sha256_file(db)
    _check(results, "8a_db_is_the_pinned_db", db_sha == pinned["database"]["sha256"] or cycles > 0,
           db_sha256=db_sha, manifest_sha256=pinned["database"]["sha256"], cycles_run=cycles,
           note="before the first cycle the DB must be byte-identical to the pinned one")
    quarantine = sm.quarantine_check(mem)
    integrity = mem.execute("PRAGMA integrity_check").fetchone()[0]
    pinned_q = pinned["database"]["quarantine"]
    _check(results, "8_quarantine_and_provenance", quarantine["ok"] and integrity == "ok"
           and quarantine["moved"] == pinned_q["moved"] and quarantine["tables_present"] == pinned_q["tables_present"]
           and quarantine["classifications"] == pinned_q["classifications"],
           manifest_moved=pinned_q["moved"],
           integrity_check=integrity, **{k: v for k, v in quarantine.items() if k != "classifications"})

    # 5/6. Zero counted picks; nothing historical classified as forward
    fwd = sm.forward_evidence_check(mem, pinned["manifest_id"])
    _check(results, "5_zero_counted_official_picks", fwd["counted_toward_baseline"] == 0 and fwd["official_picks"] == 0,
           counted=fwd["counted_toward_baseline"], official_picks=fwd["official_picks"])
    _check(results, "6_no_historical_row_as_forward", fwd["ok"], **fwd)
    mem.close()

    # 10. Failed requests count toward the limit (offline: a scratch ledger, a failing transport)
    saved = os.environ.get("API_FOOTBALL_LEDGER")
    os.environ["API_FOOTBALL_LEDGER"] = str(scratch / "selfcheck_ledger.db")
    try:
        from src.db.database import init_db
        from src.odds import quota

        sc = sqlite3.connect(":memory:")
        sc.row_factory = sqlite3.Row
        init_db(sc)

        def failing(url, headers, timeout):
            return 500, {}, b"{}"
        try:
            quota.fetch_json(sc, "api_football", "/selfcheck", "https://selfcheck.invalid/x", {}, cost=1,
                             remaining_header="x", transport=failing)
        except Exception:
            pass
        counted = ledger.used_today("api_football")
    finally:
        if saved is None:
            os.environ.pop("API_FOOTBALL_LEDGER", None)
        else:
            os.environ["API_FOOTBALL_LEDGER"] = saved
    _check(results, "10_failed_requests_count", counted == 1, scratch_ledger_count_after_one_http_500=counted)

    # 9. No overlapping cycles: the cycle lock is free now and is exclusive
    lock = db.parent / "shadow_cycle.lock"
    try:
        f1, f2 = open(lock, "a"), open(lock, "a")
        fcntl.flock(f1, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fcntl.flock(f2, fcntl.LOCK_EX | fcntl.LOCK_NB)
            exclusive = False
        except BlockingIOError:
            exclusive = True
        fcntl.flock(f1, fcntl.LOCK_UN)
        f1.close(), f2.close()
        _check(results, "9_no_overlapping_cycles", exclusive, lock=str(lock), note="plus cycle lease (tests)")
    except BlockingIOError:
        _check(results, "9_no_overlapping_cycles", False, lock=str(lock), note="a shadow cycle is running now")

    # 11. Monitoring alarm and emergency stop (on a scratch copy; the real DB is not touched)
    copy_dir = scratch / "copy"
    copy_dir.mkdir()
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    dst = sqlite3.connect(copy_dir / "engine_shadow.db")
    src.backup(dst)
    src.close(), dst.close()
    rep = subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts" / "shadow_report.py"), "--db",
                          str(copy_dir / "engine_shadow.db"), "--out", str(scratch / "report.json"),
                          "--check-stale-minutes", "90"], capture_output=True, text=True, cwd=scratch)
    (copy_dir / "SHADOW_STOP").write_text("preflight\n")
    stop = subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts" / "shadow_cycle.py"), "--db",
                           str(copy_dir / "engine_shadow.db"), "--budget", "70", "--manifest", str(a.manifest),
                           "--profile", pinned["profile"]["name"]], capture_output=True, text=True, cwd=scratch,
                          env={**os.environ, "API_FOOTBALL_KEY": "", "APIFOOTBALL_API_KEY": "",
                               "API_FOOTBALL_LEDGER": str(scratch / "stop_ledger.db")})
    stop_ledger = scratch / "stop_ledger.db"
    sent = 0 if not stop_ledger.exists() else sqlite3.connect(stop_ledger).execute(
        "SELECT COUNT(*) FROM ledger").fetchone()[0]
    stopped = stop.returncode == 0 and '"stopped"' in stop.stdout and sent == 0
    _check(results, "11_monitoring_and_emergency_stop", rep.returncode == 3 and stopped,
           stale_alarm_exit=rep.returncode, stale_alarm_fires_with_no_cycles=rep.returncode == 3,
           emergency_stop_exit=stop.returncode, emergency_stop_requests=sent,
           live_stop_file_present=(db.parent / "SHADOW_STOP").exists())
    shutil.rmtree(copy_dir, ignore_errors=True)

    # 3. Actual provider quota (one counted request, only with --check-provider)
    if a.check_provider:
        from src import config
        from src.odds import quota

        os.environ["API_FOOTBALL_CONSUMER"] = "shadow_preflight"
        try:
            api_guard.install("shadow_preflight")
            pc = sqlite3.connect(":memory:")
            pc.row_factory = sqlite3.Row
            from src.db.database import init_db

            init_db(pc)
            host = config.APIFOOTBALL_HOST
            body = quota.fetch_json(pc, "api_football", "/status", f"https://{host}/status",
                                    {"x-apisports-key": key}, cost=1, remaining_header="x-ratelimit-requests-remaining")
            resp = (body or {}).get("response") or {}
            req, sub = resp.get("requests") or {}, resp.get("subscription") or {}
            limit, current = int(req.get("limit_day") or 0), int(req.get("current") or 0)
            need = pinned["quota"]["daily_budget_all_consumers"] + pinned["quota"]["provider_min_remaining"]
            _check(results, "3_provider_quota", bool(sub.get("active")) and limit >= need
                   and limit - current >= pinned["quota"]["per_cycle_cap"] + pinned["quota"]["provider_min_remaining"],
                   plan=sub.get("plan"), active=sub.get("active"), limit_day=limit, used_today_provider=current,
                   needed_daily=need)
        except Exception as exc:
            _check(results, "3_provider_quota", False, error=f"{type(exc).__name__}: {exc}"[:300])
    else:
        _check(results, "3_provider_quota", None, note="run with --check-provider (1 counted request)")

    shutil.rmtree(scratch, ignore_errors=True)
    _write(a.out, results)
    rest = [r for r in results if not (a.offline and r["check"] == "3_provider_quota")]
    return 0 if all(r["status"] == "pass" for r in rest) else 1


def _write(out, results):
    results.sort(key=lambda r: (int(r["check"].split("_")[0].rstrip("abc")), r["check"]))
    doc = {"at_utc": datetime.now(timezone.utc).isoformat(), "all_passed": all(r["status"] == "pass" for r in results),
           "checks": results}
    text = json.dumps(doc, indent=1, default=str)
    if out:
        Path(out).write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    sys.exit(main())

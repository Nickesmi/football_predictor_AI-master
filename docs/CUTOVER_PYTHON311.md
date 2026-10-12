# Runbook — Python 3.11 cutover and frozen-baseline activation

Status: **prepared, not executed** (M2 package D). Everything below is an owner action at M3.
Verified end to end on a scratch clone with a copy of the production DB (cutover, tests, smoke test, rollback).

## 0. What runs today (verified 2026-10-12)

| Process | PID | Started | Launched by | Notes |
|---|---|---|---|---|
| Football API `uvicorn api.main:app --port 8001` (cwd = this repo) | 94530 | 2026-10-11 18:52:16 | **Antigravity IDE agent** (parent: its language server) | Python 3.13 `.venv`; loaded the M2 working tree as it was at 18:52 (Package A fixes, plus Antigravity's removal of `suggested_pick`); no `--reload` |
| `uvicorn api.main:app` (cwd = `/Users/nikantehrani/cryptopredic`) | 65948 | 2026-09-21 | — | **not this project** (earlier reports called it the football app; that was wrong) |

## 1. Python 3.11 cutover

Prerequisites: `python3.11` installed (`brew install python@3.11`), work committed, an agreed maintenance window.

1. Stop the football API (pid above; e.g. Ctrl-C in its terminal or close the IDE task that started it) and any football workers. The scripts never stop services.
2. Dry run — checks and plan, changes nothing:
   `scripts/orchestration/cutover_python311.sh`
3. Execute:
   `scripts/orchestration/cutover_python311.sh --execute`
   It backs up `data/engine.db` (online `.backup`, integrity check, SHA-256) to `~/Desktop/football_predictor_backups/cutover_<stamp>/`, keeps the current env as `.venv.py313.<stamp>`, rebuilds `.venv` on 3.11 from `requirements-lock.txt`, runs `pip check`, the full test suite and the integrity audit, then smoke-tests a temporary API on port 8099 against a **copy** of the backup (`/api/health`, `/api/baseline/status`).
4. Start the app as usual (`./run_web.command`). The launcher preflight prints `Python runtime: .venv/bin/python (3.11)`.
5. Live smoke test: open the dashboard; `curl -s 127.0.0.1:8001/api/health`; `curl -s 127.0.0.1:8001/api/baseline/status` → `{"frozen": false, ...}`.

Expected visible difference after cutover: one display average in match analysis can differ in the last decimal (Python 3.11 vs 3.13 float summation, KNOWN_ISSUES K20). Probabilities used for picks are unaffected (golden references for both runtimes are maintained).

Launchers, crontab and systemd units all use `.venv/bin/python`, so no path changes are needed. `build_desktop.sh` uses its own `.venv-desktop` and no longer touches `.venv`.

### Rollback
1. Stop the API and workers.
2. `scripts/orchestration/rollback_python311.sh <stamp>` — restores `.venv.py313.<stamp>` as `.venv`; the 3.11 env is kept as `.venv.py311.failed.<time>`.
3. Only if the database itself must be restored (not expected — the cutover does not write to it): with the app stopped, copy `cutover_<stamp>/engine.db` over `data/engine.db` and delete `data/engine.db-wal` / `-shm`. This discards anything written after the backup.

## 2. Frozen-baseline activation (after cutover, separate approval)

1. Pre-checks: Package B behaviour approved (synthetic XGB weight 0, blind-team data quality); BILQE all-tier-C behaviour reviewed (K12); app and workers stopped; `git status` clean (otherwise picks record `code_revision …+dirty`).
2. Back up the DB (as in §1.3, or run the cutover dry run's backup step manually).
3. Freeze (from a shell **without** `BASELINE_FREEZE` set):
   `.venv/bin/python scripts/freeze_baseline.py --version football-v1.0 --approved-by "<owner>"`
   It snapshots calibration models, league adjustments, gate statistics, BILQE history inputs and the pick calibrator, writes `models/baseline/football-v1.0/manifest.json`, registers it in the DB, and prints the versions and an integrity check. It refuses to overwrite an existing version.
4. Commit the manifest (`models/baseline/football-v1.0/manifest.json`; already whitelisted in `.gitignore`).
5. Activate: add `BASELINE_FREEZE=football-v1.0` to `.env` (read by the API and every worker; also uncomment it in `crontab.txt` / systemd if those are used).
6. Start the app. Startup log: `Football baseline football-v1.0: integrity OK {...}`. `curl -s 127.0.0.1:8001/api/baseline/status` → `"frozen": true, "ok": true`.
7. Then install the scheduler / activate F1 as separately approved.

While frozen: nightly refits and league-profile updates go to `*_shadow` tables; `POST /api/admin/retrain-engine` returns 409; any change to model/calibration/selection code, model artifacts, frozen data, `league_profiles` or the runtime makes `run_pipeline` refuse official picks (`baseline_integrity_error`) until a new baseline version is approved.

### Deactivation / rollback of activation
Remove `BASELINE_FREEZE` from `.env` and restart: behaviour returns to unfrozen (frozen and shadow tables are kept; picks already stored keep their provenance). Baseline versions are immutable; a corrected baseline is a new version (e.g. `football-v1.1`).

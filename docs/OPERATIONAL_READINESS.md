# Operational Readiness (M2.5)

Observed read-only on 2026-10-12/13. Nothing was stopped, restarted or reconfigured.

## 1. Running processes that touch this repository

| PID | What | Started | Parent | Notes |
|---|---|---|---|---|
| **94530** | `Python 3.13 -m uvicorn api.main:app --host 127.0.0.1 --port 8001` (Homebrew 3.13.2 framework binary with `.venv` site-packages) | 2026-10-11 18:52:16 | 36411, the Antigravity IDE language server | cwd = repo root; no `--reload`; `GET /api/health` 200 |
| 46155 → 46214 | `run_web.command` → Vite dev server on 127.0.0.1:5173 | 2026-10-10 22:52 | 36411 | Frontend only; no DB access |
| 36411 | Antigravity IDE language server | 2026-10-10 22:39 | Antigravity app (36378) | launched both of the above |
| 36491, 36778, 36856, 36857 | Antigravity plugin helpers and pyrefly | — | Antigravity | cwd = repo; editor tooling |
| — | Codex and Cursor are also running | — | — | no file changes observed since M2 |

(The earlier pid 65948 belongs to `/Users/nikantehrani/cryptopredic`, not this project.)

### PID 94530 in detail

- **Launched by an Antigravity AI agent session.** Its environment holds `ANTIGRAVITY_AGENT`, `ANTIGRAVITY_CONVERSATION_ID` and `ANTIGRAVITY_TRAJECTORY_ID` (names only inspected; values not recorded).
- No `APP_ENV`, `BASELINE_FREEZE` or provider keys are in its process environment. `src/config.py` loads `.env` at import, and `.env` defines only API-Football/RapidAPI keys and hosts. So:
  - `APP_ENV` = development;
  - `BASELINE_FREEZE` is unset (not frozen);
  - `ADMIN_API_KEY` is unset (admin API returns 503).
- **Code version:** the M2 working tree as it was at 18:52 on Oct 11 (Package A plus Antigravity's `suggested_pick` removal). It predates M2 C/D and all M2.5 fixes. `GET /api/baseline/status` returns 404, which confirms that.
  - Modules it imports lazily may come from newer on-disk code. Mixed versions are possible.
  - The process still has the defects M2.5 fixed:
    - BILQE reliability is always 50;
    - no-odds picks pass Layer 4;
    - results verification can record 33/33/33;
    - crowd-vote pseudo-odds.
- **DB connections:** `data/engine.db`, `-wal` and `-shm` are open read-write (fds 17–19). TCP LISTEN on 127.0.0.1:8001.
- **Background tasks** (`api/services/system_service.start_background_tasks`):
  1. **Nightly retrain at 03:00 local.** It last ran 2026-10-12 03:13 local (00:13 UTC). It rebuilt `model_confidence_adjustments` and wrote `calibration_models` (1,013 rows), sourced from a `prediction_errors` table that is 96 % fabricated (see `BILQE_DIAGNOSTIC_REPORT.md` §6). **It will run again every night until the process is stopped.**
  2. Live-score broadcast every 15 s.
  3. Daily fixture refresh (API-Football / cache).
- Live DB state: `picks` 0; `odds_snapshots` 0; schema identical to the pre-M0 backup (the gate check runs before every commit).

### Auto-restart behaviour

- There is no launchd agent, crontab entry or systemd unit for this project (`launchctl list`, `~/Library/LaunchAgents`, `crontab -l` checked). The repo's `scripts/orchestration/crontab.txt` and `systemd/` are templates and are not installed.
- The process restarts only if something re-runs the command. The likely source is the Antigravity agent session that started it, if that conversation resumes. The Antigravity language server itself does not supervise it.

## 2. Safe shutdown procedure (needs owner approval to run)

1. **Stop new launches.** In Antigravity, end or close the agent conversation that started the API, or quit Antigravity. That also ends the Vite server.
2. **Avoid the 03:00 refit window and any write burst.** Check that no settlement or refresh is running:
   ```bash
   sqlite3 "file:data/engine.db?mode=ro" "SELECT MAX(created_at) FROM prediction_errors"
   ```
3. **Back up first:**
   ```bash
   sqlite3 "file:data/engine.db?mode=ro" ".backup $HOME/Desktop/football_predictor_backups/pre_m3_$(date +%Y%m%d_%H%M).db"
   ```
   Then `PRAGMA integrity_check` on the backup.
4. **Stop gracefully:** `kill -TERM 94530`. Uvicorn finishes in-flight requests and runs the lifespan shutdown. Committed transactions are safe: SQLite replays any un-checkpointed WAL content the next time the database is opened. Wait until the process exits:
   ```bash
   ps -p 94530
   ```
   Use `kill -KILL` only if it hangs for more than 30 s. The WAL makes that crash-safe but loses in-flight work.
5. **Verify:**
   ```bash
   lsof data/engine.db        # must print nothing
   sqlite3 data/engine.db "PRAGMA integrity_check"
   ```
   Confirm port 8001 is free.
6. Record the stop time. Every later restart must be deliberate and must use the procedure below.

## 3. Controlled restart procedure

Decide these first (M3 decisions):
- runtime: 3.11 per `docs/CUTOVER_PYTHON311.md`, or 3.13 temporarily;
- whether `BASELINE_FREEZE` is set;
- `ODDS_REQUEST_BUDGET_*` and `ODDS_MAX_AGE_MINUTES`;
- what to do about the fabricated outcome rows;
- `APP_ENV` and `ADMIN_API_KEY`.

1. Check out the approved commit on a clean tree (`git status` empty). Run `scripts/orchestration/check_runtime.sh` (runtime preflight; it refuses a frozen baseline on a non-3.11 interpreter).
2. Rehearse on a **copy** of the backup:
   ```bash
   FOOTBALL_PREDICTOR_DB_PATH=<copy> python -c "from src.db.database import get_db; get_db()"
   ```
   Confirm the new tables are added additively and no rows change. The migration test does exactly this: `M0_EXISTING_DB_COPY=<copy> pytest tests/test_schema_migrations.py`.
3. On the first start, `init_db` will **add** these tables to the live DB:
   - `baseline_*`, `*_frozen`, `*_shadow`;
   - `odds_quotes`, `crowd_vote_signals`, `fixture_identity`, `provider_quota_log`;
   - `selection_decisions`;
   - provenance columns on `picks`.

   This is the first live-schema change since M0 and needs explicit approval.
4. If freezing: run `scripts/freeze_baseline.py` against the cleaned DB, set `BASELINE_FREEZE`, and confirm `GET /api/baseline/status` shows `ok: true`. With the freeze set, the nightly refit runs in shadow mode only.
5. Start with the launcher (`run_web.command` or `scripts/orchestration/cutover_python311.sh`), not from an IDE agent. Confirm:
   ```bash
   lsof -nP -iTCP:8001 -sTCP:LISTEN
   ```
   Then check `/api/health` and `/api/baseline/status`.
6. Run `python scripts/odds_worker.py --dry-run` and review the planned quota cost before setting any budget.
7. Rollback: stop the process, then either restore the backup from step 3 of §2 or keep the additive tables (harmless to old code). Old code ignores them. `scripts/orchestration/rollback_python311.sh` handles the runtime.

## 4. Readiness checklist (M2.5 status)

| Item | Status |
|---|---|
| Tests isolated from live DB and network | Yes (temp DB, temp HOME, network guard, blank keys) |
| Python 3.11 suite | 609 passed, 1 skipped |
| Python 3.13 suite | 608 passed, 2 skipped |
| Golden (v3-m2.5) | 9/9 on 3.11 and 3.13 |
| Migrations fresh + real-DB copy | 4/4 |
| Integrity audit | PASS, 0 weak tests |
| Frontend lint and build | PASS |
| Linux CI-equivalent (python:3.11-slim amd64) | 591 passed, 19 env-skips; golden 9/9 |
| Live DB untouched by M2.5 | Yes (schema == pre-M0; picks 0) |
| Running app on reviewed code | **No** (pid 94530, pre-M2 C) |
| Scheduler installed | No |
| Real odds flowing | No (no budget set; API-Football verified available) |

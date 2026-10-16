# M3 Cutover Runbook — Python 3.11, data remediation, controlled restart

Supersedes the operational parts of `docs/CUTOVER_PYTHON311.md` and `docs/OPERATIONAL_READINESS.md` §2–3 for M3. **Every step that changes production needs explicit owner approval.** Nothing below has been executed on production, except the items listed in §0.

## 0. Current state (2026-10-16)

| Item | State |
|---|---|
| Football API | **Stopped** 2026-10-16 15:45:56 +03. Old pid 94530 was `Python 3.13 -m uvicorn api.main:app --port 8001`, cwd = repo, parent = Antigravity language server 36411. SIGTERM; it exited in 2 s; port 8001 free; no handles on `data/engine.db`. |
| Live DB | Untouched by M3. After the stop, its `.dump` hash equals the backup's and `integrity_check` is ok. The un-checkpointed WAL (86 MB) is still beside it; SQLite replays it on the next read-write open. |
| Backup | `~/Desktop/football_predictor_backups/m3_2026-10-16/engine_pre_m3.db`, SHA-256 `37a98bb6…6809ad6`, 290,793 rows. Integrity ok, FK violations 0, table counts equal to the source, full test restore verified. |
| Service hold | `data/SERVICE_HOLD.json`, `hold_id = m3-hold-20261016-401a4675` (copy in the backup folder). While it exists, `get_db()` refuses `data/engine.db` and the API exits at import, unless `FOOTBALL_SERVICE_HOLD_RELEASE` equals the hold id. Verified: `uvicorn api.main:app` exits in 1 s and leaves the DB untouched. |
| Still running, not touched | Vite dev server (pids 46155/46214, port 5173; frontend only, no DB access; its API calls fail while the API is down). Antigravity IDE (36378/36411). `cryptopredic` uvicorn 65948 (port 8000; another project). |
| Auto-restart sources | No launchd agent, crontab or systemd unit for this project. The only restart path seen is an Antigravity agent session re-running the command. **The hold blocks that path.** |
| Scheduler | Not installed. Templates exist in `scripts/orchestration/shadow/`. |

## 1. Rollback strategy (data)

- **Never overwrite the live DB with the backup once new legitimate data exists.** Since the stop, nothing has written to it, so today a full restore and the current file are equivalent.
- After any future production run, roll back **per change** instead:
  - **Evidence remediation:** `evidence.restore_quarantined(conn, table)` reinserts quarantined rows with their original PKs and refuses to overwrite a different row. The original artifacts stay in `*_pre_m3` tables. Restore them with `DELETE FROM model_confidence_adjustments; INSERT INTO model_confidence_adjustments SELECT * FROM model_confidence_adjustments_pre_m3;` (same for `calibration_models`), with the app stopped.
  - **Additive schema** (M2.5/M3 tables and columns): harmless to old code. Rehearsed: M2 code `632f8d7` served all smoke endpoints on the migrated copy with 0 tracebacks. Leave them in place.
  - **Full restore** (last resort, only while no new data exists): stop the app; copy the backup over `data/engine.db`; delete `data/engine.db-wal` and `-shm`. This discards everything written after 15:45:56 on 2026-10-16.

## 2. Preconditions for the cutover (all need approval)

1. M3 branch reviewed and merged or tagged by the owner. Working tree clean (`git status` empty), so provenance does not carry `+dirty`.
2. Data remediation reviewed (`DATA_INTEGRITY_REMEDIATION.md` §6–8), including the BILQE side-effect and the isotonic change.
3. Decision on `APP_ENV` (`production` needs `ADMIN_API_KEY` ≥ 32 characters and an explicit `CORS_ALLOW_ORIGINS`; verified).
4. Decision on whether the nightly refit should run before the remediation is applied. **Recommendation:** apply the remediation first, otherwise the first 03:00 refit rebuilds the fabricated artifacts.
5. `BASELINE_FREEZE` stays **unset**. The official baseline is not activated by this runbook.

## 3. Procedure

1. **Fresh backup** (app stopped, hold in place):
   ```bash
   B=~/Desktop/football_predictor_backups/cutover_$(date +%Y%m%d_%H%M); mkdir -p "$B"
   .venv311/bin/python ~/Desktop/football_predictor_backups/m3_2026-10-16/online_backup.py data/engine.db "$B/engine.db" > "$B/verify.json"
   shasum -a 256 "$B/engine.db" > "$B/engine.db.sha256"
   ```
   Check that `integrity` is `ok` and `match` is `true`.
2. **Apply the approved data remediation** (separate approval; §8 of the remediation doc). Verify:
   - `evidence_quarantine` holds 120,896 rows;
   - `prediction_errors` holds 184 rows;
   - `model_confidence_adjustments` holds 3 rows, none active;
   - `calibration_models` is empty;
   - `integrity_check` is ok.
3. **Python 3.11 runtime:** `scripts/orchestration/cutover_python311.sh` (dry run), then `--execute`. It keeps the 3.13 env as `.venv.py313.<stamp>`. `.venv311` already exists and passed the suite (698 passed, 1 skipped).
4. **Schema migration** happens at the first read-write open. It is additive (11 tables, picks provenance columns, 2 `selection_decisions` columns). Rehearsed on a restored copy: no pre-existing table's content changed, nothing was removed, and `tests/test_schema_migrations.py` against the real copy passed 4/4 on 3.11 and 3.13.
5. **Start deliberately** from a terminal, not from an IDE agent:
   ```bash
   FOOTBALL_SERVICE_HOLD_RELEASE=m3-hold-20261016-401a4675 ./run_web.command
   ```
   Remove `data/SERVICE_HOLD.json` only when the owner wants unattended restarts to be possible again.
6. **Smoke tests** (all rehearsed on 3.11 against a migrated copy: 10/10 HTTP 200, 0 tracebacks):
   ```bash
   for ep in /api/health /api/baseline/status /api/fixtures/$(date +%F) /api/leagues \
             /api/calibration/isotonic/status /api/performance/overview; do
     echo "$ep $(curl -s -o /dev/null -w '%{http_code}' 127.0.0.1:8001$ep)"; done
   curl -s -H "X-API-Key: $ADMIN_API_KEY" 127.0.0.1:8001/api/debug/odds-integrity
   curl -s -H "X-API-Key: $ADMIN_API_KEY" 127.0.0.1:8001/api/debug/pending-settlements
   ```
   `/api/baseline/status` must return `{"frozen": false, ...}`. **A restart does not activate the baseline.**
7. **Odds:**
   - `python scripts/odds_worker.py --provider api_football --map-fixtures --dry-run` (plan only).
   - Set `ODDS_REQUEST_BUDGET_API_FOOTBALL` only after approval. The free plan has 100/day, shared with the fixture feeds.
8. **Settlement:** an offline smoke test was rehearsed (`auto_settle_picks` settled a test pick: won, +1.2 u). The settlement worker needs network access.

## 4. Background work that starts with the API (verified in the 3.11 rehearsal log)

| Task | Schedule | Notes |
|---|---|---|
| Nightly retrain | 03:00 local | Unfrozen: rebuilds `model_confidence_adjustments` and `calibration_models` from `prediction_errors` / `prediction_log`. Frozen: writes the shadow tables only. |
| Fixture auto-refresh | hourly at HH:05 | Uses API-Football (spends quota) |
| Live-score broadcast | every 15 s | WebSocket |

External workers (`scripts/*_worker.py`) and the shadow cycle are not started by the API. They are scheduled only by an installed cron/launchd job, and none is installed.

## 5. Shadow validation scheduling (after approval)

```bash
# isolated shadow DB (recommended): copy of the remediated candidate
cp ~/Desktop/football_predictor_backups/m3_2026-10-16/shadow/engine_shadow.db <shadow path>   # or continue the existing one
# install ONE of:
#   scripts/orchestration/shadow/com.footballpredictor.shadow.plist   (launchd, every 30 min)
#   scripts/orchestration/shadow/crontab.txt                          (cron)
.venv/bin/python scripts/shadow_report.py --db <shadow path> --out logs/shadow_report.json --check-stale-minutes 90
```
Budget: one decision fetch per tracked fixture plus discovery comes to about 25–60 requests/day. Weekends can exceed what is left of the free 100/day after the API's own fixture refresh. If both run, keep the shadow budget at or below 40, or run the API without its auto-refresh.

## 6. Rollback of the runtime

`scripts/orchestration/rollback_python311.sh <stamp>` restores the 3.13 env. The data rollback is in §1.

## 7. Linux check

The Linux CI-equivalent run (python:3.11-slim) is **BLOCKED** in M3: the Docker daemon was not available (Docker Desktop UI running, socket absent, no recovery in 2 minutes). Run it before the cutover:
```bash
docker run --rm --platform linux/amd64 -v "$PWD":/src -w /src python:3.11-slim \
  bash -c "pip install -q -r requirements-lock.txt && python -m pytest tests -q -p no:cacheprovider"
```

# Known Issues

Status as of M1.1 (2026-10-11). Severity: P0 blocks the baseline experiment, P1 must be fixed before production use, P2 hygiene. "Gov" = changes production behaviour and needs owner approval (`baseline_contract.md`).

## Open

| ID | Sev | Issue | Where | Planned |
|---|---|---|---|---|
| K1 | P0 Gov | Synthetic-trained XGBoost is blended into live 1X2 / O/U 2.5 / BTTS at weight 0.10–0.25 when data quality ≥ 45 | `src/engine/probability_engine.py:91` | M2 (tests ready in `tests/pending_m2/`) |
| K2 | P0 Gov | Hash-fallback (unknown) teams report `matches_played=20`, inflating data quality | `src/ml/team_stats_db.py` Priority 4 | M2 (tests ready in `tests/pending_m2/`) |
| K5 | P0 Gov | Automatic recalibration/selection drift (pathways P1–P9) | `docs/BASELINE_FREEZE_DESIGN.md` | owner approved freeze policy; implementation = M2 package C |
| K20 | P1 | Outputs depend on the Python minor version (`sum()` compensated from 3.12): one golden value differs 3.11 vs 3.13. 3.11 is canonical; references versioned, divergence pinned by test. Running app still on 3.13 | `match_analysis_service.py:1372,1425` | cutover to 3.11 in M3 (M2 package D) |
| K10 | P1 | No real CLV anywhere; `odds_snapshots` empty | settlement path | M4 |
| K11 | P1 | Football baseline not started (`picks` 0/1,000); scheduler not installed | crontab / F1 | M3 (owner decision) |
| K12 | P1 | BILQE rejects everything observed so far: all `qualification_log` rows tier C | `qualification_engine.py` | diagnose before M3 |
| K24 | P1 | `upsert_competition` not imported in `fixtures_service.py:986` → competition tracking fails on every fixture load (3,944 log entries since 2026-07-05) | `api/services/fixtures_service.py` | M2 package A1 |
| K25 | P1 | Auto-refresh pre-warm imports nonexistent `api.services.prediction_service` (since 26a8ba4) → predictions never pre-warmed | `api/services/system_service.py:193` | M2 package A2 |
| K26 | P2 | Admin `/api/debug/date-trace`, `/date-validation` use undefined `_api_football_to_fixture` → 500 | `api/services/debug_service.py:512,576` | M2 package A3 |
| K27 | P2 | Dead module `src/engine/classic_prediction.py` imports nonexistent `src.db.daily_repo` (not imported anywhere) | — | documented |
| K13 | P2 | `/api/debug/backtest-features` uses current team state (look-ahead); carries an explicit warning | `api/services/debug_service.py` | M8 |
| K15 | P2 | `.gitignore` ignores `*.json`; new JSON artifacts are silently untracked unless whitelisted (`.devcontainer/devcontainer.json`, `model_weights.json` are tracked-but-ignored) | `.gitignore` | narrow the pattern |
| K17 | P2 | `suggested_pick` is randomly shuffled on every request | `match_analysis_service.py:1436` | product decision |
| K18 | P2 | Phase 2 report tables are stale vs committed `backtest_results.json` | `REAL_DATA_BACKTEST_REPORT.md` | regenerate |
| K19 | P2 | Running `uvicorn` (pid 65948, started 2026-09-21) predates M0/M1 code; its logged errors are K24/K25 and exist in current code too | runtime | restart at M3 cutover (restart also activates F10 for the next refit, P1) |
| K22 | P2 | Tracked `football_predictor_ai-master.zip` (4.2 MB) contains a 2026-03 snapshot DB (no credentials found) | repo root | owner decision |
| K23 | P2 | Several admin GET endpoints have side effects (e.g. `/api/debug/settle-date/{d}`) | `api/routers/debug.py` | convert to POST later |

## Resolved in M1 / M1.1

| ID | Resolution |
|---|---|
| K3 | No hard-coded admin key; admin API disabled (503) until `ADMIN_API_KEY` is set; constant-time compare; non-development startup refuses keys < 32 chars |
| K4 | CORS from `CORS_ALLOW_ORIGINS`; development default = localhost/127.0.0.1 on the web port; `*` rejected; non-development requires explicit origins |
| K6 | API-compat tests use offline provider fakes (`tests/fakes/providers.py`); no quota used |
| K7 | Tests run on a temp DB, temp HOME, blank provider keys; a session check refuses the production DB path |
| K8 | 9 weak assertions replaced by exact expectations; integrity audit passes |
| K9 | Python 3.11 env (`.venv311`), pinned `requirements.txt`, full `requirements-lock.txt` (verified to reproduce) |
| K14 | Frontend lint: 13 → 0 errors |
| K21 | CI installs `requirements-lock.txt` on 3.11, runs integrity audit and a frontend lint/build job; verified in clean checkout and in Linux amd64 containers (M1.1) |
| K16 | Research `predictions.db` path overridable (`FOOTBALL_PREDICTOR_SNAPSHOT_DB_PATH`); tests and their subprocesses use a temp file |

# Known Issues

Status as of M1 (2026-10-11). Severity: P0 blocks the baseline experiment, P1 must be fixed before production use, P2 hygiene. "Gov" = changes production behaviour and needs owner approval (`baseline_contract.md`).

## Open

| ID | Sev | Issue | Where | Planned |
|---|---|---|---|---|
| K1 | P0 Gov | Synthetic-trained XGBoost is blended into live 1X2 / O/U 2.5 / BTTS at weight 0.10–0.25 when data quality ≥ 45 | `src/engine/probability_engine.py:91` | M2 (tests ready in `tests/pending_m2/`) |
| K2 | P0 Gov | Hash-fallback (unknown) teams report `matches_played=20`, inflating data quality | `src/ml/team_stats_db.py` Priority 4 | M2 (tests ready in `tests/pending_m2/`) |
| K5 | P0 Gov | Automatic recalibration changes production probabilities: nightly isotonic refit + league adjustments (1X2 drifts nightly today), and per-run pick calibrator once picks settle | see `docs/NIGHTLY_REFIT_REVIEW.md` | decide before M3 |
| K20 | P1 Gov | Outputs depend on the Python minor version: built-in `sum()` over floats is compensated from 3.12, so a value on a rounding boundary differs (golden: one category `avg_probability` 82.2 on 3.13 vs 82.1 on 3.11). Running app uses 3.13; declared runtime is 3.11 | `match_analysis_service.py:1372,1425` (+ any float `sum`) | owner: pick canonical runtime, or approve `math.fsum` (keeps 3.13 values, makes 3.11 identical) |
| K10 | P1 | No real CLV anywhere; `odds_snapshots` empty | settlement path | M4 |
| K11 | P1 | Football baseline not started (`picks` 0/1,000); scheduler not installed | crontab / F1 | M3 (owner decision) |
| K12 | P1 | BILQE rejects everything observed so far: all `qualification_log` rows tier C | `qualification_engine.py` | diagnose before M3 |
| K21 | P1 | CI workflow still installs `requirements.txt` (now pinned) on Python 3.11 → golden test would fail there (K20) | `.github/workflows/ci.yml` | after K20 decision |
| K13 | P2 | `/api/debug/backtest-features` uses current team state (look-ahead); carries an explicit warning | `api/services/debug_service.py` | M8 |
| K15 | P2 | `.gitignore` ignores `*.json`; new JSON artifacts are silently untracked unless whitelisted (`.devcontainer/devcontainer.json`, `model_weights.json` are tracked-but-ignored) | `.gitignore` | narrow the pattern |
| K17 | P2 | `suggested_pick` is randomly shuffled on every request | `match_analysis_service.py:1436` | product decision |
| K18 | P2 | Phase 2 report tables are stale vs committed `backtest_results.json` | `REAL_DATA_BACKTEST_REPORT.md` | regenerate |
| K19 | P2 | Running `uvicorn` (pid 65948, started 2026-09-21) predates M0/M1 code; it also logs `Pre-warm skipped … No module named 'api.services.predicti…'` and `name 'upsert_competition' is not defined` | runtime | owner restarts when ready (see K5: restart activates F10 for the next refit) |
| K22 | P2 | Tracked `football_predictor_ai-master.zip` (4.2 MB) contains a 2026-03 snapshot DB (no credentials found) | repo root | owner decision |
| K23 | P2 | Several admin GET endpoints have side effects (e.g. `/api/debug/settle-date/{d}`) | `api/routers/debug.py` | convert to POST later |

## Resolved in M1

| ID | Resolution |
|---|---|
| K3 | No hard-coded admin key; admin API disabled (503) until `ADMIN_API_KEY` is set; constant-time compare; non-development startup refuses keys < 32 chars |
| K4 | CORS from `CORS_ALLOW_ORIGINS`; development default = localhost/127.0.0.1 on the web port; `*` rejected; non-development requires explicit origins |
| K6 | API-compat tests use offline provider fakes (`tests/fakes/providers.py`); no quota used |
| K7 | Tests run on a temp DB, temp HOME, blank provider keys; a session check refuses the production DB path |
| K8 | 9 weak assertions replaced by exact expectations; integrity audit passes |
| K9 | Python 3.11 env (`.venv311`), pinned `requirements.txt`, full `requirements-lock.txt` (verified to reproduce) |
| K14 | Frontend lint: 13 → 0 errors |
| K16 | Research `predictions.db` path overridable (`FOOTBALL_PREDICTOR_SNAPSHOT_DB_PATH`); tests and their subprocesses use a temp file |

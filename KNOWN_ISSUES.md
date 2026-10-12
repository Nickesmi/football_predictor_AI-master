# Known Issues

Status as of M2 (2026-10-12). Severity: P0 blocks the baseline experiment, P1 must be fixed before production use, P2 hygiene. "Gov" = changes production behaviour and needs owner approval (`baseline_contract.md`).

## Open

| ID | Sev | Issue | Where | Planned |
|---|---|---|---|---|
| K5 | P0 Gov | Automatic recalibration/selection drift (P1–P9, P18) still active until the baseline is frozen | `docs/BASELINE_FREEZE_DESIGN.md` §4 | implemented (M2 C); activate at M3 (`docs/CUTOVER_PYTHON311.md` §2) |
| K20 | P1 | Outputs depend on the Python minor version (`sum()` compensated from 3.12): one golden value differs 3.11 vs 3.13. 3.11 is canonical; references versioned, divergence pinned by test. Running app still on 3.13 | `match_analysis_service.py:1372,1425` | cutover prepared (`docs/CUTOVER_PYTHON311.md`); execute at M3 |
| K10 | P1 | No real CLV anywhere; `odds_snapshots` empty (vote odds were never stored because of K31) | settlement path | M4 |
| K11 | P1 | Football baseline not started (`picks` 0/1,000); scheduler not installed | crontab / F1 | M3 (owner decision) |
| K12 | P1 | BILQE rejects everything observed so far: all `qualification_log` rows tier C | `qualification_engine.py` | diagnose before M3 |
| K27 | P2 | Dead module `src/engine/classic_prediction.py` imports nonexistent `src.db.daily_repo` (not imported anywhere); allow-listed in the static import guard | — | documented |
| K28 | P0 | **Football API pid 94530 was started by the Antigravity IDE agent at 2026-10-11 18:52 from the M2 working tree** (Package A + Antigravity's `suggested_pick` removal, no reload). It writes `data/engine.db`; modules it imports lazily may come from newer on-disk code | runtime | owner: stop it; restart deliberately after M2 review (see M2 report) |
| K29 | P2 | Research pickles in `models/real_data/` were saved with scikit-learn 1.9.0; the lock pins 1.8.0 (`InconsistentVersionWarning`); research/shadow only | `models/real_data/*_calibrator.pkl` | re-export or pin at research time |
| K30 | P2 | Golden v2-m2 verified on macOS 3.11/3.13; not re-run in a Linux container in M2 (v1 showed no platform drift in M1.1) | CI | confirm on first CI run |
| K13 | P2 | `/api/debug/backtest-features` uses current team state (look-ahead); carries an explicit warning | `api/services/debug_service.py` | M8 |
| K15 | P2 | `.gitignore` ignores `*.json`; new JSON artifacts are silently untracked unless whitelisted (`.devcontainer/devcontainer.json`, `model_weights.json` are tracked-but-ignored) | `.gitignore` | narrow the pattern |
| K17 | P2 | `suggested_pick` is randomly shuffled on every request | `match_analysis_service.py:1436` | product decision |
| K18 | P2 | Phase 2 report tables are stale vs committed `backtest_results.json` | `REAL_DATA_BACKTEST_REPORT.md` | regenerate |
| K22 | P2 | Tracked `football_predictor_ai-master.zip` (4.2 MB) contains a 2026-03 snapshot DB (no credentials found) | repo root | owner decision |
| K23 | P2 | Several admin GET endpoints have side effects (e.g. `/api/debug/settle-date/{d}`) | `api/routers/debug.py` | convert to POST later |

## Resolved in M1 / M1.1 / M2

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
| K1 | M2 B: synthetic XGBoost weight 0 in `estimate_probabilities`, removed from BILQE consensus and from the live-odds hybrid blend |
| K2 | M2 B: hash-fallback teams report `matches_played=0`; downstream data quality and low-data safeguards apply |
| K19 | Corrected: pid 65948 is `/Users/nikantehrani/cryptopredic`, not this project (see K28) |
| K24–K26 | M2 A: missing imports fixed (competition tracking, pre-warm, admin date endpoints, logo audit, audit_engine league map) |
| K31 | M2 A follow-up: vote-derived odds snapshots crashed on insert (missing `implied_probability`) since 26a8ba4; fixed |


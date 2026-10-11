# Changelog

## M1.1 — stabilization (2026-10-11, `m1.1/stabilization`)

No change to prediction outputs or calibration behaviour.

- Python 3.11 is the canonical runtime: golden references versioned per runtime (`golden_probabilities.py311.json` canonical, `py313` kept byte-identical); a test pins the only allowed difference.
- CI installs `requirements-lock.txt`, runs the integrity audit via `python`, adds a frontend lint/build job; devcontainer installs the lock; README setup/validation match CI.
- Docs: `BASELINE_FREEZE_DESIGN.md` (17 mutation pathways + freeze architecture), `M2_PLAN.md`, `M1_1_REPORT.md`.

## M1 — security, reproducibility, test infrastructure (2026-10-11, `m1/security-reproducibility`)

No change to prediction outputs (golden test identical on Python 3.13).

### Security
- No default admin key; admin API returns 503 until `ADMIN_API_KEY` is set; constant-time comparison.
- `APP_ENV`; outside development the API refuses to start without a ≥32-char admin key and explicit `CORS_ALLOW_ORIGINS`.
- CORS restricted to configured origins (development default: localhost/127.0.0.1 web port); `*` rejected.
- `.gitignore` covers credential files, key material, backups, SQLite files, extra venvs.

### Tests
- Test session isolated from production: temp DB, temp HOME, blank provider keys, network guard failing any external call, cache/research DB redirected.
- Offline provider fakes for API-compatibility tests.
- New: `test_security_config.py`, `test_repo_hygiene.py`.
- 9 weak assertions replaced with exact expectations (integrity audit passes).

### Frontend
- 13 lint errors fixed without functional change.

### Reproducibility
- Pinned `requirements.txt`; `requirements-lock.txt` for Python 3.11; `.venv311`.
- `FOOTBALL_PREDICTOR_SNAPSHOT_DB_PATH` override for the research snapshot DB.

### Docs
- `docs/NIGHTLY_REFIT_REVIEW.md`, `docs/M1_REPORT.md`.

## M0 — consolidation (2026-10-11, `integration/m0-consolidation`)

No change to production prediction probabilities (golden test identical before and after).

### Added
- Real-data research pipeline from the audit branch (merged with history): openfootball dataset (public domain / CC0), point-in-time features, walk-forward backtest, real-data models, champion registry, market thresholds, `src/prediction_service` (fail-closed pipeline, replay, shadow mode) and tests. Not wired into the app.
- `tests/golden/`: golden-probability regression for `estimate_probabilities()` and `_compute_match_analysis()` (tolerance 1e-9).
- `tests/pending_m2/`: tests for governed changes deferred to M2 (opt-in via `RUN_PENDING_M2=1`).
- `tests/test_schema_migrations.py`.
- `data_leakage_warning` field and docstring on `/api/debug/backtest-features` (admin only).

### Fixed
- `init_db()` now creates `model_confidence_adjustments`, `prediction_errors`, `team_learning_state`, `team_rating_history` (verbatim live DDL, idempotent).
- Previously uncommitted gated-pipeline fixes (see `AUDIT_REPORT_2026-09-13.md`): unique indexes on `picks` / `qualification_log`, UTC helpers, chronological isotonic split, `void_stale_picks`, `model_source` provenance, NS/today-only pipeline guards, `qualification_log` persistence, authoritative experiment counter, one-shot settlement worker.

### Removed
- `src/engine/settlement.py` (zero callers; unimportable; replaced by `auto_settle_picks` / `void_stale_picks` via `scripts/settlement_worker.py`).
- `src/ml/calibration.py` (unused in-sample calibrator).

### Repository
- `.gitignore`: `.env.*` (except `.env.example`), `PROJECT_MASTER_REPORT.pdf`, `data/**/*.db*`; whitelist `tests/golden/*.json`.

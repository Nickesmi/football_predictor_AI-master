# M1 — Security, Reproducibility and Test Infrastructure

Date: 2026-10-11 · Branch: `m1/security-reproducibility` (from `integration/m0-consolidation` @ 8b217fd; local only).

No change to model weights, calibration mathematics, qualification thresholds, betting logic or prediction outputs: the golden test passes unchanged on the production interpreter (3.13), and no file on a probability path changed.

## Results — before (end of M0) vs after

| Check | Before | After |
|---|---|---|
| Backend pytest, Python 3.13 | 440 passed, 1 skipped — ran against a DB copy only because `FOOTBALL_PREDICTOR_DB_PATH` was set by hand; API-compat tests hit live providers | **471 passed, 1 skipped**, isolated by default, offline |
| Backend pytest, Python 3.11 | BLOCKED (not installed) | 470 passed, 1 skipped, **1 failed** (golden, K20) |
| Golden probabilities (3.13) | PASS | **PASS** |
| Migration tests incl. real DB copy | PASS | **PASS** (4/4) |
| CI integrity audit | FAIL (9 weak) | **PASS** (456 real, 0 weak) |
| Frontend lint | FAIL (13 errors) | **PASS** (0) |
| Frontend build | PASS | **PASS** |
| Pending-M2 suite | 3 FAIL (expected) | 3 FAIL (expected) |
| UI in browser | NOT RUN | NOT RUN (would proxy to the live app, which writes to the production DB) |

## Changes

### Security (items 1–3)
- `src/config.py`: `APP_ENV` (default `development`), no default admin key, `CORS_ALLOW_ORIGINS`, `validate_security_config()`.
- `api/main.py`: refuses to start when `validate_security_config()` reports problems; CORS uses the configured list.
- `api/dependencies.py`: 503 when no key configured, 401 on mismatch, `hmac.compare_digest`.
- `status_workers.command`: dropped the hard-coded key (the endpoint it calls is public).
- `.env.example`: documents `APP_ENV`, `ADMIN_API_KEY`, `CORS_ALLOW_ORIGINS`.
- `.gitignore`: `.venv*/`, `.streamlit/secrets.toml`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.bak`, `backups/`, `*.sqlite*`.
- Tests: `tests/test_security_config.py` (auth 503/401/200, CORS allow/deny/preflight, production validation matrix, startup refusal in a subprocess), `tests/test_repo_hygiene.py` (sensitive paths ignored, no literal credentials in tracked files).

**Operational note:** admin endpoints on the local app now return 503 until `ADMIN_API_KEY` is added to `.env`. No shipped client uses them (frontend, desktop and Streamlit don't send the header). The running app is unaffected until restarted.

### Test isolation and provider mocking (items 4, 8)
- `tests/conftest.py`: before any import, sets `HOME`, `FOOTBALL_PREDICTOR_DB_PATH`, `FOOTBALL_PREDICTOR_SNAPSHOT_DB_PATH` to a temp dir and blanks provider keys; asserts the DB path is not `data/engine.db`; autouse network guard (socket connect/getaddrinfo + curl_cffi) that **fails any test attempting a non-loopback call**, even when production code swallows the error; redirects the fixture cache; `@pytest.mark.external` + `RUN_EXTERNAL_TESTS=1` for future deliberate integration tests (none exist).
- `src/prediction_service/snapshot_db.py`: path overridable via `FOOTBALL_PREDICTOR_SNAPSHOT_DB_PATH` (research module; reaches the CLI subprocess test).
- `tests/fakes/providers.py`: API-Football-shaped fixture payloads; SofaScore/live-score/logo calls stubbed. API-compat fixture tests now assert exact statuses `["FT", "STALE", "NS"]` (exercises stale-live protection); image tests assert 200 + `image/png`.
- Verified: a full run creates no file under `data/` or `.cache/`.

### Weak assertions (item 5)
Each replaced by an expectation derived independently from the input data: exact window counts (6 / 6 / 6 / 3) and match-id set equality for replay/shadow/parity; field equality for the saved prediction; exact dict for the threshold; exact fixture-id set (16 = EPL 10 + Serie A 6) and true scoreline for the replay provider.

### Frontend lint (item 6) — no functional change
Removed dead code (`parseMatchDateTime`, unused `nowMs` param, unused `pollInterval` memo, unused `categories`); kept the tab-visibility state setter (renamed value `_isTabVisible`) so re-render behaviour is unchanged; dropped unused `TennisView` props; optional catch binding with comment; `TeamLogo` hoisted out of render (stateless); two pure `useMemo` computations inlined; `vite.config.js` imports `process` from `node:process`.

### Reproducibility (item 7)
- Python 3.11.17 via Homebrew; `.venv311/` (git-ignored). The running app's `.venv` (3.13) is untouched.
- `requirements.txt` pinned to the versions that produced the golden outputs (adds `scipy`, `httpx`; `streamlit==1.65.0` resolved compatible with `pandas==3.0.3`).
- `requirements-lock.txt`: full freeze; a fresh 3.11 venv from it is identical (`pip freeze` diff empty), `pip check` clean, `import api.main` OK.

Setup:
```
/opt/homebrew/bin/python3.11 -m venv .venv311
.venv311/bin/pip install -r requirements-lock.txt
.venv311/bin/python -m pytest tests -q
```

### Recalibration review (item 9)
`docs/NIGHTLY_REFIT_REVIEW.md`. Summary: the nightly league-adjustment rebuild changes 1X2 probabilities every night today; the isotonic refit is currently stable only because its input table stopped growing (and would change after an app restart, via M0 fix F10); the investment pipeline refits its calibrator on settled picks every run once picks exist. No behaviour changed.

## Decisions needed

1. **Canonical Python runtime (K20).** 3.13 (running app) and 3.11 (declared) produce one different golden value via `sum()`. Options: (a) declare 3.13 canonical — update `runtime.txt`, CI, devcontainer; (b) declare 3.11 canonical and regenerate golden (running app's displayed category average changes when it moves to 3.11); (c) approve replacing float `sum` with `math.fsum` in the two display averages — 3.13 values unchanged, 3.11 becomes identical (verified on the failing input: both give 82.15 → 82.2).
2. **Recalibration during the baseline (K5)** — options A/B/C in the review.

## Rollback
- Discard M1 entirely: `git switch integration/m0-consolidation && git branch -D m1/security-reproducibility`.
- Python 3.11 / venv: `rm -rf .venv311` and optionally `brew uninstall python@3.11`.
- M1 did not modify the live DB, `.cache` or running processes. One `import api.main` smoke check of the lock file ran with the real HOME and may have appended startup log lines to `~/.football_predictor/logs/app.log`.

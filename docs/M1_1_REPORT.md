# M1.1 — Stabilization Report

Date: 2026-10-11 · Branch: `m1.1/stabilization` (from `m1/security-reproducibility` @ 0f45898, tag `backup/m1.1/base`; local only).

Scope: Python-runtime and test-infrastructure fixes only. No change to production probability mathematics, calibration behaviour, thresholds, selection rules, the live DB or the running process.

## 1. Python 3.11 test report

| Environment | Suite | Result |
|---|---|---|
| macOS arm64, Python 3.11.17, `.venv311` | full backend | **473 passed, 1 skipped** (opt-in real-DB migration test) |
| macOS arm64, Python 3.11.17, fresh venv from `requirements-lock.txt`, **clean checkout** (no `.env`, no `data/engine.db`, no `.cache`) | full backend (CI step) | **473 passed, 1 skipped**; `pip check` clean |
| Linux amd64 container `python:3.11`, clean clone, lock | full backend (CI step) | **473 passed, 1 skipped** |
| Linux amd64 container, **`--network none`** (DNS fails inside) | full backend | **473 passed, 1 skipped** — proves no test needs an external request, including subprocess tests |
| macOS, Python 3.11, `M0_EXISTING_DB_COPY` = copy of B4 | migration tests | 4 passed |
| macOS, Python 3.11, `RUN_PENDING_M2=1` | pending M2 | 3 failed (expected: assert M2 behaviour) |
| macOS, Python 3.13.2 (`.venv`, running app's interpreter) | full backend | 473 passed, 1 skipped |

Golden probabilities: **PASS** on 3.11 (canonical reference), on Linux amd64 3.11 (same reference — no platform drift), and on 3.13 (preserved reference).

CI integrity audit: **PASS** (456 real tests, 0 weak) — macOS and Linux.
Frontend: lint **0 errors**, build **PASS** — macOS (Node 25) and Linux container (Node 20, as CI), from `npm ci`.

GitHub-hosted CI itself: NOT RUN (requires a push; not authorized). The Linux containers run the identical workflow steps.

## 2. Golden discrepancy — investigation and resolution

- Root cause (verified on the exact inputs): `round(sum(probs)/len(probs), 1)` in `match_analysis_service.py:1425` for the Arsenal–Chelsea "Team Goals" category. Python 3.11 `sum` → 82.14999999999999 → 82.1; Python 3.12+ compensated `sum` → 82.15 → 82.2. `math.fsum` gives 82.15 on both. Not a regression, not platform-dependent (Linux 3.11 = macOS 3.11).
- Resolution, without touching production code or rewriting existing expectations:
  - existing reference preserved byte-identical as `tests/golden/golden_probabilities.py313.json`;
  - new canonical `golden_probabilities.py311.json` captured on 3.11;
  - `test_runtime_references_differ_only_at_documented_paths` requires the two references to differ at **exactly** the one documented path and values (any new divergence fails);
  - an interpreter without a reference is reported as SKIPPED, never passed.

## 3. Alignment with Python 3.11
- `runtime.txt`: already `python-3.11` (unchanged).
- CI: installs `requirements-lock.txt` + `pip check`; integrity audit run via `python` (exit code honoured); new frontend job (Node 20: `npm ci`, lint, build).
- Devcontainer (3.11 image): installs the lock (was unpinned `requirements.txt` + extra `streamlit`).
- README: setup with `.venv311` + lock; validation commands = CI steps.
- Launchers/cron/systemd still use `.venv` (3.13) — switching them changes the live app's output (one display value), so it is planned as the M3 cutover (`docs/M2_PLAN.md` package D).

## 4. Isolation and external requests
Confirmed three ways: in-process guard (any non-loopback attempt fails the test), file-system check (no new files under `data/`/`.cache`), and a full run in a container with no network. Every test runs on a temp DB/HOME with blank keys.

## 5. Running application — investigated with isolated checks only
Read-only log analysis + static scan (pyflakes in a scratch venv) + import probes. The live process and DB were not touched.

| Logged error | Cause | Present in current code | Since | Impact |
|---|---|---|---|---|
| `Competition tracking failed: name 'upsert_competition' is not defined` (3,944×) | missing import in `api/services/fixtures_service.py:986` | yes | 2026-07-05 (first log entry) | `competitions` not updated from fixture loads |
| `Pre-warm skipped …: No module named 'api.services.prediction_service'` | `system_service.py:193` imports a module that was never created in the API split (26a8ba4); real function is `fixtures_service._precompute_predictions_for_date(date_str, fixtures)` | yes | API split | auto-refresh never pre-computes predictions |

Additional static findings: `/api/debug/date-trace` and `/date-validation` (admin) crash on undefined `_api_football_to_fixture`; dead module `classic_prediction.py` imports nonexistent `src.db.daily_repo`. Fixes change runtime behaviour → proposed as M2 package A, not applied.

## 6. Deliverables
- Baseline-freeze architecture and full mutation-pathway list: `docs/BASELINE_FREEZE_DESIGN.md` (17 pathways P1–P17).
- M2 implementation plan: `docs/M2_PLAN.md`.

## 7. Rollback
- Discard M1.1: `git switch m1/security-reproducibility && git branch -D m1.1/stabilization` (base tagged `backup/m1.1/base`).
- Docker: images `python:3.11` and `node:20` were pulled for verification (`docker rmi python:3.11 node:20` to remove); Docker Desktop was started for the check and quit afterwards.
- Nothing to restore in the live DB, `.cache` or running processes.

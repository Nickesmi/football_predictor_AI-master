# M0 — Repository Consolidation Report

Date: 2026-10-11 · Branch: `integration/m0-consolidation` (local only, not pushed) · Scope approved by owner.

## 1. Goal and result

Preserve all existing work, unify the tennis branch (app) and the audit branch (real-data research) on one branch, and add the missing schema — **without changing production prediction probabilities**. Result: done. The golden-probability test captured before the merge passes unchanged after it.

## 2. Starting state (verified)

| Ref | SHA | Notes |
|---|---|---|
| `tennis-prediction-engine` | c859f21 | + 21 modified, 2 deleted, 8 untracked files (uncommitted sessions 1–4 of `AUDIT_REPORT_2026-09-13.md`) |
| `origin/claude/football-prediction-audit-229luh` | f39338a | 13 commits on top of `origin/main` 15defb0 |
| `recovery-stable` | 3268fab | ancestor of `origin/main` (= tag v1.0.0); nothing to port |
| local `main` | d4ca8fe | 1 ahead / 26 behind `origin/main`; untouched |

## 3. Backups (taken and verified before any repository change)

Location: `~/Desktop/football_predictor_backups/m0_2026-10-11/` (dir mode 700, files 600, outside the repo).

| ID | Artifact | Verification |
|---|---|---|
| B1 | `B1_worktree.tar.gz` — full tree incl. `.git`, `.cache`, uncommitted/untracked files (excl. `.venv`, `venv`, `node_modules`, live DB) | Extracted to scratch: `git status` identical; SHA-256 of every changed/untracked file identical |
| B2 | `B2_all_refs.bundle` — every ref | `git bundle verify` OK; mirror-clone tip SHAs equal live refs |
| B3 | local tags `backup/m0/{tennis,audit,main-local,recovery-stable}` | point to c859f21, f39338a, d4ca8fe, 3268fab |
| B4 | `B4_engine_pre_m0.db` (+ `.sha256`, row-count manifest, schema dump) via SQLite online `.backup` | `integrity_check` ok; all 29 tables' row counts equal live at backup time; restored copy opens with integrity ok |

Also stored: `git_status_pre_m0.txt`, `head_pre_m0.txt`, `worktree_diff_vs_HEAD.patch`.

## 4. Commits

On `tennis-prediction-engine` (local, not pushed):
- `a76ab45` preserve sessions 1–4 fixes (21 M, 2 D, 6 new tests)
- `22d73e4` add `AUDIT_REPORT_2026-09-13.md`; ignore `.env.*` (except `.env.example`) and `PROJECT_MASTER_REPORT.pdf`
- `5a494bf` golden-probability regression test (captured **before** the merge)

On `integration/m0-consolidation`:
- `e101f59` merge of the audit branch (`--no-ff`, both histories preserved)
- `ef3240b` schema migration for the four tables
- docs commit (this report, CHANGELOG, PROJECT_STATUS, KNOWN_ISSUES)

## 5. Merge resolution

| File | Git result | Resolution | Why |
|---|---|---|---|
| `src/engine/probability_engine.py` | **auto-merged cleanly** | reverted to tennis version | the audit's synthetic-XGB weight = 0 would have changed production probabilities silently; deferred to M2 |
| `src/ml/team_stats_db.py` | conflict | tennis version | audit's hash-fallback `matches_played=0` changes data quality → production; M2 |
| `api/main.py` | conflict | tennis version; warning ported | the backtest endpoint now lives in `api/services/debug_service.py`; ported docstring + `data_leakage_warning` field (admin-only response, additive) |
| `src/engine/settlement.py` | modify/delete | deletion kept | see §6 |
| `src/ml/dataset_builder.py` | auto-merged | accepted | docstring only (AST of code identical) |
| `.gitignore` | auto-merged | accepted | adds `data/**/*.db*` |
| 120+ new files | added | accepted | research pipeline; not imported by `api/` or workers |

Three audit tests assert the deferred M2 behaviour; they were moved verbatim to `tests/pending_m2/` (collected only with `RUN_PENDING_M2=1`; currently 3/3 fail as expected). Current behaviour is pinned by `tests/golden/`.

## 6. Deletion of `src/engine/settlement.py` — verification

- Zero importers on any branch (grep of `api/`, `src/`, `scripts/`, `tests/`, root entry points).
- It imported `_fetch_api_football_odds` from `api.main`, which exists nowhere → the module could not even be imported.
- Responsibilities replaced: grading → `api/services/portfolio_service.auto_settle_picks`; postponed/abandoned → `void_stale_picks`; scheduling → `scripts/settlement_worker.run_settlement` (crontab `:15` hourly). 13/13 tests in `test_settlement_scheduling.py` + `test_gated_pipeline_fixes.py` pass.
- Not replaced: closing-line value (`update_closing_odds` was imported but never called — no working CLV existed). Tracked in KNOWN_ISSUES.

`src/ml/calibration.py` (in-sample calibrator) also deleted: zero callers on any branch.

## 7. Schema migration

`init_db()` now creates `model_confidence_adjustments`, `prediction_errors`, `team_learning_state`, `team_rating_history` (+ 11 indexes). DDL copied verbatim from the live DB (no CREATE existed anywhere in git history) with `IF NOT EXISTS`.

| Check | Result |
|---|---|
| Fresh DB gets exactly the live schema | PASS |
| Idempotent (init twice) | PASS |
| Seeded existing DB: schema and rows unchanged | PASS |
| Real copy of B4: schema of all objects + row counts of all 29 tables unchanged, integrity ok | PASS (`M0_EXISTING_DB_COPY`, opt-in) |
| Live `data/engine.db` | not modified — schema dump identical to pre-M0 |

Behavioural note: on a **fresh** database the error/team-learning code paths now find empty tables instead of failing inside try/except. Existing databases are unaffected.

## 8. Golden-probability regression

`tests/golden/test_golden_probabilities.py`: 15 fixtures through `estimate_probabilities()` (the Poisson + synthetic-XGB blend used by `run_pipeline`) and 7 through `_compute_match_analysis()` (full user-facing analysis: all markets, tiers, runtime gate, BILQE). Isolated in-memory DB injected as the `get_db()` singleton; isotonic singleton reset. Covers live-DB, hard-coded and hash-fallback team stats, the nonzero XGB-weight path (`source=hybrid`) and the DQ < 45 zero-weight path.

Tolerance: absolute 1e-9 on every float (outputs are rounded to ≤ 4 dp by production code, so effectively exact). Deterministic across 3 processes with different `PYTHONHASHSEED`. `suggested_pick` is a Fisher–Yates shuffle by design; the test seeds `random` only.

Captured at `5a494bf` (pre-merge); passes at `e101f59` and `ef3240b`.

## 9. Test results (Python 3.13.2; xgboost 3.2.0, scikit-learn 1.8.0, pandas 3.0.3, numpy 2.4.6)

All backend runs used `FOOTBALL_PREDICTOR_DB_PATH` = scratch copy of B4 so tests could not touch the live DB.

| Suite | Status | Detail |
|---|---|---|
| Backend pytest (default) | PASS | 440 passed, 1 skipped (opt-in real-DB migration test), 0 failed |
| — of which API compatibility (`test_api_compatibility.py`) | PASS | unmocked; see KNOWN_ISSUES |
| — of which critical parity (audit CI test) | PASS | |
| — of which golden probabilities | PASS | 3/3 |
| Migration test vs real DB copy | PASS | 4/4 with `M0_EXISTING_DB_COPY` |
| Pending M2 suite | FAIL (expected) | 3/3 fail; they assert deferred behaviour |
| `scripts/test_integrity_audit.py` (CI step) | FAIL | 9 weak assertions, all in audit-branch `tests/prediction_service/*` (pre-existing on that branch) |
| Frontend `vite build` | PASS | built into scratch; repo `frontend/dist` untouched |
| Frontend `eslint` | FAIL (pre-existing) | 13 errors; identical set at c859f21 → none introduced |
| Python 3.11 (declared runtime) | BLOCKED | 3.11 not installed |
| Live uvicorn smoke test | NOT RUN | app startup starts provider-polling background tasks (quota); covered by TestClient tests instead |

Baseline before M0 (c859f21 + working tree): 265 passed.

## 10. Reconciliation with PROJECT_MASTER_REPORT.pdf (41 pages)

The PDF is itself an AI-generated handover reconstructed from the repository (it states no source PDF was available), dated 2026-10-11. No critical discrepancy with the M0 plan. Points resolved or refined by this audit:

| PDF item | PDF said | Verified now |
|---|---|---|
| T16 / §9.4 Q2 live counts | UNKNOWN | football `picks` 0 / 1,000; tennis 16 / 500 settled (3 won, 13 lost) |
| R8 / §9.4 Q8 four tables | owner DB status UNKNOWN | present in live DB (412 / 11,202 / 0 / 0 rows); schema now in code |
| T2 tennis tests | 231 pass, 2 fail (network) | 265 pass incl. network tests (network available here) |
| R1 conflicts | `api/main.py`, `team_stats_db.py` | plus `settlement.py` (working-tree deletion) and a **silent** clean auto-merge of `probability_engine.py` |
| R12 integrity audit | 9 weak assertions | confirmed, same 9 |
| R13 `.gitignore *.json` | new JSON silently untracked | confirmed; golden JSON whitelisted explicitly |
| R3(b) nightly refit | API refits isotonic + league adjustments at 03:00 | relevant to M2/M3 governance; golden test is isolated from it |
| §7.6 Phase 2 report vs JSON | "validated" list stale | noted; no M0 impact (production XGB weight unchanged; research models not wired) |
| Runtime observation (new) | — | `uvicorn api.main` (pid 65948, since 2026-09-21) writes `qualification_log`; 3,650 rows, all tier C |

## 11. Not done (by instruction)

No push, no deploy, no merge into `main`, running app not restarted, no scheduled predictions activated, live DB not modified, no change to probabilities, weights, thresholds or calibration.

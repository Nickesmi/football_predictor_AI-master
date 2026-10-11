# M2 — Implementation Plan (for approval)

Base: `m1.1/stabilization`. New branch `m2/governed-fixes-and-freeze`. Nothing activates the baseline; M3 does that after separate approval. Each package is its own commit series with its own test evidence; packages B and C change production behaviour and need explicit approval.

## Package A — operational bug fixes (no prediction math; approval requested because they change runtime behaviour)

| # | Bug (found in M1.1, `KNOWN_ISSUES.md`) | Fix | File | Behaviour change | Test |
|---|---|---|---|---|---|
| A1 | `upsert_competition` not imported → competition tracking has failed 3,944 times since 2026-07-05 (K24) | add `from src.db.competition_tracker import upsert_competition` | `api/services/fixtures_service.py` | `competitions` table updated again from fixture loads | test: fixture load inserts the fixture's league into `competitions` |
| A2 | `_prewarm` imports nonexistent `api.services.prediction_service` (since 26a8ba4) (K25) | call `fixtures_service._precompute_predictions_for_date(ist_date, fixtures)` with fixtures read from the day's cache; skip if no cache | `api/services/system_service.py` | predictions are pre-computed after each auto-refresh (more analysis rows → more `prediction_errors`, see P8) | test with provider fakes: pre-warm computes one analysis per cached fixture, no network |
| A3 | `/api/debug/date-trace` and `/date-validation` reference undefined `_api_football_to_fixture` → 500 (K26) | import it from `fixtures_service` | `api/services/debug_service.py` | admin endpoints work | TestClient test with admin key + fakes |
| A4 | `classic_prediction.py` imports nonexistent `src.db.daily_repo` (dead module, K27) | leave as is; document | — | none | — |
| A5 | prevent recurrence | add `pyflakes` (dev) to `requirements.txt`/lock and a test that fails on new undefined names/unresolvable `src`/`api` imports, with the A4 file and `api/backtest_snippet.py` (orphan) as an explicit allow-list | `tests/test_static_imports.py` | none | itself |

Golden: unchanged (A1–A3 are outside the captured paths). Verify on 3.11 and 3.13.

## Package B — governed probability fixes (K1, K2; changes production probabilities — before the baseline starts)

1. `src/engine/probability_engine.py::_market_weight` → returns `0.0` unconditionally (port of audit commit 8a871bc/a596374 text, provenance comment kept).
2. `src/ml/team_stats_db.py` Priority-4 hash fallback → `matches_played=0, form_last5=0.5` (+ the audit's staleness docstring).
3. Move `tests/pending_m2/*` into `tests/` (back to `test_team_stats_data_quality.py` and `tests/real_data/test_model_provenance.py`); delete `tests/pending_m2/`.
4. Regenerate golden references in the same commit: `GOLDEN_UPDATE=1` on 3.11 **and** 3.13; `KNOWN_RUNTIME_DIFFERENCES` re-derived (must still be only the `sum()` boundary cases).
5. Commit a machine-generated `docs/M2_GOLDEN_DIFF.md`: every changed value old → new. Expected: all `estimate_probabilities` sources `hybrid` → `poisson`; data quality drops for hash-fallback fixtures (`Zzyzx United v Qwerty Athletic`, `Arsenal U21 v Chelsea U21`, `Malmo FF v Liverpool`); their downstream tiers/gate verdicts in `match_analysis`. Any change outside those causal paths = stop and investigate.

Acceptance: full suite on 3.11 (clean checkout + Linux container), pending tests pass in the main suite, golden diff fully explained.

## Package C — baseline freeze infrastructure (switch OFF by default → no behaviour change until M3)

Implements `docs/BASELINE_FREEZE_DESIGN.md` §2:
1. `src/baseline/manifest.py` — build/verify manifest (code SHA, artifact SHA-256s, policy-constant hash, runtime + lock hash).
2. `src/baseline/policy.py` — canonical dump of selection constants (single place that enumerates them; test fails if a constant is added to the gate/BILQE/grade modules without being included).
3. `init_db` migration: `*_frozen` and `*_shadow` tables; `picks` provenance columns (`model_version`, `calibration_version`, `selection_policy_version`, `code_sha`, `runtime`).
4. `scripts/freeze_baseline.py` — snapshot P1/P2/P5/P6/P7 inputs into frozen tables, write `models/baseline/football-v1.0/manifest.json`; refuses to overwrite an existing version.
5. Freeze-aware code paths for P1–P7 and P9 exactly as in the design table (each guarded by `config.BASELINE_VERSION`).
6. Startup + pre-pipeline integrity check; fail closed for official picks.
7. `insert_pick` writes provenance; `get_experiment_progress()` filters by active manifest versions.
8. Tests: design §2.5 items 1–5, plus "switch off ⇒ golden identical".

Rehearsal (end of M2): on a **copy** of `data/engine.db`, run `freeze_baseline.py`, simulate 30 days with provider fakes and synthetic settlements, show official outputs constant and shadow tables changing. Report.

## Package D — runtime cutover preparation (no restart in M2)
- Point launchers (`run_web.command`, `start_*workers.command`, `scripts/orchestration/crontab.txt`, systemd units) at the 3.11 environment (`.venv311`), behind a documented one-step switch; not executed until M3.
- Restart checklist for M3: stop app → back up DB → switch env → set `BASELINE_FREEZE` → run integrity check → start.

## Out of scope for M2
Baseline activation, scheduler installation, F1, BILQE all-tier-C diagnosis (K12, before M3), CLV/odds (M4).

## Rollback
Each package is separate commits on the M2 branch; drop the branch or revert the package's commits. Package C is inert unless `BASELINE_FREEZE` is set.

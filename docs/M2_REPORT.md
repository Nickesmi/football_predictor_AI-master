# M2 — Implementation Report

Date: 2026-10-12 · Branch: `m2/governed-fixes-and-freeze` (from `m1.1/stabilization`; base tag `backup/m2/base`; local only — nothing pushed, merged or deployed; baseline not activated).

## 1. Critical findings during M2

1. **Concurrent outside agent (paused M2 on 2026-10-11).** At 18:45:34 the Antigravity IDE agent removed the `suggested_pick` feature from `api/services/match_analysis_service.py` in this working tree. With approval it was verified (0 added lines, only that feature) and restored to the M2 base; the removed version is kept in `~/Desktop/football_predictor_backups/m2_external_change_2026-10-11/`. A contract test now fails if any analysis field read by the frontend disappears.
2. **The running football API is not what earlier reports said (K28).** It is **pid 94530**, started **2026-10-11 18:52:16 by the Antigravity IDE agent** (parent = its language server), from this working tree as it was at that moment — uncommitted Package A plus Antigravity's `suggested_pick` removal, Python 3.13, no reload. It is writing `data/engine.db` (since start: 545 `qualification_log`, 7 `prediction_errors` rows; nightly production refit at 2026-10-12 00:13 UTC; competitions now tracked; 0 picks; schema unchanged). pid 65948, previously described as the running app, belongs to `/Users/nikantehrani/cryptopredic`. **Recommendation: stop pid 94530 and restart deliberately from a reviewed commit (§7).** It was not touched, per instructions.
3. **Odds were never stored (K31, fixed).** Since 26a8ba4 every SofaScore event carrying vote data made `collect_fixtures` raise (missing `implied_probability`), which is consistent with the live `odds_snapshots` table holding 0 rows.
4. **Official picks are priced against crowd-vote pseudo-odds**, not bookmaker prices (`fixture_collector._extract_odds`). In the 30-day simulation 91 of 150 fixtures produced a pick. This is a methodological decision for M3 (§8), not changed in M2.
5. A further continuous learning path was found and frozen: **P18** — every settled result updates league profiles (`settle_prediction → _update_league_profile`), not only the nightly job.

## 2. Commits (separate per package)

| Commit | Package | Summary |
|---|---|---|
| `b33f9d5` | A | missing imports / stale `api.main` references: competition tracking, prediction pre-warm, admin date-trace & date-validation, logo-audit, audit_engine league map; static undefined-name/import guard; 24-endpoint admin smoke test; frontend field contract |
| `dcf6307` | B | synthetic XGBoost zero influence (blend weight, BILQE consensus, live-odds hybrid); unknown teams `matches_played=0`; golden v1 preserved, v2-m2 canonical; attributed comparison |
| `64b4874` | A follow-up | vote-derived odds snapshots stored (`implied_probability` derived) |
| `5110258` | C | frozen baseline: manifest, frozen/shadow tables, integrity checks, provenance, reproducibility, provenance-only experiment count, status endpoint, migration, 30-day simulation |
| `ec77c08` | D | Python 3.11 cutover preparation: launcher preflight, cutover/rollback scripts, isolated desktop build env, runbook |
| (this commit) | docs | M2 report, known issues, status, changelog |

Every commit was preceded by a check that no file outside the package's own list was modified.

## 3. Package A — runtime correctness

| Fix | Evidence |
|---|---|
| `upsert_competition` import (fixtures_service) | test persists fixture leagues into `competitions`; live process now tracks competitions |
| pre-warm calls `_precompute_predictions_for_date` via new `prewarm_predictions_for_date` | tests: 3 cached fixtures for the date → 3 ready; other dates excluded; no cache → no work |
| `_api_football_to_fixture` import (debug_service) | `/api/debug/date-trace`, `/date-validation` return 200 (were 500) |
| logo-audit used removed `api.main.get_fixtures_by_date` | `/api/debug/logo-audit` returns 200 (was 500) |
| audit_engine silently used an empty league map | imports the real `LEAGUE_NAME_MAP` (51 entries) |
| vote odds insert crash (follow-up) | regression test stores home/draw/away with implied probabilities |

All 14 new regression checks fail on the pre-fix code and pass after. Static guard: pyflakes (pinned) + import resolver; only the documented dead/orphan files are allow-listed. No prediction mathematics changed (golden v1 unchanged after A).

## 4. Package B — governed probability corrections

Changes: `_market_weight` returns 0.0; BILQE consensus admits the synthetic model only if `synthetic_model_provenance()["production_validated"]` (never); `HybridPredictor` blends only under the same condition; hash-fallback teams `matches_played=0, form_last5=0.5`. The three previously pending tests run unchanged in the main suite; 4 new exclusion tests (including proof that the gates are provenance-driven, not hard-coded).

Golden references: `tests/golden/history/v1-pre-m2/` (byte-identical, SHA-256 pinned) and canonical `golden_probabilities.v2-m2.py311.json` (+ `.py313`). The 3.11↔3.13 difference remains exactly the one documented value.

**Before/after comparison** (`docs/M2_PROBABILITY_COMPARISON.md`, generated and test-enforced): 4,452 changed values, **0 unintended**.

| Cause | Values | Examples |
|---|---|---|
| E1 synthetic XGB weight 0 | 56 | Arsenal v Chelsea home 0.5469 → 0.5721, O2.5 0.4637 → 0.4572; Bayern v Dortmund O2.5 0.6459 → 0.6878; all 14 blended fixtures `hybrid → poisson` |
| E2 unknown teams 0 matches | 4,396 | data quality 100 → 50 (Galatasaray, PSV, Celtic, U21, Zzyzx), 85 → 35 (Malmo, Women's SL); analysis Zzyzx v Qwerty DQ 70 → 20 ("medium" → "blind"), 281 of 300 markets changed, max 16.2 pp; Friendly Town v City (hash path in analysis) 145 markets, max 0.6 pp |
| E3 BILQE consensus | 0 in golden | golden fixtures have no odds, so every pick fails the edge layer first; proven by unit tests |

Analysis (`match_analysis`) probabilities for fixtures without unknown teams are unchanged — the analysis path never blended XGBoost.

## 5. Package C — frozen baseline (implemented, not activated)

**Configuration**

| Setting | Meaning |
|---|---|
| `BASELINE_FREEZE` (env or `.env`) | active baseline version; unset = unchanged behaviour |
| `BASELINE_MANIFEST_DIR` | default `models/baseline` |
| `scripts/freeze_baseline.py --version V --approved-by NAME` | creates immutable baseline `V` |
| `GET /api/baseline/status` | integrity report |

**Manifest** (example from the simulation; the real one is created at M3):

```json
{
  "baseline_version": "football-sim-v1",
  "model_version": "model-9eb19e477fcc",
  "calibration_version": "cal-0c258d45d82a",
  "selection_policy_version": "sel-4392f079a085",
  "runtime": {"python": "3.11", "requirements_lock_sha256": "e5ee2368…7906"},
  "approved_by": "M2 simulation",
  "code_revision_at_freeze": "64b4874…+dirty",
  "files": {"model": "20 files", "calibration": "3 files", "selection": "9 files"},
  "artifacts": ["calibration_models_frozen", "model_confidence_adjustments_frozen", "pick_calibrator",
                "gate_market_stats", "qualification_history", "league_profiles"],
  "policy": {"inputs_may_update": ["team_state Elo and rolling form after verified finished matches", "fixtures", "odds", "results"],
             "frozen": ["model code, coefficients, features, Elo formulas/K", "calibration artifacts", "league adjustments",
                        "runtime gate statistics", "BILQE history inputs", "selection rules", "python minor + dependency lock"]}
}
```

Behaviour per pathway: `docs/BASELINE_FREEZE_DESIGN.md` §4. Provenance stored on every pick: baseline, model, calibration and selection-policy versions, code revision, runtime, UTC input timestamp, and the resolved inputs with raw and calibrated probabilities (exact recomputation via `reproduce_probabilities`). Only picks whose provenance is complete and equal to the active manifest count toward the 1,000.

Migration (additive): 8 provenance columns on `picks`; tables `baseline_versions`, `baseline_frozen_artifacts`, `calibration_models_{frozen,shadow}`, `model_confidence_adjustments_{frozen,shadow}`. Verified on a copy of the production DB: only `picks` gained columns, all existing rows and objects unchanged, idempotent. Not applied to the live DB.

**Evidence**: 14 freeze tests (immutability; frozen reads vs live growth with counterfactual; shadow routing; production refit refused / admin retrain 409; tamper → refusal for frozen table, artifact, league profiles, model file, runtime; provenance + exact reproduction + experiment count; Elo/form still update; unfrozen picks never count). **30-day simulation** on a copy of the production DB (`docs/M2_FREEZE_SIMULATION.md`): 0 invariant violations; 91 official picks, all with complete provenance, all reproduced exactly, 91 counted; Elo/form moved every day; shadow learning ran nightly (Premier League profile: frozen 254 samples vs shadow 404).

## 6. Package D — Python 3.11 cutover preparation

Launcher preflight (`scripts/orchestration/check_runtime.sh`, used by `run_web.command`, `start_workers.command`, `start_tennis_workers.command`): prints the interpreter, warns off 3.11, refuses a frozen baseline on another runtime (reads `BASELINE_FREEZE` from env or `.env`). `cutover_python311.sh` (dry run by default) and `rollback_python311.sh`; both refuse while this deployment's API/workers run (process check scoped to this directory) and never stop services. `build_desktop.sh` now uses `.venv-desktop` (it previously rebuilt the live `.venv`). Runbook: `docs/CUTOVER_PYTHON311.md`.

Verified on a scratch clone with a copy of the production DB: backup + integrity, rebuild on 3.11.17, 543 tests, integrity audit, smoke `/api/health` and `/api/baseline/status` on port 8099, rollback to 3.13. On the real repo the dry run correctly refused (API running) and changed nothing.

## 7. Test results

| Check | Result |
|---|---|
| Full suite, Python 3.11 (canonical) | **543 passed**, 1 skipped (opt-in real-DB migration test) |
| Full suite, Python 3.13 | 542 passed, 2 skipped (+ pyflakes guard: pyflakes only in the 3.11 lock) |
| Golden 3.11 / 3.13 | **8 / 8 passed** each |
| Migration tests incl. real DB copy | 4 passed |
| CI integrity audit | **PASS** (0 weak) |
| Frontend lint / build | PASS / PASS (no frontend change in M2) |
| Simulation (30 days) | 0 violations |
| Cutover rehearsal (clone) | PASS, rollback PASS |
| Production data | live schema identical to pre-M0, `picks` = 0, no test or script wrote outside temp/scratch (running process K28 writes as noted) |
| Linux container | NOT RUN in M2 (K30) |

Package-level gates were run after A, B, C and D (suite, golden, integrity, migrations fresh + copy, production check; frontend after D).

## 8. Remaining issues and M3 decisions

Full list: `KNOWN_ISSUES.md`. Decisions needed before the official baseline:

1. **K28**: stop the Antigravity-launched API; keep other agents off this repository.
2. **Odds source**: official picks currently use crowd-vote pseudo-odds. Starting the 1,000-pick sample on them measures edge against crowd sentiment, not the market. Recommend real bookmaker odds (The Odds API integration exists) before activation, or an explicit owner decision to baseline on vote odds.
3. **K12**: BILQE rejects everything (it does not gate official picks, which use `assign_grade`, but the inconsistency should be understood).
4. Review the Package B probability changes (`docs/M2_PROBABILITY_COMPARISON.md`).
5. **K30**: confirm golden v2 on the first Linux CI run.

## 9. Deployment and rollback

Nothing is deployed. To use M2 locally (owner action):
1. Stop pid 94530 (and any football workers).
2. `git switch m2/governed-fixes-and-freeze` (already checked out).
3. Optional now / required for the baseline: Python 3.11 cutover — `docs/CUTOVER_PYTHON311.md` §1.
4. Start with `./run_web.command`. On first DB access `init_db` applies the additive migration (provenance columns + baseline tables); back up first (the cutover script does).
5. Baseline activation only at M3 — runbook §2.

Rollback:
- Code: `git switch m1.1/stabilization` (or `git branch -D m2/governed-fixes-and-freeze`; base tag `backup/m2/base`). Individual packages: `git revert <commit>`.
- Python: `scripts/orchestration/rollback_python311.sh <stamp>`.
- Database: the M2 migration is additive and old code ignores the new columns/tables; to return to the exact pre-migration file, restore the backup taken before starting (cutover backup or `B4_engine_pre_m0.db` for the pre-M0 state, which discards later writes).
- Baseline: remove `BASELINE_FREEZE` from `.env` and restart.

## 10. Recommendation on M3

The engineering for M3 is ready: probability corrections are in and fully attributed, the freeze is implemented and proven over 30 simulated days, provenance and reproducibility are enforced, and the 3.11 cutover is rehearsed with rollback. **Recommendation: proceed to M3 preparation (stop the outside process, cut over to 3.11, rehearse `freeze_baseline.py` on a DB copy), but do not start the official 1,000-pick baseline until the odds-source decision (§8.2) is made**, because the baseline's measured edge and ROI depend on it and cannot be changed once the sample begins.

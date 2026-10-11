# Football Predictor AI — Technical Audit Report

**Date:** 2026-09-08
**Scope:** Full-codebase audit of the football prediction pipeline (`src/engine`, `src/ml`, `src/db`, `api/`), focused on data leakage, forced/fabricated predictions, and calibration integrity, per the standing audit mandate.
**Method:** Direct code inspection (not simulated), existing test suite execution, and inspection of the live SQLite schema in this environment. No synthetic "before/after" numbers are presented anywhere in this report — see "What I did NOT do" at the bottom for why.

## 0. Top-line finding

This is **not** a naive or toy prediction system. It already has real engineering discipline in places most such projects skip entirely:
- A dedicated `performance_gate.py` that refuses to promote a market to a "bankroll pick" unless it has a minimum sample size, hit rate, and Brier score in `prediction_log` (i.e. it already implements Phase 11's "NO BET" contract for gated picks).
- A `production_audit.py` / `audit_engine.py` pair that already scores live-data freshness, blind-prediction percentage, probability-math identities (1X2 sums to 100%, O/U pairs sum to 100%, etc.), and model calibration, and refuses to call itself "production ready" below a threshold.
- `prediction_logger.log_predictions()` already refuses to log a prediction for a match whose date is in the future, and `prediction_log` captures `predicted_prob` at the moment a prediction was actually made — so its accuracy/Brier/calibration numbers are a legitimate point-in-time record, not a replay.
- The README already carries an honest risk disclaimer and does not claim any accuracy figure.

That context matters: several of the audit template's "assume the worst" phases (fabricated 100% accuracy, forced predictions, no calibration) do not apply here — those guardrails already exist. The real bugs found were narrower but still material, and are fixed below with regression tests.

## 1. Bugs found and fixed (this session)

### 1.1 — CRITICAL: Blind team lookups reported as fully-observed data
**File:** `src/ml/team_stats_db.py`, `get_team_stats()`, Priority-4 hash-fallback branch (was line ~427).
**Root cause:** `TeamVenueStats` is a dataclass with `matches_played: int = 20` as its default. The hash-based fallback — used for a team that is in neither the hardcoded season tables nor the live `team_state` DB, i.e. a team the system has **zero real observations** for — built its `TeamVenueStats` without passing `matches_played`, so it silently inherited the default of `20`.
**Consequence:** `FeatureBuilder.compute_data_quality()` penalizes teams with `min(matches_played) < 15`. A genuinely blind guess (deterministic hash noise, not a statistic) was reporting `matches_played=20` and therefore incurred **zero** of that penalty — it looked exactly as trustworthy as a team with a real 20-match sample. This directly undermines the "NO BET on insufficient evidence" contract that `risk_control.apply_risk_filter()` and `performance_gate.py` are built to enforce, because the `data_quality` score feeding those gates was inflated at the source.
**Fix:** the hash-fallback branch now explicitly sets `matches_played=0, form_last5=0.5`. This correctly drives `compute_data_quality()` down (verified: two unknown teams now score well under the "medium" threshold), which in turn lowers `_market_weight()`'s XGBoost blend weight to 0, raises `risk_control`'s confidence floor, and can trip the `data_quality >= 35` hard gate in `apply_risk_filter()`.
**Regression tests:** `tests/test_team_stats_data_quality.py` (3 tests, passing) — asserts the hash-fallback path reports zero matches played, asserts two blind teams score under the quality threshold, and asserts known hardcoded teams are unaffected (no regression).

### 1.2 — HIGH: Mislabeled "backtest" endpoint leaks future information
**File:** `api/main.py`, `/api/debug/backtest-features` (`run_feature_backtest`), and the orphaned duplicate `api/backtest_snippet.py`.
**Root cause:** this endpoint reruns `_compute_match_analysis()` — the **live** prediction path — against historical rows from `match_history` to rank feature-flag configurations. But `_compute_match_analysis()` reads `team_state` via `get_team_stats()`, and `team_state` only stores each team's **current** rolling ELO/form (see `src/engine/live_updater.py`), not a snapshot as of the historical match's date. A match from three months ago is therefore scored using team strength that already reflects everything that happened *since*, including the match's own outcome and beyond — textbook look-ahead leakage.
**Consequence:** the endpoint's `accuracy` / `brier_score` output looks like a walk-forward backtest but is not one; anyone administering the system (it is admin-key gated, so not public-facing) could reasonably mistake it for validated real-world performance.
**Fix:** added an explicit, code-level leakage warning in the docstring and a `data_leakage_warning` field in the JSON response pointing operators to the actually-valid source (`prediction_log` / `/api/debug/model-validation`, which log point-in-time predictions — see §0). I did not attempt to bolt a full point-in-time snapshot-reconstruction system onto `team_state` in this pass; that is real infrastructure work (see §3, recommended follow-up) and rushing it without historical data to validate against would risk introducing a *different* bug while claiming to fix this one.

### 1.3 — MEDIUM: Fabricated CLV counter
**File:** `src/engine/settlement.py`, `run_daily_settlement()`.
**Root cause:** the function imported `update_closing_odds` but never called it, and unconditionally incremented `clv_count += 1` for every unsettled pick with the comment "we just bump the count for the hook." No closing-line-value data was ever written or computed.
**Consequence:** `run_daily_settlement()` returned `{"clv_updated": N}` implying N real CLV updates happened, when zero did. Any dashboard or report reading that field would display a fabricated metric — exactly what the audit mandate prohibits ("NEVER fabricate model performance").
**Fix:** removed the fake increment; `clv_count` now stays at its true value (0, since real CLV mapping isn't implemented) instead of reporting invented work. Left a `TODO` describing what real CLV wiring would require (mapping `api_odds` to each pick's market/selection, which the pipeline's own code elsewhere already flags as "fragile" cross-provider matching).

### 1.4 — LOW: Stale hardcoded season priors, undocumented
**File:** `src/ml/team_stats_db.py` (module docstring).
**Root cause:** the fallback team-stats table was labeled "2024/25 season data" with no staleness caveat, and the current date in this environment is September 2026 — the hardcoded priors are now up to two seasons old. This tier is only used when a team has no live-ingested history (Priority 1), so it mainly matters for cold-start/newly-promoted teams.
**Fix:** documented the staleness explicitly and clarified this table is a cold-start prior, not ground truth, and that Priority-1 live data always supersedes it. No behavioral change — this is a case where the honest fix is disclosure, not deletion (the priors are still a reasonable prior for a team the live system hasn't seen yet).

## 2. Areas inspected and found sound (no change made)

- **`src/engine/live_updater.py` (`on_match_finished`)** — the core state-update path. Correctly reads pre-match state, computes the new rolling average/ELO from the just-finished result, and only *then* persists — there is no path by which a match's own result leaks into the pre-match features used to predict it. Idempotent via `match_history` id check.
- **`src/db/prediction_logger.py`** — `log_predictions()` explicitly rejects predictions timestamped after the match's kickoff and de-duplicates by `(match_id, market_type)`. `get_backtest_summary()` / `audit_model_validation()` compute Brier score, log loss, and calibration gap directly from this point-in-time log — this is legitimate, not leaked.
- **`src/engine/probability_engine.py`** — already treats the XGBoost models conservatively: `_market_weight()` returns `0.0` below `data_quality < 45` and caps the blend at `0.25` even for the best-performing target, with an explicit comment that Poisson (fed by real `team_state`) remains the anchor because the XGBoost AUCs are only modest. This is the right instinct; §3 below explains why those AUCs shouldn't be trusted at all yet, not just discounted.
- **`performance_gate.py`** — the per-market gate (min sample size, min hit rate, max Brier, league-reliability floor) is a real, already-implemented version of Phase 11's "no forced prediction" requirement.
- **Test suite** — 278 pre-existing tests, all passing before and after this session's changes (281 including the 3 new regression tests). `scripts/test_integrity_audit.py` already exists specifically to fail CI on weakened assertions (`assert True`, bare `is not None`, etc.) — a good sign the test suite hasn't been gamed.
- Scaffolding files `src/ml/ensemble_predictor.py`, `catboost_predictor.py`, `lightgbm_predictor.py`, `neural_predictor.py`, `tabpfn_predictor.py` are **not imported anywhere** in the live pipeline (verified by repo-wide grep) — they are dead code, not silently-active risk. `hybrid_predictor.py` and `model_benchmark.py` are imported (by `odds_scanner.py` and admin debug endpoints respectively) but are outside the `run_pipeline()` critical path.

## 3. The one architectural gap I did not — and should not — patch blind

**`src/ml/trainer.py` + `src/ml/dataset_builder.py`: the bundled XGBoost models (`models/xgb_*.pkl`) are trained exclusively on synthetic data** — randomly generated team profiles (`np.random.default_rng`) run through a Poisson outcome simulator, with zero real historical matches involved. `models/training_metrics.json` reports AUCs of 0.51–0.58, which is expected and *consistent* — those numbers measure how well XGBoost reconstructs the synthetic generator's own formula, not real predictive skill on real matches.

This is architecturally the most significant finding in the audit, but I'm not treating it as a bug to silently "fix" with more synthetic data or a cosmetic AUC bump, because:
1. There is no real historical match dataset available in this environment to train on (`match_history` has 0 rows here — this container starts empty; the live deployment presumably accumulates it via `live_updater.on_match_finished`).
2. Fabricating a "fixed" model trained on more synthetic data, or hand-waving a higher reported AUC, would be exactly the kind of fake-accuracy result the audit mandate explicitly forbids.
3. The system already mitigates this correctly today: Poisson (fed by real, live `team_state`) is the anchor; XGBoost is a low-weight, AUC-gated second opinion that gets **zero** weight whenever `data_quality < 45`. That is the right posture for a model trained on synthetic data — I made sure it isn't being misrepresented (§1.1 fixed the one path where its input trust signal, `data_quality`, could be gamed).

**What I did instead:** documented this clearly in `src/ml/dataset_builder.py`'s module docstring (§1 above) so nobody — including a future session — mistakes the training metrics for validated real-world accuracy, and wrote down the concrete, correct fix for when real data exists:

**Recommended follow-up (needs real data, so out of scope for this pass):**
- Build a `RealDatasetBuilder` that reconstructs point-in-time features from `match_history` (team state *as of* each match's date, not current state — this requires either periodic state snapshots or replaying `on_match_finished` up to a cutoff date), producing one row per historical match with a real 0/1 outcome.
- Replace `StratifiedKFold` (random shuffle, currently used in `trainer.py`) with **chronological walk-forward splits** (train on seasons N..N+k, validate on season N+k+1, roll forward) once there's enough real history — random k-fold is invalid for this time series regardless of data source.
- Re-run `_market_weight()`'s AUC gate against the new, real metrics; only then would raising the XGBoost blend weight above today's conservative 0.10–0.25 be justified.
- This also fixes §1.2 for free: once `match_history`-derived features are point-in-time-correct, the same builder can back the `/api/debug/backtest-features` endpoint without leakage.

## 4. Final status

| Item | Status |
|---|---|
| Data-leakage bugs found | 1 (backtest-features endpoint, §1.2) — mitigated via disclosure, not yet structurally fixed (needs point-in-time infra, see §3) |
| Fabricated-metric bugs found | 1 (CLV counter, §1.3) — fixed |
| Confidence/gating bugs found | 1 (blind-team data quality, §1.1) — fixed, regression-tested |
| Documentation/staleness issues | 1 (stale season priors, §1.4) — disclosed |
| Pre-existing test suite | 278/278 passing, unchanged by this session |
| New regression tests added | 3, all passing |
| Real historical data in this environment | **None** (`match_history`, `matches`, `picks`, `prediction_log` all empty — fresh container) |

**Verdict: Needs More Testing — specifically, needs real production data.** The system's own audit tooling (`scripts/production_audit.py`, `audit_engine.audit_production_readiness()`) is the correct instrument to answer "is this production ready right now," and it requires a populated database to run meaningfully; this environment has none. I am not reporting a production-readiness score, accuracy figure, or brier score anywhere in this document, because every one of those tables is empty here and inventing numbers would violate the audit's own absolute rules. Run `python3 scripts/production_audit.py` and `audit_production_readiness(date_str)` against the live deployment's real database to get a grounded answer — the machinery to do so already exists and was verified working in §2.

## What I did NOT do, and why

- I did not report a "before vs after accuracy/Brier/calibration" table, because no real predictions exist in this environment to measure either side of it. Any numbers I typed would be fabricated.
- I did not retrain the XGBoost models "to fix" the low AUC, because doing so on more synthetic data would not improve real-world skill and would misrepresent the fix as more substantial than it is (§3).
- I did not attempt a full point-in-time snapshot-reconstruction system for `team_state` in this pass (§1.2/§3) — that's a real feature, not a bug-sized fix, and building it without real data to validate against risks shipping an unverified new leakage path while believing it's fixed.

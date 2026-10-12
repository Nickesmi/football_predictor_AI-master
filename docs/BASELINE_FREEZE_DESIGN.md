# Football v1.0 Baseline — Freeze Architecture

Date: 2026-10-11 · Status: **implemented in M2 (see §4), not activated** · Owner policy (2026-10-11):

- no automatic production retraining, isotonic replacement, league-adjustment update, or recalibration of pick probabilities from accumulating results;
- no silent change to model weights, qualification thresholds or market-selection rules;
- historical data collection and settlement continue; shadow models may keep learning;
- every official prediction records model, calibration and selection-policy versions.

## 1. Inventory — every path that can change prediction or selection behaviour

Found by tracing all writers of learned-state tables and model files (`INSERT/UPDATE` on `calibration_models`, `model_confidence_adjustments`, `team_state`, `team_learning_state`, `team_rating_history`, `league_profiles`, …; `pickle.dump`/`json.dump` under `models/`) and all reads of accumulating outcome tables (`prediction_log`, `picks`, `prediction_errors`, `match_history`) inside prediction and selection code.

Class: **M** = model/calibration parameter (must freeze) · **S** = selection input that learns from outcomes (must freeze) · **D** = input data / features (continues by policy) · **C** = code or configuration (governed by version + startup check) · **R** = research/shadow only.

| ID | Pathway | Trigger | Writes / reads | Affects | Class | Live status today |
|---|---|---|---|---|---|---|
| P1 | Nightly isotonic refit `IsotonicCalibrator.fit_all` (`api/services/system_service.py:278`) | automatic, 03:00 server-local, in the API process | fits on `prediction_log`; writes `calibration_models` + in-memory singleton (replaces only if better on 20 % split) | analysis probabilities for non-bypassed market types (`match_analysis_service.py:1109-1125`, `classic_prediction.py:257`) | M | runs nightly; output identical for 5 nights because `prediction_log` stopped growing on 2026-08-31; will change if it grows again or after a restart onto the F10 split fix |
| P2 | Nightly league-adjustment rebuild `rebuild_confidence_adjustments` (`system_service.py:310`) | automatic, same job | reads `prediction_errors`; writes `model_confidence_adjustments` | 1X2 via `get_league_adjustment`/`apply_league_adjustment` (`match_analysis_service.py:643`); league reliability in `performance_gate.py:104`; BILQE league reliability (`qualification_engine.py:815`) | M | **changing nightly** (7 leagues weighted, e.g. Premier League n=254) |
| P3 | `POST /api/admin/retrain-engine` (`calibration_service.retrain_engine`) | manual, admin | P1 + P2 | as P1, P2 | M | admin API disabled until a key is set (M1) |
| P4 | `GET /api/debug/settle-date/{d}` (`debug_service.py:190-209`) | manual, admin | `store_prediction_record`, `update_team_ratings` (`team_learning_state`, `team_rating_history`), `rebuild_confidence_adjustments` | team-rating scale factor in analysis (`match_analysis_service.py:269`), similar-match engine, BILQE; plus P2 | M | `team_learning_state` has 0 rows (never run) |
| P5 | Per-run pick calibrator `ProbabilityCalibrator.fit_from_db` (`src/engine/pipeline.py:93`) | automatic, every `run_pipeline` | reads settled `picks` | 1X2 / O/U 2.5 / BTTS probabilities that grade and select official picks | M | inert (0 settled picks); **would recalibrate inside the frozen sample** |
| P6 | BILQE historical CLV/ROI (`qualification_engine.py:888-902`) | automatic, every qualification | reads all `picks` (`clv_pct`, `pnl_units`) | layers 7–8 soft penalties → tier | S | inert (0 picks); drifts as baseline picks settle |
| P7 | Runtime bankroll gate market stats (`performance_gate.py:72`) | automatic, every analysis | reads `prediction_log` aggregates + P2 output | `bankroll_qualified`, pick-gate rejection | S | stable while `prediction_log` is static; changes with P2 |
| P8 | Outcome logging `store_prediction_record` (every analysis, `match_analysis_service.py:1613`; results) | automatic | writes `prediction_errors` | feeds P2 (feedback loop) | D | active (+15 rows today) |
| P9 | Isotonic load on process start (`get_isotonic_calibrator` → `load_from_db`) | every restart | reads latest `calibration_models` per market | as P1 | M | restart picks up whatever P1 last wrote |
| P10 | Synthetic XGBoost artifacts (`models/xgb_*.pkl`, `training_metrics.json` AUC → blend weight) | static; `trainer.train` has no automatic caller | file read | 1X2 / O/U 2.5 / BTTS blend (K1) | M | static; weight → 0 in M2 |
| P11 | Team live state `on_match_finished` (`fixtures_service.py:572`, `results_service.py:653`, `live_service.py:266`) | automatic on finished matches | writes `team_state`, `match_history` (Elo, rolling form) | Poisson inputs | D | active — intended model inputs |
| P12 | Static priors and profiles in code (`team_stats_db.py` tables, Poisson league profiles, `league_profiles` seed via `INSERT OR IGNORE`, Dixon-Coles ρ, shrink factors, gate tables, BILQE weights, `assign_grade`, execution rules) | code change only | — | everything | C | static |
| P13 | Interpreter and library versions | environment | — | float results (K20: one value differs 3.11 vs 3.13) | C | running app on 3.13; canonical 3.11 |
| P14 | Odds and fixture data (SofaScore pseudo-odds, API-Football) | automatic | `matches`, `odds_snapshots` | edges, EV | D | active |
| P15 | Research pipeline (`champion_registry.json`, `market_thresholds.json`, `real_data_trainer`, shadow tables) | scripts only | own files / `predictions.db` | nothing in production | R | not wired |
| P16 | Tennis calibration and Elo (`tennis_calibration.py`, `tennis_player_state`) | automatic | tennis tables | tennis only | out of scope (separate 500-prediction contract) | gated |
| P17 | `suggested_pick` shuffle (`match_analysis_service.py:1436`) | every analysis | — | which pick is "suggested" (display) | C | random by design; not used by `run_pipeline` |

No other writer of learned state was found. `model_weights.json` is not read anywhere; scoreline tables are read only by debug reports.

## 2. Architecture

### 2.1 Baseline manifest (single source of truth)
`models/baseline/v1.0/manifest.json`, committed, immutable once the baseline starts:

| Field | Content |
|---|---|
| `model_version` | `football-v1.0` + git SHA of the code + SHA-256 of `models/xgb_*.pkl`, `training_metrics.json` + hash of the static priors/profiles (P12) |
| `calibration_version` | `cal-v1.0-<hash>` of the frozen snapshot rows (P1/P2/P5/P7 inputs, §2.2) |
| `selection_policy_version` | `sel-v1.0-<hash>` of a canonical dump of every selection constant: `assign_grade`, risk filter, execution rules, `MARKET_RULES`, BILQE weights/tiers, `league_profiles` seed |
| `runtime` | `python 3.11.x` + SHA-256 of `requirements-lock.txt` |
| `frozen_at`, `approved_by` | timestamp, owner |

### 2.2 Frozen calibration snapshot
New tables, written once by a `freeze_baseline` command and never by any automatic job:
- `calibration_models_frozen(version, market_type, fitted_json, …)` — copy of the models P9 would load at freeze time.
- `confidence_adjustments_frozen(version, league_name, …)` — copy of `model_confidence_adjustments`.
- `gate_market_stats_frozen(version, market_type, samples, hit_rate, brier)` — the P7 aggregates.
- `pick_calibrator_frozen(version, state_json)` — P5 state (identity at v1.0, since there are 0 settled picks).
- `qualification_history_frozen(version, clv_mean, clv_n, roi, roi_n)` — P6 inputs (neutral at v1.0).

### 2.3 Freeze switch
`BASELINE_FREEZE=football-v1.0` (env), read once at startup and exposed as `config.BASELINE_VERSION`. When set:

| Pathway | Behaviour when frozen |
|---|---|
| P1, P2 nightly job | still runs, but writes to `calibration_models_shadow` / `confidence_adjustments_shadow` only; production singletons are never replaced |
| P3, P4 admin endpoints | `409 Conflict` ("baseline frozen") unless `?target=shadow` |
| P5 | `run_pipeline` loads `pick_calibrator_frozen` instead of `fit_from_db` |
| P6 | BILQE reads `qualification_history_frozen` |
| P7 | gate reads `gate_market_stats_frozen` and frozen league reliability |
| P9 | isotonic singleton loads `calibration_models_frozen` for the manifest's version |
| P10, P12, P13 | startup integrity check recomputes the manifest hashes and the Python minor; **any mismatch → official pick generation refuses (fail closed) with a logged reason**; analysis still serves, labelled non-official |
| P8, P11, P14 | unchanged — data collection and settlement continue |
| P15 | unchanged — shadow/research continues in its own DB |

When `BASELINE_FREEZE` is unset, behaviour is exactly today's (golden test unchanged).

### 2.4 Provenance on every official prediction
`picks` gains `model_version`, `calibration_version`, `selection_policy_version`, `code_sha`, `runtime` (ALTER TABLE ADD COLUMN, NULL for historical rows), filled from the manifest by `insert_pick`. `get_experiment_progress()` counts only rows whose versions equal the active manifest — a pick produced under any other version cannot enter the 1,000.

### 2.5 Verification (tests to add with the implementation)
1. Frozen mode, simulate 30 days: settle picks, grow `prediction_errors`, run the nightly job and `run_pipeline` daily → official probabilities and grades for a fixed fixture set are byte-identical every day; shadow tables did change.
2. Tamper tests: change a pkl, a gate constant, or run on 3.13 → official pick generation refuses.
3. Admin retrain/settle-date in frozen mode → 409; with `target=shadow` → production unchanged.
4. Every inserted pick carries the manifest versions; mismatching rows are excluded from the experiment count.
5. Golden test unchanged with the freeze switch off.

## 3. Open points for the owner
- P11 (team Elo/form updating after each match) is treated as **input data** that keeps evolving — that is how the Poisson model is meant to see current form. Confirm, or the v1.0 definition must snapshot team state too (which would make predictions increasingly stale).
- P13: the official baseline should run on the canonical 3.11 runtime; the running app moves from `.venv` (3.13) to 3.11 at the cutover.

## 4. Implementation (M2 package C)

Implemented in `src/baseline/` (`state.py`, `freeze.py`, `fingerprint.py`, `tables.py`), `scripts/freeze_baseline.py`, and freeze-aware branches in the modules below. With `BASELINE_FREEZE` unset every branch takes the pre-M2 path (golden references unchanged).

| Pathway | Frozen-mode behaviour (as built) | Where |
|---|---|---|
| P1 nightly isotonic refit | runs on a separate `IsotonicCalibrator(production=False)` that writes `calibration_models_shadow` | `system_service.run_nightly_refit`, `isotonic_calibrator._store_model` (`write_target`) |
| P2 nightly league rebuild | `_update_league_profile` writes `model_confidence_adjustments_shadow` | `error_intelligence` (`write_target`) |
| **P18 (new, found in M2)** per-result league update `settle_prediction → _update_league_profile` (results pipeline, every finished match) | same shadow routing as P2 | `error_intelligence` |
| P3 `POST /api/admin/retrain-engine` | production singleton refuses (`BaselineFrozenError`) → HTTP 409 | `isotonic_calibrator.fit_all`, handler in `api/main.py` |
| P4 `GET /api/debug/settle-date/{d}` | not refused: outcome data and team ratings are inputs (Decision 1); its league rebuild is routed to shadow | — |
| P5 per-run pick calibrator | loads the frozen `pick_calibrator` state; never refits | `pipeline._pick_calibrator` |
| P6 BILQE history | frozen `qualification_history` artifact | `qualification_engine._pick_history_for_qualification` |
| P7 runtime gate | frozen `gate_market_stats`; league reliability from the frozen adjustments | `performance_gate` (`read_source`) |
| P9 isotonic load / league-adjustment reads | read `*_frozen WHERE baseline_version = <active>` | `read_source` in `isotonic_calibrator`, `error_intelligence`, `performance_gate` |
| P10/P12/P13 code, artifacts, runtime | content SHA-256 of model / calibration / selection files and model pickles, Python minor + lock hash, frozen-table and artifact hashes, `league_profiles` hash, manifest file = DB copy | `state.integrity_report` |
| Official picks | `run_pipeline` calls `require_official_integrity` first; any mismatch → no picks, `baseline_integrity_error` in the result, CRITICAL log | `pipeline.run_pipeline` |
| Provenance | every stored pick: `baseline_version`, `model_version`, `calibration_version`, `selection_policy_version`, `code_revision` (git SHA, `+dirty` if modified), `runtime`, `input_data_timestamp` (UTC, when inputs were resolved), `inputs_json` (resolved team stats, raw and calibrated probabilities) | `picks` columns, `picks_repo` |
| Point-in-time reproducibility | `reproduce_probabilities(inputs_json, frozen calibrator)` recomputes stored probabilities exactly | `pipeline` |
| Experiment count | counts a settled pick only if all provenance fields are present and equal the active manifest | `picks_repo.get_experiment_progress` |
| Status | `GET /api/baseline/status`; integrity logged at API startup | `system_service` |

Deviation from §2.2: frozen calibration/adjustment data live in `<table>_frozen` copies (same columns + `baseline_version`) instead of separately named tables; JSON artifacts live in `baseline_frozen_artifacts`. The guarantee is the same: production readers cannot see later writes, and any edit to the frozen rows is detected by hash.

Evidence: `tests/test_baseline_freeze.py` (14 tests: immutability, routing, counterfactual drift without the freeze, 409, tamper → refusal for frozen table / artifact / league profiles / model file / runtime, provenance + exact reproduction, Elo/form still updating), `docs/M2_FREEZE_SIMULATION.md` (30 simulated days on a copy of the production DB, 0 invariant violations).

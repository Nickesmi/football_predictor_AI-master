# Automatic Recalibration Review (M1, item 9)

Date: 2026-10-11 · Status: **review only — no behaviour changed** (owner approval required).

**Answer: yes.** Three automatic mechanisms can change production probabilities without any code change, and one of them is changing them every night right now. Unless frozen or pinned, they would alter probabilities during the 1,000-pick baseline, which `baseline_contract.md` forbids ("alter production probabilities").

## 1. Nightly job in the API process — `api/services/system_service.py::_nightly_retrain_loop`

Started by the FastAPI lifespan (`start_background_tasks`). Runs daily at **03:00 server-local time**, guarded by `.cache/retrain.lock` (skips if run < 1 h ago). Same work is exposed manually as admin `POST /api/admin/retrain-engine`.

### 1a. Isotonic calibration — `IsotonicCalibrator.fit_all(conn)`

| Aspect | Finding |
|---|---|
| Training data | `prediction_log` rows with `actual_outcome IS NOT NULL` (legacy table) |
| Replacement rule | new fit replaces the in-memory/DB model only if it beats the old one on the 20 % hold-out split (champion/challenger) |
| Where applied | `_compute_match_analysis` (only for market types not on the bypass list — effectively BTTS and a few others) and `classic_prediction.py` |
| Observed (live DB, read-only) | 1,004 `calibration_models` rows; latest refit 2026-10-11 00:11 UTC; **the last 5 fits per market are byte-identical** |
| Why stable today | `prediction_log` has received no rows since 2026-08-31, so every refit sees the same data |
| When it would change outputs | (1) `prediction_log` resumes receiving rows; (2) **the running app is restarted on the M0 code** — fix F10 orders the 80/20 split by `match_date` instead of row id, so the next 03:00 refit trains on a different split and may replace the current models |

### 1b. League confidence adjustments — `rebuild_confidence_adjustments(conn)`

| Aspect | Finding |
|---|---|
| Training data | `prediction_errors` (still growing: +15 rows on 2026-10-11) |
| Output | `model_confidence_adjustments` (412 leagues; refreshed 2026-10-11 00:11 UTC) |
| Where applied | `get_league_adjustment` → `apply_league_adjustment` on **1X2 in `_compute_match_analysis`**; also read by `performance_gate` (league reliability) and BILQE |
| Weighting | sample weight 0 (< 50), 0.25 (< 100), 0.50 (< 250), 0.75 (< 500), higher above |
| Currently active | 7 leagues with status `developing`, e.g. Premier League n = 254 (factor 1.169), Friendlies Clubs n = 1,552 (1.331) |
| Effect | **1X2 probabilities for these leagues drift every night** as samples grow and weights step up |

## 2. Per-run bin calibrator in the investment pipeline — `src/engine/pipeline.py`

`run_pipeline` calls `_calibrator.fit_from_db(conn)` on **every run**, fitting `ProbabilityCalibrator` (10 bins) on settled `picks` (`result IN ('won','lost')`). Applied to 1X2, O/U 2.5 and BTTS before grading.

- Today: no effect (0 settled picks).
- Once the baseline starts: every daily run refits on the accumulating sample, so **the probabilities that decide which picks enter the frozen sample would change during the sample itself**. This is the most direct conflict with the Golden Rule.

## 3. Not automatic

`POST /api/admin/retrain-engine` (manual; admin-only — after M1 it is disabled unless `ADMIN_API_KEY` is configured).

## Options for the owner (none implemented)

| Option | Effect | Cost |
|---|---|---|
| A. Freeze at baseline start | env flag disables the nightly loop and `fit_from_db` in `run_pipeline`; calibration/adjustment tables snapshotted and loaded read-only | simplest; calibration does not improve during the sample (which is the contract's intent) |
| B. Pin a versioned snapshot | record the calibration/adjustment version used for each pick; refits go to a shadow table only | more work; keeps learning visible without affecting the sample |
| C. Allow and document | accept drift; tag each pick with the calibration state at prediction time | baseline metrics then mix several model states — conflicts with "evaluation sample = v1.0 baseline" |

Recommendation: decide before M3 (baseline activation), together with M2. Option A matches `baseline_contract.md` most closely.

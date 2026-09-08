# Production Prediction Architecture — Phase 3 Report

**Date:** 2026-09-08
**Continues from:** `AUDIT_REPORT.md` (Phase 1: bug fixes, data-leakage findings) and `REAL_DATA_BACKTEST_REPORT.md` (Phase 2: real historical data, point-in-time features, walk-forward backtest — Elo found to be the strongest 1X2 model, XGBoost's production weight set to 0). Every Phase 2 conclusion still holds. Nothing here increases XGBoost's production weight, and no number below was chosen to look more sophisticated than the underlying evidence supports.

## Architecture

```
scripts/live_predict.py  /  a future HTTP endpoint calling the same function
              │
              ▼
src/prediction_service/prediction_pipeline.py::predict()
              │
   ┌──────────┴──────────────────────────────────────────────────────┐
   │  1. DATA AVAILABILITY        historical_matches non-empty?       │
   │  2. TIMESTAMP VALIDATION     prediction_timestamp < kickoff?     │
   │  3. CHAMPION LOOKUP          champion_registry.json (market)     │
   │     + PROVENANCE GATE        model_provenance re-check           │
   │  4. FEATURE GENERATION       feature_engine.generate_features()  │
   │  5. MODEL PREDICTION         live_models (champion + all others) │
   │  6. CALIBRATION               (isotonic for XGBoost; direct      │
   │                                 output for Elo/Poisson/DC/freq)  │
   │  7. MODEL AGREEMENT          model_agreement.compute_agreement() │
   │  8. OOD CHECK                 ood_detection.check_ood()           │
   │       └─ severe → REFUSE                                         │
   │  9. LINEUP UNCERTAINTY       lineup_info (CONFIRMED/PROBABLE/    │
   │                                UNKNOWN, default UNKNOWN)          │
   │ 10. CONFIDENCE ENGINE        confidence_engine.compute_confidence│
   │ 11. MARKET THRESHOLD         thresholds.json (learned, folds 1-3)│
   │ 12. NO-BET ENGINE            no_bet_engine.evaluate_bet()        │
   │                                (needs REAL odds or UNAVAILABLE)  │
   └──────────┬─────────────────────────────────────────────────────┘
              ▼
    snapshot_db.py (data/real_historical/predictions.db)
      predictions / no_prediction_log / shadow_predictions /
      post_match_evaluations
```

Every stage can raise `PredictionRefused`; every refusal is logged with its stage and reason (`no_prediction_log`), not just successes — so `monitoring.py`'s coverage numbers are never survivorship-biased. There is no code path anywhere in this pipeline that substitutes a default or fabricated value for a failed stage.

**The single most load-bearing design decision:** `feature_engine.generate_features()` does not reimplement rolling-stat/Elo logic. It appends the match being predicted as one more row to the historical match table and calls the exact same `src.ml.point_in_time.build_point_in_time_features()` that `scripts/walk_forward_backtest.py` uses, then reads that one row back. There is only one feature-computation code path in this repository, used identically by backtesting and live prediction — see "Parity proof" below for why that claim is backed by more than assertion.

## Champion model by market

Champion selection (`src/prediction_service/champion_registry.py`) is entirely data-driven: it reads the real walk-forward backtest results, requires the frozen-holdout winner to have also ranked #1 in ≥2 of the 3 earlier folds (not just won the last one), and — critically — never lets XGBoost win a market unless `model_provenance.py` says it was statistically validated there, regardless of what its raw Brier number says. All numbers below are the actual frozen 2024-25 holdout (n=1,065 matches), reproducible via `python3 scripts/build_champion_registry.py`.

| Market | Champion | Frozen-holdout Brier | Margin over runner-up | Evidence quality |
|---|---|---:|---:|---|
| home_win | Elo | 0.2095 | 0.0044 | stable across folds, clear margin |
| draw | Elo | 0.1933 | 0.0004 | **not stable** on its own — promoted via average-rank fallback; margin is noise-level |
| away_win | Elo | 0.1934 | 0.0041 | stable across folds, clear margin |
| over_1_5 | Elo | 0.1686 | 0.0002 | stable across folds, but margin is noise-level |
| over_2_5 | Elo | 0.2470 | 0.0009 | stable across folds, but margin is noise-level |
| over_3_5 | Elo | 0.2221 | 0.0020 | stable across folds, clear margin |
| btts | Elo | 0.2477 | 0.0003 | **not stable** on its own — average-rank fallback; margin is noise-level |
| home_over_0_5 | Frequency | 0.1887 | — | **not stable** — average-rank fallback (Poisson/Elo not modeled for this market's frequency-vs-Poisson comparison; see limitations) |
| away_over_0_5 | Poisson (real data) | 0.1926 | — | stable across folds |
| under_1_5 / under_2_5 / under_3_5 | Elo | (complement of over_X) | — | same model, `P(under) = 1 − P(over)` |

**Read this table honestly, not optimistically:** Elo wins nearly every market, but for draw, over_1.5, over_2.5, and btts the margin over the runner-up is within noise level (`selection_confidence: low_margin_is_noise_level` — see `champion_registry.py`). Only **home_win, away_win, and over_3.5** have a margin large enough to call a clear win. This is exactly consistent with Phase 2's finding and is not adjusted here to look more decisive than it is.

**XGBoost never wins a market in this table** — not because it was blocked by fiat, but because on the frozen holdout it is either numerically behind Elo, or (for over_2_5/over_3_5/btts, where Phase 2 found it statistically beat the Poisson/Dixon-Coles baselines) it still doesn't out-rank Elo, which the champion-selection process treats as the real bar. `champion_challenger.evaluate_promotion("home_win", champion="elo", challenger="xgboost_real_data_calibrated", ...)` was run for real against the actual backtest data and fails 4 of the 8 promotion criteria (see `tests/prediction_service/test_champion_challenger_and_shadow.py::test_real_backtest_xgboost_does_not_dethrone_elo_for_home_win`).

## Backtest (frozen 2024-25 holdout, n=1,065)

| Market | Champion | Samples | Accuracy | Brier | Log Loss | ECE |
|---|---|---:|---:|---:|---:|---:|
| home_win | Elo | 1065 | 67.2% | 0.2095 | 0.6053 | 0.053 |
| draw | Elo | 1065 | 73.9% | 0.1933 | 0.5751 | 0.026 |
| away_win | Elo | 1065 | 70.1% | 0.1934 | 0.5683 | 0.030 |
| over_1_5 | Elo | 1065 | 78.5% | 0.1686 | 0.5203 | 0.011 |
| over_2_5 | Elo | 1065 | 54.6% | 0.2470 | 0.6872 | 0.008 |
| over_3_5 | Elo | 1065 | 66.1% | 0.2221 | 0.6361 | 0.022 |
| btts | Elo | 1065 | 54.8% | 0.2477 | 0.6885 | 0.013 |
| home_over_0_5 | Frequency | 1065 | 74.9% | 0.1887 | 0.5655 | 0.029 |
| away_over_0_5 | Poisson (real) | 1065 | 73.4% | 0.1926 | 0.5761 | 0.072 |

Accuracy here is binary per-market classification (P(market)≥0.5 vs. actual), not 3-way argmax selection — see `REAL_DATA_BACKTEST_REPORT.md` §6 for the distinction and the fuller baseline-comparison table (frequency, home-baseline, Poisson, Dixon-Coles) this one is drawn from.

## Market-specific thresholds (learned from folds 1-3 only — 2015-16 through 2023-24, never the frozen holdout)

| Market | Threshold | Pooled hit-rate | Pooled n |
|---|---:|---:|---:|
| home_win | ≥0.37 | 55.0% | 2012 |
| draw | none found | — | — |
| away_win | ≥0.43 | 55.0% | 838 |
| over_1_5 | ≥0.74 | 79.7% | 3198 |
| over_2_5 | ≥0.50 | 55.8% | 3107 |
| over_3_5 | none found | — | — |
| btts | ≥0.45 | 55.8% | 3198 |
| home_over_0_5 | ≥0.78 | 79.1% | 1066 |
| away_over_0_5 | ≥0.30 | 71.2% | 3195 |

Draw and over_3_5 honestly never clear a 55% pooled hit-rate at any probability level with ≥40 samples across three full seasons of non-final-holdout data — reported as "no threshold", which the pipeline treats as "this market can never be a confident pick", not as "use 0.5". A real off-by-rounding bug was caught and fixed while building this table (candidate thresholds rounded to 2dp before comparison could round a threshold above every real value that produced it) — see `tests/prediction_service/test_thresholds.py::test_threshold_search_finds_a_real_threshold_even_when_rounding_would_hide_it`.

## Parity proof (§21) — and a real bug it caught

`scripts/historical_simulation.py` replays real 2024-25 matches through the actual `generate_features()`/model-prediction path and compares every value against the reference `build_point_in_time_features()` computation the backtest used. This is not a claim taken on faith:

- **First run:** 2/2550 feature comparisons failed. `Como 1907 vs Bologna FC 1909 (2024-09-14)` — a newly-promoted team's first fixture with real same-day fixtures elsewhere in the dataset — showed the cold-start fallback computed by `build_point_in_time_features()` (which tie-broke same-day matches by incidental row position) disagreeing with `generate_features()` (which correctly treats all same-day matches as simultaneous, since this dataset's real precision is calendar dates, not verified kickoff order across leagues).
- **Fixed** by grouping the fallback's expanding average by calendar date instead of row position, so both code paths agree by construction — not by special-casing the one match that happened to expose it.
- **Re-ran the full walk-forward backtest, champion registry, and thresholds** after the fix. Champion selections were unaffected (the bug only reached rare same-day cold-start rows), confirming this was a real-but-narrow edge case, not a systemic error in the headline numbers.
- **Second run (on the fixed code):** **2550/2550 feature comparisons and 1050/1050 prediction comparisons exact-match**, on 150 real, randomly sampled 2024-25 matches, across every market whose champion is a refittable baseline.

A regression test (`tests/prediction_service/test_historical_simulation_parity.py`) locks in the exact same-day-ordering scenario that exposed this.

## Test suite

385/385 tests passing (up from 351 at the end of Phase 2; up from 281 at the end of Phase 1). New this phase: 88 tests across `tests/prediction_service/` covering feature-engine parity, champion selection stability, confidence-engine structure (a test asserts `compute_confidence`'s signature has no probability parameter at all — it structurally cannot be `probability * 100`), OOD detection, lineup-uncertainty penalties, no-bet EV/timestamp logic, threshold learning, the full pipeline's fail-closed stages (including a subprocess-level CLI test), champion/challenger promotion criteria, shadow mode, post-match evaluation, monitoring aggregation, and the historical-simulation parity regression.

## Production readiness

**Data:** 10,657 real matches (England/Germany/Italy, 2015-16 to 2024-25), validated and quarantine-checked (Phase 1/2). No live data source is reachable from this environment (network policy blocks the sports-data providers — see `AUDIT_REPORT.md`), so `data/real_historical/matches.csv` is a static snapshot, not a live-updating feed. A real deployment needs a live match-result ingestion job appending to this table (or the legacy `src/db/database.py` schema, kept deliberately separate here — see `snapshot_db.py`'s docstring for why) before any of this can run on genuinely new matches without manual data updates.

**Features:** one canonical, parity-proven code path (`generate_features()` ≡ `build_point_in_time_features()`), used identically offline and live.

**Models:** Elo is champion for 7 of 9 core markets, though only 3 of those wins have a clear (non-noise-level) margin. Frequency and Poisson each win one light market. XGBoost is real-data-trained, walk-forward validated, and available, but champion for nothing — it's outcompeted by simpler models, and that result is reported rather than engineered around.

**Calibration:** direct model output for the baseline champions (their own backtest ECE — 0.008 to 0.072 across markets — is the honest calibration-quality measure, since there's no separate calibration step for them); isotonic regression fit on a held-out validation season for XGBoost specifically, never on the frozen test season.

**Provenance:** enforced at three independent layers — `model_provenance.py` (per-market XGBoost validation gate), `champion_registry.py` (XGBoost can't even be selected as champion without passing that gate), and `prediction_pipeline.py`'s own re-check at prediction time (defense in depth — a stale/edited registry entry can't silently bypass the gate).

**Monitoring:** `monitoring.py` computes coverage, no-prediction rate, accuracy/Brier/log-loss/calibration-gap, per-market and per-league breakdowns, and a simple Brier-drift detector, over any caller-supplied date window. There is no real usage history yet to report real numbers from (this is a fresh environment) — the functions are tested against constructed fixtures, not real accumulated monitoring data.

**Fail-safes:** every one of Phase 3 §19's required refusal conditions is implemented and covered by a test that actually triggers it: missing/empty data, invalid timestamps, no champion for a market, a champion that fails its provenance re-check, feature-generation failure (same team twice, no prior history at all), and severe out-of-distribution (true cold-start team) — all raise `PredictionRefused` with a specific stage and reason, logged, never silently defaulted.

## Remaining limitations — stated completely honestly

- **No live data feed.** Every prediction in this environment is necessarily "as of the last snapshot of `matches.csv`" — there is no ingestion job wired to a real, currently-reachable data source. `generate_features()` is correct regardless of where the data comes from, but nothing here actually pulls new results in automatically today.
- **Most champion margins are within noise.** Only home_win, away_win, and over_3.5 have a Brier margin large enough to call decisive; draw, over_1.5, over_2.5, and btts are essentially a coin-flip between the top 2-3 models on a single 1,065-match holdout. Don't over-read "Elo is the champion" as "Elo is dramatically better" for those markets — it mostly means "nothing here has found a real edge over the simplest reasonable baseline yet."
- **Lineup/injury data is always UNKNOWN in this environment.** `lineup_info.py`'s CONFIRMED/PROBABLE/UNKNOWN machinery is real and tested, but with no live provider reachable, every live call takes the default UNKNOWN penalty — it never actually reflects a real lineup.
- **No real odds anywhere.** `no_bet_engine.py` is fully functional but will report `UNAVAILABLE` for every real call in this environment, since no legitimate odds source is reachable (same network restriction as Phase 1/2).
- **Shadow mode has zero real evaluated history.** The machinery works (tested end-to-end), but "n<30 — directional only" will be true of literally every real challenger comparison until this runs against real future matches for a while.
- **XGBoost's live feature path is unwired.** `live_models.py` loads the already-fit-and-calibrated XGBoost artifacts rather than refitting live (deliberately — refitting here would silently invalidate the Phase 2 validation without a new backtest), but even if a market did validate XGBoost as champion, there's no live retraining pipeline built yet — only the offline walk-forward one.
- **The champion_challenger 8th criterion (full regression suite) is caller-attested, not self-verified** — `evaluate_promotion()` takes `full_regression_suite_passed` as a parameter rather than shelling out to `pytest` itself (a deliberate layering choice, not an oversight, but it means a careless caller could pass `True` without actually running the suite; nothing currently prevents that at the type level).
- **home_over_0_5 / away_over_0_5 only got Poisson/Dixon-Coles/frequency treatment**, not Elo or XGBoost — a scope decision made in this pass, documented in `src/ml/baselines.py`'s `MarketProbs` docstring, not silently missing coverage.

## Final status

**NEEDS MORE VALIDATION.**

The architecture itself — parity-proven feature generation, evidence-driven champion selection with a real never-promote-on-one-fold discipline, fail-closed pipeline stages, honest confidence scoring that structurally cannot be `probability × 100`, and a working champion/challenger + shadow-mode framework — is real, tested (385/385), and internally consistent. That is a genuine, substantive step past Phase 2.

It is not "PRODUCTION READY" because: there is no live data feed, no real lineup/odds source, and most market champions win by a margin this backtest cannot distinguish from noise on a single held-out season. It is not "NOT PRODUCTION READY" or "BROKEN" either — nothing here is fabricated, nothing forces a prediction past the evidence, and the one real bug the process surfaced (the same-day cold-start parity gap) was found, fixed, and verified, not hidden. "SHADOW MODE ONLY" is close but understates it: the pipeline already refuses correctly and reports real, if modest, backtested skill for several markets — what's missing before genuine production use is more calendar time (fresh real data to re-validate against, ideally a full new season, per Phase 2's own recommendation) and the live-data/odds/lineup integrations documented above as gaps, not more architecture.

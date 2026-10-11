# Real-Data Walk-Forward Backtest — Phase 2 Report

**Date:** 2026-09-08
**Continues from:** `AUDIT_REPORT.md` (Phase 1). That report's conclusions are unchanged: the unknown-team confidence bug is fixed, the mislabeled backtest endpoint is flagged, the fake CLV counter is removed, and the bundled `models/xgb_*.pkl` remain synthetic-trained.
**This report answers a different question:** given *real* historical football data and strict point-in-time validation, does any model here actually predict unseen matches better than strong, simple baselines? All numbers below come from code in this repository actually executing against real match data — none are invented. Reproduce with `python3 scripts/build_real_dataset.py && python3 scripts/walk_forward_backtest.py`.

## 1. Data

The live production database in this environment has 0 real historical matches (see `AUDIT_REPORT.md`), and outbound access to the odds/fixture providers the live system normally uses is blocked by this environment's network policy. To do a genuine backtest at all, a real dataset was needed from somewhere legitimate.

**Source:** [openfootball](https://github.com/openfootball) — a long-running, public-domain project that compiles official football results into a plain-text format. This is real match data (real teams, real scores, real dates), not synthetic and not fabricated. `raw.githubusercontent.com` was reachable from this environment; the sports-data provider APIs the live system normally uses (`api.football-data.org`, `api.sofascore.com`) were not.

| | |
|---|---|
| Number of real matches | 10,657 |
| Leagues | Premier League (England), Bundesliga (Germany), Serie A (Italy) |
| Seasons | 2015-16 through 2024-25 (10 seasons) |
| Date range | 2015-08-08 to 2025-05-25 |
| Quarantined (rejected, not inserted) | 3 matches — all tagged `[awarded]` (forfeited results, not organic on-pitch outcomes) |
| Per-file integrity check | 30/30 season files match their declared `# Matches N` header exactly (accounting for quarantined rows) |
| Fields available | date, kickoff time (where present), season, league, home/away team, home/away goals, half-time goals (where present) |
| Fields explicitly UNAVAILABLE (not estimated, not fabricated, not present at all) | xG, shots, shots on target, possession, corners, cards, team ratings, odds, injuries, lineups |

**Missing-data percentage:** N/A for the fields this dataset provides — every retained row has a complete date, teams, and full-time score (that's what the validator enforces; incomplete rows are quarantined, not filled in). For the fields listed as unavailable above, that's 100% missing because the source has none — not something to compute a percentage of.

## 2. Data validation (Phase 2 §2)

Implemented in `src/data/openfootball_parser.py`, enforced before insertion, tested in `tests/real_data/test_openfootball_parser.py`:

- Duplicate matches (same league/season/date/home/away) → rejected, kept first occurrence only.
- Invalid/implausible scores (negative, >20 goals) → quarantined.
- Home/away team name equal → quarantined.
- Half-time score inconsistent with full-time score (HT > FT) → quarantined.
- Non-organic results (`[awarded]`, `[abandoned]`, `[postponed]`, `[cancelled]`) → quarantined, not treated as real football outcomes.
- Date parsing infers the correct calendar year from the season string (openfootball's export omits the year on most lines) and is tested against both the pre-2018-ish `TeamA v TeamB` format and the newer `TeamA score TeamB` format.
- Per-file expected-vs-parsed count cross-check against each file's own declared match count header.

## 3. Point-in-time feature reconstruction (Phase 2 §4) — and a leakage bug this caught

`src/ml/point_in_time.py` builds one feature row per match using **only matches strictly before that match's date**: Elo entering the match (standard incremental Elo, home-advantage adjusted), rolling scored/conceded (last 5 and last 10 matches, any venue), venue-specific rolling scored/conceded, and recent form points — all computed via `shift(1)` before any rolling/expanding window, so a match can never see its own result or a future one.

**A genuine leakage bug was found and fixed during this work, by its own test suite:** the cold-start fallback (used when a team has zero prior matches — e.g. a newly-promoted team's very first game in the dataset) was originally computed as a single constant — the league-wide average goals over the *entire* dataset, including seasons that hadn't happened yet relative to that early match. `tests/real_data/test_point_in_time_leakage.py::test_future_match_never_leaks_into_an_earlier_matchs_features` caught this directly: it plants an extreme score in a later match and asserts an earlier match's features don't change — they did, because the cold-start default shifted. Fixed by replacing the global constant with a chronologically **expanding** average (goals-per-team-per-match using only matches before the current one), re-verified by the same test. This is exactly the kind of automated leakage test Phase 2 §3 asks for, and it did its job.

`assert_no_leakage()` independently recomputes a same match's "matches played before" count from a raw date-filtered slice and cross-checks it against the vectorized feature-builder's output — this runs automatically at the start of every backtest (`scripts/walk_forward_backtest.py`) and would abort the run if it ever disagreed.

## 4. Walk-forward validation (Phase 2 §5)

Chronological, expanding-window, never shuffled:

| Fold | Train | Validate (calibration only) | Test |
|---|---|---|---|
| 1 | 2015-16 … 2019-20 | 2020-21 | 2021-22 |
| 2 | 2015-16 … 2020-21 | 2021-22 | 2022-23 |
| 3 | 2015-16 … 2021-22 | 2022-23 | 2023-24 |
| 4 (**frozen final holdout**) | 2015-16 … 2022-23 | 2023-24 | **2024-25** |

The 2024-25 season was never used for feature design, hyperparameter choices, calibration fitting, or threshold decisions — every one of those was fixed before this fold ran, and its numbers below come from a single evaluation pass. `tests/real_data/test_walk_forward_and_baselines.py` enforces the fold chronology and that the last fold's test season is the dataset's final season programmatically (not just by convention).

## 5. Models

| Model | Training data | Features | Validation |
|---|---|---|---|
| Frequency baseline | Train split's empirical class rate | none (constant) | walk-forward |
| Home-team baseline | Train split's majority class per market | none (degenerate) | walk-forward |
| Elo | None fit besides Elo K/home-adv constants (standard values); outcome mapping (elo-diff → market probability) is a logistic regression fit on train | point-in-time Elo diff | walk-forward |
| Poisson (real data) | League scoring profile from train | point-in-time rolling scored/conceded (10-match) | walk-forward |
| Dixon-Coles | Same as Poisson + `rho` fit by MLE on train | same + low-score correction | walk-forward |
| XGBoost (real data) | `src/ml/real_data_trainer.py`, fit on train only | full point-in-time feature set (Elo, rolling 5/10, venue splits, form, data-sufficiency) | fit(train) → calibrate(val, isotonic) → evaluate(test) once |
| XGBoost (bundled, `models/xgb_*.pkl`) | **Synthetic only** — excluded from this backtest entirely; see `AUDIT_REPORT.md` §3 | n/a | not applicable — never evaluated against real data because it was never trained on any |
| Market-implied odds | **Unavailable** — no real historical odds obtained in this environment (network policy blocks the odds provider; openfootball carries no odds) | n/a | n/a |

## 6. Performance — frozen final holdout (2024-25 season, n=1,065 matches, touched once)

Binary per-market metrics (each market scored as its own yes/no classification — e.g. "home_win" is P(home wins) vs. actual home-win indicator; markets are not mutually-exclusive-selection accuracy).

### Home Win

| Model | Accuracy | Log Loss | Brier | ECE |
|---|---:|---:|---:|---:|
| Frequency baseline | 0.602 | 0.676 | 0.242 | 0.045 |
| Home-team baseline (always "yes") | 0.398 | 9.284 | 0.602 | 0.602 |
| **Elo** | **0.672** | **0.605** | **0.210** | 0.053 |
| Poisson (real data) | 0.645 | 0.637 | 0.223 | 0.067 |
| Dixon-Coles | 0.642 | 0.641 | 0.224 | 0.082 |
| XGBoost (real data, calibrated) | 0.664 | 0.631 | 0.215 | 0.055 |

### Draw

| Model | Accuracy | Log Loss | Brier | ECE |
|---|---:|---:|---:|---:|
| Frequency baseline | 0.739 | 0.575 | 0.193 | 0.020 |
| Elo | 0.739 | 0.575 | 0.193 | 0.026 |
| Poisson (real data) | 0.739 | 0.577 | 0.194 | 0.043 |
| Dixon-Coles | 0.739 | 0.575 | 0.193 | 0.030 |
| XGBoost (real data, calibrated) | 0.739 | 0.601 | 0.194 | 0.028 |

*Every model — including the trivial "always predict the base rate" frequency baseline — lands within 0.001 Brier of each other on draws. None of them found real signal beyond the base rate. This matches well-known football-analytics literature: draws are the hardest outcome to predict, and no model here is an exception.*

### Away Win

| Model | Accuracy | Log Loss | Brier | ECE |
|---|---:|---:|---:|---:|
| Frequency baseline | 0.659 | 0.643 | 0.225 | 0.024 |
| **Elo** | **0.701** | **0.568** | **0.193** | 0.030 |
| Poisson (real data) | 0.639 | 0.615 | 0.213 | 0.093 |
| Dixon-Coles | 0.642 | 0.612 | 0.212 | 0.084 |
| XGBoost (real data, calibrated) | 0.688 | 0.584 | 0.199 | 0.054 |

### Over 1.5 / Over 2.5 / Over 3.5 / BTTS (Brier score, frozen holdout)

| Market | Frequency | Elo | Poisson | Dixon-Coles | XGBoost (real, cal.) |
|---|---:|---:|---:|---:|---:|
| Over 1.5 | 0.169 | 0.169 | 0.179 | 0.179 | 0.173 |
| Over 2.5 | 0.248 | 0.247 | 0.260 | 0.260 | 0.247 |
| Over 3.5 | 0.224 | 0.222 | 0.236 | 0.236 | 0.226 |
| BTTS | 0.247 | 0.248 | 0.258 | 0.257 | 0.249 |

## 7. Baseline comparison — stated plainly, per the audit's Final Rule

**Elo is the strongest model in this backtest for 1X2 (home/draw/away), full stop.** It matches or beats every other model — including the real-data-trained XGBoost — on home_win and away_win Brier score and log loss, across all four walk-forward folds, not just the final one (see the per-fold table in `data/real_historical/backtest_results.json`). The real-data XGBoost model does **not** clearly beat Elo anywhere.

**The real-data XGBoost model does beat the point-in-time Poisson/Dixon-Coles baselines** on several goals-markets (over_2_5, over_3_5, btts) and on away_win, with 95%-bootstrap-CI statistical significance (`data_leakage`-free, computed only on the frozen 2024-25 holdout — see `final_holdout_significance_vs_baselines` in the results JSON). It is also significantly *worse* than Elo on away_win and over_1_5.

Net effect, market by market, using the strict "beats at least one baseline with significance AND is not significantly worse than either" bar (`src/ml/model_provenance.py`):

| Market | Real-data XGBoost production-validated? |
|---|---|
| home_win | No |
| draw | No |
| away_win | No (significantly *worse* than Elo) |
| over_1_5 | No (significantly *worse* than Elo) |
| over_2_5 | **Yes** |
| over_3_5 | **Yes** |
| btts | **Yes** |

**The synthetic-trained bundled model (`models/xgb_*.pkl`) was not evaluated here at all** — it cannot be, since it was never trained on a single real match; comparing it to real-data baselines would be meaningless. It remains permanently `SYNTHETIC_EXPERIMENTAL`.

**Market-implied odds baseline: not available.** No real historical odds could be obtained in this environment (see §1). This baseline is not represented anywhere in the tables above rather than being filled in with invented odds.

### Out-of-distribution slice (thin-history teams)

Of 1,065 matches in the frozen holdout, 40 (3.8%) involved a team with fewer than 10 prior matches in the training-through-date history (newly promoted teams, mostly). Home-win Brier for the real-data XGBoost model: 0.229 on thin-history matches vs. 0.214 on sufficient-history matches — worse, as expected, though the sample (40 matches) is too small to draw a statistically confident conclusion from on its own; treat it as a directional flag, not proof, and see `data_sufficiency` in the feature table for how a live system could use this to reduce confidence or withhold a prediction per Phase 11.

## 8. Production gating (Phase 2 §7/§14)

`src/ml/model_provenance.py` is now the single source of truth for whether any model may influence a live prediction:

- **Synthetic model:** hard-coded `production_validated: False`, unconditionally, forever. No AUC value can change this.
- **Real-data model:** `production_validated` is computed per-market directly from the significance table above — being "real-data trained" is necessary but not sufficient.
- `src/engine/probability_engine.py::_market_weight()` — the function the live pipeline actually calls to decide how much weight an XGBoost prediction gets — now returns **0.0 unconditionally** for every market. This is deliberately more conservative than the provenance gate alone would require (over_2_5 and btts did clear the statistical bar): the live production database has zero real matches to build this model's point-in-time features from, and no code yet exists to build those features from the live `team_state`/`match_history` tables in the first place (`src/ml/point_in_time.py` currently only operates on the offline dataset built in this report). Wiring that up is real integration work for a future session, not a config flip — see the code comment in `probability_engine.py` for the exact reasoning, and don't re-enable a weight without re-reading it.

Regression tests: `tests/real_data/test_model_provenance.py` (5 tests) enforce this gate's logic, including that a real-data model significantly worse than a baseline is never validated regardless of provenance.

## 9. Statistical significance

All comparisons use a 2,000-resample bootstrap of the paired per-match Brier-score difference on the frozen 2024-25 holdout only (`_bootstrap_brier_diff_ci` in `scripts/walk_forward_backtest.py`), reporting the 95% CI of (Brier(XGBoost) − Brier(baseline)); a CI excluding zero is treated as significant. Full numbers for every market × baseline pair are in `data/real_historical/backtest_results.json`'s `final_holdout_significance_vs_baselines`. This is a single held-out season (1,065 matches); the significance calls above are honestly reported but should be read as "consistent with a real, if modest, effect on this season" rather than a large-sample guarantee — repeating this walk-forward process as more seasons accumulate would strengthen or weaken these specific per-market conclusions, and the code is built to make that trivial (re-run `scripts/walk_forward_backtest.py`).

## 10. Leakage — cumulative status across both audit phases

| | Phase 1 (`AUDIT_REPORT.md`) | Phase 2 (this report) |
|---|---|---|
| Detected | `/api/debug/backtest-features` uses live (non-point-in-time) team_state | Cold-start feature fallback leaked future seasons' scoring average into early matches |
| Fixed | Disclosed via warnings (structural fix deferred — needs live point-in-time infra) | **Fixed** — expanding-window fallback, caught and re-verified by `tests/real_data/test_point_in_time_leakage.py` |
| Remaining | Live `/api/debug/backtest-features` still leaks (documented, not silently trusted) | None known in the offline real-data pipeline; `assert_no_leakage()` runs automatically on every backtest execution |

## 11. Final verdict

**NEEDS MORE VALIDATION.**

Reasoning:
- The data pipeline, point-in-time feature reconstruction, walk-forward split, calibration-leakage discipline, and statistical-significance testing are now real, tested, and working — this was the actual Phase-2 deliverable, and it's in place with 23 new passing regression tests (304/304 total, up from 281/281 in Phase 1).
- The honest result of running it: **Elo — the simplest model in the comparison — is the best model for match-result (1X2) prediction**, and the real-data XGBoost model only significantly beats the baselines on three goals/BTTS markets, on a single held-out season. Per the audit's Final Rule, this is reported as-is rather than hidden or reframed as a win for the more complex model.
- Not "PRODUCTION READY": the validated real-data model has no live feature-extraction path wired to production, and production presently has 0 real matches to feed one anyway.
- Not "NOT PRODUCTION READY" / "BROKEN": the existing production system already defaults to Poisson (not the synthetic XGBoost, which is now hard-disabled at weight 0.0 by code, not just convention) for exactly the markets where this backtest shows Elo is stronger — the live system's actual conservative posture turns out to be closer to right than wrong, it just wasn't provable until this backtest existed.
- What "more validation" concretely means: (a) re-run this walk-forward process as additional real seasons accumulate, to see whether the over_2_5/over_3_5/btts significance holds up rather than being a one-season artifact; (b) if it does, build the live point-in-time feature path from `team_state`/`match_history` before enabling any nonzero production weight; (c) separately, consider whether adding Elo (not XGBoost) as a blended signal for 1X2 is worth pursuing, since it's the actual best performer here and isn't currently used in the live blend at all.

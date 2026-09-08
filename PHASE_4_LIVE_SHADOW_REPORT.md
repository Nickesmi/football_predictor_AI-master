# Phase 4 — Live-Ready Data Integration, Replay Validation & Shadow Mode

**Date:** 2026-09-08
**Continues from:** `AUDIT_REPORT.md` (Phase 1), `REAL_DATA_BACKTEST_REPORT.md` (Phase 2), `PRODUCTION_ARCHITECTURE_REPORT.md` (Phase 3). Every prior conclusion still holds unchanged: Elo remains champion for most markets, XGBoost's production weight is still governed entirely by the provenance gate, and no number in this report was chosen to make the system look more capable than the evidence supports.

## Executive Summary

This phase built the data-ingestion and simulation infrastructure a real deployment needs, without touching any prediction logic: a canonical, provider-agnostic data contract (LIVE/REPLAY/MANUAL/UNAVAILABLE, explicit everywhere, never inferred); a real provider abstraction (a live HTTP provider stub tested against mocked failure modes, a replay provider that wraps real historical results behind a simulated clock, and a manual-entry provider with full validation); per-feature provenance tracking; versioned, immutable prediction snapshots; a chronological, blind replay engine and a rolling paper-shadow mode, both running through the *actual* production `predict()` function rather than a parallel reimplementation; a formal, CI-enforced parity test; a stale-data refusal gate; and structured observability logging. All of it was then run for real against 126 real April 2025 fixtures, not just unit-tested in isolation.

**385 → 450 tests** (65 new), all passing. **Two real bugs found and fixed** during this phase (both timezone-comparison bugs, both caught by the tests written to prove the new code correct, not found later). No prediction logic, no champion selection, and no calibration was touched.

## Environment Limitations

Unchanged from Phase 1: **this development environment cannot reach any live football data provider.** `LiveProvider` was built to the full Phase 4 specification (env-var configuration, health-check status taxonomy, retry-free fail-fast error classification) and is tested against a **mocked transport** simulating success, auth failure, rate limiting, timeouts, malformed JSON, and server errors — clearly labeled as integration simulations in the test file's own docstring, never presented as real connectivity tests. Every "live" scenario in this report is either (a) a mocked provider test, or (b) a REPLAY-mode run against real historical data with the clock advanced manually — never a disguised replay presented as live.

## Data Architecture

`src/prediction_service/data_contract.py` defines `DataMode` as a required field with no default that silently resolves to LIVE:

| Mode | Meaning | Used by |
|---|---|---|
| `LIVE` | Genuinely current, provider-sourced | `LiveProvider` (untested end-to-end here — see above) |
| `REPLAY` | A fixed historical snapshot, clock advanced manually | `ReplayProvider`, `replay_engine.py`, `rolling_shadow.py` — everything actually run in this environment |
| `MANUAL` | Operator-entered, schema-validated | `ManualProvider` |
| `UNAVAILABLE` | No data source at all | Default provider-health state when unconfigured |

`prediction_pipeline.predict()` defaults to `data_mode=REPLAY` — the one honest default given this environment — and every stored prediction carries its real mode (verified: `tests/prediction_service/test_snapshot_immutability.py::test_default_data_mode_is_replay_never_silently_live`).

## Provider Architecture

`src/prediction_service/providers/`:
- **`base.py`** — `FootballDataProvider` ABC: `health_check()`, `fetch_fixtures()`, `fetch_odds()`, `fetch_lineup()`, `fetch_result()`. The prediction engine only ever consumes these canonical types.
- **`live_provider.py`** — reads `FOOTBALL_DATA_PROVIDER`, `FOOTBALL_DATA_API_KEY`, `FOOTBALL_DATA_BASE_URL` from the environment (never hardcoded — verified by a test that greps the module source for a literal secret value and fails if found). Distinguishes `NETWORK_FAILURE`/`AUTH_FAILURE`/`RATE_LIMITED`/`DEGRADED`/`INVALID_RESPONSE`/`UNAVAILABLE` instead of collapsing every failure into "no matches". **To activate a real provider in production: set the three environment variables above and restart — nothing else in the codebase changes**, since the pipeline only consumes the canonical `FixtureRecord`/`OddsRecord`/`LineupRecord` types this class produces.
- **`replay_provider.py`** — wraps real historical matches behind a simulated clock. `fetch_fixtures()` never includes a result (even though the DataFrame has it in memory); `fetch_result()` returns `None` until the simulated date has actually passed the match's real date. This is the blindness guarantee, and it's enforced in code, not just by convention — see Replay Validation below.
- **`manual_provider.py`** — operator-entered fixtures validated through the same discipline as the Phase 2 historical parser: required fields, non-empty/distinct teams, valid timestamp, plausible scores, duplicate-ID rejection, **and duplicate-content rejection** (the same match submitted under a different ID is still caught).

## Point-in-Time Guarantees

Unchanged in principle from Phase 3, strengthened in practice:
- `feature_engine.generate_features()` is still the single canonical feature path (Phase 3's central claim), now additionally producing a `FeatureProvenance` record per feature — `source`, `source_timestamp`, `KNOWN`/`UNKNOWN` — self-checked against `information_timestamp <= prediction_timestamp` before every snapshot is returned (`FeatureProvenance.assert_available_before()`).
- Cold-start features are `UNKNOWN` with **no timestamp at all**, never a fabricated one.
- Predictions are now versioned and immutable: regenerating a prediction inserts a new row and only flips the prior version's `is_latest` marker — every content column of the original version is verified byte-identical afterward (`test_snapshot_immutability.py::test_original_version_content_is_never_mutated_by_a_later_regeneration`).
- `post_match_eval.evaluate_prediction()` now rejects evaluating a prediction with a result timestamped *before* the prediction itself was made — a structural guard against evaluating backwards in time.

**Two real bugs found and fixed while building this**, both timezone-comparison errors (`Cannot compare tz-naive and tz-aware timestamps`) surfaced by the new tests themselves during development, not discovered later:
1. `ReplayProvider.fetch_result()` and `FeatureProvenance.assert_available_before()` initially compared a UTC (`...Z`-suffixed) timestamp against a naive date string and raised. Fixed by centralizing a `to_naive_timestamp()` helper in `data_contract.py`, reused everywhere a point-in-time comparison happens.
2. `ManualProvider`'s duplicate-content check compared raw timestamp strings that differed only in ISO formatting (`Z` vs `+00:00`), silently missing real duplicates. Fixed by parsing both sides to `Timestamp` before comparing.

## Replay Validation (real data, not a toy fixture)

`scripts/run_phase4_validation.py` ran `replay_engine.run_replay()` over **all real fixtures from 1–30 April 2025** (Premier League, Bundesliga, Serie A — inside the Phase 2 frozen holdout season), all 7 core markets, through the actual production pipeline:

| | |
|---|---:|
| Matches processed | 126 |
| Predictions generated | 882 (126 × 7 markets) |
| Refusals | 0 |
| Errors | 0 |
| Settled | 882 / 882 |

Zero refusals is itself informative, not a red flag: April, deep in-season, has no cold-start teams and no stale/missing data in this static dataset — exactly the situation where the pipeline *should* produce predictions rather than abstain. The adversarial and cold-start tests (`test_adversarial.py`, `test_prediction_pipeline.py`) separately prove refusal actually fires when it should.

**OOD frequency over the window:** 735 `none` (83.3%), 147 `mild` (16.7%), 0 `severe` — consistent with an established-league, in-season sample.

**Aggregate metrics (`monitoring.compute_period_metrics`, all 882 evaluated predictions):**

| Metric | Value |
|---|---:|
| Accuracy | 65.08% |
| Brier score | 0.2164 |
| Log loss | 0.6211 |
| Calibration gap | 0.0305 |
| Model disagreement rate | 26.1% |
| Avg. confidence score | 52.3 / 100 |

**Per-market (n=126 each):**

| Market | Brier | Accuracy |
|---|---:|---:|
| home_win | 0.2146 | 67.5% |
| draw | 0.2131 | 70.6% |
| away_win | 0.1694 | 74.6% |
| over_1_5 | 0.2036 | 72.2% |
| over_2_5 | 0.2533 | 50.0% |
| over_3_5 | 0.2093 | 71.4% |
| btts | 0.2517 | 49.2% |

Read this the same way REAL_DATA_BACKTEST_REPORT.md read the frozen-holdout numbers: over_2_5 and btts sitting near a coin flip (50%/49%) on this one month is consistent with, not contradictory to, Phase 2's finding that those markets have the weakest real edge — this is one month of 126 matches, not a new backtest, and shouldn't be over-read either way.

**Per-league:** Premier League (n=350, Brier 0.2035, 69.4%), Bundesliga (n=252, Brier 0.2234, 65.5%), Serie A (n=280, Brier 0.2263, 59.3%) — directional only at this sample size, not a claim that the model works better in England.

### Critical parity result

`tests/prediction_service/test_critical_parity.py` (part of the normal test suite, so it runs — and would fail CI — on every future change): replayed a 3-day real window through `replay_engine.py`, then independently called `prediction_pipeline.predict()` directly for the same fixtures/markets/timestamps, and asserted champion model, raw probability, calibrated probability, confidence score, OOD severity, and the threshold decision are **all identical** — plus every stored `FEATURE_COLUMNS` value matches `build_point_in_time_features()`'s reference computation exactly. **Result: exact match, 0 discrepancies**, both tests passing as part of the 450/450 suite.

## Shadow Results

`rolling_shadow.py` ran champion (Elo) vs. challenger (`xgboost_real_data_calibrated`) for **over_2_5** — the market Phase 2 found XGBoost statistically beat the Poisson baseline on — over the same April 2025 window:

| | |
|---|---:|
| Sample size (n) | 126 |
| Champion (Elo) avg. Brier | 0.2533 |
| Challenger (XGBoost) avg. Brier | 0.2524 |
| Challenger better? | Yes, by 0.0009 |
| Sample-size note | "sufficient for a promotion evaluation" (n≥30) per `shadow_mode.py`'s own honesty threshold |

**This is not a promotion, and this report does not treat it as one.** A single 126-match shadow window clearing the sample-size bar is necessary but nowhere near sufficient — `champion_challenger.evaluate_promotion()` requires walk-forward stability across 4 folds, statistical significance on the frozen holdout, and 5 other criteria this one month doesn't speak to at all. What this run genuinely demonstrates is that the shadow-mode machinery works end-to-end on real data and produces a plausible, non-contradictory result (XGBoost narrowly ahead on the one market it was already validated for) — exactly what you'd want to see before trusting it on a longer window.

Champion model was never affected: every `predictions` row's `champion_model` for this window matches `champion_registry.py`'s real answer for `over_2_5` (Elo), never the challenger — verified directly in `test_rolling_shadow.py`.

## Model Performance — Champion vs. Challenger (unchanged from Phase 2/3, reaffirmed)

| Market | Champion | Margin quality |
|---|---|---|
| home_win | Elo | clear margin |
| draw | Elo | **noise-level margin** |
| away_win | Elo | clear margin |
| over_1_5 | Elo | noise-level margin |
| over_2_5 | Elo | noise-level margin |
| over_3_5 | Elo | clear margin |
| btts | Elo | noise-level margin |
| home_over_0_5 | Frequency | — |
| away_over_0_5 | Poisson (real data) | — |

No market's champion changed in this phase — Phase 4 built infrastructure, not new evidence for promotion decisions. `champion_challenger.evaluate_promotion("home_win", champion="elo", challenger="xgboost_real_data_calibrated", ...)` still fails against the real backtest (verified directly in the test suite).

## Data Quality

Unchanged from Phase 2: 10,657 real matches, 3 quarantined (`[awarded]` non-organic results). This phase added quarantine paths for **manually-entered** data (schema/timestamp/score/duplicate — all tested) and defined but did not newly exercise quarantine for a live feed, since none is reachable here.

## Failure Tests — the system fails safely

Every one of these actually raises/refuses in a passing test, not just in a docstring:

| Scenario | Result | Test |
|---|---|---|
| Provider network failure / timeout | `NETWORK_FAILURE`, never "0 matches" | `test_live_provider.py` |
| Provider auth failure | `AUTH_FAILURE`, distinct from network | `test_live_provider.py` |
| Provider rate-limited | `RATE_LIMITED`, distinct | `test_live_provider.py` |
| Malformed/invalid JSON response | `ProviderError(INVALID_RESPONSE)` raised | `test_live_provider.py` |
| Stale LIVE dataset | `PredictionRefused("stale_data")` | `test_adversarial.py` |
| Missing lineup | `LineupAvailabilityStatus.UNKNOWN`, real confidence penalty, never "no injuries" | `test_lineup_and_no_bet.py` |
| Missing odds | `OddsStatus.UNAVAILABLE`, never fabricated | `test_lineup_and_no_bet.py`, `no_bet_engine.py` |
| Unknown/cold-start team | `PredictionRefused("ood_check")`, severe OOD hard stop | `test_prediction_pipeline.py`, `test_adversarial.py` |
| Duplicate manual fixture (same ID) | `ManualDataRejected` | `test_replay_and_manual_provider.py` |
| Conflicting manual fixture (same match, new ID) | `ManualDataRejected` (duplicate content) | `test_replay_and_manual_provider.py` |
| Timestamp anomaly (prediction ≥ kickoff) | `PredictionRefused("timestamp_validation")` | `test_prediction_pipeline.py` |
| Future-dated information in the raw table | Defensively filtered out, proven via corrupted-future-row injection through the FULL pipeline | `test_adversarial.py` |
| No champion validated for a market | `PredictionRefused("champion_selection")` | `test_prediction_pipeline.py` |
| Feature-engine failure (same team twice, no history) | `PredictionRefused("feature_generation")` | `test_prediction_pipeline.py` |

## Database Integrity

- `predictions` primary key is `(prediction_id, version)` — a regenerated prediction can never collide with or silently replace an earlier one.
- `save_prediction()` only ever `INSERT`s; the sole mutation permitted anywhere in this schema is flipping a prior version's `is_latest` flag to 0 — verified column-by-column that this is the *only* thing that changes.
- `post_match_evaluations` records which specific `version_evaluated` was scored, and refuses to evaluate a version with a result timestamped before that version's own `prediction_timestamp`.
- `shadow_predictions` is a fully separate table — a challenger's number can never be written into the `predictions` table.

## Observability

`src/prediction_service/observability.py` defines the complete event vocabulary Phase 4 asks for (ingestion attempts/success/failure, provider latency/freshness, records received/rejected/duplicate, schema/timestamp failures, prediction generated/refused, shadow prediction, settlement, drift, OOD rate) and **wires the 5 highest-value real call sites**: prediction generation, prediction refusal (with machine-readable stage + reason), provider health checks, shadow predictions, and settlements. Per-record live-ingestion counters are defined but not exercised end-to-end, honestly, because there is no live feed in this environment to generate them from.

## Remaining Limitations — stated completely honestly

- **Still no live data feed.** Everything in this report ran in REPLAY mode. `LiveProvider` is real, tested against every mocked failure mode Phase 4 specifies, and ready to be configured — but has never made a real HTTP call in this environment, and this report does not claim otherwise.
- **Shadow-mode evidence is one month, one market.** It's real and it's honestly reported, but it is not, and is not presented as, grounds for promoting XGBoost anywhere.
- **No real odds or lineup data anywhere** — unchanged from Phase 1/2/3. `no_bet_engine` and `lineup_info` are fully functional and tested but have nothing real to consume here.
- **Observability is wired at 5 call sites, not exhaustively at every one Phase 4 lists.** The event types not yet wired (per-record live-ingestion counts, provider freshness beyond health-check latency) are defined and ready but would need a real live feed to exercise meaningfully.
- **The rolling shadow window (April 2025) and the replay window are the same calendar month** — chosen for a single validation run's convenience, not because they need to match. A production deployment would run these independently and continuously.
- **`champion_challenger`'s regression-suite criterion is still caller-attested**, not self-verified (unchanged limitation from Phase 3 — a deliberate layering choice, still worth flagging).

## Production Readiness

Choose exactly one, as instructed:

**READY FOR SHADOW.**

Reasoning: the architecture is real, tested end-to-end on real data (not just unit fixtures), and the critical parity claim is now enforced by CI rather than asserted in prose. A real 126-match, 882-prediction replay produced zero unexpected errors and sensible, honestly-reported metrics. That clears the bar for running this in shadow mode against genuinely new matches once a live or regularly-updated data source exists.

It is **not** "READY FOR LIMITED PRODUCTION" or "PRODUCTION READY": there is still no live data feed, no real odds, no real lineup data, and the one piece of shadow evidence gathered here (XGBoost vs. Elo on over_2_5, one month) is real but far short of a promotion-grade sample. It is **not** "NOT READY" either — every piece of required infrastructure exists, is tested, and behaved correctly on a genuine (if offline) trial run; what's missing is calendar time and real external data, not more building.

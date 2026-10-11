# Football Predictor AI — Technical Audit
**Date:** 2026-09-13
**Scope:** Gated prediction pipeline (`picks` / `qualification_engine` path), per user direction. The legacy `prediction_log`/`main.py`/`streamlit_app.py` path was audited previously (see `audit_report.json`, `clean_audit_report.json`, `calibration_results.json`) and is out of scope for fixes this round.
**Governing constraint:** `baseline_contract.md` — no retraining, no ensemble/hyperparameter changes, no new features until 1,000 settled picks exist. That threshold is currently **0/1,000**. Every fix below is a bug/wiring fix; none retrains a model, changes ensemble weights, or adds a feature.

---

## 1. Executive summary

The gated pipeline (`picks` + `qualification_engine`) is well-designed on paper — a genuine multi-factor confidence engine (`risk_control.py`, `qualification_engine.py`), a real isotonic calibrator with chronological split and champion/challenger safety gate (`isotonic_calibrator.py`), and reachable NO-BET logic (`performance_gate.py`). But **it has never produced a single settled pick in production**, for three independent, compounding reasons:

1. The daily cron job that actually runs (`collection_worker.py`) never calls the code that writes to `picks` — it writes to the legacy `prediction_log` table instead (the one with confirmed leakage/duplicates from the prior audit).
2. Even when picks are manually generated via the one-off API endpoint, `settlement.py` looks up results using a team-name key built from a **different data provider** than the one used to generate the picks — a silent, permanent join miss.
3. The three shipped ML models (`models/xgb_*.pkl`) were trained entirely on **fabricated random data**, never a real match — confirmed by `training_metrics.json` (ROC AUC 0.51–0.58, essentially random).

None of this is a tuning problem. It's an integration/wiring problem plus one very serious training-data problem that the freeze correctly prevents me from silently patching by retraining.

---

## 2. Findings

### CRITICAL

**F1 — Scheduled worker never writes real picks to the `picks` table.**
- File: `scripts/collection_worker.py:82` calls `api.main._compute_match_analysis(...)`, which writes to `prediction_log` only.
- The only code path that inserts into `picks` is `src/engine/pipeline.py:202` (`insert_pick`), reachable only via `api/services/results_service.py:889 run_investment_pipeline_service()` → a manual, on-demand HTTP route (`api/routers/results.py:21`). No worker, cron entry (`scripts/orchestration/crontab.txt`), or systemd timer ever calls it.
- Consequence: three months after `baseline_contract.md` was written (2026-06-15), `picks` and `qualification_log` both still have 0 rows. The 1,000-pick evidence-gathering gate can never be satisfied as the system is currently wired.
- Fix: make the scheduled daily job call `run_investment_pipeline_service(today)` (or `run_pipeline` directly) for the day's fixtures, in addition to/instead of `_compute_match_analysis`.

**F2 — Settlement joins picks to results using a different provider's team names than the one used to generate the picks.**
- File: `src/engine/settlement.py:46-51` builds a results lookup keyed on API-Football team-name strings (`f"{home}_vs_{away}".lower()`); picks are generated from SofaScore-sourced fixtures (`src/engine/fixture_collector.py`). Any spelling/diacritic/naming difference between the two providers ("Man Utd" vs "Manchester United", "Paris SG" vs "Paris Saint-Germain", etc.) makes `results_map.get(match_key)` (line 75) miss silently — no error, no log, the pick just never settles.
- This alone would keep the pick count near-permanently stuck at "pending" even after F1 is fixed.
- Fix: settle by a stable match identifier (the SofaScore `event_id`/`match_id` already stored on the pick/match row), not by reconstructed team-name strings, or build an explicit team-alias reconciliation table if a stable cross-provider ID isn't available for every match.

**F3 — Production models were trained on synthetic random data, never a real match.**
- `src/ml/trainer.py` only ever consumes `DatasetBuilder.build_synthetic()` (`src/ml/dataset_builder.py:104-136`), which generates fabricated team profiles and outcomes via `np.random.Generator`. `models/training_metrics.json` confirms `n_samples: 3000` with ROC AUC 0.51–0.58 across btts/over_1.5/over_2.5/over_3.5/home_win/draw/ht_over_0.5 — barely above a coin flip, consistent with fitting noise.
- **Per the freeze, I am not retraining these models in this pass.** This is flagged as the top blocker for real predictive accuracy and must be the first thing addressed once `baseline_contract.md`'s gate reopens: implement `DatasetBuilder.build_from_history()` against `match_history` with a chronological cutoff, and only then retrain.

### HIGH

**F4 — No DB-level uniqueness on `picks` or `qualification_log`.**
`src/db/database.py:171-207` (picks), `:418-439` (qualification_log) — only non-unique indexes exist. Any re-run of pick generation (e.g., retrying after F1's fix) can silently duplicate rows for the same match/market/selection, corrupting hit-rate and Brier statistics exactly as happened in the legacy table (6,355 duplicate pairs, per `audit_report.json`).

**F5 — No cross-provider match reconciliation → possible duplicate matches.**
`matches`/`match_history` key on SofaScore's own event IDs (`fixture_collector.py:68`); `api_football_fetcher.py` uses API-Football's own IDs for the same real-world fixtures. Nothing reconciles the two, so the same match can exist as two DB rows depending on which provider ingested it first.

**F6 — Inconsistent timezone handling, no central UTC policy.**
`src/config.py` has no timezone constants. `audit_engine.py:235`, `prediction_logger.py:106` use naive local `datetime.now()`; `isotonic_calibrator.py:265`, `error_intelligence.py:336`, `team_intelligence.py:81` use `datetime.utcnow()`. Mixed naive-local/naive-UTC comparisons (odds-age checks, "has this match started") will silently misclassify by the server's UTC offset. The recent "UTC-IST alignment" commit fixed a symptom, not this root cause.

**F7 — Fabricated CLV metric.**
`src/engine/settlement.py:63-72` — the closing-line-value block does nothing but increment a counter (`clv_count += 1`) with a comment admitting it's a stub, yet reports `clv_updated` to callers as if real CLV had been computed. This is exactly the kind of "manipulated metric that looks better than it is" the brief prohibits — even though nobody is currently reading picks, this must not ship as a real number once `picks` starts filling.

**F8 — Settlement only grades three markets; others silently never settle.**
`settlement.py:87-98` grades `1X2`, `O/U 2.5`, and `BTTS` only. Any pick in corners, cards, handicap, or correct-score markets has no grading branch — it accumulates as permanently pending with no warning.

**F9 — No point-in-time ("as-of") state reconstruction anywhere in the feature layer.**
`src/db/team_state.get_team_state()`, `src/ml/team_stats_db.get_team_stats()`, `src/db/team_intelligence.get_team_rating()`, and `src/engine/similar_match_engine.find_similar_match_stats()` all read only the *current* mutable row, with no `as_of` timestamp parameter. This is harmless for same-day live prediction (today's team state can't reflect the future), but it means **no backtest or walk-forward replay against `match_history` can be trusted yet** — every historical "prediction" would actually see each team's end-of-season Elo/form, not the state that existed before that match. This must be fixed (versioned state, `as_of` threading) before any Phase-12-style backtesting is attempted; it is a prerequisite for honest evaluation, not a feature addition to the model itself.

### MEDIUM

**F10 — Isotonic calibrator's "chronological" split is ordered by DB row ID, not match date.**
`src/engine/isotonic_calibrator.py:135` orders by `market_type, id` for the 80/20 train/test split. If any row was ever backfilled out of temporal order, "train" can silently include later matches than "test," breaking the walk-forward guarantee this code otherwise correctly implements. Fix: order by kickoff/match date instead of autoincrement ID.

**F11 — Postponed/cancelled matches have no explicit terminal status.**
`settlement.py:76`: `if not res or res["status"] not in ("FT","AET","PEN"): continue` — a postponed/abandoned fixture is skipped forever rather than being marked VOID, so it accumulates as permanently-pending with no closure path.

**F12 — Hardcoded stale season stats as silent fallback.**
`src/ml/team_stats_db.py:29-564` bakes in ~250 teams' fixed 2024/25 scored/conceded/corners/cards constants, used silently whenever the live DB has no rows for a team. `feature_builder.py` does apply a data-quality penalty for this, but the fallback values themselves go stale every season without any expiry check.

**F13 — `similar_match_engine` has no chronological filter (latent, not currently triggered).**
`similar_match_engine.py:172-180` queries `match_history` for similar matches with no `WHERE match_date < :as_of` guard. Currently harmless in live use (only completed matches exist to query), but the function has no way to exclude future-relative-to-target matches if it is ever called during a backtest — same root cause as F9.

**F14 — Random (not time-series) cross-validation in the trainer.**
`src/ml/trainer.py:109` uses `StratifiedKFold(shuffle=True)`. Currently inconsequential because the training data is i.i.d. synthetic (F3), but the moment real chronological match data replaces it, this would leak future matches into training folds for past matches unless changed to `TimeSeriesSplit`. Documented here so it isn't missed when F3 is eventually addressed — not changed now (would be pointless without F3, and touching trainer.py's CV strategy edges into "tuning," which is frozen).

### LOW

**F15 — Dead, unsafe in-sample calibrator.**
`src/ml/calibration.py::train_calibration_models` fits isotonic regression and reports Brier score on the *same* data it was fit on (no holdout), plus a fragile monkey-patch of sklearn internals (`ir.f_ = lambda...`). Grep confirms zero callers anywhere else in the codebase — it appears fully superseded by `isotonic_calibrator.py`. Recommend deleting to remove the risk of it being wired in again by mistake later.

---

## 3. What's actually working well (so this doesn't read as all-bad)

- **Calibration engineering in `isotonic_calibrator.py` is genuinely solid**: real sklearn `IsotonicRegression`, fit per market on 109,871 rows (well above its own `MIN_SAMPLES_FOR_FIT=200`), pre-binning + Laplace smoothing against memorization, a real chronological train/test split (modulo F10), and a champion/challenger gate that only activates a refit if it demonstrably beats the incumbent on held-out data. This is exactly the kind of calibration discipline the brief asks for — it just needs F10 fixed and F1/F2 fixed so it can eventually calibrate on the (currently empty) `picks` table too, not only the leakage-tainted `prediction_log`.
- **The confidence engine is real, not a renamed probability.** `risk_control.py::score_confidence()` and `qualification_engine.py`'s weighted `pick_quality_score` (data quality 15%, model consensus 15%, calibration 15%, market edge 30%, volatility/ROI/CLV/liquidity/similar-match 5% each) genuinely combine multiple independent signals.
- **NO-BET logic is real and reachable**, not dead code: `performance_gate.py` hard-rejects below data-quality/sample-size/hit-rate/Brier floors per market; `qualification_engine.py` explicitly returns `"No Pick"` for Tier C scores. Both are on the live call path.
- **`baseline_contract.md`'s discipline is exactly right** — the reason the system hasn't shipped 100,000 more leakage-tainted, overconfident picks since June is that nothing was wired to run automatically. That's a lucky accident of the wiring bug, but the governance intent (don't scale a system before you've proven it) is sound and should stay in force.

---

## 4. Fix plan for this session (bug/wiring fixes only — no retraining, no ensemble/feature changes)

| # | Fix | Finding | Risk if skipped |
|---|-----|---------|------------------|
| 1 | Wire the daily scheduled job to call the real pipeline (`run_investment_pipeline_service`) so qualifying picks land in `picks` | F1 | Gate never reaches 1,000 picks; freeze never lifts |
| 2 | Settle by stable match ID instead of cross-provider team-name string | F2 | Picks generated by fix #1 would never settle |
| 3 | Add DB-level unique constraints on `picks`/`qualification_log` | F4 | Reruns duplicate rows, corrupting hit-rate/Brier stats |
| 4 | Fix or explicitly disable the fabricated CLV counter | F7 | A fake metric ships once picks exist |
| 5 | Extend settlement grading to cover all markets actually produced, or explicitly VOID unsupported ones | F8 | Non-1X2/O-U/BTTS picks silently never close |
| 6 | Centralize UTC-aware datetime handling | F6 | Silent misclassification of match timing |
| 7 | Fix isotonic calibrator's split to order by match date, not row ID | F10 | Walk-forward guarantee silently broken on backfilled data |
| 8 | Explicit VOID status for postponed/cancelled matches | F11 | Permanently-stuck pending picks |
| 9 | Delete dead in-sample calibrator | F15 | Risk of accidental future misuse |

**Explicitly deferred (not attempted this session, with reasons):**
- F3 (retrain on real data) — forbidden by the freeze; requires `DatasetBuilder.build_from_history()` plus a real walk-forward retrain, which is out of scope until the pick-gate reopens.
- F9/F13 (point-in-time state versioning) — needed before any backtest can be trusted, but is a substantial schema/architecture change (versioned team-state history), not a small bug fix; recommend as the very next piece of work, separate from today's wiring fixes.
- F5 (cross-provider match ID reconciliation), F12 (season-stat freshness) — real but lower urgency than the picks-are-produced-and-settle-correctly chain; flagged for follow-up.
- F14 (time-series CV in trainer) — inconsequential until F3 is addressed; changing it now would be dead code motion.

Each implemented fix will get a regression test. None of this enables retraining, changes model weights, or adds features — it makes the existing, already-designed pipeline actually run and actually close the loop so real evidence can start accumulating toward the 1,000-pick threshold `baseline_contract.md` requires before any model work resumes.

---

## 5. What was actually implemented, and a correction to F2

Per your direction, F1 (wiring the daily cron job to call the real pipeline) was **not** implemented — that remains for you to enable deliberately, since it starts real production behavior and API spend.

While implementing the F2 fix, closer investigation than the original fork pass revealed the true picture is more specific — and worth recording accurately rather than leaving the original framing standing:

- **`src/engine/settlement.py` (where F2/F7/F8/F11 were originally located) turned out to have zero callers anywhere in the codebase — it was dead code**, never invoked by anything. It has been **deleted** rather than patched, per the project convention of not leaving unused code in place.
- **The actually-scheduled settlement path is `scripts/settlement_worker.py`** (installed via `scripts/orchestration/crontab.txt`, though not currently installed on this machine — verified via `crontab -l`). It correctly settles by stable SofaScore match ID (no cross-provider name-join problem), but its `picks`-grading branch queried for market strings like `"Home Win"`/`"Over 2.5 Goals"` while `pipeline.py` (the only code that writes to `picks`) uses `"1X2"`/`"O/U 2.5"`/`"BTTS"` with selections like `"home"`/`"over"`/`"yes"`. **These never matched — the branch was live but functionally dead.** It also wrote `result = 'W'/'L'`, while `get_portfolio_summary()` and the production `ProbabilityCalibrator.fit_from_db()` (used by `pipeline.py` itself) only recognize `'won'`/`'lost'` — so even a matching row would never have been counted as a win or a loss anywhere.
- **The correct settlement logic already existed**, fully working, in `api/services/portfolio_service.py::auto_settle_picks()` — right vocabulary, right `'won'`/`'lost'` convention, right join (via `get_unsettled_picks()`, which joins `picks` to `matches` by `match_id`). It was simply never called by anything scheduled — the same "correct code, zero automation" pattern as F1.

**Fix applied:** `scripts/settlement_worker.py` now calls the existing `auto_settle_picks()` (and the new `void_stale_picks()`, below) instead of its broken inline branch, rather than adding a fourth reimplementation of pick-grading logic.

### Fixes implemented this session

| Finding | Change | File(s) |
|---|---|---|
| F2 (corrected) | Settlement worker now delegates `picks` grading to the already-correct `auto_settle_picks()` | `scripts/settlement_worker.py` |
| F4 | Added `UNIQUE INDEX` on `picks(match_id, market, selection)` and `qualification_log(match_id, market)` | `src/db/database.py` |
| F6 | Added `now_utc()`/`parse_utc()` helpers; fixed the future-leakage guard in `log_predictions()` (was comparing match date against naive server-local time instead of UTC) and the odds-staleness check in `audit_engine.py` (same bug) | `src/config.py`, `src/db/prediction_logger.py`, `src/engine/audit_engine.py` |
| F10 | Isotonic calibrator's 80/20 split now orders by `match_date`, not autoincrement `id`; the row-fetch was extracted into `_fetch_settled_predictions()` so the ordering is independently testable | `src/engine/isotonic_calibrator.py` |
| F11 | New `void_stale_picks()`: a pick whose match is >48h old and never reached FT/AET/PEN is marked `'void'` (pnl 0) instead of staying `pending` forever; `get_portfolio_summary()` now also reports a `voided` count | `api/services/portfolio_service.py`, `src/db/picks_repo.py` |
| F15 | Deleted the dead in-sample (no holdout) calibrator | `src/ml/calibration.py` (removed) |
| — | Deleted dead code with zero callers, superseded by `auto_settle_picks()` | `src/engine/settlement.py` (removed) |

**Verification:** 10 new regression tests added (`tests/test_gated_pipeline_fixes.py`, plus a shared `tests/conftest.py` fixture that initializes the real schema in-memory). Full suite: **243 passed, 0 failed** (`pytest tests/`). The schema migration (new unique indexes) was also applied to the real `data/engine.db` and verified clean — both tables were empty, so no constraint violations were possible.

**Deferred, unchanged this session:** F1 (live cron wiring — your call), F3 (retrain on real data — frozen), F5 (cross-provider match ID reconciliation), F9/F13 (point-in-time state versioning), F12 (season-stat freshness), F14 (time-series CV in trainer). Also newly noted but not acted on: `scripts/live_worker.py` (which keeps `matches.status`/goals current for already-ingested matches) is likewise not in the installed crontab template — same "correct code, not scheduled" pattern as F1, left alone under the same freeze.

---

## 6. Session 2 — evidence correction, F16, and F1-readiness verification

Per your review, the historical 43.5%/77.9%/82.5%/15.4% figures are **retracted as evidence of model or calibration quality.** Forensic investigation (reading the actual origin scripts — `audit_predictions.py`, `analysis_calibration.py`, `clean_audit.py`, all still present in the repo root) confirmed:

- `audit_predictions.py` flags future-leakage rows but explicitly does not filter them out before computing the 43.5% high-confidence figure (`# For now, keep them but flag`) — that figure includes the leakage-tainted rows.
- `analysis_calibration.py` (source of the 77.9%/82.5% calibration buckets) applies no dedup or leakage filtering at all.
- The properly-cleaned `clean_audit.py` (331 rows) finds **zero** rows at ≥80% confidence — there has never been a valid clean measurement of that bucket, positive or negative.
- Both scripts define "confidence" as literally `predicted_prob >= 80` — the probability-as-confidence conflation, confined to this legacy analysis code, not the gated pipeline's actual confidence engine.
- The original leakage guard (added 2026-06-10) and dedup (added 2026-07-26) post-date the June 3 audit; re-running the original leakage definition against the live DB today finds 0 qualifying rows. Duplicates are down >95% but not zero (see F17).

**New findings, discovered while verifying F1-activation readiness:**

- **F16 (critical) — `log_qualification()` had zero callers anywhere.** `qualify_picks()` runs live daily inside `_compute_match_analysis()`, with a real DB connection, producing genuine tier/rejection verdicts — but nothing persisted them. `qualification_log`'s 0 rows was proof of missing persistence, not proof the gate rejects everything. **Fixed**: `_compute_match_analysis()` now takes an optional `fixture_id`, threads it into `qualify_picks(match_id=...)`, and calls `log_qualification()` when a fixture_id is present. Wired at both call sites that have a real fixture id (`get_match_analysis()`, `collection_worker.py`). Ad-hoc callers with no fixture_id (tests, backtest snippets) are unaffected — persistence is opt-in on a real identity, not mandatory.
- **F17 (high, legacy-only, not touched) — coarse dedup key.** `prediction_logger.py`'s live dedup groups at `market_type` (10 coarse buckets), not the actual market — e.g. all of Over 0.5/1.5/2.5/3.5 Goals collapse into `"goals"`. This is very likely the dominant explanation for the original 95.6% "duplicate" rate (genuinely distinct markets counted as duplicates of each other), and is a live bug in the legacy path today. Confirmed **not present** in the gated pipeline: `picks`' new unique index keys on the actual `(match_id, market, selection)`, and `qualification_log`'s keys on `(match_id, market)` using the specific market name, not a bucket — verified empirically (regression tests confirm 3 distinct `1X2` selections for one match all persist without collision).
- **F18 (new) — pick provenance was being computed and discarded.** `market_value.py` computes `model_source` per candidate, but `Pick`/`insert_pick` had no column for it. **Fixed**: added `model_source` to the `picks` schema (migrated via `ALTER TABLE`, applied to the live DB), the `Pick` dataclass, `insert_pick`/`insert_picks_batch`, and `pipeline.py`'s candidate→Pick conversion. Manually-placed picks (`portfolio_service.place_pick`, a separate manual API writer to `picks`) now default to `model_source="manual"` so they're distinguishable from genuine pipeline output if that endpoint is ever used.
- **F19 (critical) — `run_pipeline` had no guard against already-started fixtures, and `run_investment_pipeline_service` had no guard against non-today dates.** Neither is hypothetical: a re-run mid-day, or a call with a past `date_str`, would generate a "pre-match" pick using team state that may already reflect that match's actual result. **Fixed**: `run_pipeline` now filters fixtures to `status == "NS"` only; `run_investment_pipeline_service` now refuses any `date_str` other than today (UTC).
- **Unflagged pre-existing bug, found while testing F19 — `run_pipeline` was completely broken.** `pipeline.py` imported `_fetch_api_football_odds` from `api.main`, a function that no longer exists there (confirmed by grep: it exists nowhere in the codebase). This import is unconditional, outside any try/except — **`run_pipeline` would raise `ImportError` on every single invocation**, independent of anything else in this audit. The surrounding code that used it was already non-functional even before the import broke (an empty crossreference loop, `pass`, with comments admitting the cross-provider mapping was abandoned mid-implementation). **Fixed**: removed the dead block; SofaScore pseudo-odds (already inserted by `fixture_collector.py`) remain the odds source, `odds_status = "sofascore_only"`.

**Your 9-point pre-activation checklist — status:**

| # | Requirement | Status |
|---|---|---|
| 1 | `qualify_picks()` → `log_qualification()` persistence works | ✅ Fixed + tested (F16) |
| 2 | `qualification_log` receives exactly the verdict produced | ✅ Tested — row count matches qualified+rejected count exactly |
| 3 | `pipeline.insert_pick()` only receives legitimately qualified picks | ⚠️ **Reframed**: `pipeline.py` does not call `qualification_engine.qualify_picks()` at all — it has its own, separate grading gate (`assign_grade()`: edge/confidence/league_reliability → A+/A/B, else "Pass" is dropped, `pipeline.py:178-179`). That gate is real and verified by test. The BILQE 10-layer system and the picks-table gate are two independent, unconnected qualification mechanisms today. |
| 4 | Duplicate constraints cannot silently destroy legitimate markets | ✅ Verified — both unique indexes key on exact market/selection, not a coarse bucket (unlike F17's legacy key); confirmed by test |
| 5 | Every pick has immutable prediction timestamp/provenance | ✅ Fixed (F18) — `created_at` was already immutable; `model_source` was being discarded, now persisted |
| 6 | Settlement operates on the exact same market vocabulary | ✅ Fixed earlier this session (F2 correction) and tested |
| 7 | The 1,000 counter counts only settled gated-pipeline picks | ✅ Confirmed by code — `picks` has exactly two writers: `pipeline.py::insert_pick` (automated) and `portfolio_service.place_pick` (manual API, now tagged `model_source="manual"` if ever used) |
| 8 | No legacy `prediction_log` row can enter the experiment | ✅ Confirmed by code — no path copies `prediction_log` into `picks` |
| 9 | No historical backfill can enter the experiment | ✅ Fixed (F19) — `run_investment_pipeline_service` now rejects non-today dates |
| 10 | No current/future result can influence a prediction made before kickoff | ✅ Fixed (F19) — already-started/finished fixtures are now skipped before pick generation |

**Not done, and not proposed to be done without asking:** disabling/gating the manual `place_pick` API endpoint. It's real (item 7's caveat) but is a product/access-control decision, not a bug fix — recommend treating "don't call this endpoint during the frozen sample" as an operational rule rather than removing admin functionality unilaterally.

**F1 itself remains NOT activated.** Everything above makes the pipeline safe *if* activated, but activating it is still the one action in this list that starts real production behavior, spends API quota, and — per your own framing — should mark the start of the frozen experimental sample rather than something folded into a routine fix pass. That decision is intentionally left for explicit confirmation.

**Verification:** 251 tests passing (17 new this turn, across `tests/test_f16_qualification_persistence.py` and `tests/test_pipeline_safeguards.py`), 0 failures. The `model_source` schema migration was applied to the real `data/engine.db` and confirmed present.

---

## 7. Session 3 — final read-only F1-readiness audit (no code changes except one additive test)

Full findings reported to the user in-conversation. Summary of what changed: added `test_ns_safeguard_survives_a_rerun_after_kickoff` (tests/test_pipeline_safeguards.py) — proves the NS filter reads live event status, not a stale DB value, across two sequential `run_pipeline` calls for the same match_id. No production code was modified this session. 252 tests passing.

Key new findings (all reporting-only, none fixed, per explicit read-only instruction):
- No single authoritative "settled picks toward 1,000" query exists. `scripts/production_audit.py:67` and `scripts/validate_bilqe.py:109-111,135-143` both use `SELECT COUNT(*) FROM picks WHERE result IS NOT NULL` — doesn't exclude `model_source='manual'` or `result='void'`. `api/services/analytics_service.py::get_warehouse_stats()` reports an unrelated `target:10000`/`progress_pct` computed from the legacy `prediction_log`, not `picks` — a real confusion risk if read as "experiment progress."
- `scripts/validate_bilqe.py` is itself the project's own pre-existing, already-designed gate for eventually promoting BILQE (`qualification_engine`) into the picks pipeline — and it already requires ≥1,000 settled `picks` rows before that can happen. The two-systems separation the user flagged is already correctly anticipated by this script; no merge is required or implied now.
- `qualification_log.match_date` = the date `_compute_match_analysis` ran (`_get_istanbul_today()`), not necessarily the fixture's actual date — only diverges if a fixture is ever pre-analyzed ahead of its real date. `get_match_analysis()`'s `start_time` parameter is accepted but never used (grep: single occurrence, in the signature only).
- `log_qualification()` swallows per-pick insert failures at DEBUG level and returns a success `count` that its caller never checks — if persistence partially/fully fails, `qualification_log` would silently under-represent the true verdict with no visible signal, while the API response (built from the in-memory result, not from what was actually persisted) would still look complete.
- `void_stale_picks()` (F11) has no API route — its only caller is `scripts/settlement_worker.py`. Activating F1 (pipeline/pick generation) without also scheduling the settlement worker means postponed/cancelled fixtures' picks will sit pending indefinitely in practice, even though the code that would resolve them works correctly.
- The today-only date guard lives in `run_investment_pipeline_service()`, one layer above `run_pipeline()` itself; confirmed (by grep) there is exactly one production caller of `run_pipeline`, so this is a defense-in-depth gap, not an active bypass today.

**Statement on F1 activation:** safe with respect to prediction integrity (no leakage/backfill path, no crash, correct settlement vocabulary, correct persistence on the happy path — all proven by test). Not yet a complete, self-sufficient solution: activating it should be paired with (a) also scheduling `settlement_worker.py`, and (b) settling on one authoritative, correctly-filtered "count toward 1,000" query before relying on the number. F1 was not activated this session.

**Additional test added on request** — `test_post_match_leakage_scenario_produces_zero_picks_and_never_consults_team_state` (`tests/test_pipeline_safeguards.py`): stronger than the plain status-rerun test. Simulates a fixture reaching FT with a real result AND the home team's `team_state` row being mutated to reflect having played (and won) that exact match — the same state a real settlement update would produce. Proves, via a spy on `estimate_probabilities`, that a second `run_pipeline()` call for that match (a) produces zero tracked matches, (b) never even invokes the probability engine for it (not just "doesn't store a pick" — the leaked state is never consulted at all), and (c) leaves zero rows in `picks` for that match_id. 253 tests passing overall.

---

## 8. Session 4 — Blocker 1 (experiment counter) and Blocker 2 (settlement scheduling) resolved

### Blocker 1 — authoritative 1,000-pick population

**All writers to `picks` enumerated by grep** (three, confirmed exhaustive): `pipeline.py:196` (automated), `portfolio_service.py::place_pick` (manual, `model_source='manual'`), and a raw `INSERT INTO picks` in `collection_worker.py:73` for low-data-quality fixtures (`market='NO PICK'`, `result='NO PICK'`, no `model_source` set at all → defaults to `'unknown'`).

**Provenance rule chosen: an allowlist, not a blocklist.** `estimate_probabilities()` (`probability_engine.py`) can only ever set `source` to `"poisson"`, `"fallback"`, or `"hybrid"` (confirmed by grep of every `source =` assignment) — those three values became `_AUTOMATED_PIPELINE_MODEL_SOURCES` in `src/db/picks_repo.py`. `model_source != 'manual'` alone was insufficient — it would not have excluded the `'unknown'`-tagged "NO PICK" placeholder row, nor any future unanticipated tag. An allowlist fails closed: anything not explicitly recognized is excluded by default.

**New function**: `get_experiment_progress(conn)` (`src/db/picks_repo.py`) — `SELECT COUNT(*) FROM picks WHERE result IN ('won','lost') AND model_source IN ('poisson','fallback','hybrid')`, plus `excluded_voided`, `excluded_non_pipeline`, `pending_eligible` for transparency. Void picks are real rows but carry no win/loss evidence, so they don't count toward the 1,000 (this is a documented interpretive choice — `baseline_contract.md` doesn't define void's role explicitly).

**Wired in**: `scripts/production_audit.py` (was a naive `COUNT(*) WHERE result IS NOT NULL`), `scripts/validate_bilqe.py` (both its gate check and its baseline-data fetch for the internal BILQE-vs-current comparison), `api/services/analytics_service.py::get_warehouse_stats()` (added `experiment_progress` block; relabeled the previously-misleading `target:10000`/`progress_pct` — which was computed from the **legacy `prediction_log`**, not `picks` — to `legacy_prediction_log_target`/`legacy_prediction_log_progress_pct` so it can no longer be mistaken for experiment progress). Confirmed no frontend/test code depended on the old key names.

**10 regression tests** (`tests/test_experiment_progress.py`): automated pick counts; hybrid/fallback sources also count; manual pick excluded; unrecognized/novel model_source excluded (allowlist behavior, not just known-bad blocklist); the exact `collection_worker.py` "NO PICK" placeholder excluded; legacy `prediction_log` row excluded (different table, can't appear); `qualification_log` row excluded (different table, can't appear); pending pick excluded; voided pick excluded; composite mixed-population test.

### Blocker 2 — settlement operational readiness

**Traced the real path**: `pipeline.py::insert_pick` → `picks` (pending) → match reaches FT (via `fixture_collector`/`live_worker`, which update `matches.status`/goals) → `scripts/settlement_worker.py::run_settlement()` → `auto_settle_picks()` + `void_stale_picks()` → `won`/`lost`/`void`.

**Both scheduling mechanisms already reference settlement_worker.py correctly**: `scripts/orchestration/crontab.txt:11` (hourly at `:15`) and `scripts/orchestration/systemd/settlement_worker.{service,timer}` (`Type=oneshot`, `OnCalendar=*-*-* *:15:00`). Nothing was missing from the scheduler config itself.

**Real bug found and fixed**: `settlement_worker.py`'s `__main__` block was `while True: run_settlement(); time.sleep(300)` — an infinite loop, directly contradicting its own declared `Type=oneshot` systemd service and the hourly-cron invocation model (both expect one execution per trigger, exactly like `odds_worker.py` and `collection_worker.py` already do correctly). This would have prevented systemd from ever considering the service "done," and would spawn overlapping processes at every subsequent cron trigger. Fixed to run once and exit; removed the now-unused `import time`.

**2 regression tests** (`tests/test_settlement_scheduling.py`): a static AST check confirming no loop construct remains in the `__main__` block; and an integration test calling the real `run_settlement()` entrypoint (network calls mocked, DB swapped for the in-memory fixture) proving a pending pick reaches `result='won'` with correct P&L through the actual scheduled function, not just through `auto_settle_picks()` called in isolation.

**Not done**: actually installing the crontab or enabling the systemd timers on any host. That's an operational/infrastructure action outside this repo's code — confirmed via `crontab -l` that nothing is installed on this local dev machine — and carries the same "starts real recurring production behavior" caution as F1 itself, so it wasn't done unilaterally.

### Blocker 3 — F16 error visibility: classified, not fixed (per your conditional)

Grep for any `SELECT ... FROM qualification_log` anywhere in the codebase outside its own writer returns **zero results** — nothing reads from it, no dashboard, no gate, no downstream decision. This conclusively classifies it as **(B) supplemental telemetry**, not (A) authoritative audit state. Per your instruction, left unchanged; documenting the limitation: if `log_qualification()` silently fails (partially or fully), `qualification_log` will under-represent the true verdict with no visible signal, and the API response (built from the in-memory result) won't reflect it. This is a real gap, but not a contamination risk to the 1,000-pick experiment (which is measured from `picks`, not `qualification_log`), so it does not block F1.

**Verification**: 265 tests passing (12 new this turn), 0 failures. Confirmed by `git diff --stat`: zero changes to any file underlying F3/F5/F9/F12/F13/F14 (trainer.py, dataset_builder.py, team_state.py, team_stats_db.py, similar_match_engine.py, team_intelligence.py, models/, ensemble_predictor.py, hybrid_predictor.py, calibration engine) this session. F1 remains **not activated**.

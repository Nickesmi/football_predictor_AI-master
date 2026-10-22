# M3.5 — Frozen 14-day forward shadow protocol

**Status: FROZEN at manifest v4 `m35-f206ea91f8facda2`**. The manifest pins code commit `0708a80` and code fingerprint `215dbba63b5b8f43…`. It supersedes v3 `m35-f00a477c1cad226d`, v2 `m35-59658cae50a480cc` and v1; all are kept unchanged.

> Re-pin record (2026-10-22, before any observation existed): v3 → v4 because runtime files changed. The changes were the caffeinate ownership fix and the emergency stop during a cycle, which closes the cycle with `shadow_cycles.status = 'stopped'`. No field, metric, threshold or stop criterion of this protocol changed.

Host: **this MacBook Pro** (owner's choice). See `docs/M3_5_MAC_RUNBOOK.md`.

Changing any rule, metric or threshold below after activation invalidates the run. A change needs a new manifest version and a restart of the 14-day count.

This is a research observation experiment. It is **not** the official 1,000-pick baseline:
- no pick is counted;
- no bet is placed;
- `counts_toward_baseline` is constrained to 0 by the database.

## 1. What runs

| Item | Pinned value |
|---|---|
| Profile | Medium: all 14 tracked competitions, decision 120 min before kickoff, 30-min slots, discovery every 12 h, no closing capture |
| Inputs | identity-v1 on rh1 point-in-time history (`identity-v1-rh1`), rebuilt each cycle from stored finished fixtures |
| Model | `model-714d281b0043`. Calibration `unfrozen` (code `cal-f4f5719a1a4e`). Selection `sel-1263b6f5c072`. |
| Policies recorded | legacy `legacy-m2.5-pipeline`, `baseline-policy-v1-proposal.2670eba922e2`, `v1.1-proposal.785bb9063e49`. All research only. |
| Odds | genuine bookmaker quotes only, K53 revisions, `fetched_at ≤ decision time` |
| Quota | 70 per UTC day for all consumers (shared ledger), 20 per cycle, 2 attempts per fixture window. Failures count. Stop at provider remaining ≤ 20. |
| Start state | shadow DB sha256 `0f892593a0d4e97b…`, 0 observations, 0 picks, 647 history fixtures |

## 2. Genuine forward observation (definition)

An observation is forward evidence **only if all** of these hold:
1. A shadow cycle wrote it, and its `cycle_id` is in `shadow_cycles`.
2. Its `manifest_id` equals `m35-f206ea91f8facda2`.
3. `decided_at < kickoff`.
4. It was written once per fixture (`UNIQUE (fixture_id)`), never updated afterwards.

Never forward evidence:
- rh1 history;
- the 184 verified pre-existing prediction rows;
- quarantined rows;
- replays;
- anything written after downtime for a fixture whose decision window passed (it is not observed at all);
- any row from another manifest.

`src/shadow/manifest.forward_evidence_check` computes the split. The preflight and the daily review run it.

## 3. Recorded for every observation

| Required field | Where it is stored |
|---|---|
| Decision timestamp, fixture kickoff | `decided_at`, `kickoff`, `minutes_to_kickoff` |
| Provider fixture and competition ids | `fixture_id` (API-Football), `provider_league_id`, `competition_key` |
| Canonical home and away team ids | `home_canonical_team_id`, `away_canonical_team_id` (`cth-…`); provider ids in `provider_home_team_id` and `provider_away_team_id`; full resolution in `identity_json` |
| Exact historical input availability | `inputs_available_json`: per side, the status, matches used, the last fixture used (`as_of_fixture_id`) and its `available_from` (kickoff + 3 h) |
| Prediction probabilities, model version | `raw_probabilities_json`, `forecast_json`, `model_version`, `calibration_version`, `code_revision`, `code_fingerprint`, `manifest_id` |
| Bookmaker, market, selection, decimal odds | `candidates_json`: `bookmaker`, `market`, `selection`, `odds`, `odds_type` |
| Price collection time, provider update time | `candidates_json`: `odds_fetched_at` (our collection), `odds_observed_at` (provider stamp); `quote_status`, `quote_age_minutes`, `quote_bucket` |
| Input data quality | `data_quality`, `model_source`, `probability_inputs_json` |
| Qualification and rejection reasons | `legacy_json`, `v1_json`, `v1_1_json` (selected plus funnel with reasons), `bilqe_json` (layer gates, rejections by layer), `error` (`NO_FORECAST: TEAM_IDENTITY_UNRESOLVED`, …) |
| Eventual verified result | `shadow_outcomes` (regulation-time score from API-Football `score.fulltime`, status FT/AET/PEN, `recorded_at`). Settled selections are in `shadow_selection_results`. |
| Missed slots, provider failures | `shadow_cycles.missed_slots`, `gap_minutes`, `status` (`finished`, `skipped`, `stopped`, or `running` if killed); `report_json.errors`; `provider_quota_log` (every request with status); the shared ledger |

Unresolved identities are recorded as `NO_FORECAST` with the identity evidence; they are never filled in. Missing data is stored as missing.

## 4. Three separate questions (never mixed)

**A. Operational reliability.** Did the experiment observe honestly and completely?
- Cycles run against expected (48 per day), missed slots, and gap distribution.
- Share of in-window fixtures observed, deferred or missed.
- Requests per day (≤ 70), provider errors, 429s and refusals.
- Integrity: 0 observations at or after kickoff, 0 prices used after the decision, 0 duplicates, 0 counted.
- Identity resolution share and history completeness per competition.

The pass criteria are an engineering judgement and say nothing about the model.

**B. Prediction quality** (research, small sample):
- per market (1X2, O/U 2.5, BTTS): Brier score and log loss against verified results;
- reliability by probability bucket;
- per-competition sample sizes, with E0–E5 eligibility from M3.3, research only.

Expect about 200 forward observations in 14 days: about 100 fixtures per week in the 14 competitions (`SHADOW_14_DAY_PLAN.md`). That is too small to establish calibration per competition.

**C. Profitability** (research; **cannot be established by this run**):
- flat-stake P&L and ROI per recorded policy;
- odds age at decision time.

CLV is unavailable because Medium has no closing capture. Provider odds stamps were 2.7–4.9 h old, and that ambiguity is unresolved. **No profitability claim is permitted from this experiment.**

## 5. Daily review (about 5 minutes)

```
python scripts/shadow_report.py --db /srv/shadow/engine_shadow.db --out /srv/shadow/reports/<date>.json --check-stale-minutes 90
```

Record the following:
- `integrity.ok`;
- `operational_summary`;
- `cycle_health.missed_slots`;
- the ledger day total and consumers;
- the provider remaining;
- `forward_evidence_check`.

## 6. Stop criteria (stop immediately and report)

Any one of these stops the run:
- `integrity.ok` false;
- any observation with `decided_at ≥ kickoff`;
- any counted row;
- any pick in the shadow DB;
- a ledger day total above 70, or any consumer outside `shadow_cycle`, `shadow_settle` and `shadow_preflight`;
- the provider remaining at or below 20 at the start of a UTC day;
- a cycle refused for a manifest mismatch: code, identity, history or schema changed on the host;
- a stale alarm (exit 3) lasting more than 6 h without a known cause;
- Mac-specific: the experiment caffeinate is not running, or the Mac is on battery, for more than 1 h while cycles are due;
- an unplanned reboot or logout. Stop, record it, and decide before resuming.

**Sleep and downtime are recorded, not stop criteria by themselves.** Missed slots and lost decision windows are counted in A. A fixture whose window passed while the Mac slept is never observed later. A fixture observed after wake keeps its true, shorter lead time.

## 7. End of run (day 14)

1. Run the final `shadow_report`, `forward_evidence_check`, and the ledger totals by day and consumer.
2. Write up A, B and C separately, with sample sizes.
3. Hold a GO/NO-GO review. Any official baseline decision is a separate, later approval.

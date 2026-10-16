# Shadow Validation Report (M3 Phase F)

Commit `3889d0a`. Code: `src/shadow/cycle.py`, `src/shadow/report.py`, `src/shadow/store.py`, `src/shadow/legacy.py`, `scripts/shadow_cycle.py`, `scripts/shadow_report.py`. Scheduler templates (not installed): `scripts/orchestration/shadow/`. Tests: `tests/test_shadow_validation.py` (13).

## 1. Status

| Item | State |
|---|---|
| Official 14-day shadow period | **Not started.** Prerequisites need approval: cutover, activated remediation, odds budget, scheduler. |
| Observation period actually elapsed | See §4: a component-verification run on 2026-10-16, a few hours on one evening. It is **not** a substitute for the 14 days. |
| Counted toward the 1,000-pick baseline | **0.** Enforced: `counts_toward_baseline` has `CHECK (= 0)` on observations and on hypothetical results; the cycle never writes `picks`. |
| Simulated or retrospective selections | None are recorded as forward observations. Simulations appear only in the policy comparison and are labelled there. |

## 2. What each cycle does

1. **Discovery** (every 6 h; also for dates of observed fixtures still awaiting a result): API-Football `/fixtures?date=` for today and tomorrow (UTC). Fixtures from the 14 tracked competitions are stored. The API-Football event is mapped onto any other stored row for the same match. Final scores are recorded from `score.fulltime` (regulation time, which is what 1X2, O/U 2.5 and BTTS settle on).
2. **Odds** (free plan, per fixture): one mandatory `/odds?fixture=` when a fixture enters the pre-decision window (kickoff − 135 min), and one closing capture in the last 45 min while budget remains. A paid plan can use league-date pages (`odds_mode="league_date"`). The budget guard, `provider_quota_log` and the 7 s throttle all apply.
3. **Decision snapshot** (once per fixture, at the first cycle with 0 < minutes to kickoff ≤ 120). The observation records:
   - forecast (1X2, O/U 2.5, BTTS) and its point-in-time inputs;
   - model source and data quality;
   - code revision and model/calibration versions;
   - quote status and age bucket;
   - all priced candidates with EV, price, bookmaker, quote age and fetch time;
   - legacy would-be picks with rejection reasons;
   - v1 with actual provenance and v1 with frozen provenance assumed;
   - BILQE layer gates, tier counts and rejections by failed layer.
4. **Settlement:** each would-be selection settles won/lost (flat 1 u). CLV is computed only when the same bookmaker published a later pre-kickoff price that we fetched before kickoff. Otherwise CLV is left undefined, never estimated.

Missing data stays missing. Examples: `NO_QUOTES`, `UNMAPPED`, a BILQE `error`, `observation_errors`.

## 3. Report contents (`scripts/shadow_report.py`)

The report covers:
- the actual period (first/last cycle, elapsed days, largest gap);
- fixture coverage;
- quote coverage per age bucket (0–60 / 61–120 / 121–240 / > 240) with acceptance at the window;
- versions;
- data quality;
- model sources;
- candidate and EV counts;
- legacy, v1 and v1-assuming-frozen rejection funnels;
- selections by market and league;
- BILQE gate failures and tiers;
- provider requests and errors per day, with the lowest remaining quota;
- settlement consistency;
- results and CLV per policy;
- Brier score and log loss per market;
- reproducibility (forecasts recomputed from stored inputs);
- a staleness check (`--check-stale-minutes`, exit 3) for monitoring.

## 4. Component-verification run (isolated shadow DB, 2026-10-16)

Shadow DB: `~/Desktop/football_predictor_backups/m3_2026-10-16/shadow/engine_shadow.db`, a copy of the remediated candidate. Cycle outputs are `cycle_*.json`; the report is `shadow_report_*.json`.

**Cycle 1 (13:14 UTC, forced discovery):**
- 2 fixture requests: 51 tracked fixtures, kicking off from 2026-10-16 17:00 to 2026-10-17 19:00 UTC.
- 16 league-date odds requests returned the free-plan season error ("Free plans do not have access to this season"). They produced nothing.
- This found K44. The collector was changed to per-fixture mode, and provider error payloads now count as failures.
- 0 observations, because no fixture was inside the decision window.

_Results of the 30-minute cycles from 14:47 to 17:17 UTC are added below when the run completes._

# Shadow Validation Report (M3 Phase F)

Commit `3889d0a`. Code: `src/shadow/cycle.py`, `src/shadow/report.py`, `src/shadow/store.py`, `src/shadow/legacy.py`, `scripts/shadow_cycle.py`, `scripts/shadow_report.py`. Scheduler templates (not installed): `scripts/orchestration/shadow/`. Tests: `tests/test_shadow_validation.py` (19).

## 1. Status

| Item | State |
|---|---|
| Official 14-day shadow period | **Not started.** Prerequisites need approval: cutover, activated remediation, odds budget, scheduler. |
| Observation period actually elapsed | 2026-10-16 14:47:36 → 18:28:31 UTC (six cycles; host asleep about 15:48–18:27). 6 genuine forward observations. **Not** a substitute for the 14 days. |
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

### 4.1 Six-cycle test (2026-10-16), completed

The six cycles ran from a worktree pinned at `b0d4e7a`, so M3.1 code changes could not alter them. They used the legacy probability inputs, before the K43 fix. Budget was 60/day; requests are listed below.

| Cycle | Planned (UTC) | Started | Finished | Odds requests | New quotes | Observations | Notes |
|---|---|---|---|---|---|---|---|
| 2 | 14:47 | 14:47:36 | 14:47:36 | 1 OK | 298 | 0 | decision fetch for the 17:00 kickoff |
| 3 | 15:17 | 15:17:37 | 15:17:39 | 0 | 0 | 1 | Çorum FK v Rizespor, 102 min before kickoff |
| 4 | 15:47 | 15:47:39 | 15:47:39 | 1 OK | 301 | 0 | decision fetch for the 18:00 kickoff |
| 5 | 16:17 | **18:27:59** | 18:28:30 | 5 OK | 1,564 | 5 | late: the machine slept about 15:48–18:27 |
| 6 | 16:47 | 18:28:31 | 18:28:31 | 0 | 0 | 0 | ran at once after waking |
| 7 | 17:17 | 18:28:31 | 18:28:31 | 0 | 0 | 0 | ran at once after waking |

Totals: 7 odds requests (all HTTP 200, 0 errors, 0 provider error payloads), 2,163 new quotes. The lowest remaining count the provider reported was 69/100.

**Observation kinds:**
- genuine forward: **6**, all recorded before kickoff (2.0–102.4 min before);
- synthetic: 0;
- retrospective: 0 recorded as observations. The M3.1 replay (§4.2) is retrospective and stored separately.

**Missed:** Heerenveen v Excelsior (18:00 UTC). Its decision odds were fetched at 15:47, but no cycle ran inside its decision window. No closing capture happened for any fixture.

| Fixture (kickoff UTC) | Decided (min before KO) | dq | Quote age at decision | Bucket | Candidates | Legacy | v1 (frozen assumed) | BILQE |
|---|---|---|---|---|---|---|---|---|
| Çorum FK v Rizespor, Süper Lig (17:00) | 15:17 (102) | 54 | 197.2 | 121–240 | 7 (4 positive edge) | **2 A+ picks: under 2.5 @2.44, BTTS no @2.45 (same match)** | 0 (input quality, league unproven, EV) | 0 qualified; market-edge layer rejected 71 |
| Eintracht Frankfurt v 1. FC Köln, Bundesliga (18:30) | 18:27 (2) | 58 | 259.8 | > 240 | 0: NO_REAL_ODDS | — | — | 0; market edge 77 |
| Frosinone v Sassuolo, Serie A (18:45) | 18:27 (17) | 56 | 320.7 | > 240 | 0: NO_REAL_ODDS | — | — | 0; market edge 77 |
| Le Mans v Toulouse, Ligue 1 (18:45) | 18:27 (17) | 54 | 69.7 | 61–120 | 7 (3 positive) | 0 | 0 | 0; market edge 71 |
| Burnley v Wolves, Championship (19:00) | 18:27 (32) | 52 | 283.7 | > 240 | 0: NO_REAL_ODDS | — | — | 0; market edge 74 |
| Deportivo La Coruna v Levante, La Liga (19:00) | 18:27 (32) | 50 | 266.6 | > 240 | 0: NO_REAL_ODDS | — | — | 0; data-quality gate failed |

| Measure | Value |
|---|---|
| Fixture coverage | 7 of 7 tracked fixtures with kickoffs in the run window got odds; 6 of 7 observed |
| Odds coverage at decision, 240-min window | 2/6 (33 %); 120 min: 1/6; 60 min: 0/6 |
| Every price used existed before the decision | yes, 6/6 |
| Legacy would-be picks | 2 (one match, correlated) |
| v1 / v1.1 selections | 0 / 0 |
| Settlement | **pending**: 0 outcomes recorded. Needs 1 request (`/fixtures?date=2026-10-16`), outside the approved six cycles, so it is not sent (`ODDS_FRESHNESS_AND_QUOTA.md` §4 item A). |
| CLV | not available (no closing captures) |
| Failures | none in requests. One missed decision snapshot and all closing captures, caused by host sleep. |

This is **not** a 14-day validation. It covers one evening and 6 observations.

### 4.2 Offline replay with M3.1 code (retrospective, not forward)

`src/shadow/replay.py` recomputed the six observations at their original decision times with identity-checked inputs, using only quotes fetched by then. No provider requests were made. Data: `m3_1/shadow_replay.json`.

- All 12 team identities resolved.
- The recorded legacy decisions were reproduced, including the same 2 A+ picks.
- Data quality rose for Burnley v Wolves (52 → 58) and Deportivo v Levante (50 → 54). Everything else was unchanged.
- v1.1 (frozen assumed, no provisional list) selected nothing. Rejections: `INPUT_QUALITY_LOW`, `LEAGUE_EVIDENCE_CONTAMINATED`, `RISK_ADJUSTED_EV_TOO_LOW`.

## 5. Readiness changes in M3.1

- Shadow observations use team-identity-v1 inputs. Unresolved identity is recorded as `NO_FORECAST: TEAM_IDENTITY_UNRESOLVED`, with identity provenance and API-Football team ids.
- v1.1 decisions are recorded and settled next to legacy and v1.
- Per-cycle request cap (default 12).
- `scripts/shadow_cycle.py` refuses `data/engine.db` while `data/SERVICE_HOLD.json` exists, even with `--allow-production`.
- Scheduler templates are disabled: the launchd plist has `Disabled` true and placeholders; the cron lines are commented out.
- Tests prove that a cycle:
  - never writes picks, never freezes a baseline and never counts;
  - never changes `calibration_models` / `model_confidence_adjustments`;
  - respects the request cap and budget 0;
  - and that the replay is offline and never uses later prices.

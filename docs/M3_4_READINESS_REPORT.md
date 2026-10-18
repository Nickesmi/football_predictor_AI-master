# M3.4 Readiness Report — Controlled Shadow Deployment

Branch `m3.4/shadow-readiness`, from `697430e` (tagged `backup/m3.3/complete`). Local only.

**The shadow experiment is not activated.**
- No scheduler is installed and no provider polling runs.
- Production is not restarted; the service hold is active and the live database is unmodified.
- No paid usage, no bets, no baseline counting.

## Commits

| Commit | Package | Content |
|---|---|---|
| `a467a2d` | A | Candidate-pathway verification tests (rh1, identity, ids, availability cut, bookmaker-only, golden reproduction) |
| `f2325ef` | D | Shared API ledger across consumers, process lock, stop on HTTP 429, downtime / no-back-fill tests, dry run with other consumers |
| `6662ef1` | E | Report: operational summary, history completeness, model-evidence disclaimer |
| `2f5c4a5` | C | Linux validation script and shadow runtime Dockerfile (not built) |
| (docs commit) | — | This report |

## 1. Candidate pipeline configuration and provenance (Package A)

| Element | Configuration | Proof |
|---|---|---|
| Profile | `PROFILES["medium"]`: all 14 competitions, no closing capture, ≤ 20 requests per cycle, ≤ 2 attempts per fixture window, discovery deduplicated (20 h) | `test_medium_profile_is_the_candidate_configuration` |
| Team history | **rh1**, rebuilt every cycle from finished stored fixtures of each competition's provider league ids. Same update rule as production (parity test). | `test_observation_uses_rh1_identity_and_never_team_state` (a misleading `team_state` row with 30 matches is ignored) |
| Identity | team-identity-v1 normalisation on (competition, name). Youth, reserve and women's teams distinct. Unresolved → `NO_FORECAST: TEAM_IDENTITY_UNRESOLVED`. | `test_unresolved_or_unknown_teams_get_no_forecast` |
| Provenance per observation | history version, canonical team id (`cth-…`), competition ids (API-Football + SofaScore), as-of fixture id, `available_from`, identity version, code revision, model and calibration versions, policy versions | same test |
| Point in time | A result is usable only from `available_from` = kickoff (local, read as UTC) + 3 h. A fixture that kicked off 2 h before the decision is **not** in the inputs; 61 min later it is. | `test_a_result_is_unusable_before_its_availability_timestamp` |
| Odds storage | K53 revisions; as-of reads use quotes fetched by the decision time | Package C tests (M3.3) |
| Bookmaker source | Outside `APP_ENV=test`, synthetic prices are invisible. Crowd votes are never prices. Only `odds_type = bookmaker` reaches candidates. | `test_only_genuine_bookmaker_prices_reach_a_shadow_decision` |
| Probability mathematics | unchanged (legacy Poisson, synthetic XGBoost weight 0) | goldens |
| Forward-style check | The shadow runner, at the candidate golden's decision time on the golden's seed, reproduces `golden_identity.identity-v1-rh1-m3.3` **exactly** for every fixture (6 forecasts, 2 NO_FORECAST) on 3.11 and 3.13 | `test_shadow_pathway_reproduces_the_candidate_rh1_golden` |
| Production model | **No switch.** `PROBABILITY_INPUTS` unset = legacy. | — |

## 2. Linux verification (Package C)

**Status: BLOCKED (not passed).**

**Blocker** (re-checked 2026-10-18 07:xx UTC):
- `docker info` exits 1.
- `docker desktop start` replies "Docker Desktop is already running", but the engine never comes up. The UI process is alive and the backend is not; the socket has been stale since 2026-10-13.
- No other Linux runtime is installed (no colima, podman, lima, multipass or OrbStack; UTM is installed but has no prepared VM).
- I did not restart Docker Desktop, because it is an application outside this scope.

**Prepared:**
- `scripts/orchestration/linux_validation.sh [<restored DB copy>]` runs everything below in an isolated `python:3.11-slim` (linux/amd64) container. The repo is mounted read-only, there are no API keys, and the conftest network guard is active. It reports `BLOCKED` and exits 3 when there is no daemon (tested).
  - full suite and goldens;
  - shadow, identity and odds tests;
  - migrations on the real DB copy;
  - the Medium dry run with 20 requests from other consumers;
  - the integrity audit.
- `deploy/shadow/Dockerfile`: a non-root runtime that runs one shadow cycle per invocation (Medium, shared ledger) with a `/shadow` volume. **Not built.** An example `.dockerignore` excludes `.env`, data, caches and virtualenvs.

**Remediation plan** (owner action, about 10 minutes):
1. Quit Docker Desktop fully and reopen it, or run `docker desktop restart`.
2. Confirm with `docker info`.
3. Run `scripts/orchestration/linux_validation.sh ~/Desktop/football_predictor_backups/m3_2026-10-16/engine_pre_m3.db`.
4. If Docker still fails, use the target Linux host itself: clone, `python3.11 -m venv .venv`, `pip install -r requirements-lock.txt`, then the same commands as the script.

The shadow must not be deployed on Linux before this passes.

## 3. Operational, process and database audit (Package B, checked 2026-10-18 07:08 UTC)

| Item | State |
|---|---|
| Service hold | active, `m3-hold-20261016-401a4675` |
| Production DB | `engine.db` 2026-10-16 15:35, WAL 15:43, both before the stop. WAL-aware `.dump` hash `4fc62ae03b391eab` = verified backup. No open handles. |
| Football processes | demo pid 2670 (`scripts/demo_server.py`, cwd `…/localhost_copy/demo`, DB `…/localhost_copy/engine.db`, port 8001); Vite pid 46214 (5173, frontend only). Not football: pid 65948 is `cryptopredic` (port 8000). |
| Demo cap | persistent usage file: **1 attempt, 1 sent** of 5 (`/fixtures?date=2026-10-18`, 2026-10-17 20:36 UTC); dates fetched: 2026-10-18 only |
| API traffic | no established TCP connection from any Python process; no other API consumer running |
| Schedulers | no crontab; no launchd job; no systemd (macOS); no `shadow_cycle`, `odds_worker` or experiment process. `caffeinate -t 300` (pid 84774) belongs to this Claude Code session. |
| Shared cache | `retrain.lock` moved to evidence with a manifest (complete). The four fixture caches are unchanged (by decision). A cache-validity review is **pending** before any production restart. |
| Git | clean after each commit |

## 4. API budget and host requirements (Package D)

### Budget (API-Football free plan: 100/day; shadow budget 70/day across all consumers)

| Guard | Enforcement | Verified by |
|---|---|---|
| 70/day across all consumers | `API_FOOTBALL_LEDGER`: one persistent ledger reserved **before** sending, checked against all consumers' total for the UTC day; failures count | `test_shared_ledger_enforces_one_daily_budget_across_consumers`, `test_failed_requests_count_against_the_shared_budget`, `test_ledger_survives_restarts`; dry run with 20 used elsewhere: day total **70** (50 shadow, 16 refused) |
| Provider-reported remaining | `ODDS_MIN_REMAINING = 20` (global, from provider headers) | M2.5 tests |
| 20 per cycle | `max_requests_per_cycle` | `test_request_cap_per_cycle`; dry run peak cycle 18 |
| Retry limit | ≤ 2 attempts per fixture window | `test_daily_budget_holds_across_restarts_failures_and_retries` |
| Provider throttling (HTTP 429) | stops the cycle's remaining requests; retried next cycle within the attempt cap | `test_provider_throttling_stops_the_cycle` |
| Duplicate schedulers | OS file lock per shadow DB, plus the DB cycle lease | `test_a_second_cycle_process_is_refused_while_one_runs`, `test_overlapping_cycles_after_wake_are_skipped_not_duplicated` |
| Downtime recovery | gaps recorded as missed slots; no observation is ever created after kickoff (no back-filling) | `test_no_retrospective_observation_after_downtime`, `test_missed_slots_are_recorded_after_a_sleep` |
| Honest missed observations | deferral until 60 min before kickoff, then recorded as NO_QUOTES; fixtures never observed are listed | dry run (`not_observed`) |

**Expected Medium usage:** about 312 requests over 14 days, about 22 per day, peak about 46–56 on Saturdays. Peak-day dry run: 62 requests, 52/53 observed.

### Is the experiment useful despite stale odds?

| Measurement | Needs fresh odds? | Expected over 14 days (Medium, ~270 fixtures) |
|---|---|---|
| Forecast calibration (1X2, O/U 2.5, BTTS) | no | ~270 settled forecasts per market. Enough to estimate a Brier score with about ±0.03 precision; not enough for per-competition calibration. |
| Identity and history completeness | no | all observed fixtures |
| Odds availability and age at decision | measures them | ~61 % within 240 min (~165 fixtures), ~7 % within 120, ~4 % within 60 (28 measurements so far) |
| Market-value funnel (v1/v1.1) | yes | ~165 fixtures can be evaluated; data quality ≥ 60 in about 22 % of fixtures, so few pass the input rules |
| Research selections | yes, plus owner list | ≈ 0: no provisional list, calibration unverified |
| CLV | closing prices | none (Medium has no closing capture) |

**Verdict:** useful for calibration evidence, identity and data-coverage measurement, and operational proof. **Not useful for proving profitability.**

### Host requirements

- Always on for 14 days. The Mac sleeps (K51): use an always-on Linux host, or the Mac on AC with `caffeinate` and accept recorded gaps.
- UTC clock with NTP.
- Python 3.11 with `requirements-lock.txt`, or the prepared image.
- About 1 GB of disk.
- Outbound HTTPS to `v3.football.api-sports.io` only; no inbound ports.
- An isolated directory holding the shadow DB, its `workspace/` (cache), the shared ledger and logs.
- The API key in the environment only, never in the image.
- Linux verification passed on the host or in its container (§2).

## 5. Fourteen-day monitoring and rollback

**Daily (about 5 minutes):**
```bash
python scripts/shadow_report.py --db <shadow>/engine_shadow.db --out <shadow>/reports/$(date +%F).json --check-stale-minutes 90
#  exit 3 = stale (host down or scheduler stopped)
```
Check in the report:
- `integrity.ok`: must be true;
- `operational_summary`: forward observations, identity share, odds-in-window share, provider errors;
- `cycle_health.missed_slots`;
- `provider_quota` and the shared ledger day total (≤ 70);
- `daily[<day>]`: coverage, qualification and identity.

**Stop criteria** (stop the timer and report):
- `integrity.ok` false;
- any observation decided at or after kickoff;
- any `counts_toward_baseline` ≠ 0;
- the ledger day total above 70;
- the provider reports fewer than 20 remaining.

**Stop or rollback:**
- Disable the timer: `systemctl disable --now footballpredictor-shadow.timer`, or `launchctl unload <plist>`.
- The shadow DB, workspace and ledger are isolated; keep them as evidence.
- No production rollback is needed: production is held and untouched.

**End of day 14:**
- final `shadow_report`;
- per-competition eligibility (E0–E5) and calibration per market;
- odds-age distribution;
- then a GO/NO-GO review.

## 6. GO / NO-GO checklist — controlled 14-day forward shadow

| # | Criterion | Status |
|---|---|---|
| 1 | Shadow pathway = candidate pipeline (rh1, identity-v1, ids, point in time, K53, bookmaker-only) | **GO**: proven by tests and golden reproduction |
| 2 | Cannot touch production, count, freeze or recalibrate | **GO**: tests |
| 3 | Budget 70/day across consumers, 20 per cycle, retry and throttle limits | **GO**: tests and dry run |
| 4 | Duplicate scheduler protection; downtime handled honestly | **GO**: tests |
| 5 | Monitoring and stop criteria defined | **GO**: §5 |
| 6 | Python 3.11 and 3.13 suites, goldens, migrations, audit, frontend | **GO**: 859/1 skip, 858/2 skip, 16/16, 4/4, 732 real / 0 weak, pass |
| 7 | **Linux verification** | **NO-GO until passed**: Docker blocked (§2) |
| 8 | Always-on host chosen | **PENDING** (owner) |
| 9 | Shared cache validity review (only before a production restart) | **PENDING**; not needed for the shadow (isolated workspace) |

**Recommendation:**
- **GO for the shadow once items 7 and 8 are resolved** (Linux check passes on the chosen host, or the Mac is chosen with recorded gaps) and you approve.
- **Official 1,000-pick baseline: NO-GO** (unchanged reasons in `M3_3_REPORT.md` §12).

## 7. Decisions requiring owner approval

1. Restart Docker Desktop (or provide a Linux host) and run `linux_validation.sh`.
2. The host: an always-on Linux machine, or this Mac with `caffeinate`.
3. Shadow budget 70/day, per-cycle cap 20, profile Medium, and the shared ledger path. All API consumers, including any demo, must use the same ledger.
4. The shadow DB: a fresh copy of the remediated candidate (recommended) or the existing M3 shadow DB.
5. Installing the disabled timer template.
6. Whether to confirm the 18 registry maps (not needed by rh1).
7. Calibration evidence definition and the E0–E5 thresholds (from M3.3). These are research-only; no provisional competitions or exposure rules are approved.

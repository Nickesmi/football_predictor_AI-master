# Fourteen-Day Shadow Experiment — Deployment Plan and Quota Estimate (M3.2, Decision 5)

**Prepared, not activated.** No scheduler is installed. Nothing polls a provider. Nothing runs in the background.

## 1. Requirements and how they are met

| Requirement | Implementation | Verified by |
|---|---|---|
| Isolated shadow DB | `scripts/shadow_cycle.py --db <path>`; recommended: a fresh copy of the remediated candidate | path checks |
| Never modify the live DB | Refuses `data/engine.db` while `SERVICE_HOLD.json` exists, even with `--allow-production`; `get_db()` hold check | `test_shadow_cycle_refuses_live_db_under_hold_even_with_allow_production` |
| Service hold stays | The shadow never releases it; no restart path | — |
| Scheduler disabled by default | launchd plist `Disabled`, cron lines commented, systemd templates not installed, all with placeholders | `test_scheduler_templates_are_disabled` |
| No duplicate observations after sleep/wake | `UNIQUE(fixture_id)` on observations; a cycle lease (10 min) records an overlapping start as `skipped`; systemd `Persistent=false` (no catch-up burst) | `test_overlapping_cycles_after_wake_are_skipped_not_duplicated` |
| Record missed cycles and provider failures | Every cycle stores its gap and missed 30-min slots; failures are in `provider_quota_log` and the cycle report | `test_missed_slots_are_recorded_after_a_sleep` |
| Enforce request budgets | Daily `--budget` (logged per provider), `--max-requests-per-cycle` (templates: 20), `ODDS_MIN_REMAINING=20`, 7 s throttle; an observation waits for its decision fetch instead of recording a spurious NO_QUOTES | `test_request_cap_per_cycle`, `test_observation_waits_for_the_decision_fetch_when_the_cap_is_hit` |
| Preserve original timestamps | `observed_at`, `fetched_at`, `decided_at`, `kickoff`; prices used must satisfy `fetched_at <= decided_at` | daily integrity check `prices_used_after_decision = 0` |
| Exclude from the 1,000-pick baseline | `counts_toward_baseline` CHECK = 0; no `picks` writes; no freeze | `test_cycle_never_freezes_counts_or_recalibrates` |
| Always-available host | §3 | — |
| Daily integrity, odds-coverage, calibration and qualification reports | `scripts/shadow_report.py`: `integrity`, `daily[day]` (integrity, odds coverage, qualification, identity), `calibration`, `cycle_health`, staleness exit code 3 | `test_daily_report_has_integrity_coverage_calibration_and_qualification` |

## 2. Request budget (API-Football free plan: 100 requests per UTC day)

Volume from stored fixtures of the 14 tracked competitions (2026-08-15 to 10-14): about 100 fixtures per week.
- Saturday about 40 (peak 53);
- Sunday about 23 (peak 40);
- Thursday about 15;
- other weekdays 2–7.

International breaks have fewer fixtures.

| Request type | Rule | Per 14 days |
|---|---|---|
| Discovery `/fixtures?date=` | today + tomorrow every **12 h** (recommended; was 6 h) = 4 per day | 56 |
| Results | dates of observed fixtures still awaiting a result, about 1 per day | 14–20 |
| Decision fetch `/odds?fixture=` | 1 per tracked fixture (mandatory) | ~200 |
| Closing fetch | 1 per fixture in the last 45 min, only while the budget lasts | 0–200 |
| **Total** | | **~270 (no closing) to ~475 (full closing)** |

Peak day (Saturday, 53 fixtures): 53 + 4 + 1 = 58 requests without closing. **Recommended daily budget: 70**, with `ODDS_MIN_REMAINING=20` and a per-cycle cap of 20. That leaves 30 or more for the production app if it is ever released during the experiment. Closing capture, and therefore CLV, will be partial on busy weekends.

There is no paid usage. The shadow cannot exceed the daily budget, and the provider-reported remaining count is a second guard.

## 3. Hosting recommendation

The 2026-10-16 test lost a decision snapshot and every closing capture because the Mac slept. Options:

| Option | Cost | Availability | Notes |
|---|---|---|---|
| **A. A small always-on Linux host** (VM, or an existing home server) with the systemd timer templates | a VM is a paid service and **needs approval**; an existing machine has no added cost | high | UTC clock with NTP; `Persistent=false`; repository checkout plus `.venv` (3.11); shadow DB on local disk |
| B. This Mac with launchd plus `caffeinate -s` / `pmset` (no sleep on AC, lid open) | none | medium: updates, reboots and lid closing still stop it | the cycle records missed slots, so gaps are visible |
| C. A hosted CI scheduler (e.g. scheduled workflows) | varies | timing jitter, ephemeral disk | not recommended: the shadow DB must persist |

**Recommendation:** A on an existing always-on machine if one is available, otherwise B for a first week with daily gap monitoring.

## 4. Activation checklist (each step needs approval)

1. Choose the host and the shadow DB: copy the remediated candidate into a new path; the M3 shadow DB is kept as evidence.
2. Approve the daily budget (70) and per-cycle cap (20).
3. Approve the registry entries to be used (or none). Identity-v1 is always used in shadow.
4. Install one template (systemd timer on Linux, or launchd on the Mac) after filling the placeholders.
5. Daily: `scripts/shadow_report.py --check-stale-minutes 90` (exit 3 means stale) and a review of `integrity.ok`.
6. Day 14: final report. Freshness p95, per-competition eligibility (E0–E5) and calibration feed the GO/NO-GO review.

## 5. What the 14 days can and cannot show

- **Can show:** odds coverage and age at decision time; identity and input-quality coverage; eligibility per competition; settlement integrity; early calibration (about 200 forecasts).
- **Cannot show:** a verified track record for any competition. The minimum is 100 verified settled predictions per competition, about 2–3 months for the busiest competition (about 12 tracked fixtures per week). Profitability cannot be shown either.

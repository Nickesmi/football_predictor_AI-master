# M3.1 Report — Team Identity, Evidence Quality and Shadow Readiness

Branch `m3/data-integrity-shadow` (continues M3; head before M3.1 was `b0d4e7a`). Local only.
- Production was not restarted and the service hold is still in place.
- Nothing was pushed, merged or deployed.
- No baseline counting, no real-money placement, no paid API usage.

## 1. Commits

| Commit | Content |
|---|---|
| `140e216` | `src/identity/`: competition registry, team-identity-v1 resolver, identity-checked inputs (`resolve_probability_inputs_v2`), coverage/before-after script, 29 tests |
| `9ce2822` | `src/policy/league_evidence.py`, `src/policy/baseline_v1_1.py` (proposal, inactive), comparison script, 15 tests; v1 league rule pluggable (v1 unchanged) |
| `d9bebad` | Shadow: identity-checked observations, v1.1 decisions, provider team ids, per-cycle request cap, hold refusal; scheduler templates disabled; 6 new tests |
| `4657ec4` | Shadow: offline retrospective replay; 1 test |
| `f098026` | This report, `TEAM_IDENTITY_REPORT.md`, `LEAGUE_EVIDENCE_POLICY_PROPOSAL.md`, `ODDS_FRESHNESS_AND_QUOTA.md`, updated `SHADOW_VALIDATION_REPORT.md`, `KNOWN_ISSUES.md` |

Not changed:
- probability mathematics and model weights;
- calibration code;
- qualification thresholds and BILQE;
- the production input path (`resolve_probability_inputs`);
- the official pipeline.

Golden references 9/9 on 3.11 and 3.13.

## 2. Six-cycle shadow test (`SHADOW_VALIDATION_REPORT.md` §4)

- **Code:** pinned to `b0d4e7a`. I moved the run to a frozen worktree before its first cycle (same schedule and budget) so M3.1 edits could not leak into it.
- **Period:** 2026-10-16 14:47:36 → 18:28:31 UTC. The host slept about 15:48–18:27, so three cycles ran late.
- **Observations:** 6 genuine forward observations, all before kickoff (2–102 min). 0 synthetic, 0 retrospective. 1 fixture missed; 0 closing captures.
- **Requests:** 7 odds requests, all OK; 2,163 quotes.
- **Odds coverage at decision:** 33 % at 240 min, 17 % at 120 min, 0 % at 60 min.
- **Selections:**
  - legacy: 2 A+ picks on one match (correlated, dq 54);
  - v1 and v1.1: 0;
  - BILQE: 0 qualified.
- **Settlement:** pending. It needs 1 request, which is not approved.
- **CLV:** not available.
- **Status:** not a 14-day validation.

## 3. K43 team identity (`TEAM_IDENTITY_REPORT.md`)

- **Root causes:**
  - the profile key is reused as the history key (Championship → "Premier League", "La Liga" → "LaLiga");
  - substring substitution ("Rangers" → *angers*, "Gent" → *argentina*, …);
  - hash-generated statistics;
  - static tables claiming 20 matches;
  - country-less labels that mix competitions ("Arsenal" history contaminated by another country's "Premier League" club).
- **Resolver:** versioned, deterministic, fail-closed.
  - explicit provider-id mappings, re-validated;
  - exact normalised name within the competition;
  - competition-verified history provenance;
  - youth, reserve and women markers kept;
  - ambiguous identities never auto-matched;
  - no static, substring or hash fallback;
  - identity provenance recorded in the inputs.
- **Coverage** (492 teams): 93.7 % resolved, 3.7 % ambiguous, 2.2 % no history, 1 unverified, 1 contaminated. Legacy used hash statistics for 317 of the same teams and substring matches for 23.
- **Before/after** (51 upcoming fixtures):
  - 39 resolved; 12 fail closed;
  - 22 unchanged, 17 changed by 3–37 pp;
  - mean data quality of resolved fixtures 54.7 → 62.4;
  - fixtures with dq ≥ 60: 4 → 10.
- **Early-season limit:** most correctly identified teams still have only 3–8 recorded matches, so their data quality stays below 60.
- **Stopped for approval.** Making v2 the production default is a material change to model inputs and forecast coverage. v2 is used in shadow only.

## 4. League evidence (`LEAGUE_EVIDENCE_POLICY_PROPOSAL.md`)

- Four classes, counted per competition id: verified sufficient, verified insufficient, none, contaminated/unverifiable. Only verified-sufficient evidence has a reliability value.
- Today: 13 of 14 tracked competitions are contaminated/unverifiable, 1 has none, 0 are sufficient.
- BILQE Layer 3 passes 14/14 after remediation (default 65). Strict verified passes 0/14.
- **Proposal: policy v1.1.**
  - Data quality (resolved identity, dq ≥ 60) is kept separate from demonstrated performance.
  - The forward baseline can start only on owner-listed provisional competitions, with a higher EV bar (0.04), a 0.5 u advisory stake and a `provisional_no_track_record` label.
  - Missing evidence never passes.
  - Not active.

## 5. Odds freshness (`ODDS_FRESHNESS_AND_QUOTA.md`)

- **Semantics:** one provider `update` stamp per fixture, shared across a league batch. Collection, decision and kickoff times are stored separately. Closing is derived (none captured yet).
- **Real coverage** (18 fixtures): 0 % at 60 min, 5.6 % at 120 min, 61.1 % at 240 min. For same-day fixtures the last provider update was 4.4–5.6 h before kickoff.
- **No look-ahead:** every price used existed in our records before the decision (6/6; asserted again in the replay).
- **Window:** not widened. The free API-Football feed is unlikely to meet the 95 % criterion at ≤ 240 min.
- **Quota plan for approval** (nothing scheduled):
  - A: 1 request to settle the six observations;
  - B: about 33 requests for the timestamp-semantics experiment;
  - C: about 600–750 requests over 14 days, 60/day cap, 12/cycle.

## 6. Shadow readiness (`SHADOW_VALIDATION_REPORT.md` §5)

| Must not | How it is prevented | Verified by |
|---|---|---|
| Access the live DB during the hold | `get_db()` and `api.main` hold check; `shadow_cycle.py` refuses `data/engine.db` while `SERVICE_HOLD.json` exists, even with `--allow-production` | `test_shadow_cycle_refuses_live_db_under_hold_even_with_allow_production`, `test_service_hold.py` |
| Activate the official baseline | The cycle never calls `freeze_baseline` or `run_pipeline`; `baseline_versions` unchanged | `test_cycle_never_freezes_counts_or_recalibrates` |
| Count shadow picks | `CHECK (counts_toward_baseline = 0)` on observations and results; no `picks` writes | same test; `test_counting_columns_cannot_be_set` |
| Spend quota unexpectedly | Daily `--budget` (default required), `--max-requests-per-cycle` 12, `ODDS_MIN_REMAINING` 20, throttle; scheduler templates disabled | `test_request_cap_per_cycle`, `test_budget_zero_sends_nothing_and_still_observes`, `test_scheduler_templates_are_disabled` |
| Trigger production recalibration | The cycle runs no refit; `calibration_models` / `model_confidence_adjustments` row counts unchanged | `test_cycle_never_freezes_counts_or_recalibrates` |

Any live shadow deployment (scheduler install, budget, shadow DB choice) is a separate approval.

## 7. Validation

| Check | Result |
|---|---|
| Python 3.11 (`.venv311`, `pytest tests`) | **PASS**: 748 passed, 1 skipped (M3: 698) |
| Python 3.13 (`.venv`, `pytest tests`) | **PASS**: 747 passed, 2 skipped (M3: 697) |
| Golden v3-m2.5 | **PASS**: 9/9 on 3.11 and 3.13 |
| Integrity audit | **PASS**: 633 real, 0 weak, 2 skipped |
| Migrations (fresh + real-DB copy) | **PASS**: 4/4 on 3.11 and 3.13 |
| Identity-mapping tests | **PASS**: 29 |
| League evidence / v1.1 tests | **PASS**: 15 |
| Shadow + replay tests | **PASS**: 19 |
| Frontend lint / build | **PASS** / **PASS** |
| Linux (python:3.11-slim) | **BLOCKED**: Docker daemon not running (stale socket from 2026-10-13, backend process absent). Retried; not restarted. |
| Working tree | Clean after each commit; all commits authored here; no unexpected file changes |
| Live DB | Untouched (`engine.db` mtime 15:35, WAL 15:43, both before the 15:45:56 stop); hold file unchanged |

The localhost demo started on request (pid 24022, port 8001) runs against a **copy** of the backup, with provider keys blank.

## 8. GO / NO-GO checklist for baseline activation

| # | Criterion | Status |
|---|---|---|
| 1 | Production data remediated and reviewed | **NO**: candidate only (M3) |
| 2 | Team identity correct in the official input path | **NO**: v2 ready, production default unchanged (approval) |
| 3 | Identity review queue cleared (18 ambiguous, 11 no-history, 1 unverified, 1 contaminated) | **NO** |
| 4 | Missing league evidence never passes (BILQE and official policy) | **NO** in production (BILQE default 65); **YES** in v1/v1.1 proposals |
| 5 | Selection policy approved and wired before freeze | **NO**: v1.1 proposal |
| 6 | Real-odds coverage ≥ 95 % within the approved window | **NO**: 61 % at 240 min (n=18); 0–6 % at ≤ 120 min |
| 7 | No look-ahead in prices | **YES**: verified on all decisions |
| 8 | 14-day shadow completed | **NO**: one evening, 6 observations |
| 9 | Settlement and CLV verified in shadow | **NO**: settlement pending (1 request); no closing captures |
| 10 | Python 3.11 cutover rehearsed | **YES** (M3) |
| 11 | Linux verification | **BLOCKED** |
| 12 | Scheduler cannot act unexpectedly | **YES**: disabled, capped, hold-aware |
| 13 | Data quality sufficient for selections | **NO**: early season, most fixtures dq < 60 even with correct identity |

**Recommendation: NO-GO for baseline activation.** It is GO for approving the next steps listed in §10.

## 9. Deployment and rollback implications

| Change (if approved) | Deployment effect | Rollback |
|---|---|---|
| v2 inputs as production default | Changes probabilities wherever legacy used hash, static or substring statistics (17 of 39 sample fixtures, up to 37 pp). Fixtures with unresolved identity get no forecast. New `team_identity_map` table (additive). Golden references for the official pipeline must be regenerated in the same commit. | Revert to `resolve_probability_inputs` (one call site per pipeline). The additive table is harmless. Picks keep their recorded identity provenance. |
| BILQE Layer 3 using evidence classes | BILQE qualifies nothing without approved provisional competitions or verified evidence | Revert the Layer 3 reliability source; no data migration |
| Policy v1.1 wired into `run_pipeline` | Selection code changes, so any frozen manifest becomes invalid. Wire before freezing `football-v1.0`. | Version flag back to legacy; decisions keep their policy version |
| Live shadow scheduler | Spends up to the approved budget per day; writes only to the chosen shadow DB | Unload the launchd job or remove the cron lines; the shadow DB is disposable |
| Settlement request (plan item A) | 1 request; writes outcomes to the isolated shadow DB only | None needed |

The service hold, live DB and production runtime are unaffected by everything in M3.1.

## 10. Pending approvals

1. Plan item A (1 request): settle the six observations.
2. Plan item B (about 33 requests): timestamp-semantics experiment.
3. v2 identity inputs for the official pipeline (and whether also for analysis/BILQE), with golden regeneration.
4. Explicit mappings for the identity review queue (owner-reviewed list).
5. League-evidence policy: provisional list, EV bar, stake, sufficiency thresholds; BILQE Layer 3 change.
6. M3 data remediation in production and the cutover (unchanged from M3).
7. Live shadow deployment: scheduler, daily budget, shadow DB, and a non-sleeping host.
8. Linux verification once Docker works.

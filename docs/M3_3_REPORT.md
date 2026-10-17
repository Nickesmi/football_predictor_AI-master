# M3.3 Report — Historical Reconstruction, Identity Governance, Odds Correctness, Shadow Readiness

Branch `m3.3/history-identity-odds`, from `03bd799` (tagged `backup/m3.2/complete`). Local only.

Nothing was pushed, merged, deployed or restarted:
- the service hold is active and the live database is unmodified;
- no scheduler was activated;
- no paid quota was used, no bets were placed and no baseline counting started.

## 1. Commits

| Commit | Package | Content |
|---|---|---|
| `dbea35d` | A | Point-in-time team history rebuilt per competition id (rh1); audit script; registry audit (18 maps accepted, 13 exclusions confirmed); M3.2 golden reproduced with the M3.2 registry state |
| `9f955b0` | B | identity-v1-rh1 inputs, candidate golden `identity-v1-rh1-m3.3`, comparison script; shadow uses rh1. **Committed with one failing test**, fixed in `5530f1a`. |
| `5530f1a` | B (fix) | Identity modes name the SofaScore provider for pipeline fixtures (they were all UNKNOWN_COMPETITION) |
| `251f07e` | C | Quote revisions (K53), revision-aware as-of and closing reads, migration and rollback |
| `51f9f27` | E | Low/medium/full profiles, competition filter, discovery dedup, budget estimator |
| `0df470c` | E (verification) | Retry cap, CLI profile tests, offline Medium dry run, crontab template on medium |
| `3d3396f` | F | Five separate decision layers; VALIDATED needs verified calibration |
| `e5c5ca4` | E/C check | Retries and closing fetches keep one observation and every revision |
| (docs commit) | D + report | This report, seven companion documents, `KNOWN_ISSUES.md` |

Unchanged:
- Poisson mathematics, model weights and calibration coefficients;
- the production default input path (`PROBABILITY_INPUTS` unset = legacy);
- every earlier golden reference (SHA-256 pinned).

## 2. Package A — historical team-data reconstruction (`TEAM_HISTORY_RECONSTRUCTION_AUDIT.md`)

- **Method:** rh1 replays 647 finished stored fixtures of the 14 tracked competitions, using provider league ids only.
  - Keyed by (competition, normalised name); youth, reserve and women's teams stay distinct.
  - Feed duplicates count once, with all source fixture ids kept.
  - Collisions are excluded.
  - Each state row carries its fixture id and a conservative `available_from` (kickoff + 3 h).
- **Same mathematics:** a parity test shows exact equality with `on_match_finished` on clean data. This is a data-only reconstruction, so no new model or calibration strategy is needed.
- **Result:** 492 teams, all resolved, 0 collisions, 1,294 point-in-time rows.
- **Legacy sources today for the same teams:**

  | Source | Teams |
  |---|---|
  | Hash statistics | 315 |
  | Correct-key `team_state` | 109 |
  | Static "20 matches" | 31 |
  | LIKE fallback (another record) | 17 |
  | Static substring (another team) | 20 |

- **Named cases:**

  | Team | Legacy today | Rebuilt |
  |---|---|---|
  | Arsenal | 9 matches, mostly another country's club | 1 clean English match |
  | Inter | Internacional (Brazil) | 4 Serie A fixtures |
  | Chelsea U21 | the senior Chelsea record | no history (fails closed) |
  | Rangers | Angers' entry | 4 Premiership fixtures |
  | Gent | Argentina's entry | 4 Pro League fixtures |

- **Temporal integrity:** tested. Results become usable only from kickoff + 3 h (or the end of the matchday + 3 h when the kickoff is unknown), later fixtures are excluded, and postponed fixtures are ignored.

## 3. Identity mapping (`TEAM_REGISTRY_APPROVAL_EVIDENCE.md`)

- **31/31 registry entries audited:**
  - **18 maps accepted** under the owner-delegated criteria: the record verifies by competition fixtures, rh1 resolves the same name, no collision, consistent raw names;
  - **13 exclusions confirmed** for the `team_state` path (contaminated or unverifiable).
- **Owner confirmation is pending.** The entries are marked approved with their basis recorded, and are reversible one field at a time.
- **On rh1, all 13 excluded teams resolve** from their own competition fixtures with no alias.
- **Unresolved cases on rh1:**
  - teams with no observed fixture in a tracked competition before the decision (e.g. youth sides, newly seen clubs), which get no forecast;
  - provider team ids are not yet bound (they bind at the first identity-checked resolution with an id).

## 4. Package B — probability changes (`IDENTITY_RH1_PROBABILITY_COMPARISON.md`)

Real upcoming fixtures (51), decision at kickoff − 120 min:

| | Legacy | identity-v1 (team_state + registry) | **identity-v1-rh1** |
|---|---|---|---|
| Forecasts | 51 (with substituted or synthetic inputs) | 44 | **51** |
| 1X2: changed / mean / max | — | 20 / 5.07 / 37.4 pp | 47 / 6.08 / 37.6 pp |
| O/U 2.5: changed / mean / max | — | 20 / 7.74 / 39.8 pp | 47 / 9.82 / 37.4 pp |
| BTTS: changed / mean / max | — | 20 / 6.05 / 30.7 pp | 47 / 7.62 / 28.8 pp |
| Mean data quality | 55.7 | 62.2 | 61.0 |
| Forecast and dq ≥ 60 | 4 | 11 | 11 |

- **Candidate golden:** `golden_identity.identity-v1-rh1-m3.3`, identical on 3.11 and 3.13. Every change is attributed.
- **Switch:** off in the production configuration.

## 5. Package C — odds-history correction (`ODDS_HISTORY_CORRECTION.md`)

- **K53 fixed.** A changed price under an unchanged provider stamp becomes a new revision. Identical repeated responses insert nothing. A new stamp with an unchanged price is kept. Rows are never updated.
- **As-of reads:** the latest stamp, then the latest revision collected by the decision time. A price collected after a decision never enters it.
- **Closing / CLV:** reconstructed on the same order.
- **Migration:** in place, checksum-verified. Rehearsed on copies with 2,163 and 3,335 quotes (identical checksums, exact rollback). Rollback archives revisions and never deletes them.
- **Experiment replay:** the timestamp-experiment responses pass through the store with no loss (308 + 0 + 308 rows).

## 6. Package D — timestamp experiment (`TIMESTAMP_EXPERIMENT_FINDINGS.md`)

- **Requests:**
  - 11 sent: 1 `/status` (Free plan, active) and 10 `/odds?fixture=`, all HTTP 200;
  - 2 refused by the budget guard (the research DB's daily count included today's settlement request). Not re-run;
  - provider-reported **remaining after the last request: 89/100**.
- **Timing deviation:** the rounds ran at about T−100, T−50 and T−15 min (not T−6 h / −2 h 30 / −30 min), because only late-evening fixtures were known without a discovery request.
- **Stamp changes:** 1 (Lille v Brest, 13:36:15 → 18:00:33). It came with **real price changes**: 1,329 values, 28 of them in 1X2, O/U 2.5 and BTTS.
- **Unchanged:** 5 pairs, with stamp and prices both unchanged.
- **Not observed:** a price change under an unchanged stamp, or a stamp change with unchanged prices.
- **Verdict: ambiguous.** The data are consistent with "last price change" and do not exclude "last check". No definitive claim.
- **Stamp age at fetch:** 1/10 within 60 min, 1/10 within 120 min, 6/10 within 240 min.

## 7. Package E — shadow costs and coverage (`SHADOW_BUDGET_OPTIMIZED.md`)

| 14 days | Low (6 competitions) | **Medium (recommended)** | Full (+ closing) |
|---|---|---|---|
| Fixtures observed | ~120 | ~270 (all tracked) | ~270 |
| Provider requests | ~162 | **~312** | ~582 |
| Average / peak day | 12 / 23 | 22 / 46 (≈ 56 at the observed 53-fixture peak) | 42 / 89 (over budget) |
| Fits 70/day on the peak | yes | yes | no (~19 closing fetches lost) |

**Medium, verified offline** with `scripts/shadow_dry_run.py`: a peak Saturday (53 fixtures, injected HTTP 500s, 2 h host sleep).

| | Budget 70 | Budget 40 |
|---|---|---|
| Requests that UTC day | 62 sent, 0 refused | exactly 40 sent, 20 refused |
| Observations | 52 of 53, all quoted | 52 (35 quoted, 17 recorded honestly as NO_QUOTES) |
| Missed slots recorded | 4 | — |
| Integrity checks | all pass | all pass |
| Picks | 0 | 0 |

- The one unobserved fixture: its fetch failed and its retry fell inside the simulated sleep. It is recorded as missed, never filled in.
- Retries are capped at 2 attempts per fixture window.
- The daily budget holds across restarts and failures.
- The `--profile` flag is applied (tested), and all three templates (launchd, systemd, cron) use `--profile medium`.
- Retries and closing fetches never duplicate an observation or lose a revision (tests).

**Expected coverage under Medium:**
- **Fixtures:** every tracked fixture gets a decision fetch and an observation, provided the host stays up.
- **Odds:** at decision time about 61 % of fixtures have a quote within 240 min, about 7 % within 120 and about 4 % within 60. That is from 28 genuine measurements in total: probe 12, shadow decisions 6, experiment 10.

## 8. Package F — selection policy (`SELECTION_POLICY_ISSUES.md`)

Five separate statuses per decision, never scores: input quality, calibration evidence, forward performance, market value, selection status.
- VALIDATED needs verified calibration **and** demonstrated performance. **Today nothing can be validated.**
- RESEARCH_PROVISIONAL decisions carry an explicit "not validated, not evidence of profitability, paper units only" disclaimer.

Decisions still open:
1. The definition of verified calibration.
2. E0/E2/E3 thresholds versus a longer observation period or a history backfill (lowering is not recommended).
3. The provisional list (proposed: empty until a competition is eligible).
4. Exposure limits.
5. BILQE Layer 3 adopting the evidence classes.

## 9. Validation

| Check | Result |
|---|---|
| Python 3.11 full suite (`.venv311`, `pytest tests`) | **PASS**: 831 passed, 1 skipped (the env-gated real-DB migration test, run separately) |
| Python 3.13 comparison suite | **PASS**: 830 passed, 2 skipped |
| Golden tests: legacy v3-m2.5, history v1/v2, identity-v1-m3.2, **candidate identity-v1-rh1-m3.3** | **PASS**: 16/16 on 3.11 and 3.13 |
| Team identity and point-in-time history tests | **PASS** (registry 18, identity 29, rh1 history 12) |
| Odds-history and as-of tests | **PASS** (K53 13, odds M3 24, odds layer) |
| Shadow tests incl. replay, settlement, lease, retry cap, profiles | **PASS** (41) |
| Migrations on a restored production copy | **PASS**: 4/4 on 3.11 and 3.13 |
| CI integrity audit | **PASS**: 705 real tests, 0 weak |
| Frontend lint / build | **PASS** / **PASS** |
| **Linux (python:3.11-slim container)** | **BLOCKED**: Docker Desktop UI process present, but the daemon is not running. `docker info` exits 1; `~/.docker/run/docker.sock` is a stale socket from 2026-10-13; no `com.docker.backend` process. Retried this milestone; not restarted (an outside application). |

## 10. Production state (verified at the end of M3.3)

- `data/engine.db` mtime 2026-10-16 15:35 and WAL 15:43, both before the 15:45:56 stop. No process has it open.
- `data/SERVICE_HOLD.json` is unchanged (`m3-hold-20261016-401a4675`).
- No football API on port 8001 except the localhost demo started on request, which runs on a **backup copy** with blank keys.
- No cron, launchd or systemd job; no `shadow_cycle` process.

## 11. Unresolved critical defects and risks

| # | Defect | Where it bites | Status |
|---|---|---|---|
| K54/K55/K56 | Legacy production inputs: LIKE fallback reads and **writes** another club's row; Arsenal contaminated; hash and static statistics for 315 teams | the production legacy path (default) | fixed only in identity-v1-rh1 (switch off) |
| K32/K46 | Fabricated outcome rows and retrospective calibration source | production artifacts | remediation candidate ready (M3); not applied |
| K36/K45 | BILQE treats missing league evidence as 65 | production BILQE | proposal only |
| K44 | Free API-Football odds are mostly 2.7–4.9 h old at decision time; stamp semantics ambiguous | coverage of any freshness rule | provider limitation |
| K50 | Short stored history: most fixtures have data quality < 60 | value of selections | backfill would need requests |
| K51 | Host sleep stops scheduled cycles | shadow deployment | needs an always-on host |
| Linux | No container verification since M2.5 | deployment on Linux | blocked (Docker) |
| Registry | 18 accepted maps await owner confirmation | identity-v1 (team_state path) | pending |

## 12. Recommendation

**Controlled 14-day forward shadow (research only): CONDITIONAL GO.**

Evidence:
- All suites and goldens pass, and the Medium dry run stays inside budget with integrity checks passing.
- Shadow observations cannot count, freeze, recalibrate or touch the live DB (tests).
- The scheduler templates are disabled and ready.

Conditions, each needing approval:
1. An always-on host (or this Mac with launchd and `caffeinate`, accepting recorded gaps).
2. A Linux container check on that host if it is Linux.
3. Daily budget 70, profile medium, per-cycle cap 20.
4. A fresh isolated shadow DB.
5. Whether to confirm the 18 registry maps (rh1 does not need them).

**Official 1,000-pick baseline: NO-GO.** Reasons:
- The legacy production inputs are still defective.
- The remediation is not applied.
- No competition has verified forward evidence.
- Calibration is unverified.
- Odds freshness is insufficient.
- Linux verification is blocked.

M3.3 is complete. Stopping for review; M3.4 is not started.

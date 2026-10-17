# M3.2 Report — Identity-Corrected Predictions, Conservative Evidence Policy, Shadow Experiment Preparation

Branch `m3.2/identity-evidence-shadow-prep`, from `f098026` (tagged `backup/m3.1/complete`). Local only.

Nothing was pushed, merged, deployed or restarted. The service hold is active, the live database is unmodified, and nothing was placed with real money or counted toward the baseline.

## 1. Commits

| Commit | Content |
|---|---|
| `afb7bc4` | Reviewed team registry (31 entries, all proposed), canonical team ids, competition ids; registry tests |
| `4753ae4` | Identity-v1 golden (new, versioned; 3.11 = 3.13), inactive `PROBABILITY_INPUTS` pipeline switch with `NO_FORECAST / TEAM_IDENTITY_UNRESOLVED`, exact-record reads (no LIKE fallback), legacy references SHA-pinned |
| `28bb9d3` | One-request settlement tool; identity comparison by market and with the proposed registry |
| `0dbc066` | Competition eligibility (E0–E5) for the research-provisional mode; v1.1 requires owner list **and** eligibility |
| `4387470` | Shadow: cycle lease (no duplicates after sleep/wake), missed-slot accounting, daily integrity reports, disabled systemd templates |
| `cf8c041` | Minimal odds-timestamp experiment tooling (offline plan/analyse; guarded fetch); K53 characterisation |
| `ef829d7` | Shadow: an observation waits for its decision fetch; per-cycle cap 20 in templates |
| `5eb2ed6` | Shadow discovery every 12 h by default |
| (docs commit) | This report and the six companion documents; `KNOWN_ISSUES.md` |

Unchanged:
- probability mathematics, model coefficients and calibration;
- qualification thresholds and BILQE;
- the production default input path (legacy);
- the legacy golden references, byte-identical.

## 2. Deliverables

| # | Document |
|---|---|
| 1 | this report |
| 2 | `SHADOW_SETTLEMENT_RESULTS.md` |
| 3 | `TEAM_IDENTITY_MAPPING_REVIEW.md` |
| 4 | `IDENTITY_PROBABILITY_COMPARISON.md` |
| 5 | `PROVISIONAL_LEAGUE_EVIDENCE_POLICY.md` |
| 6 | `ODDS_TIMESTAMP_EXPERIMENT.md` |
| 7 | `SHADOW_14_DAY_PLAN.md` |
| 8 | GO/NO-GO assessment: §5 below |

## 3. Results by decision

**Decision 1, settlement:**
- Exactly **1 request** (`/fixtures?date=2026-10-16`, HTTP 200, free plan, 99 remaining afterwards).
- Written only into the isolated shadow DB.
- 6 of 6 observations settled from final FT scores; nothing postponed or unresolved.
- 1X2 most-likely correct 3/6. Brier: 1X2 0.602, O/U 2.5 0.316, BTTS 0.306 (n = 6, no statistical weight).
- The legacy selection's 2 would-be picks both lost (−2.0 u). v1 and v1.1 had 0 selections. No CLV (no closing prices).

**Decision 2, identity:**
- The identity-checked pathway is implemented, tested and versioned. It is not activated.
- New finding: the legacy `get_team_state` LIKE fallback maps "Inter" → Internacional (Brazil) and "Chelsea U21" → Chelsea.
- The English "Arsenal" record that production uses today is 7/8 another club's fixtures (competition 116).
- 31 review cases: 18 proposed maps verified by competition fixtures, 13 exclusions (no unverified aliases).
- 51 real fixtures:
  - identity-v1 forecasts 39 (44 with the proposed maps); 17 change;
  - mean absolute change 4.8 pp (1X2), 6.6 pp (O/U 2.5), 5.1 pp (BTTS); max 37.4 pp;
  - data quality ≥ 60 rises from 2 to 10 fixtures.

**Decision 3, league evidence:**
- Performance evidence (verified forward results only, no default score) is kept separate from competition eligibility (identity, input quality, odds, markets, calibration provenance).
- Research-provisional decisions are labelled, carry a higher EV bar and a half stake, and need owner listing **and** measured eligibility.
- Today: 0 competitions strict-eligible, 0 research-eligible (insufficient sample).

**Decision 4, odds:**
- Minimal experiment designed: 13 requests (4 fixtures × 3 fetches + 1 discovery; floor 7). Not run.
- It distinguishes last-change, last-check and unreliable stamps by comparing stamps and prices across repeated fetches.
- Current coverage: 0 % / 5.6 % / 61.1 % at 60 / 120 / 240 min. The 240-minute window is unchanged and provisional for shadow research.

**Decision 5, shadow:**
- Full 14-day plan prepared:
  - about 270–475 requests over 14 days;
  - recommended daily budget 70 and per-cycle cap 20;
  - an always-on host (an existing Linux machine, or a no-sleep Mac).
- Infrastructure:
  - lease against duplicate cycles, missed-slot records;
  - the observation waits for its decision fetch;
  - daily integrity, coverage, qualification and calibration reports;
  - all templates disabled.
- Not activated.

## 4. Validation (Decision 6)

| Check | Result |
|---|---|
| Python 3.11 full suite (`.venv311`, `pytest tests`) | **PASS**: 792 passed, 1 skipped (the env-gated real-DB migration test, run separately below) |
| Python 3.13 full suite | **PASS**: 791 passed, 2 skipped |
| Legacy golden v3-m2.5 + history | **PASS**: unchanged, SHA-256 pinned |
| Identity-corrected golden (identity-v1-m3.2) | **PASS** on 3.11 and 3.13 (reference files identical across runtimes) |
| League-evidence / eligibility tests | **PASS** (18) |
| Shadow settlement and shadow tests | **PASS** (33, including final-status-only settlement, lease, missed slots, deferral, daily reports) |
| Odds timestamp / as-of tests | **PASS** (24 odds-M3 including K53 characterisation, 4 experiment) |
| Identity and registry tests | **PASS** (29 + 18) |
| Migrations (fresh + real-DB copy) | **PASS**: 4/4 on 3.11 and 3.13 |
| Integrity audit | **PASS**: 666 real tests, 0 weak |
| Frontend lint / build | **PASS** / **PASS** |
| Linux (python:3.11-slim) | **BLOCKED**: Docker daemon not running (`docker info` exit 1; backend process absent). Not restarted. |
| Working tree | clean after each commit; all commits from this session; no unexpected changes |
| Live DB | unmodified since before the 2026-10-16 stop; service hold file unchanged |

Intended probability changes are all confined to the identity-v1 pathway and recorded in the identity golden and comparison. The production (legacy) path is unchanged: v3-m2.5 golden passes byte-identical.

## 5. GO / NO-GO assessment

| # | Criterion | Status |
|---|---|---|
| 1 | Production data remediated and reviewed | **NO**: isolated candidate only |
| 2 | Identity-correct inputs in the official path | **READY, NOT ACTIVE**: needs approval of the comparison + golden |
| 3 | Identity review queue | **PREPARED**: 18 proposed maps await approval; 13 exclusions need a per-competition `team_state` rebuild (K49) |
| 4 | Missing evidence never passes | **YES** in v1/v1.1 and eligibility; **NO** in production BILQE (default 65) |
| 5 | Selection policy approved and wired before freeze | **NO**: v1.1 proposal |
| 6 | Odds freshness ≥ 95 % within an approved window | **NO**: 61 % at 240 min; timestamp semantics unknown (experiment designed) |
| 7 | No look-ahead | **YES** |
| 8 | 14-day shadow completed | **NO**: plan ready, not activated |
| 9 | Settlement verified | **YES** for the 6 observations (one request) |
| 10 | CLV | **NO**: no closing captures yet |
| 11 | Data quality sufficient | **NO**: early season; most corrected fixtures dq < 60 |
| 12 | Scheduler cannot act unexpectedly | **YES** |
| 13 | Linux verification | **BLOCKED** |
| 14 | Contaminated history in production inputs (Arsenal, LIKE fallback) | **NO**: present in the legacy path today |

**Recommendation: NO-GO for official baseline activation.**

## 6. Approvals requested

1. The identity comparison and the identity-v1 golden. If approved, `PROBABILITY_INPUTS=identity-v1` for the official pipeline, plus a decision on match analysis/BILQE.
2. The 18 proposed registry maps (or a subset), and a per-competition `team_state` rebuild to clear the 13 exclusions (K49; changes inputs).
3. Competition eligibility thresholds E0–E5, the provisional EV bar, stakes and exposure limits, and the BILQE Layer 3 change.
4. The 13-request odds-timestamp experiment, on one Saturday with an awake host.
5. The 14-day shadow: host, shadow DB, daily budget 70, per-cycle cap 20, registry entries in use.
6. A K53 fix (odds_quotes unique key to include the price). This is a schema change for an unreleased table.
7. Production remediation and cutover (unchanged from M3).
8. Linux verification when Docker is available.

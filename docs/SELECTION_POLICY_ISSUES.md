# Selection-Policy Review — Issues Requiring Approval (M3.3 Package F)

Commit `3d3396f`. Code: `src/policy/baseline_v1_1.py`, `src/policy/competition_eligibility.py`, `src/policy/league_evidence.py`. **Policy v1.1 stays inactive.** No stake rule was changed, and no real-money placement exists.

## 1. Five separate questions

Every v1.1 decision now reports five **statuses**, never scores. None is inferred from another.

| Layer | Question | Source | Today |
|---|---|---|---|
| 1. Input quality | Are this forecast's inputs good enough? | identity resolved (identity-v1 / rh1), data quality ≥ 60, non-fallback model | 11 of 51 sample fixtures pass |
| 2. Calibration evidence | Has the model shown calibrated probabilities on verified forward data? | `CalibrationEvidence` (verified forward sample, Brier) | **UNVERIFIED**: no verified forward calibration sample exists (6 settled shadow forecasts) |
| 3. Forward predictive performance | Has the model demonstrated performance in this competition? | `league_evidence` classes from VERIFIED_SETTLED predictions only | 0 of 14 competitions: 13 contaminated/unverifiable, 1 none |
| 4. Market value | Is there positive value against a genuine, fresh price? | bookmaker price, provider age ≤ 240 (provisional), fetched ≤ 60 min before the decision, odds 1.30–6.00, edge ≤ 0.15, risk-adjusted EV ≥ 0.02 (0.04 provisional) | measured per candidate |
| 5. Selection status | VALIDATED, RESEARCH_PROVISIONAL or REJECTED | VALIDATED needs 1–4 (verified calibration **and** demonstrated performance). PROVISIONAL needs 1 and 4, an owner-listed competition and research eligibility (E0–E5). | nothing can be VALIDATED; nothing is PROVISIONAL (no list; eligibility sample too small) |

- Missing evidence never becomes a reliability score. `forward_performance` is an evidence class; `calibration_evidence` is VERIFIED or UNVERIFIED.
- A research-provisional decision carries this disclaimer: *"research-provisional: not a validated selection, not evidence of profitability, paper units only"*.
- **Change in M3.3:** VALIDATED also requires verified calibration (`CALIBRATION_NOT_VERIFIED` otherwise). This is stricter and needs approval.

## 2. Review of the proposed thresholds

| Rule | Proposed | Assessment |
|---|---|---|
| E0 sample ≥ 20 observed fixtures per competition | | The medium shadow profile gives about 12–26 per competition in 14 days, so several competitions will remain INSUFFICIENT_SAMPLE after 14 days. Consider 28 days, or accept that. |
| E1 identity ≥ 90 % | | Reachable with rh1 (51/51 resolved in the sample) |
| E2 input quality ≥ 60 % of fixtures with dq ≥ 60 | | **Likely unreachable this autumn**: 11 of 51 sample fixtures have dq ≥ 60 (stored history is short). Options: (a) keep it and wait for history; (b) approve a provider backfill of past fixtures (requests); (c) lower it. (c) is **not recommended**: it would admit thin-input forecasts. |
| E3 odds ≥ 80 % within 240 min | | **Not met by API-Football free** on current data (33–61 %). The timestamp experiment informs whether the stamp age is the right clock. |
| E4 markets ≥ 3 bookmakers for all three markets | | Met in the probe (5–6 bookmakers) |
| E5 calibration provenance recorded | | Met by shadow observations |
| Provisional risk-adjusted EV ≥ 0.04, 0.5 u paper stake, one per match | | Conservative. Unchanged. |
| Exposure limits (≤ 5 provisional per day, ≤ 2 per competition) | not yet in code | needs a decision |
| Verified calibration sample for VALIDATED | not yet defined | **needs a definition**, e.g. ≥ 300 settled forward forecasts per market with Brier and reliability-curve checks |

## 3. Decisions required

1. Approve the five-layer model and the stricter VALIDATED rule (verified calibration as well as performance).
2. Define "verified calibration": sample size, metrics, per market or pooled.
3. E0/E2/E3: keep, extend the observation period, or approve a history backfill. Lowering any threshold is not recommended.
4. The provisional competition list stays empty until competitions are research-eligible. Confirm.
5. The exposure limits for provisional research decisions.
6. BILQE Layer 3: adopt the evidence classes (replacing "unknown = 65").

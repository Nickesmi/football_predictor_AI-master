# League-Evidence Handling and Policy v1.1 — Proposal (M3.1)

Commit `9ce2822`. Code:
- `src/policy/league_evidence.py`
- `src/policy/baseline_v1_1.py`
- `scripts/compare_league_evidence.py`
- v1 league rule made pluggable, with v1 behaviour unchanged

Tests: `tests/test_league_evidence_policy.py` (15). Data: `m3_1/league_evidence_comparison.json`.

**Not active.** BILQE and the official pipeline are unchanged.

## 1. Problem

After removing the contaminated league adjustments, BILQE Layer 3 gives every league with no active adjustment the "unknown league" reliability of **65**. The gate is 60, so missing evidence passes as if reliability had been demonstrated.

| BILQE Layer 3 on the 14 tracked competitions | Pass |
|---|---|
| Original artifacts (fabricated-data adjustments) | 12 / 14 |
| Remediated candidate (no active adjustments) | **14 / 14**, all through the default 65 |

## 2. Evidence classes (per competition id, not per label)

Evidence is counted through the fixture's provider league id, because a label like "Premier League" spans about 25 competitions. Only `prediction_errors` rows classified `VERIFIED_SETTLED` count.

| Class | Definition | Reliability value |
|---|---|---|
| VERIFIED_SUFFICIENT | ≥ 100 verified settled forward predictions | computed from them only |
| VERIFIED_INSUFFICIENT | 1–99 | none (count reported) |
| NO_VERIFIED_EVIDENCE | 0, and no quarantined rows | none |
| CONTAMINATED_OR_UNVERIFIABLE | 0 verified, quarantined rows exist | none; neither positive nor negative |

Current state, on the remediated candidate:
- 13 of the 14 tracked competitions are **CONTAMINATED_OR_UNVERIFIABLE**;
- 1 has **NO_VERIFIED_EVIDENCE**;
- 0 are sufficient;
- all 77 verified settled predictions belong to untracked competitions.

## 3. Alternatives compared

| Alternative | Rule | Tracked competitions passing today |
|---|---|---|
| A. Current BILQE default | active adjustment's reliability, else 65 | 14 (remediated) — **rejected**: missing evidence becomes reliability |
| B. Strict verified | VERIFIED_SUFFICIENT and reliability ≥ 60 | **0**: no baseline can start |
| C. Approved provisional (proposed) | B, **or** the owner lists the competition as provisional | exactly the listed ones |
| D. Neutral-prior shrinkage to 0 | missing evidence → reliability 0 | 0: same as B |

Simulation (seed 20261016, 2,000 matches, same generator as the v1 comparison):

| Scenario | legacy | v1 (illustrative list) | v1.1 default | v1.1 provisional (illustrative 6) |
|---|---|---|---|---|
| Efficient market: selections / expected ROI | 632 / −4.55 % | 81 / −4.72 % | 0 | 65 / −4.74 % |
| Model has an edge: selections / expected ROI | 365 / +10.17 % | 65 / +13.31 % | 0 | **50 / +15.28 %** |

As before, no selection rule creates an edge the model does not have. When there is an edge, v1.1's stricter provisional rule keeps the selections that pay.

## 4. Proposal: policy v1.1

1. **Data quality** is a per-candidate requirement, separate from track record:
   - both team identities resolved by team-identity-v1 (`TEAM_IDENTITY_UNRESOLVED` otherwise);
   - data quality ≥ 60;
   - a non-fallback model;
   - the price, freshness, provenance, edge-sanity and one-per-match rules from v1.
2. **Demonstrated performance** is a per-competition requirement. Only VERIFIED_SUFFICIENT with reliability ≥ 60 counts (`evidence_status = demonstrated_track_record`, full 1 u advisory stake). Nothing is assigned a default reliability.
3. **Initial forward baseline without an invented record:**
   - a competition without demonstrated performance may be selected only if the owner lists it in `provisional_competitions`;
   - provisional selections need risk-adjusted EV ≥ 0.04 (v1: 0.02) and carry a 0.5 u advisory stake;
   - every decision is labelled `provisional_no_track_record`.

   These forward selections build the track record. Retrospective, fabricated and unverifiable rows are never used to qualify anything.
4. **Rejection codes:** `LEAGUE_UNKNOWN`, `LEAGUE_NO_VERIFIED_EVIDENCE`, `LEAGUE_EVIDENCE_INSUFFICIENT`, `LEAGUE_EVIDENCE_CONTAMINATED`, `LEAGUE_UNRELIABLE`.
5. **Version tag:** `baseline-policy-v1.1-proposal.<config hash>`. Any threshold or list change is a new version.

### Decisions for the owner

- Which competitions, if any, go on the provisional list, and on what basis. For example, competitions with the deepest odds coverage, rather than any assumed reliability.
- The provisional EV bar and stake.
- Whether BILQE Layer 3 adopts the same evidence classes. That would replace "unknown = 65" with "no demonstrated record = not a pass", and BILQE would then qualify nothing until approval or evidence. BILQE also still receives no odds (K36).
- Whether `min_settled` = 100 and reliability ≥ 60 define "sufficient".

# Provisional League-Evidence Policy — Proposal (M3.2, Decision 3)

Code (all inactive):
- `src/policy/league_evidence.py`: performance evidence classes (M3.1)
- `src/policy/competition_eligibility.py`: input and price eligibility (M3.2, commit `0dbc066`)
- `src/policy/baseline_v1_1.py`: validated vs research-provisional decisions

Tests: `tests/test_league_evidence_policy.py` (18). Data: `m3_2/league_evidence_comparison.json`.

## 1. Principle

Two different questions are never mixed:

| Question | Source | Can yield a reliability score? |
|---|---|---|
| **Has the model demonstrated predictive performance in this competition?** | Verified forward settled predictions only (`league_evidence`) | Yes, only for VERIFIED_SUFFICIENT (≥ 100 verified settled predictions) |
| **Are this competition's inputs and prices good enough for a forward experiment?** | Shadow observations (`competition_eligibility`) | **No**. Eligibility is never performance evidence. |

Missing, contaminated or unverifiable performance evidence gets no score. In particular it is never 65 (the BILQE default).

## 2. Competition eligibility criteria (research-provisional mode)

Measured per competition from shadow observations. All must hold.

| Criterion | Requirement (proposed) | What it checks |
|---|---|---|
| E0 Sample | ≥ 20 observed fixtures | Enough observations to measure E1–E4 |
| E1 Verified team identity | ≥ 90 % of observed fixtures with both teams resolved by team-identity-v1 (approved registry entries only) | No cross-team, contaminated or fallback statistics |
| E2 Point-in-time input quality | ≥ 60 % of identity-resolved fixtures with data quality ≥ 60 | Enough recorded history before the decision |
| E3 Genuine pre-match odds | ≥ 80 % of observed fixtures with a bookmaker quote ≤ 240 min old (provisional window) at decision time, fetched before the decision | Real, recent prices |
| E4 Supported markets | ≥ 80 % of quoted fixtures with 1X2, O/U 2.5 and BTTS each priced by ≥ 3 bookmakers | Market depth for the three supported markets |
| E5 Calibration provenance | Every observation records model and calibration versions. Validated (official) use additionally needs frozen calibration. | Reproducibility |

Status: `RESEARCH_ELIGIBLE`, `NOT_ELIGIBLE` (with the failed criteria), or `INSUFFICIENT_SAMPLE`.

**Today:** all 14 tracked competitions are `INSUFFICIENT_SAMPLE`. Six observations exist, at most one per competition.

## 3. Decision modes

| | Strict (validated) | Research-provisional |
|---|---|---|
| Competition requirement | VERIFIED_SUFFICIENT and verified reliability ≥ 60 | Owner-listed **and** RESEARCH_ELIGIBLE (no performance requirement) |
| Candidate requirements | All v1 rules: bookmaker price, provider age ≤ 240 (provisional), fetched ≤ 60 min before the decision, odds 1.30–6.00, data quality ≥ 60, non-fallback model, resolved identity, edge ≤ 0.15, frozen provenance | Same |
| Uncertainty / EV | risk-adjusted EV `(p − 0.03)·odds − 1 ≥ 0.02` | **≥ 0.04** (higher bar without a track record) |
| Label on every decision | `demonstrated_track_record` | `provisional_no_track_record` (research-only, never a validated selection) |
| Advisory stake (paper only) | 1.0 u flat | 0.5 u flat |
| Counts toward the 1,000-pick baseline | Only after the owner activates a frozen baseline | Never, unless the owner explicitly defines a provisional baseline |

No threshold was lowered compared with v1, and nothing is relaxed to increase volume.

## 4. Strict vs provisional — comparison

| Sample | Strict | Research-provisional |
|---|---|---|
| Today's real data (14 competitions) | 0 eligible (no verified evidence) | 0 eligible (insufficient sample) |
| Simulation, efficient market (2,000 matches): selections / expected ROI | 0 | 65 / −4.74 % (eligibility assumed for 6 competitions, labelled as such) |
| Simulation, model has an edge | 0 | 50 / **+15.28 %** |
| Simulation with today's real eligibility | 0 | 0 |

Reading:
- Strict mode can only start after the model has a verified record, which a forward experiment must first build.
- The research-provisional mode is that experiment, run under stricter EV rules and explicit labels.
- Neither creates an edge the model does not have. The efficient-market simulation loses in every mode.

## 5. Market and stake rules for owner review

1. Markets: 1X2, O/U 2.5, BTTS only. Corners, cards, player props, handicaps and others are excluded.
2. At most one selection per match (highest risk-adjusted EV).
3. Advisory flat stakes only: 1.0 u validated, 0.5 u provisional. No Kelly sizing in the baseline.
4. Proposed exposure limits (not yet in code; for review): at most 5 provisional selections per UTC day and at most 2 per competition per day.
5. **No real-money placement exists or is authorised.** Stakes are paper units for evaluation.

## 6. Decisions for the owner

1. Approve or adjust E0–E5 thresholds, the provisional EV bar (0.04) and the stakes.
2. The provisional competition list. Proposed process: none until a competition is RESEARCH_ELIGIBLE after the 14-day shadow; then the owner lists it explicitly.
3. Whether provisional selections may ever form a separate "provisional baseline" (recommended: no; keep them as research).
4. Whether BILQE Layer 3 adopts the same evidence classes. That replaces "unknown = 65" and means BILQE qualifies nothing until the evidence or the eligibility exists.

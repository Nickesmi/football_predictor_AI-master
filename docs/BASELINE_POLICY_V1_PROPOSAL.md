# Baseline Selection Policy v1 — Proposal (M3 Phase D)

Commit `d1b0afb`. Code: `src/policy/baseline_v1.py`. Comparison harness: `scripts/compare_selection_policies.py` (shared legacy evaluator in `src/shadow/legacy.py`). Tests: `tests/test_baseline_policy_v1.py` (25).

**Status: proposal. Not wired into `run_pipeline`, not frozen, not active.** The shadow cycle records its decisions next to the legacy ones.

## 1. Rules

Every candidate is checked against all rules, and every failure is reported:

| Code | Rule | Default |
|---|---|---|
| `PROVENANCE_NOT_FROZEN` | `model_version` and `calibration_version` present, and calibration frozen (baseline manifest active) | required |
| `MARKET_NOT_ALLOWED` | 1X2, O/U 2.5, BTTS only. Corners, cards, player props, handicaps and anything else are excluded. | — |
| `ODDS_NOT_BOOKMAKER` | Genuine bookmaker price (`odds_type = bookmaker`). Synthetic, crowd-vote and model-fair prices are refused. | — |
| `ODDS_AGE_UNKNOWN` / `ODDS_STALE` | Provider price age known and ≤ limit. A price stamped in the future is refused. | 240 min |
| `ODDS_FETCH_TIME_UNKNOWN` / `ODDS_NOT_RECONFIRMED` | This system fetched the price ≤ limit before the decision | 60 min |
| `ODDS_OUT_OF_RANGE` | Decimal odds in [min, max] (the existing "odds sanity" band) | 1.30–6.00 |
| `INPUT_QUALITY_LOW` / `MODEL_FALLBACK` | `data_quality` ≥ min, and the model source is not fallback/unknown | 60 |
| `LEAGUE_UNPROVEN` / `LEAGUE_UNRELIABLE` | The league is on the owner-approved list, **or** has ≥ N verified settled forward predictions (evidence classifier) with reliability ≥ R. Missing history never qualifies. | N = 100, R = 60, list empty |
| `EDGE_IMPLAUSIBLE` | Model edge over the price ≤ cap (larger edges are more likely model error) | 0.15 |
| `RISK_ADJUSTED_EV_TOO_LOW` | `(p − margin) × odds − 1 ≥ min EV` (uncertainty haircut on the model probability) | margin 0.03, min EV 0.02 |
| `CORRELATED_EXPOSURE` | At most one selection per match: the highest risk-adjusted EV is kept | — |

Additional properties:
- **Stake:** flat 1.0 unit, advisory only. No placement code exists or was added.
- **Versioning:** thresholds live in a frozen `PolicyConfig`. Its SHA-256 is part of the version tag, so a threshold change is a new version. Default tag: `baseline-policy-v1-proposal.2670eba922e2`. There is no runtime relaxation.
- **League records:** `load_league_records` counts only `prediction_errors` rows classified `VERIFIED_SETTLED` by `m3-evidence-v1`. Today no league has more than 9, so **no league qualifies by record**.

## 2. Comparison with the current behaviour

Legacy means the unchanged pipeline steps 4–5 plus grading:
1. positive edge;
2. `score_confidence` with `league_profiles`;
3. `apply_risk_filter`;
4. `assign_grade` ≠ Pass.

v1 is shown with frozen provenance assumed, so its other rules are visible. With today's real provenance (unfrozen) v1 selects nothing, by design.

### 2.1 Simulated (seed 20261016, 2,000 matches, 14,000 candidates; `policy_compare/compare.json`)

Each selection has a known true probability. Bookmaker prices carry a 5 % margin. Expected ROI is evaluated against the true probabilities.

| Scenario | Policy | Selections | Matches with a selection | Selections per match | Expected ROI (flat) | Selections with negative true EV |
|---|---|---|---|---|---|---|
| Efficient market (model = truth + N(0, 0.05); price = truth + margin) | legacy | 632 | 534 | 1: 444, 2: 83, 3: 6, 4: 1 | **−4.55 %** | 100 % |
| | v1, no approved leagues | 0 | 0 | — | — | — |
| | v1, illustrative approved list | 81 | 81 | 1 only | −4.72 % | 100 % |
| Model has an edge (model = truth + N(0, 0.02); price = truth + N(0, 0.04) + margin) | legacy | 365 | 330 | 1: 298, 2: 29, 3: 3 | +10.17 % | 2.5 % |
| | v1, no approved leagues | 0 | 0 | — | — | — |
| | v1, illustrative approved list | 65 | 65 | 1 only | **+13.31 %** | **0 %** |

The illustrative approved list is Premier League, LaLiga, Serie A, Bundesliga, Ligue 1 and Champions League. It is for comparison only, not a recommendation to approve.

Interpretation:
- **No selection rule creates an edge the model does not have.** When the market is sharper than the model, both policies lose. v1 loses on about 8× fewer selections.
- When the model has a real edge, v1's stricter selection is more accurate (+13.3 % vs +10.2 %, and no negative-EV selections), at about 1/6 of the volume.
- v1 removes correlated same-match exposure, which legacy takes in 10–17 % of the matches it selects.

v1 rejection funnel (efficient market, illustrative list; a candidate can fail several rules):

| Reason | Count |
|---|---|
| RISK_ADJUSTED_EV_TOO_LOW | 12,272 |
| ODDS_NOT_RECONFIRMED | 6,888 |
| INPUT_QUALITY_LOW | 6,524 |
| LEAGUE_UNPROVEN | 6,062 |
| ODDS_STALE | 5,320 |
| MODEL_FALLBACK | 1,372 |
| ODDS_OUT_OF_RANGE | 919 |
| CORRELATED_EXPOSURE | 27 |

Legacy rejections in the same scenario:

| Reason | Count |
|---|---|
| NO_POSITIVE_EDGE | 9,187 |
| EDGE_BELOW_LEAGUE_MINIMUM | 3,633 |
| PROBABILITY_BELOW_MINIMUM | 2,096 |
| DATA_QUALITY_BELOW_MINIMUM | 355 |
| CONFIDENCE_BELOW_FLOOR | 4 |

### 2.2 Forward real-odds sample (11 fixtures on 2026-10-17, real API-Football quotes, production probability engine)

This sample is not counted and is not a forward-prediction record. The fixtures had not been played when it was taken.

| | Legacy | v1 (no list) | v1 (illustrative list) |
|---|---|---|---|
| Candidates | 63 | 63 | 63 |
| Selections | **12 on 6 matches** (graded A+ ×10, A ×2) | 0 | 0 |
| Same-match selections | 3 matches with 2–3 picks each | — | — |
| Claimed edges | 11–38 % (e.g. under 2.5 at 70.6 % model vs 32 % implied, odds 3.10) | — | — |

v1 rejections (illustrative list):

| Reason | Count |
|---|---|
| INPUT_QUALITY_LOW | 56 |
| LEAGUE_UNPROVEN | 42 |
| RISK_ADJUSTED_EV_TOO_LOW | 37 |
| EDGE_IMPLAUSIBLE | 8 |
| ODDS_OUT_OF_RANGE | 1 |

Reading:
- 56 of 63 candidates had `data_quality` < 60. API-Football team and league names do not match the stored team-state names, for example "La Liga" vs "LaLiga". The engine fell back to league-average inputs, and its probabilities differ wildly from the market.
- Legacy graded those differences as A+ value.
- Legacy also takes unknown league ids (API-Football 39, 140, …) as "Unknown" with reliability 4.0. It still issued A+ grades because the claimed edges were huge.
- v1 refused all of them.

**This is a model-input problem (new known issue), not a policy problem.** Name canonicalisation between API-Football and the team-state tables must be fixed and verified before any baseline. Fixing it changes probability inputs, which needs approval.

### 2.3 Historical comparison

This was not possible. The database has no historical bookmaker quotes: `odds_snapshots` and `odds_quotes` were empty before M3. Retrospective `prediction_log` rows have no prices.

## 3. Decisions needed

1. Approve, change or reject each threshold in §1. Every change produces a new version tag.
2. The league qualification route: an owner-approved list (which leagues, on what evidence?) or a verified record only. With a verified record only, v1 selects nothing until each league has 100 verified settled forward predictions.
3. Whether BILQE keeps "unknown league = 65" (it now passes leagues with no evidence) or adopts v1's rule.
4. The flat 1u advisory stake vs the legacy Kelly-blended grades.
5. When to wire v1 into `run_pipeline` behind a version flag. That changes selection code and invalidates any frozen manifest, so it must happen **before** freezing `football-v1.0`.

# Identity-Corrected Probability Comparison — identity-v1-rh1 (M3.3 Package B)

Commits:
- `9f955b0`: rh1 inputs, candidate golden, comparison
- `5530f1a`: pipeline provider fix

Data: `m3_3/rh1_comparison.json`. Code: `src/identity/inputs.resolve_probability_inputs_rh1`, `scripts/compare_rh1_inputs.py`.

**Switch:** `PROBABILITY_INPUTS` accepts `legacy` (default; production), `identity-v1` and `identity-v1-rh1`. Production configuration is unchanged: the switch is off, the app is stopped and the hold is active.

**Unchanged:**
- Poisson mathematics, rho, blending (synthetic XGBoost weight 0);
- model weights and calibration coefficients;
- the profile key and venue transform;
- risk control, grading and BILQE.

The Poisson-parity test shows identical outputs for identical inputs.

## 1. Golden references

| Reference | Status |
|---|---|
| `golden_probabilities.v3-m2.5.py311/py313` (production legacy) | unchanged, SHA-256 pinned |
| `history/v1-pre-m2`, `history/v2-m2` | unchanged, SHA-256 pinned |
| `golden_identity.identity-v1-m3.2.*` | unchanged; reproduced with the M3.2 registry state (`load_as_proposed`) |
| **`golden_identity.identity-v1-rh1-m3.3.py311/py313`** (new candidate; identical across runtimes) | legacy vs rh1 at a fixed decision time on seeded fixtures with known traps |

Candidate golden, intended changes (decision 2026-10-01 12:00 UTC):

| Fixture | Legacy | rh1 | 1X2 / O/U 2.5 / BTTS max change | Data quality |
|---|---|---|---|---|
| Everton v Chelsea | team_state (4 matches each) | 3 / 4 fixtures before T; the 2026-10-04 result is excluded | 9.2 / 1.9 / 0.0 pp | 58 → 54 |
| Arsenal v Liverpool | contaminated record (8 matches, mostly another club) | 1 English fixture | 31.2 / 5.0 / 3.7 pp | 58 → 52 |
| Inter v Bologna | **Internacional (Brazil)** via LIKE | 2 Serie A fixtures | 2.9 / 8.3 / 7.7 pp | 58 → 54 |
| Middlesbrough v Cardiff | hash statistics | 2 Championship fixtures | 12.2 / 13.2 / 9.2 pp | 50 → 54 |
| Gent v Club Brugge | static substring "argentina" | 1 Pro League fixture | 35.1 / 4.6 / 19.8 pp | 50 → 52 |
| Rangers v Hibernian | static substring "angers" | 1 Premiership fixture | 15.7 / 27.7 / 19.8 pp | 50 → 52 |
| Chelsea U21 v Everton U21 | LIKE → senior teams | **NO_FORECAST** (no history) | — | — |
| Newcomer FC v Chelsea | hash statistics | **NO_FORECAST** (no observed fixture) | — | — |

## 2. Real fixtures — 51 upcoming tracked fixtures (2026-10-16/17), decision at kickoff − 120 min

| | identity-v1 (team_state + approved registry) | **identity-v1-rh1** |
|---|---|---|
| Forecasts | 44 / 51 | **51 / 51** |
| No forecast | 7 (6 no history, 1 unverified) | 0 |
| **1X2:** fixtures changed / mean / max change | 20 / 5.07 / 37.4 pp | 47 / 6.08 / 37.6 pp |
| **O/U 2.5:** fixtures changed / mean / max | 20 / 7.74 / 39.8 pp | 47 / 9.82 / 37.4 pp |
| **BTTS:** fixtures changed / mean / max | 20 / 6.05 / 30.7 pp | 47 / 7.62 / 28.8 pp |
| **Data quality**, mean (legacy → variant, same fixtures) | 56.2 → 62.2 | 55.7 → 61.0 |
| **Forecast eligibility** (forecast and data quality ≥ 60) | 11 | 11 (legacy: 4 of 51) |

**Why rh1 changes more fixtures than identity-v1:**
- rh1 also replaces correctly identified `team_state` rows. Those rows hold later or other-competition results, while the rebuild uses only the competition's fixtures before the decision.
- Every change is an input change (point-in-time, competition-pure history). None is a change of model.

**Why data quality stays near 60:** most teams have 1–7 stored current-season fixtures. The data-quality formula penalises fewer than 15 matches. This is a real lack of history, not a defect.

## 3. Known and unresolved teams (tests)

- **Known:** `test_known_and_unresolved_teams` checks Everton (3 matches; the later result is excluded), Arsenal (1, not the other club), Inter (2, not Internacional), Gent, and Rangers.
- **Unresolved, fail closed:**
  - Chelsea U21 and Newcomer FC (golden);
  - `HISTORY_NOT_BUILT` when the rebuild never ran;
  - the pipeline's rh1 mode records `NO_FORECAST / TEAM_IDENTITY_UNRESOLVED`, never a pick (`test_pipeline_rh1_mode_gives_unresolved_teams_no_forecast`).

**Bug found and fixed (`5530f1a`):** pipeline fixtures from `collect_fixtures` carry no provider. Both identity modes would therefore have marked every official fixture `UNKNOWN_COMPETITION`. Commit `9f955b0` was made while that test was failing, and `5530f1a` is the fix.

## 4. Approval

This is a data-only correction: same mathematics, verified point-in-time inputs. No new model or calibration strategy is needed, so I did not stop for approval. Activating `PROBABILITY_INPUTS=identity-v1-rh1` remains an owner decision. It also requires a rebuild schedule (rh1 must be rebuilt before each pipeline run; the shadow runner already does this).

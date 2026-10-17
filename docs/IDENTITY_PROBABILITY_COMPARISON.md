# Identity-Corrected Probability Comparison (M3.2, Decision 2)

Commits:
- `4753ae4`: identity-v1 golden, inactive pipeline switch, exact-record reads
- `28bb9d3`: market-by-market comparison

Data: `~/Desktop/football_predictor_backups/m3_2026-10-16/m3_2/team_identity_comparison.json`.

**Status:**
- The identity-checked pathway is implemented and tested in isolation.
- `run_pipeline` uses it only with `PROBABILITY_INPUTS=identity-v1`. The default is `legacy`, unchanged, and the production app is not running.
- The probability mathematics and model coefficients are unchanged.

## 1. What changes and what does not

| Unchanged | Changed (inputs only) |
|---|---|
| `estimate_probabilities_from_inputs` (Poisson, rho, blending; synthetic XGBoost weight 0) | Which historical record feeds a team: the competition-verified `team_state` record, or nothing |
| Poisson profile key (`LEAGUE_KEY_MAP`), venue transform, neutral-venue decision | No static season tables, substring matches, hash statistics or `get_team_state` LIKE fallback |
| Calibration, risk control, grading, BILQE | Unresolved or ambiguous teams: **no forecast** and `NO_FORECAST / TEAM_IDENTITY_UNRESOLVED` (never a selection) |
| Legacy golden references (`v3-m2.5`, `history/*`), pinned by SHA-256 | Provenance: `inputs_json.probability_inputs_version` plus per side `identity`: provider, provider team id, canonical team id (`tsr-…`), competition ids, record, label, source (`normalised_name_in_competition`, `explicit:…`, `registry:<id>`), evidence, identity version |

## 2. Legacy defects quantified (492 teams in tracked-competition fixtures)

| Legacy source of a team's statistics | Teams |
|---|---|
| Hash-generated synthetic statistics | 314 |
| `team_state` under the profile key | 109 |
| Static league table, exact name (claims 20 matches) | 31 |
| **`get_team_state` LIKE substring fallback (another record)** | 18 |
| Static table **substring** (another team) | 20 |

New in M3.2: `src/db/team_state.get_team_state` falls back to an **unordered SQL LIKE substring match** in the same label. Examples:
- "Inter" → Internacional (Brazil);
- "Chelsea U21" → Chelsea;
- "Werder Bremen" → SV Werder Bremen (correct here only by luck).

The identity pathway reads the resolved record by exact key and never calls it (regression test `test_identity_inputs_never_use_the_like_substring_fallback`).

## 3. Golden references

| Reference | Purpose | Status |
|---|---|---|
| `golden_probabilities.v3-m2.5.py311/py313.json` | Production (legacy) outputs | **unchanged**, SHA-256 pinned in `test_golden_identity_inputs.py` |
| `history/v1-pre-m2`, `history/v2-m2` | Superseded references | unchanged, SHA-256 pinned |
| **`golden_identity.identity-v1-m3.2.py311/py313.json`** (new; identical on 3.11 and 3.13) | Legacy vs identity outputs on the M3 regression fixtures: Everton–Chelsea (API-Football and SofaScore ids), Middlesbrough–Cardiff, Espanyol–Getafe, Werder–Union, Arsenal–Liverpool (contaminated), Rangers–Hibernian (substring), Atletico–Sevilla (ambiguous, with and without the proposed registry), Lazio–Porto (UEL neutral trap), Chelsea U21–Everton U21 | new canonical reference for identity-v1 |

Each intended change recorded in the identity golden:

| Fixture | Legacy source | Identity-v1 | Max change |
|---|---|---|---|
| Everton v Chelsea (both id spaces) | same records | same | 0.0 pp |
| Werder Bremen v Union Berlin | LIKE fallback that happened to hit the right records | same records | 0.0 pp |
| Middlesbrough v Cardiff | hash / hash | Championship records; dq 50 → 80 | 22.2 pp |
| Espanyol v Getafe | static tables ("20 matches") | La Liga records; dq 100 → 58 | 10.2 pp |
| Lazio v Porto (UEL) | static / hash | UEL records, legacy venue treatment kept; dq 35 → 41 | 7.5 pp |
| Arsenal v Liverpool | contaminated record | NO_FORECAST (CONTAMINATED_HISTORY) | — |
| Rangers v Hibernian | substring "angers" / hash | NO_FORECAST (no history) | — |
| Atletico Madrid v Sevilla | static tables | NO_FORECAST (AMBIGUOUS); resolved only with the proposed registry | — |
| Chelsea U21 v Everton U21 | LIKE fallback to the **senior** teams | NO_FORECAST | — |

`test_every_probability_change_is_attributed` enforces that any changed fixture had a non-identical legacy source.

## 4. Market by market — 51 real upcoming fixtures (2026-10-16/17)

| | Identity-v1 | + proposed registry (isolated only) |
|---|---|---|
| Fixtures with a forecast | 39 / 51 | 44 / 51 |
| Fixtures changed | 17 | 20 |
| 1X2: mean / max absolute change | 4.83 / 37.4 pp | 5.07 / 37.4 pp |
| O/U 2.5: mean / max | 6.59 / 37.4 pp | 7.74 / 39.8 pp |
| BTTS: mean / max | 5.05 / 28.8 pp | 6.05 / 30.7 pp |
| Mean data quality (same fixtures, before → after) | 54.7 → 62.4 | 56.2 → 62.2 |
| Fixtures with data quality ≥ 60 (before → after) | 2 → 10 | 4 → 11 |

Unchanged fixtures (22 of 39) are bit-identical to legacy. Changed fixtures were mostly hash statistics in legacy (26 of 34 changed sides). The largest moves come from Championship teams that legacy looked up under "Premier League".

**Data quality stays low after the fix** (early season: 3–8 recorded matches per team). Most correctly identified fixtures remain below the v1/v1.1 input-quality threshold of 60.

## 5. Activation (not done)

1. Owner approves this comparison and the identity golden.
2. Set `PROBABILITY_INPUTS=identity-v1` for the official pipeline (and decide separately for match analysis/BILQE, which still use the legacy lookup).
3. Freeze any baseline only after the switch, because the inputs version is part of each pick's provenance.

**Rollback:** unset `PROBABILITY_INPUTS`. Picks keep their recorded inputs version.

# BILQE Diagnostic Report — why every pick was Tier C

M2.5, 2026-10-12/13. Branch `m2.5/operational-readiness`. Evidence was gathered read-only from `data/engine.db` and by re-running BILQE on an isolated copy of the database (`scripts/diagnose_bilqe.py`). No thresholds were changed.

## 1. Summary

| Question | Answer |
|---|---|
| Observed | 4,195 `qualification_log` rows across 53 matches, **all Tier C**. 4,187 rejected by "League reliability 50 < 60" and 8 by "model disagreement" (the synthetic XGBoost, removed in M2). |
| Root cause | **Code defect.** Layer 3 read league reliability with `getattr(adj, "reliability", 50.0)`, but `get_league_adjustment` returns a dict, so the value was always 50. The hard gate is `< 60`, so every pick failed Layer 3. Fixed in `97ef889` with regression tests. |
| Second defect | Layer 4 returned a **neutral 50 (pass) when there were no odds**, so without real prices a pick could reach Tier B on probability alone. Fixed in `97ef889`: no real odds now means `passed=False` with "NO ACTIONABLE BET: no real bookmaker odds". |
| After the fixes | Without real odds, nothing qualifies, and that is now correct: 0 of 4,134 candidates. With real odds that carry a genuine edge, picks reach **Tier B, never A or S**. The soft layers have no history and sit at a neutral 50, which caps the score at about 69. |
| Data defect (major) | 10,808 of 11,209 `prediction_errors` rows are fabricated 33/33/33 "home" records (2026-06-04 to 2026-08-31), created by a results-verification bug (fixed in `927e402`). League reliability, live league adjustments and the nightly refit are all computed from this table. |
| Classification | 1 code defect (fixed), 1 rule defect (no-odds pass, fixed), missing data (no real odds were ever collected), a methodological issue in the reliability formula (needs a policy decision), and legitimate rejections (low data quality, genuinely weak leagues). |

## 2. Layers, inputs and thresholds

Score weights: data quality 0.15, consensus 0.15, calibration 0.15, market edge 0.30, and 0.05 each for volatility, ROI, CLV, liquidity and similar-match. xG reality is reported but not weighted. Tiers: S/A need a score of at least 80 (S at least 90 plus extra conditions) and data quality of at least 70. B needs a score of at least 70.

| Layer | Input (live source) | Hard gate | Observed values | Missing-data handling |
|---|---|---|---|---|
| L1 Data quality | `data_quality_score` from analysis | < 50 rejects the match | 37 and 16 for 2 matches (145 candidates); 59–100 otherwise | none needed |
| L2 Model consensus | `xgboost_predictions` vs Poisson | spread > 15 pp | 0 rejections after M2 (synthetic XGB has no weight) | passes when only one model is present (score 50 neutral) |
| L3 Calibration | `get_league_adjustment(league)` → `reliability`, `cal_gap` | reliability < 60 | **before fix: always 50**; after: active leagues 28.7–38.2 | unknown or inactive league → default 65 (passes) |
| L4 Market edge | `bookmaker_odds` (implied vs model) | edge < 4 pp | live call passes no odds at all | **before fix: score 50, pass**; after: reject (NO ACTIONABLE BET) |
| L5 Volatility | `team_learning_state` | > 85 | table empty → 0 for every team | 0 = maximally stable (silently optimistic) |
| L6 xG reality | analysis xG vs averages | never rejects | — | neutral |
| L7 CLV | historical CLV | never rejects | no CLV history | neutral 50 |
| L8 Historical ROI | league/market ROI, ≥ 500 samples | never rejects | no samples | neutral 50 |
| L9 Liquidity | bookmaker count, sharp book, stability | < 2 books and no sharp; unstable and no sharp | — | **no odds → passes, neutral 50** |
| L10 Similar match | similar-match hit rate | never rejects | no history | neutral 50 |

## 3. Rejection funnel (52 distinct matches, 4,134 candidate picks)

Re-run on an isolated copy with the fixed code. Synthetic prices exist only in memory and are never stored.

| Stage | No odds (reality) | Synthetic, zero edge | Synthetic, 6 pp edge, 2 books incl. sharp |
|---|---|---|---|
| Candidates | 4,134 | 4,134 | 4,134 |
| After L1 data quality (match level) | 3,989 (−145) | 3,989 | 3,989 |
| After L5 volatility (match level) | 3,989 (0) | 3,989 | 3,989 |
| After L3 calibration | 3,021 (−968) | 3,021 | 3,021 |
| After L2 consensus | 3,021 (0) | 3,021 | 3,021 |
| After L4 market edge | **0** (no real odds) | **0** (edge ≤ 0) | 3,021 |
| After L9 liquidity | — | — | 3,021 |
| Tier S / A / B / C | 0 / 0 / 0 / 3,989 | 0 / 0 / 0 / 3,989 | **0 / 0 / 2,207 / 1,782** |

In the 6 pp scenario, 813 picks scored 68–69, just below the B threshold of 70. No pick can reach 80 while CLV, ROI, liquidity and similar-match all sit at a neutral 50.

L3 rejections by league (active adjustments only): Premier League 38.2 (254 samples), USL Championship 30.2, Segunda División 29.7, Primera División 28.9, Primera Nacional 28.7. All other leagues are inactive (fewer samples or zero weight) and fall back to the default 65.

Candidate market mix: corners 1,260; cards 1,050; half-time 751; handicap 398; goals 276; team goals 242; result 123; BTTS 32; combo 2. **55 % are corners or cards.** BILQE has no market restriction.

## 4. Impact assessment

| Factor | Effect on tiers | Classification |
|---|---|---|
| Real odds | Decisive: L4 carries 0.30 of the weight and now gates. No real odds were ever collected (`odds_snapshots` empty) and the live call passes none. | Missing data + wiring gap |
| League reliability | Before the fix it rejected 100 %. After: 968 candidates (23 %) in 5 active leagues. | Code defect (fixed) + policy question (§5) |
| Unknown-team data quality | 145 candidates (2 matches with DQ 37 / 16) | Legitimate rejection |
| Consensus | 8 historic rejections, all from synthetic XGB | Resolved in M2 |
| Calibration gap | Feeds L3 score only | — |
| EV / edge | Without real odds, positive probability is not value | Legitimate rejection (rule now enforced) |
| Risk (volatility) | Never rejects: `team_learning_state` is empty, so volatility is 0 | Missing data, silently optimistic |
| ROI / CLV / similar-match | Neutral 50 caps the score at about 69 | Missing data; A/S unreachable until history exists |
| Market restrictions | None in BILQE: corners and cards dominate | Policy question |

## 5. Methodological issue: reliability formula vs default

The reliability score is volume 25 + accuracy 35 + calibration 20 + Brier 20. On the current data it peaks around 50 even with 1,552 samples, while a league with **no** evidence defaults to 65 and passes the 60 gate. L3 therefore favours leagues with no track record over leagues with a measured one. This is a policy matter (scale, default or gate). It is **not** changed here and needs an owner decision.

## 6. Fabricated outcome records

- `results_service.get_results_verification_service` read `analysis["prediction"]["result"]`, a key that does not exist, and stored 33/33/33 with predicted outcome "home" for every verified match.
- 10,808 of 11,209 `prediction_errors` rows have this shape. Only 401 rows are real (155 settled, at most 10 per league), so no league would be active on real data.
- Fixed in `927e402` (`prediction_from_analysis`, which never defaults). The same pattern was fixed in `debug_service` settle-date and in `match_analysis_service`.
- **The live rows were not modified.** Cleaning or quarantining them changes league adjustments that live 1X2 probabilities use (7 weighted leagues), so it needs an owner decision. Until the app is restarted on frozen M2.5 code, the running app's 03:00 nightly refit keeps rebuilding `model_confidence_adjustments` and `calibration_models` from these rows. The last run was 2026-10-12 00:13 UTC (1,013 calibration rows).

## 7. Decisions needed (no threshold was changed)

1. Clean or quarantine the 10,808 fabricated `prediction_errors` rows (recommended: move to a quarantine table and keep a backup), then recompute league adjustments in shadow only.
2. Reliability formula vs the 65 default for unknown leagues (§5).
3. Whether BILQE should restrict markets (corners and cards are 55 % of candidates), and whether BILQE should be wired to real odds at all (the live call passes `bookmaker_odds=None`; official picks do not use BILQE).
4. Whether Tier A/S may be awarded while CLV/ROI/similar-match have no history (currently impossible; arguably correct).
5. Volatility from an empty `team_learning_state` should probably count as missing data, not as 0.

## 8. Reproduce

```bash
sqlite3 "file:data/engine.db?mode=ro" ".backup /tmp/bilqe_copy.db"     # read-only copy
python scripts/diagnose_bilqe.py --db /tmp/bilqe_copy.db --out /tmp/bilqe.json   # refuses data/engine.db
```

Regression tests: `tests/test_bilqe_qualification.py` (6) and `tests/test_prediction_records.py` (3). All of them fail on the pre-fix code.

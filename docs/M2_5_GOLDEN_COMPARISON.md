# M2.5 — Golden Comparison v2-m2 → v3-m2.5 (BILQE fixes)

Before: `tests/golden/history/v2-m2/golden_probabilities.py311.json` · After: `tests/golden/golden_probabilities.v3-m2.5.py311.json`

| Cause | Description | Changed values |
|---|---|---|
| E4 | BILQE fixes (qualification block only) | 22 |
| **UNINTENDED** | not explained by E4 | **0** |

| Fixture | Calibration gate (before → after) | Rejected picks | Rejection reasons after |
|---|---|---|---|
| Arsenal v Chelsea [Premier League] | 50.0 reject → 65.0 pass | 78 → 78 | NO ACTIONABLE BET: no real bookmaker odds (78) |
| Brazil v Argentina [World Cup] | 50.0 reject → 65.0 pass | 85 → 85 | NO ACTIONABLE BET: no real bookmaker odds (85) |
| Friendly Town v Friendly City [Club Friendly Games] | None reject → None reject | 0 → 0 |  |
| Inter v Bologna [Serie A] | 50.0 reject → 65.0 pass | 77 → 77 | NO ACTIONABLE BET: no real bookmaker odds (77) |
| Leeds United v Arsenal [Premier League] | 50.0 reject → 65.0 pass | 73 → 73 | NO ACTIONABLE BET: no real bookmaker odds (73) |
| Liverpool v Manchester City [Premier League] | 50.0 reject → 65.0 pass | 79 → 79 | NO ACTIONABLE BET: no real bookmaker odds (79) |
| Zzyzx United v Qwerty Athletic [Allsvenskan] | None reject → None reject | 0 → 0 |  |

## Unintended differences

None.

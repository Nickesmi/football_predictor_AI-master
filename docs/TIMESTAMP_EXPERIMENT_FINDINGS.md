# API-Football Timestamp Experiment — Findings (M3.3 Package D)

Tooling: `scripts/odds_timestamp_experiment.py` (commit `cf8c041`). Raw responses, analysis and quota log:
- `~/Desktop/football_predictor_backups/m3_2026-10-16/m3_3/ts_experiment/` (`raw/fetch_001–010.json`, `analysis.json`, `prerequisites.json`, `revision_replay.txt`, `research.db`)

All responses went to that isolated research location. No production database was touched.

## 1. Prerequisites (checked before any odds request)

| Check | Result |
|---|---|
| API key configured | yes (not printed) |
| Plan | `GET /status` → `subscription.plan = "Free"`, `active = true`, `requests.current = 1` of `limit_day = 100` (the earlier settlement request) |
| Paid subscription needed | no |
| Endpoints available on the plan | `/odds?fixture=` (current-season odds verified on this plan since M3) and `/status` |
| Rate limit | 7 s spacing between requests (the free plan returns HTTP 429 at about 10/min) |
| Isolated output | yes |

## 2. Requests

| | Count |
|---|---|
| Approved maximum | 13 |
| **Sent** | **11**: 1 `/status` + 10 `/odds?fixture=` (all HTTP 200) |
| Refused by the budget guard | 2 (round 3, Roma v Genoa and Valencia v Athletic). The research DB is a copy of the shadow DB, so its daily counter also held today's settlement request and reached its cap of 12 early. **Not re-run**: no additional rounds. |
| Provider-reported remaining after the last request | **89** of 100 |

## 3. Design actually run (timing deviation)

The approved design fetched at kickoff −6 h, −2 h 30 and −30 min. When the experiment started (17:05 UTC), the only known upcoming fixtures kicked off at 18:45–19:00, and using a discovery request for later fixtures would have cost budget. Three rounds were run inside the last ~100 minutes before kickoff, which is the window where decisions are made:

| Round | Fetch time (UTC) | Fixtures |
|---|---|---|
| 1 | 17:06–17:07 | Lille v Brest, Troyes v Lens (Ligue 1, 18:45); Roma v Genoa (Serie A, 18:45); Valencia v Athletic (La Liga, 19:00) |
| 2 | 17:55 | same four |
| 3 | 18:30–18:31 | Lille v Brest, Troyes v Lens (the other two refused, §2) |

## 4. Observations

| Fixture | Provider `update` at 17:07 / 17:55 / 18:30 | Prices (hash of all bookmakers × bets × values) | Pair classification |
|---|---|---|---|
| Lille v Brest | 13:36:15 / 13:36:15 / **18:00:33** | same / **changed** (1,329 of 3,234 values; 28 in 1X2, O/U 2.5 and BTTS, e.g. Bet365 home 2.00 → 2.05, 1xBet home 1.99 → 2.08) | NO_REFRESH, then **CHANGE** |
| Troyes v Lens | 13:36:15 / 13:36:15 / 13:36:15 | same / same | NO_REFRESH, NO_REFRESH |
| Roma v Genoa | 13:27:15 / 13:27:15 / — | same | NO_REFRESH |
| Valencia v Athletic | 14:27:18 / 14:27:18 / — | same | NO_REFRESH |

- **Pairs:** CHANGE 1, NO_REFRESH 5, CHECK 0, INCONSISTENT 0.
- **Batching:** the two Ligue 1 fixtures shared one stamp in rounds 1–2. In round 3 only one of them moved, so updates are not strictly league batches.
- **No price change under an unchanged stamp** was seen (no INCONSISTENT pair).

**Stamp age at fetch** (10 responses): ≤ 60 min **1/10**, ≤ 120 min **1/10**, ≤ 240 min **6/10**. Values: 160–295 min, plus 30 min after the 18:00:33 update.

**Revision replay:** the Lille v Brest responses were fed through the corrected store (Package C).

| Fetch | New rows | Stamp / revision |
|---|---|---|
| 17:07 | 308 | 13:36:15, revision 0 |
| 17:55 (identical delivery) | 0 | — |
| 18:30 | 308 | new stamp 18:00:33, revision 0 |

No price was lost. No same-stamp revision was needed, because the stamp moved together with the prices.

## 5. Verdict

**Ambiguous. No definitive claim.**
- The one stamp movement coincided with a real price change. That is consistent with "last price change" and also with "last check".
- No CHECK pair (stamp moved, prices unchanged) and no INCONSISTENT pair (prices changed, stamp unchanged) was observed. Either would have settled the question.

**What the observations do show:**
1. For 3 of 4 fixtures the provider record did not change at all during the last 100 minutes before kickoff, while its stamp was 2.7–4.9 h old.
2. When the record changed, the stamp moved with it and many prices changed at once.
3. The stamp therefore never misdescribed the prices in this sample.

**Consequence for the freshness rules (60 / 120 / 240 min).** With 1/10 within 120 min, a 60- or 120-minute provider-age rule would reject almost every price, and 240 accepts 6/10. Whether an old stamp means "current but unchanged" or "not re-checked" stays open. The 240-minute limit therefore stays **provisional for shadow research only**, and it was not widened.

Settling the question needs fetches spaced over hours on fixtures with long pre-match windows, which is the originally approved T−6 h / T−2 h 30 / T−30 min design. That would be a new approval.

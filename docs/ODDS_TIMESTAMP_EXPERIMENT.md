# Minimal Odds-Timestamp Experiment — Design for Approval (M3.2, Decision 4)

Tooling (commit `cf8c041`):
- `src/odds/timestamp_experiment.py`
- `scripts/odds_timestamp_experiment.py` (`plan` and `analyse` are offline; `fetch` sends one request per call, only within an explicit approved count)
- tests: `tests/test_odds_timestamp_experiment.py`

**Nothing was run.** The 33-request experiment was not performed.

## 1. The five timestamps

| Timestamp | Where it comes from | Stored as |
|---|---|---|
| Provider "update" (one per fixture) | `/odds?fixture=` → `response[0].update` | `odds_quotes.observed_at`; raw in the experiment files |
| Our collection time | our clock when the response arrives | `fetched_at`; experiment file `fetched_at` |
| Decision time | our clock at evaluation | `decided_at` |
| Kickoff | `fixture.date` | `commence_time` |
| Closing observation | the last fetch before kickoff | experiment: the T−30 min fetch |

The open question is whether the provider stamp means the **last price change** or the **last check**. Neither of those is our collection time.

## 2. Method

The same fixture is fetched several times before kickoff. For each consecutive pair of fetches, the provider stamp and a hash of every bookmaker × bet × value × price are compared:

| Stamp | Prices | Classification | Meaning |
|---|---|---|---|
| moved | changed | CHANGE | consistent with either semantics |
| moved | identical | **CHECK** | the stamp is a check time: an old stamp means a stale check |
| same | identical | NO_REFRESH | nothing new between the fetches |
| same | changed | **INCONSISTENT** | the stamp does not describe the prices (caching); freshness cannot be measured with it |

Verdict:
- any INCONSISTENT → `STAMP_UNRELIABLE`;
- any CHECK → `STAMP_IS_LAST_CHECK`;
- CHANGE only → `STAMP_IS_LAST_CHANGE_OR_CHECK`;
- only NO_REFRESH → `UNDETERMINED`.

Raw responses are kept for every fetch, because the normal store's unique key has no price. A changed price under an unchanged stamp would be dropped there (K53, characterised by a test).

## 3. Minimum request count

| Design | Fixtures | Fetches per fixture | Discovery | Requests | Share of free daily quota |
|---|---|---|---|---|---|
| **Recommended minimum** | 4 (2 leagues × 2, so league-batch updates are visible) | 3: kickoff −6 h, −2 h 30, −30 min | 1 (`/fixtures?date=`), only if the fixtures are not already known | **13** | 13 % |
| Absolute floor | 2 (1 league) | 3 | 1 | 7 | 7 % |
| M3.1 proposal (superseded) | 8 | 4 | 1 | 33 | 33 % |

Timing: one match day with Saturday kickoffs (about 12:30–19:00 UTC), on a host that stays awake. The fetches fall at about 06:30–18:30 UTC. Each fetch is one invocation:

```bash
python scripts/odds_timestamp_experiment.py plan --db <isolated shadow db>
python scripts/odds_timestamp_experiment.py fetch --db <isolated shadow db> --fixture <id> \
    --approved-requests 13 --out-dir <dir>
```

The tool refuses once the approved count is reached. Then run the offline analysis:

```bash
python scripts/odds_timestamp_experiment.py analyse --out-dir <dir>
```

## 4. How the result decides freshness

| Verdict | Consequence for the 60 / 120 / 240-min rules |
|---|---|
| STAMP_IS_LAST_CHECK | The provider age is a real staleness measure. The current data (0 % ≤ 60, 5.6 % ≤ 120, 61 % ≤ 240) then show that free API-Football prices are too stale for any window ≤ 120 min, and marginal at 240. |
| STAMP_IS_LAST_CHANGE_OR_CHECK, with CHECK never seen, and prices unchanged across long gaps | An old stamp can still be a current price. Freshness must then be judged by our re-fetch time (the "fetched ≤ 60 min before decision" clock), not by the provider stamp. This could justify a different rule, but **only on that evidence, never to raise coverage**. |
| STAMP_UNRELIABLE | Provider timestamps cannot support a freshness policy. A different provider is needed (paid; needs approval). |
| UNDETERMINED | Repeat once on another day (another 13 requests, separate approval). |

## 5. Current measurements (no new requests)

| Window | Coverage at decision (18 fixtures, 2026-10-16) |
|---|---|
| 60 min | 0 % |
| 120 min | 5.6 % |
| 240 min (provisional, shadow only) | 61.1 % |

The 240-minute limit stays provisional for shadow research. It is not widened.

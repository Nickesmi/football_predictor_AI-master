# Odds Freshness and Quota Analysis — API-Football (M3.1)

Research provider: API-Football on the existing free key. No paid usage. **No external request was made in M3.1** apart from the already-running six-cycle shadow test that M3 started. That test made 7 odds requests between 14:47 and 18:28 UTC.

## 1. Timestamp semantics

| Timestamp | Source | Meaning | Stored as |
|---|---|---|---|
| Provider update time | `response[].update` of `/odds?fixture=` | The provider's last update of that fixture's odds record. **One value per fixture, not per bookmaker or price.** Fixtures of one league share the same value (e.g. three Championship fixtures at 09:17:17, three Bundesliga fixtures at 09:32:17), so updates run in league batches. It may mark the last change rather than the last check. That cannot be separated without repeat fetches (§4, item B). | `odds_quotes.observed_at` |
| Collection time | Our clock when the response arrived | When the price entered our records | `odds_quotes.fetched_at` |
| Decision time | Our clock when a forecast and selection were evaluated | Only quotes with `fetched_at <= decision time` and `observed_at <= decision time` are used (`actionable_quotes`) | `selection_decisions.decided_at`, `shadow_observations.decided_at` |
| Kickoff | `fixture.date` (UTC) | Quotes with `observed_at >= kickoff` are dropped as in-play or late | `odds_quotes.commence_time` |
| Closing observation | Latest pre-kickoff quote from the same bookmaker, fetched before kickoff | Needed for CLV | derived (`shadow/cycle._closing`) |

Measured lags (18 fixtures):

| Measure | Values |
|---|---|
| Our fetch − provider update | 69.7 – 320.7 min (shadow), 192.8 – 282.3 min (probe) |
| Kickoff − last provider update (same-day fixtures) | 86.7, 261.8, 287.7, 298.7, 299.6, 315.7, 337.7 min |

For six of the seven same-day fixtures, the provider's last update was **4.4–5.6 hours before kickoff**, and nothing newer existed when we fetched 2–135 minutes before kickoff.

## 2. Real coverage at decision time

| Sample | n | ≤ 60 min | ≤ 120 min | ≤ 240 min | No quote |
|---|---|---|---|---|---|
| Shadow decisions (genuine forward, 2026-10-16) | 6 | 0 (0 %) | 1 (16.7 %) | 2 (33.3 %) | 0 |
| Probe (measurement 22–27 h before kickoff, 2026-10-16) | 12 | 0 | 0 | 9 (75.0 %) | 1 |
| Combined | 18 | **0 %** | **5.6 %** | **61.1 %** | 1 |

Ages at the six shadow decisions: 197.2, 259.8, 320.7, 69.7, 283.7 and 266.6 min. At the provisional 240-minute window, 4 of the 6 fixtures had no usable price, and the pipeline correctly produced NO_REAL_ODDS.

**Verified:** every price used by a decision existed in our records before that decision. `fetched_at <= decided_at` and `observed_at <= decided_at` hold for all candidates in all six observations. The offline replay asserts the same.

The 240-minute window was not widened. The sample is small, but the direction is clear: API-Football's free pre-match odds are not refreshed close to kickoff often enough for any window ≤ 240 min to reach the 95 % coverage criterion. A wider window would only hide that.

## 3. Freshness policy recommendation

- Keep **240 min provisional, for shadow only**. Do not raise it to reach a coverage target.
- Keep the two-clock rule of v1/v1.1: provider age ≤ window **and** our fetch ≤ 60 min before the decision.
- Collect the 14-day distribution before setting the official window. If the 95th percentile of provider age at decision time exceeds 240 min (expected from these data), the free API-Football feed **cannot support** an official baseline with fresh prices. The decision is then:
  - (a) a faster provider (paid; needs approval);
  - (b) explicitly labelled stale-price research only.

## 4. Request-count and quota plan (for approval; nothing scheduled)

Free plan: 100 requests per UTC day, shared with the production fixture refresh (currently stopped by the service hold), and about 10 requests per minute.

| Item | Purpose | Requests | When |
|---|---|---|---|
| A. Settle the six observations | `/fixtures?date=2026-10-16` (final scores; results of the 17:00–19:00 UTC kickoffs) | **1** | Once |
| B. Timestamp-semantics experiment | 8 fixtures on one day, each fetched at kickoff −6 h, −3 h, −1 h and −15 min, to measure whether `update` and prices move between fetches | **32** (+1 discovery) | One day |
| C. 14-day shadow, weekday | Discovery 8 (2 dates every 6 h) + 1 decision fetch per tracked fixture (about 5–20) + closing fetch (same count, while budget remains) | about 20–50 per day | 10 days |
| C. 14-day shadow, weekend day | Discovery 8 + decision fetches (about 40–50); closing capture limited by the cap | 60 (cap) | 4 days |
| **C. 14-day total** | | **about 600–750** | |

Safeguards already in code:
- `--budget` (daily, logged per provider);
- `--max-requests-per-cycle` (default 12);
- `ODDS_MIN_REMAINING=20` (stops when the provider reports fewer than 20 left);
- the 7 s throttle;
- no paid endpoint;
- the shadow refuses the live DB while the hold exists.

Recommended daily budget for C: 60, which leaves at least 40 for production once it runs again. On busy weekends closing capture, and therefore CLV, will be partial. The report states this.

**The machine must stay awake for scheduled cycles.** During this test it slept from about 15:48 to 18:27 UTC. One fixture's decision snapshot (Heerenveen–Excelsior) and all closing captures were lost, and three cycles ran late. A scheduler needs `caffeinate` / launchd wake settings or a host that does not sleep.

# Real Bookmaker Odds Integration (M2.5)

Commits `61bba8c`, `65738f0` and `93a8b28` on `m2.5/operational-readiness`. Code: `src/odds/`, `src/engine/market_value.py`, `src/engine/decisions.py`, `scripts/odds_worker.py`. Tests: `tests/test_odds_layer.py` (48), `tests/test_decision_chain.py` (8).

## 1. Defects found before the integration

| # | Defect | Effect |
|---|---|---|
| O1 | **SofaScore crowd votes were the official pipeline's only "odds".** `fixture_collector._extract_odds` stored `1 / vote share` as bookmaker `sofascore_vote`, market 1X2, in `odds_snapshots`. | On the pre-fix code, a 10/45/45 vote alone produced an **A+ official pick, "odds 10.0", "edge 61.2 %", 2.0u stake** (reproduced on HEAD `927e402`). This was latent in the live app only because the insert crashed (K31). The M2 A fix made the insert work, so the defect would have gone live on the next pipeline run. |
| O2 | `find_value` used the **maximum price ever stored** per selection, across all bookmakers and all time. | Stale and outlier prices inflated the edge. |
| O3 | The Odds API rows used raw keys (`h2h`, team names, the totals line dropped), while `find_value` looked up `1X2`/`home`. | Even collected odds could never match the model. |
| O4 | The Odds API rows stored the provider event id as `match_id`. That violates the foreign key, so the whole batch raised and was swallowed. | Nothing was ever stored. |
| O5 | No quota control. Every scanner/worker call fetched a whole competition per match. | Unbounded paid-quota use once a key is set. |

## 2. Four kinds of price-like data

| Kind (`OddsType`) | Meaning | Stored where | Can support a bet? |
|---|---|---|---|
| `bookmaker` | Real price from a named bookmaker via a provider | `odds_quotes` | **Yes**, if fresh and pre-match |
| `crowd_vote` | SofaScore user votes | `crowd_vote_signals` as shares (`votes`, `share`) | Never; it is not a price |
| `model_fair` | 1 / model probability | Never stored; derived for display (`model_fair_odds`) | Never |
| `synthetic_test` | Explicitly fake price for tests | `odds_quotes` only when `APP_ENV=test`, baseline not frozen and the DB is not `data/engine.db` | Only in isolated tests; displayed as "SYNTHETIC TEST ODDS (not a bookmaker price)" |
| missing | No fresh real price | — | No: the fixture gets its forecast plus **NO ACTIONABLE BET** |

The table itself has `CHECK (odds_type IN ('bookmaker','synthetic_test'))`, and `Quote` refuses `crowd_vote` and `model_fair`.

## 3. Design

| Requirement | Implementation |
|---|---|
| Canonical fixture mapping | `identity.resolve_fixture`: normalised names (accents, punctuation, FC/AFC… dropped, a short list of verified aliases) **and** kickoff within ±2 h when the stored kickoff is absolute; names + date ±1 day when it is only "HH:MM". No substring or fuzzy matching. Each provider maps to at most one fixture; two candidates from one provider means unmapped ("ambiguous"). One real match stored by two feeds receives the same quotes. Mappings are kept in `fixture_identity`. |
| Bookmaker identity | Provider bookmaker key/name, lower-cased; required. |
| Market/selection/line mapping | `1X2`, `TOTALS`+line, `BTTS`, `AH`+line, `DC`, `DNB`. Lines are quarter-point validated (`2.5`, `-0.25`). The Odds API outcomes are matched exactly to team names or "Draw"; anything else is skipped and counted. |
| Decimal normalisation | `decimal_odds()` accepts 1.01–1000 and finite values only; invalid values are rejected, never repaired. |
| Source attribution | `provider`, `provider_event_id`, `bookmaker`, `raw_market`, `raw_selection` on every quote; picks keep `odds` provenance in `inputs_json`; decisions keep bookmaker, provider, type and observation time. |
| Timestamps | `observed_at` (the provider's own market/bookmaker update time), `commence_time`, `fetched_at`; all ISO UTC. |
| Duplicate prevention | `UNIQUE (fixture_id, provider, bookmaker, market, selection, line, observed_at)`, `INSERT OR IGNORE`; duplicates are reported. |
| Freshness | `actionable_quotes`: each bookmaker's **latest** price, observed before kickoff, while the match has not started, and no older than `ODDS_MAX_AGE_MINUTES` (default **60**, owner to confirm, §6). Older prices are superseded, never maximised over. |
| Missing odds | No candidate → `selection_decisions` row `NO_ACTIONABLE_BET` / `NO_REAL_ODDS` with the forecast kept. |
| Historical snapshots | Every distinct observation is kept, so the price history per bookmaker supports CLV later. |
| Failure handling | Network or HTTP errors, invalid JSON and API-Football `errors` payloads produce no quotes. Each is logged and reported. |
| Quota monitoring | `provider_quota_log` records every attempt (`ok`/`error`/`refused`), cost (actual `x-requests-last` where given), and the provider's remaining/used headers. Logged endpoints contain no credentials. |
| Budget guard | **No request unless `ODDS_REQUEST_BUDGET[_<PROVIDER>]` > 0 (default 0).** `ODDS_MIN_REMAINING` (default 20) also refuses when the provider reported fewer remaining requests. API-Football's daily limit is shared with fixture feeds. The legacy `TheOddsAPIProvider` path (scanner, execution, portfolio refresh) now goes through the same guard and storage. |

Value detection (`find_value`) still takes the best fresh price across bookmakers. That is the pre-existing line-shopping policy and it is not changed. Only 1X2, O/U 2.5 and BTTS are priced by the official model; quotes for other markets or lines are stored but never used for decisions.

## 4. Providers

### The Odds API (primary candidate)

- **Status: no `ODDS_API_KEY` configured. Nothing was requested.**
- Endpoint `GET /v4/sports/{sport}/odds?regions=eu&markets=h2h,totals,spreads&oddsFormat=decimal`. One request returns every upcoming event of a competition.
- Cost per request = markets × regions (3 with the defaults). Per the provider's documentation, btts and draw_no_bet are served by the per-event endpoint only, so they are not requested. This is inferred from the documentation and not verified with a live call.
- 14 tracked competitions. Sport keys follow each fixture's league-id scheme: SofaScore ids for pipeline fixtures, API-Football ids for `api_football`/`matches_db` rows, because the id spaces overlap.

**Expected quota use** (credits; check current plan prices with the provider before buying):

| Schedule | Credits per sweep | Per day | Per 30 days |
|---|---|---|---|
| 1 sweep/day, only competitions with fixtures (typically 4–8) | 12–24 | 12–24 | 360–720 |
| 4 sweeps/day, all 14 competitions | 42 | 168 | ~5,000 |
| Same, h2h only | 14 | 56 | ~1,700 |

The free tier (500 credits/month at the time of writing) covers roughly the first row. Anything beyond it is paid and **needs owner approval**.

### API-Football (alternative)

- **Verified on 2026-10-12 with one request** (`/odds?date=2026-10-13`, 1 of 100 daily free requests; nothing stored). The existing free key returns **current-season (2026) pre-match odds**: 10 fixtures on page 1 of 11, with bookmakers including Bet365, William Hill, Betfair, 1xBet, Marathonbet and Betano. The parser produced **3,269 canonical quotes** from that page. The sample's `update` time was 2026-10-12 08:15 UTC.
- Limits:
  - The free plan is 100 requests/day, shared with the fixture feeds.
  - `/odds?fixture=` costs 1 request per fixture.
  - `/odds?date=` is paged (11 pages for one date).
  - **The response carries no team names**, so quotes attach only to fixtures stored with the same API-Football fixture id (`provider = 'api_football'`). Official pipeline fixtures are SofaScore events, so a teams lookup is needed before API-Football odds can reach official decisions (M3 work item).
- No money is involved. The quota cost of a daily collection is about 10–30 requests (one per tracked fixture).

## 5. Pipeline behaviour now

1. `collect_fixtures` stores fixtures; crowd votes go to `crowd_vote_signals`.
2. Probabilities and calibration are unchanged (golden 9/9 identical).
3. `find_value` reads only actionable real quotes (or labelled synthetic ones in isolated tests).
4. Risk filter and grading thresholds are unchanged. `risk_rejection_reasons` reports the same checks.
5. Each fixture gets a `selection_decisions` record: `ACTIONABLE`, `NO_ACTIONABLE_BET` or `NO_FORECAST`. Each priced selection gets `PICK`, or `REJECTED` with reasons (`NO_POSITIVE_EDGE`, `EDGE_BELOW_LEAGUE_MINIMUM`, `CONFIDENCE_BELOW_FLOOR`, `PROBABILITY_BELOW_MINIMUM`, `DATA_QUALITY_BELOW_MINIMUM`, `GRADE_PASS`).
6. Picks carry the odds provenance (type, provider, bookmaker, price, observed time) in `inputs_json`, and `odds_source` on display.

No real-money placement exists or was added.

## 6. Owner decisions

1. **Provider and budget:** start with the API-Football free key (verified, no cost) and/or buy The Odds API credits. Set `ODDS_REQUEST_BUDGET_<PROVIDER>` accordingly.
2. **Freshness window** `ODDS_MAX_AGE_MINUTES`: 60 by default. API-Football prices may be several hours old, so a 60-minute window would reject most of them. A window longer than the provider's refresh cycle is not a fresh price.
3. **Collection schedule** (for example, once in the morning and once 1–2 h before kickoff). The scheduler is not installed.
4. Whether BILQE should consume real odds (it currently receives none).

## 7. Operating

```bash
python scripts/odds_worker.py --dry-run --days 1          # plan + estimated cost, sends nothing
ODDS_REQUEST_BUDGET_THE_ODDS_API=24 python scripts/odds_worker.py
ODDS_REQUEST_BUDGET_API_FOOTBALL=20 python scripts/odds_worker.py --provider api_football
sqlite3 data/engine.db "SELECT provider, status, SUM(cost) FROM provider_quota_log GROUP BY 1,2"
```

The dry run on a copy of the live DB found 0 upcoming not-started fixtures: every stored match is FT, and the fixture feed only stores matches it has seen. Collection therefore depends on the fixture refresh running first.

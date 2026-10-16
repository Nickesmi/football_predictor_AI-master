# Odds Provider Validation — API-Football (M3 Phase C)

Commits `f48c834` and `3889d0a`. Code:
- `src/odds/providers/api_football.py`
- `src/odds/collector.py`
- `src/odds/store.py`
- `src/odds/freshness.py`
- `src/engine/market_value.py`
- `src/engine/decisions.py`
- `scripts/validate_odds_provider.py`
- `scripts/odds_worker.py`

Tests: `tests/test_odds_m3.py` (23) and the existing `tests/test_odds_layer.py` (48).

No paid service was enabled. No production odds budget was set. Every live request went into isolated databases.

## 1. Changes

| Requirement | Implementation |
|---|---|
| Provider fixture id + team-name mapping | `GET /fixtures?date=` (1 request per date, with team names and UTC kickoff) → `identity.resolve_fixture`: normalised names plus kickoff ±2 h, or names plus date ±1 day for time-only kickoffs. At most one fixture per stored provider; ambiguity leaves the event unmapped. API-Football ids are stored in `fixture_identity`. `collect_api_football` now requests odds for every fixture with an identity, not only rows stored as `api_football`. |
| No odds on the wrong fixture | An odds response whose `fixture.id` differs from the requested id is rejected (team names come from the requested fixture). A kickoff that moved more than 2 h from the mapped time is rejected with "remap required". Odds can reach a pipeline fixture only through `fixture_identity`. |
| Bookmaker, market, selection, line, price | Unchanged strict M2.5 parsing. Bookmaker name is required. Canonical markets only. Quarter-point lines. Decimal odds 1.01–1000. Invalid values are rejected and never repaired. |
| Timestamps and attribution | `observed_at` (provider update time), `commence_time`, `fetched_at`, plus `provider`, `provider_event_id`, `bookmaker`, `raw_market`, `raw_selection`. Candidates and `selection_decisions` now also carry `odds_fetched_at` and `odds_age_minutes` (additive columns). |
| Pre-match vs in-play | `/odds` is pre-match only (`/odds/live` is never called). Quotes with `observed_at >= commence_time` are dropped before storage and counted (`quotes_after_kickoff`). `actionable_quotes` also requires `observed_at < commence_time` and a not-started match. |
| Crowd votes separate | Unchanged: `crowd_vote_signals` only; the `odds_quotes` CHECK refuses non-price kinds. |
| Missing / ambiguous / expired / invalid | Unmapped or ambiguous events, provider error payloads (counted as failures), prices older than the window, and invalid values all produce no quote and are reported. |
| Quote as of the prediction time | `actionable_quotes(now=decision time)` requires `fetched_at <= now` as well as `observed_at <= now`. Replaying an earlier decision can never use a price collected later, even when the provider stamped it earlier. |
| Quota and update interval | Every request is logged in `provider_quota_log` with the provider's remaining-requests header. The budget guard is unchanged (default 0). New: requests are spaced `API_FOOTBALL_MIN_INTERVAL_SECONDS` apart (default 7). The free plan returned HTTP 429 above about 10 requests per minute. |

## 2. Live validation (isolated DB, 2026-10-16)

Probe: `scripts/validate_odds_provider.py`.
- DB: `~/Desktop/football_predictor_backups/m3_2026-10-16/odds_probe/engine_probe.db`, a copy of the backup.
- Fixtures: date 2026-10-17, the first 12 not-started fixtures in tracked competitions.
- Result files: `probe_2026-10-17.json` and `probe_2026-10-17_retry.json`.

| Measure | Result |
|---|---|
| `/fixtures?date=2026-10-17` | 1,665 fixtures, of which 44 are tracked and not started |
| Fixtures probed | 12: Premier League 1, Championship 3, La Liga 1, Serie A 1, Bundesliga 3, Süper Lig 3 |
| First pass | 9 OK, 3 HTTP 429 (per-minute limit). Throttle added. Retry of the 3: all OK. |
| Fixtures with quotes | **11 / 12**. For the other one the provider returned no odds. |
| Quotes stored | 3,335 (2,767 + 568). Bookmakers: 6 for 1X2, 5 for O/U 2.5, 5 for BTTS. 33,412 values for unsupported markets were skipped (expected) |
| Wrong-fixture, moved-kickoff and in-play rejections | 0 / 0 / 0 |
| Requests | 16 OK + 3 × 429 (probe), plus 19 for the shadow cycle and 1 diagnostic request. The lowest remaining count the provider reported was 75 of 100. |

### Quote age at measurement time (provider `update` stamp to decision time)

| Bucket (minutes) | Fixtures | Share of 12 | Accepted at 240 |
|---|---|---|---|
| 0–60 | 0 | 0 % | — |
| 61–120 | 0 | 0 % | — |
| 121–240 | 9 | 75.0 % | 9 |
| > 240 | 2 | 16.7 % | 0 (rejected as stale) |
| No quote | 1 | 8.3 % | 0 |

Individual ages were 192.9 ×2, 206.8 ×3, 209.9, 221.8 ×3, 251.8 and 283.4 minutes.

Coverage within the window: **240 min: 75.0 %**, **120 min: 0 %**, **60 min: 0 %**.

### Free-plan restriction found

`GET /odds?league=&season=2026&date=` returns HTTP 200 with `errors: {"plan": "Free plans do not have access to this season, try from 2022 to 2024."}`. The shadow cycle's first run made 16 such requests and received nothing. Provider error payloads are now counted as failures, not as "skipped values". The shadow collector defaults to per-fixture requests, which do serve current-season odds on the free plan. The league-date mode stays available for a paid plan.

## 3. Freshness policy recommendation

Evidence so far is one probe of 12 fixtures. API-Football's own update stamps were **193–283 minutes old** at a random moment, consistent with a refresh roughly every 3–4 hours.

1. A 60- or 120-minute `ODDS_MAX_AGE_MINUTES` would reject **every** API-Football free-plan price. With that window, a frozen baseline would produce no picks.
2. Keep **240 minutes provisionally for shadow only**, as instructed. Record the real age of every quote (done: `odds_age_minutes`, buckets in the shadow report).
3. Use two clocks in the official policy (implemented in policy v1):
   - **provider price age ≤ 240 min**;
   - **our fetch ≤ 60 min before the decision**, which shows the price was still being published shortly before the decision.

   Neither clock proves a bet was executable at that price.
4. **Before official collection**, set the window from the shadow distribution: the 95th percentile of quote age at decision time, if it is ≤ 240. If the 95th percentile exceeds 240, or coverage stays below the 95 % exit criterion, the free API-Football feed cannot support the baseline as specified. The options then are:
   - (a) an explicitly longer, labelled window (owner decision);
   - (b) a faster provider such as The Odds API (paid; needs approval).
5. Quota plan on the free plan (100 requests/day, shared):
   - discovery 8/day (2 dates every 6 h);
   - results 1–3/day;
   - one decision fetch per tracked fixture (weekday about 5–20, weekend about 40–50);
   - closing fetches only while budget remains.

   A daily budget of 70–80 with `ODDS_MIN_REMAINING=20` fits on weekdays. On busy weekends closing capture, and therefore CLV, will be partial.

## 4. Coverage and rejection rates — to be filled by the shadow period

`scripts/shadow_report.py` reports the same four buckets plus `NO_QUOTES` / `UNMAPPED` / `STARTED` per observed fixture. The values above are the only live measurements so far.

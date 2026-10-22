# Local demo: cached fixtures by default, owner-authorized refreshes (2026-10-23)

Branch `demo/quota-policy`, from `2bd13a5`, built in the worktree `.worktrees/demo-quota`. Not merged, not deployed. The running demo (pid 2670) and the vite server were not restarted or changed.

## 1. Root cause of "Football API unavailable: demo API-Football allowance of 5 requests is used up"

| Step | Evidence (`localhost_copy/demo/demo_api_usage.db`, read-only) |
|---|---|
| The demo had a **lifetime** cap of 5. It requested a date the first time the interface asked for one that was not cached. | `scripts/demo_server.py` (`fetch_once_per_date`) |
| **The interface asked for a new date by itself.** `App.jsx` follows "today" in Asia/Tehran. A 60-second clock switches the selected date at Tehran midnight, and the new date is loaded at once. A tab coming back into view also reloads with `force_refresh` for today, and failed loads retry 3 times. | 2026-10-20 **20:30:24** and 2026-10-21 **20:30:46** UTC. Tehran midnight is 20:30 UTC: one request for the new day each time, with nobody clicking. 10-17 20:36, 10-18 19:44 and 10-19 18:49 came from page opens. |
| The 5 requests were used by 2026-10-21. | 5 rows `allowed = 1`, one `/fixtures?date=…` each for 10-18 to 10-22 |
| At Tehran midnight on 10-23 (2026-10-22 20:30:36 UTC) the open page asked for 10-23. The guard refused it **before sending** (row 6, `allowed = 0`). The fixture service then reported the refusal as "Football API unavailable…", with no fixtures. | row 6; no 10-23 cache file |
| Side defect: the **backend** demo wrapper (`fetch_once_per_date` in `scripts/demo_server.py`, not the frontend) marked the refused attempt as fetched in `demo_fetched_dates` (it marked in `finally`). So 10-23 could never have been fetched later, even with a new allowance. | `demo_fetched_dates` holds 2026-10-23 |
| **Implicit time zone:** the date switch happened at **Tehran** midnight. Commit `a76ab45` (2026-10-11), with the working code from about 2026-09-20, silently changed "today", the provider request and the stored local times from Europe/Istanbul to Asia/Tehran. In Istanbul it was still 23:30 on 10-22. | `git show a76ab45^:api/services/fixtures_service.py`; cache kick-off minutes flip from mostly `:00` to mostly `:30` on 2026-09-20 |

**Cached dates were never lost.** 2026-05-24 → 2026-10-22 are all in `.cache/`, 146 files. Today, 10-23, had none, and the error replaced the list.

## 2. What changed

**Backend:**
- **`src/ops/demo_quota.py`**:
  - **Legacy record kept:** `demo_api_usage` is read and written exactly as before (lifetime `--cap` ≤ 5). On the live demo it stays 5/5. It is never reset or rewritten.
  - **Owner grants:** new append-only `demo_allowance_grants`. Each grant has a total (1–200), a daily limit per UTC day (1–20), an optional expiry and a required note. Revoking sets `revoked_at`.
    - **There is no grant by default, so zero new requests are allowed.**
    - Requests under grants are logged in `demo_requests`: time, UTC day, path (no credentials), grant, allowed, reason, HTTP status, the provider's remaining count, and the outcome (`ok`, `http_error`, `error` or `refused`).
  - **Counting:**
    - every attempt is counted before it is sent, including failures and HTTP 429;
    - a single refresh can send **at most 1** request, so a retry needs a new click;
    - the check and the insert run in one `BEGIN IMMEDIATE` transaction, so several processes cannot exceed a grant;
    - counters live in the usage DB and survive restarts.
  - **Explicit only:** a request is allowed only inside `explicit()`, which is entered only by the refresh endpoint. Page loads, `/live/scan`, predictions and any other path are refused and logged as `not an explicit user action`. DNS for `*.api-sports.io` outside a counted request stays blocked.
  - **Provider quota:** refused when the provider's own remaining count today is at or below `--provider-floor`. The default is 30, which is 10 above the shadow's floor of 20.
  - **Shared ledger:** with `--ledger … --ledger-budget N` (N ≤ 70), each grant request is also reserved in the shadow's ledger (`src/odds/ledger.py`) as consumer `demo`. It is refused when the all-consumer total for the day would exceed N.
  - **Same key as the shadow:** if `~/FootballShadow/etc/api_key.env` holds the demo's key, grant requests are refused unless the shared ledger is configured. SHA-256 digests are compared in memory; the key is never logged, stored or sent. An unreadable key file counts as "same".
- **`api/demo_mode.py`** (new; loaded only by the demo server):
  - `GET /api/fixtures/{date}` reads only the cache and the demo DB, and never contacts API-Football. It returns `{fixtures, message?, demo: {cached, cached_fixtures, last_updated_at, period, data_source: "cache", live: false, quota}}`.
  - `last_updated_at` is the last **recorded successful** fetch: a refresh, or a legacy fetch whose request was allowed. It is not the file time, because every page load rewrites the cache file. For 10-23 it is "unknown" (the attempt was refused).
  - `GET /api/demo/status` and `GET /api/demo/fixture-cache/{date}` send nothing.
  - `POST /api/demo/refresh-fixtures/{date}` is the only sending path:
    - 409 if the date is already cached (never requested twice) or a refresh of it is already running (a cross-process lock);
    - 403 with the reasons if no allowance applies, and nothing is sent;
    - 502 with the error on a provider failure; the request is counted, the date is not marked fetched, and nothing is cached;
    - 200 with the updated quota on success.
- **`scripts/demo_server.py`:**
  - new flags `--cap` (default **0**), `--ledger`, `--ledger-budget`, `--provider-floor` and `--shadow-root`;
  - it refuses any DB inside the shadow root, as well as `data/engine.db`;
  - the ledger is no longer set process-wide;
  - background tasks stay disabled.
- **`scripts/demo_allowance.py`** (new): `status`, `grant` (needs `--i-authorize-api-football-requests`) and `revoke`. It sends nothing.

**Frontend:**
- **`frontend/src/demo/demoRefresh.js`** (pure rules, tested with `node --test`) and **`components/DemoBar.jsx`**. In demo mode:
  - a **DEMO** strip shows "Cached snapshot · not live · updated 3 h ago (…)", or "Cached results snapshot" for past dates;
  - it shows the allowance, e.g. "2 left today · 14 left in authorized allowance", next to a **Refresh fixtures (1 request)** button;
  - the button is disabled, with the reason, when the date is already cached, no allowance remains, or a refresh is running. Cached fixtures stay visible;
  - before sending, a confirmation shows the cost and what will remain;
  - an uncached date shows a message instead of an error.
- **`App.jsx`:**
  - in demo mode there is no reload on tab focus and no coverage polling, and "Scan Live Odds" is disabled;
  - date changes only read the cache.
- Outside demo mode (`/api/demo/status` absent) the interface behaves exactly as before.

**Not changed:**
- the production DB, production service and hold;
- the shadow runtime (`~/FootballShadow`, pinned at `7b8a29c`, which this branch does not touch), the shadow DB and ledger, and the launchd templates;
- models, calibration, selection, and the baseline.

The demo works only on its isolated DB copy and its own cache. It cannot write forward observations, calibration or baseline counts. This branch changes `src/` and `api/`, so its code fingerprint differs from manifest v4. That is intended: the shadow keeps running its own pinned worktree.

## 2b. Second pass (2026-10-23): fixture states, explicit time zone, no automatic external requests

- **A refused, failed or incomplete request is never a successful fetch.**
  - A date counts as fetched only when its own fixtures are stored, or a successful response said none are scheduled.
  - The legacy `demo_fetched_dates` table is no longer trusted (it holds the bogus 10-23 mark). It is left unchanged.
  - A failed refresh never writes or replaces the cache.
- **Explicit states** in every fixture response (`demo.state`) and refresh result:
  - `cached`: fixtures served from the cache, with the time of the last recorded successful fetch;
  - `fresh`: fixtures just retrieved by a refresh;
  - `no_fixtures`: the provider was asked successfully and has nothing scheduled (recorded, never asked again);
  - `quota_exhausted`: no authorized request remains, HTTP 429 (returned as 429), or the provider's "request limit" message;
  - `provider_error`: a network or HTTP failure, or another provider error (502), with the error text kept and shown;
  - `not_cached`: nothing stored yet, and a refresh is possible.
- **Time zone:**
  - Calendar days are explicit **Europe/Istanbul** days (`--display-tz`; the frontend uses the server's `display_timezone`, otherwise `VITE_DISPLAY_TZ`, default Europe/Istanbul).
  - New fixtures store `kickoff_utc`, the provider's instant in UTC. A refresh asks the provider with `timezone=Europe/Istanbul`.
  - Older cache files have only local strings. Their basis is inferred per file, only when the kick-off minutes are decisive: at least 20 fixtures and a 2:1 majority. That gives Asia/Tehran from about 2026-09-20 and Europe/Istanbul before.
  - Fixtures of unknown basis are shown as stored, flagged `time_basis: unknown`, and never moved to another day.
  - Each fixture carries `kickoff_utc`, `display_timezone` and `time_basis` (`utc`, `inferred:<zone>` or `unknown`).
  - The production fixture service is unchanged, apart from the extra `kickoff_utc` field and an optional `request_tz` parameter whose default is still Tehran.
- **No automatic external requests** (`src/ops/demo_network.py`):
  - In the demo process, DNS for any external name is refused unless permitted. That covers `http.client`, `urllib`, `requests` and `httpx`.
  - `curl_cffi`, which does its own DNS, is refused at `Session.request`.
  - Permitted are: API-Football through the counted explicit refresh, logo images (`…/image` on SofaScore, `media.api-sports.io`) and loopback.
  - The smoke run showed the old demo's **SofaScore direct-fetch fallback ran on every page load** for past and today's dates (7 calls in one run). They are now refused and counted in `/api/demo/status` → `external_requests_blocked`. Match analysis still answers (HTTP 200).
- **Frontend in demo mode:**
  - no reload on tab focus, no polling and **no retries** of a failed load;
  - date changes only read the cache;
  - the DEMO strip shows the state label, the snapshot time and "Times in Europe/Istanbul".

## 3. Restoring current fixtures (each step needs your go-ahead)

Today's fixtures need **1 API-Football request**. Nothing below has been run.

```
M=~/Desktop/football_predictor_ai-master; W=$M/.worktrees/demo-quota
L=~/Desktop/football_predictor_backups/m3_2026-10-16/localhost_copy
# 0. Back up the usage record (the new code adds empty tables to it; existing rows are never changed)
sqlite3 $L/demo/demo_api_usage.db ".backup $L/demo/demo_api_usage.pre-policy.db"
# 1. Stop the old demo (the interface loses its backend until step 3)
kill -TERM 2670
# 2. Give the worktree the demo's key: it reads <project>/.env (same file as now; never copied into git)
ln -s $M/.env $W/.env
# 3. Start the new demo on the same port, data and usage record (--cap 5 = the legacy record, already 5/5)
cd $W && $M/.venv311/bin/python scripts/demo_server.py --db $L/engine.db --workdir $L/demo --port 8001 --cap 5
# 4. New interface: stop the old vite (pid 46214) and start the worktree's, or use another port
kill -TERM 46214; cd $W/frontend && ./node_modules/.bin/vite --host 127.0.0.1 --port 5173 --strictPort
# 5. Authorize an allowance (section 6), then press "Refresh fixtures" for today in the interface
```

After step 3 the interface already shows every cached date, with update times, and a clear message for 10-23. It does that without any allowance.

## 4. Test results (worktree, mocked API-Football, isolated DBs; no network)

| Suite | Result |
|---|---|
| `tests/test_demo_refresh_policy.py` (new, 20 tests) | **20 passed** |
| `tests/test_demo_fixture_states.py` (new, 11 regression tests: the October 23 failure and its grant follow-up, Istanbul/Tehran boundaries, legacy basis inference, unknown basis, repeated loads from 20:00 to 22:00 UTC, no fixtures scheduled, provider limit in the body, a failed refresh keeping the cache, network guard) | **11 passed** |
| `tests/test_demo_quota.py` (2 tests updated to the new rule: page loads never fetch) | **7 passed** |
| Full suite, Python 3.11 | **951 passed, 2 skipped** (migration test without a DB copy; service-hold test, since the worktree has no hold file) |
| Full suite, Python 3.13 | **950 passed, 3 skipped** (the same two, plus `pyflakes` not installed) |
| Goldens | 16 passed |
| Migrations on a DB copy | 4 passed |
| Integrity audit | PASSED |
| Frontend `npm test` (`node --test src/demo/`) | **13 passed** (including the 20:30/21:00 UTC boundary and no retries in demo mode) |
| Frontend `eslint .` | clean |
| Frontend `vite build` | built (output in the scratchpad, not in the repo) |
| Smoke run of the new server on **copies** of the real demo DB, usage DB and cache (port 8011, no key) | Istanbul days 10-21/10-22 served (255/177 fixtures, basis `inferred:Asia/Tehran`) with their true fetch times; today = 10-23 (Istanbul); 10-23 `quota_exhausted` with a message; analysis 200; 7 automatic SofaScore data calls refused; status `5/5, refused 1, no owner-authorized allowance`; refresh → 403; cached date → 409; **0 requests**; usage copy unchanged |

The scenarios you listed:

| Scenario | Test |
|---|---|
| Exhausted allowance with a cache | `test_exhausted_allowance_still_serves_cached_fixtures_and_sends_nothing` |
| Authorized refresh | `test_authorized_refresh_sends_once_caches_and_never_repeats_a_cached_date` |
| Missing cache | `test_missing_cache_shows_a_message_and_sends_nothing` |
| Repeated reload | `test_repeated_reloads_force_refresh_and_tab_returns_never_send` |
| Duplicates | the same date → 409, and `test_one_refresh_can_never_send_more_than_one_request` |
| Concurrency | `test_concurrent_refreshes_of_one_date_send_once` (6 threads → 1 send), `test_several_processes_cannot_exceed_one_allowance` (4 processes × 4 tries, grant 3 → 3) |
| Daily reset | `test_daily_limit_resets_at_utc_midnight_and_total_holds` |
| Restart | `test_usage_persists_across_restarts` |
| Failures and 429 | `test_failures_are_counted_not_cached_and_retryable_only_with_allowance` (500, 429 with remaining 0, connection error) |
| Shared quota | `test_same_key_as_the_shadow_requires_the_shared_ledger`, `test_shared_ledger_daily_total_of_all_consumers_holds` (70 used → refused, 69 → 1 sent, total ≤ 70), `test_provider_floor_blocks_before_sending` |
| Other paths | `test_any_other_code_path_is_refused_even_with_an_allowance`, `test_demo_refuses_the_shadow_experiments_database` |
| Owner control | `test_grants_need_explicit_confirmation_limits_and_can_be_revoked`; the legacy rows are byte-identical before and after |

## 5. Recommended demo allocation (the shadow keeps its 70 a day)

API-Football's free plan allows **100 requests a day per key** (the shadow preflight reads the real `limit_day`; adjust if your plan differs). The shadow stops when the provider reports **≤ 20** left, and it needs up to **70** + 1 (preflight). So anything else using the **same key** must stay at **≤ 9 a day**, or the shadow loses requests. The demo needs 1 request per new date.

| Situation | Recommendation |
|---|---|
| **A separate API key for the demo** (best) | grant `--total 70 --daily 5 --days 14`. No ledger is needed, and there is no interaction with the shadow. |
| Same key, **before** the shadow is activated (now) | grant `--total 4 --daily 2 --days 3`. Do not refresh on the activation day before the provider check (step 2 wants ≥ 90 remaining). |
| Same key, **during** the 14 days | **0 (no grant)**, using cached fixtures only. If you need current fixtures: start the demo with `--ledger ~/FootballShadow/run/api_football_ledger.db --ledger-budget 70` and grant at most `--daily 1 --total 14`. **Each demo request then reduces the shadow's day by one** (the ledger ceiling of 70 counts all consumers), and the shadow monitor will alert `unexpected consumers ['demo']`. That is correct visibility, not a fault. The demo refuses on its own if you forget the ledger once the key is entered in the shadow. |

## 6. Authorizing requests (owner only)

```
U=~/Desktop/football_predictor_backups/m3_2026-10-16/localhost_copy/demo/demo_api_usage.db
cd ~/Desktop/football_predictor_ai-master/.worktrees/demo-quota
../../.venv311/bin/python scripts/demo_allowance.py --usage-db $U status            # sends nothing
../../.venv311/bin/python scripts/demo_allowance.py --usage-db $U grant --total 4 --daily 2 --days 3 \
    --note "current fixtures before shadow activation" --i-authorize-api-football-requests
../../.venv311/bin/python scripts/demo_allowance.py --usage-db $U revoke --id 1      # stop at any time
```

- The running demo picks up a grant on its next refresh; no restart is needed.
- Grants above 200 in total or 20 a day, or grants without a note or the confirmation flag, are refused.
- Nothing resets the legacy 5/5 or past grants.

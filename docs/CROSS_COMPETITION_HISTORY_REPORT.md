# Cross-Competition History and Data Recovery — Review Report (2026-11-04)

Branch `demo/cross-competition-history` (worktree `.worktrees/demo-xcomp`, from `demo/football-data` @ fb549ff), **uncommitted, not deployed**.
Data: `~/Desktop/football_predictor_backups/demo_xcomp_2026-11-03/` (`run5/` = candidate database, `eval/` = before/after, `browser/` = browser run).

**Nothing deployed.** The running demo (pids 13288 / 13338, `localhost_copy/engine.db`), its caches, the production database and service hold, the shadow experiment and its manifest, the shared ledger, the credentials, and the model weights and calibration were all left unchanged. No API-Football (or any provider) request was sent. Every evaluation and browser run ran with outbound sockets disabled, or behind the demo network guard.

## 1. Diagnosis: why Feyenoord vs Inter showed "Prediction unavailable"

Pipeline: fixture cache (API-Football ids) → `build/get_match_analysis` → `resolve_for_analysis` (team-identity-v1) → `team_state` record → `_venue_stats` → Poisson/XGBoost → `MatchDetail.jsx`.

| # | Defect | Evidence |
|---|---|---|
| D1 | **Precompute poisoned the analysis cache.** On page load `_precompute_predictions_for_date` called `_compute_match_analysis(home, away, league)` without provider ids and stored the result under the fixture id. The endpoint then served that legacy, name-only result even when it was called with the ids. | The live response for fixture 1635662 has no `identity` key; its note is the legacy text `home_team 'Feyenoord' has NO real data — using fallback defaults` (`match_analysis_service.py:420`). |
| D2 | **Identity was per-competition only.** A Champions League fixture searched only the UCL labels: Feyenoord has 2 UCL matches there and Inter none. Their verified domestic records (Eredivisie, Serie A) were never considered, although the legacy feature definition uses domestic statistics as the UCL base (`team_stats_db.py:334`). | `resolve_for_analysis` on the live DB: Inter `UNRESOLVED_NO_HISTORY: no uefa-champions-league history`. |
| D3 | **`team_state` disagreed with its own `match_history`.** The live update loads state with a substring `LIKE` fallback, so results went into other clubs' rows. Inter's 5 Serie A results are in `match_history`, but no Inter/Serie A state row exists. Before: only 16,349 of 18,670 state rows agree with their history. | Snapshot integrity check. |
| D4 | **Completed results never imported.** 10,585 finished API-Football results in the caches were not in the DB: 357 in the 14 top competitions, 10,228 elsewhere. The Nov 1 rebuild (290 results) was made on a copy and never deployed. It could not create a record for a club with no history (Inter/Serie A). | `backlog` scan. |
| D5 | **Fragmented records.** One club stored under two spellings in one label ("FC Bayern München" / "Bayern München") is `AMBIGUOUS` and never merged. The live `LIKE` fallback had hidden this; an exact replay exposes it. | Bayern resolved to its 2-match UCL record. |
| D6 | **Leakage.** Before: 320 served predictions on 31 Oct–3 Nov used a record that already contained results on or after kick-off. | `eval/before.json`. |
| — | **Cannot be fixed locally:** cache files from 16 Oct onwards were saved before kick-off (status `NS`). Results from 16 Oct to 2 Nov exist nowhere on disk. Recovering them needs provider requests (not authorised). | Cache scan. |

## 2. Changes (code)

| File | Change |
|---|---|
| `api/services/match_analysis_service.py` | New `build_match_analysis(...)`: identity resolution + compute + cache, with nothing recorded. The endpoint (`get_match_analysis`) and the precompute share it. `persist_qualification=False` keeps the precompute's old behaviour: no `qualification_log` writes. |
| `api/services/fixtures_service.py` | Precompute calls `build_match_analysis` with `fixture_identity_params(f)`, the same parameters the frontend sends. Fixes D1. |
| `src/identity/club_membership.py` (new) | Provider-team-id membership (`provider_team_competitions`, built from the caches). `domestic_league()` returns the club's domestic league: one with a country, not "World", where the club appeared in a league round (`Regular Season/Apertura/Clausura - N`), or a tracked domestic league. Cups and friendlies are excluded. Only fixtures before the match count. Ties and multiple spellings are refused. Youth, reserve and women's sides have their own ids, so they can never link to the senior club. |
| `src/identity/analysis_inputs.py` | In a continental club competition (UCL/UEL/UECL, or another "World" league), a club with fewer than 5 verified matches there uses its **verified domestic-league record**, resolved by team-identity-v1 inside that league with provenance checked. The two records are never averaged. Poisson profile ("Champions League"), venue transform and models are unchanged. **Leakage guard:** a record whose last match is on or after the kick-off date is never used. New notes: domestic source, and "statistics come from different competitions; the model applies no league-strength adjustment". Domestic fixtures behave as before. |
| `scripts/recover_cached_results.py` (new) | Recovery on a copy (§3). |
| `scripts/evaluate_prediction_coverage.py`, `scripts/browser_regression_xcomp.py` (new) | Measurement tools. |
| `frontend/.../predictionInputs.js`, `MatchDetail.jsx` | The leakage note counts as blocking. The unavailable panel no longer says "fallback defaults"; it lists the precise reasons. |

## 3. Data recovery (isolated copy; `run5/recovered.db`)

The source was taken as an SQLite online backup (read-only). The source database and caches were not written.

| | Value |
|---|---|
| Finished cached results (unique fixture ids, status FT) | 37,432 candidates |
| Already stored (same id) | 26,999 |
| **Recovered** | **10,314** (332 top-14, 9,982 other leagues); Aug 2,720 · Sep 3,562 · Oct 4,032 |
| Records created from a provider team id (no record before, e.g. Inter / Serie A) | 1,911 sides; 0 label/name collisions between clubs |
| Rejected: kick-off time basis unknown | 1,313 (date not provable; not guessed) |
| Rejected: team identity unresolved | 118 (top-14: 6 — Arsenal `CONTAMINATED` ×3, Rangers `UNVERIFIED` ×3) |
| Rejected: score conflicts (cache vs DB, or within the cache) | 1 + 1 |
| Spelling merges (provider team id evidence only, logged in `team_record_merges`) | 71 clubs (e.g. Bayern München → FC Bayern München [157], Newcastle → Newcastle United [34]); 40 refused (one name used by several ids); 54 spellings without id evidence left unmerged |
| Corners / cards / xG of recovered results | NULL; nothing estimated |
| Provenance | `recovered_results`: fixture id, kick-off, league, provider ids and names, identity decision, cache files with SHA-256, importer version |
| Replay | Full history, oldest first, through the unchanged `live_updater.on_match_finished`, exact (name, label, venue) lookup |
| Integrity | `integrity_check ok`, 0 FK violations, 0 duplicate history ids; state rows consistent with history: **20,258 / 20,258** (before 16,349 / 18,670) |

Named records (snapshot → recovered):

| Club / label | Before | After | Recovered results |
|---|---|---|---|
| Feyenoord / Eredivisie | 6, to 20 Sep | 8, to 10 Oct | 2 |
| Inter / Serie A | **no record** | 7, to 28 Oct | 2 (+5 stored, replayed) |
| Real Madrid / La Liga | 3, to 20 Sep | 7, to 10 Oct | 3 |
| Barcelona / La Liga | 2, to 16 Sep | 6, to 10 Oct | 4 |
| Manchester United / Premier League | 2, to 20 Sep | 5, to 10 Oct | 2 |
| FC Bayern München / Bundesliga | 2 (split) | 6, to 10 Oct | 4 + merge |
| Paris Saint-Germain / Ligue 1 | 2 (to May 2025, split) | 8, to 10 Oct | 5 + merge |
| Arsenal / Premier League | 9 (contaminated, league 116) | unchanged, still refused | 0 |
| Rangers / Premiership | unverified | unchanged, still refused | 0 |

Even famous clubs have short samples here (5–8 domestic matches). The caches only start in August, and October results after the 15th are missing (§1).

## 4. Prediction coverage, before vs after (cached days 31 Oct – 3 Nov, 3,212 fixtures)

"Served" means what the page shows: the precompute result.

| 3 Nov 2026 (230 fixtures) | Before (deployed code, live DB) | Code only | Data only | **After** |
|---|---|---|---|---|
| Predictions available | 148 | 148 | 175 | **185** |
| Team sides with a verified record (of 460) | 327 | — | — | **393** (44 via domestic league) |
| Sides with fewer than 5 matches | 166 | — | — | 142 |
| Sides with history ending ≥ 21 days before the match | 217 | — | — | 289 (more records now resolve, but end 10 Oct) |
| Median sample per verified side | 4 | — | — | 7 (≥10 matches: 38 → 124 sides) |
| Blocking: no history / contaminated / ambiguous / unverified | 129 / 2 / 1 / 1 | | | 63 / 2 / 1 / 1 |

All days: before 2,917 served, after 2,843. The drop is the leakage guard (356 sides on 31 Oct–2 Nov whose record already contains that day's results). Before, **320 served predictions used post-kick-off data; after, 0.**

3 Nov, Champions League (after):

| Fixture | Before | After (records) | 1X2 % |
|---|---|---|---|
| **Feyenoord v Inter** | unavailable | Feyenoord / Eredivisie 8 (to 10 Oct) · Inter / Serie A 7 (to 28 Oct) | 65.4 / 18.0 / 16.6 |
| Galatasaray v VfB Stuttgart | unavailable | Süper Lig 6 · Bundesliga 5 | 47.6 / 23.2 / 29.3 |
| Shakhtar v Sporting CP | unavailable | Ukrainian Premier League 7 · Primeira Liga 8 | 54.3 / 24.9 / 20.8 |
| Bodo/Glimt v Lille | unavailable | Eliteserien 9 · Ligue 1 8 | 48.4 / 26.2 / 25.4 |
| Lask Linz v Slovan Bratislava | unavailable | Austrian Bundesliga 7 · Super Liga 9 | 39.6 / 23.6 / 36.8 |
| Atletico Madrid v Bayern | legacy tables | La Liga 8 · Bundesliga 6 | 38.9 / 24.3 / 36.8 |
| Barcelona v Aston Villa | legacy tables | La Liga 6 · Premier League 7 | 80.0 / 11.8 / 8.2 |
| Man United v Roma | legacy tables | Premier League 5 · Serie A 7 | 26.5 / 27.3 / 46.3 |
| Villarreal v PSG | legacy tables | La Liga 8 · Ligue 1 8 | 35.0 / 24.8 / 40.2 |

Gains on 3 Nov by competition: National League Cup 14, J2 League 10, Champions League 5, Saudi Division 2 4, and others.

**Still unavailable on 3 Nov: 45 fixtures.** Most are cups and lower or youth leagues with no stored history: Scottish Challenge Cup 10, Indonesian League Cup 7, Kings Cup 4, Saudi Division 2 4, Sergipano 2 3, others. Five fixtures that were served before are now refused. Each had been served only through the legacy name path, with no verified identity:
- Colo Colo: two records, `AMBIGUOUS`.
- SC Villa and Police (Uganda): `CONTAMINATED`. Each shares the "Premier League" label with a same-named club in another country's league; this was already in the snapshot.
- Copa de la Reina and Copa del Rey: no history in those cups.

## 5. Tests

| Suite | Result |
|---|---|
| Backend `tests/` (.venv311) | **987 passed, 2 skipped** |
| New `tests/test_cross_competition_history.py` | 21 tests covering all 12 requested areas: Feyenoord Eredivisie/UCL; Inter Serie A/UCL (never the Colombian "Inter"); cross-provider ids; aliases (VriendenLoterij); duplicates (same id, other id, score conflict); youth/ambiguous/two leagues/two names/two clubs sharing one label; sparse; missing statistics stay NULL; chronological replay; leakage (record and membership); 16 concurrent read-only resolutions; precompute = endpoint inputs; id-only merges |
| Frontend `npm test` / eslint | 20 / 20 pass; lint clean |
| DB integrity (candidate) | ok (§3) |
| Browser (Chromium, isolated stack :8011/:5183, recovered DB copy, no key, non-local requests aborted) | **PASS**: Feyenoord v Inter shows a prediction with all four notes visible; 24 concurrent analysis requests all 200 and identical on repeat; an unavailable fixture (Loud v Furia, Kings Cup) shows the panel with the reason and no percentages; the only blocked hosts were logo images (media.api-sports.io); no provider attempt |

## 6. Limits and items for your decision

1. **League strength.** Cross-league fixtures compare raw domestic rolling averages (Eredivisie goals against Serie A goals). The model has no league-strength adjustment. Every such prediction carries a note saying so. Adding one would be a model change, and needs separate approval. Barcelona v Aston Villa (80 / 12 / 8) shows the effect.
2. **Missing results, 16 Oct – 2 Nov.** They are not on disk. Most top-league records end on 10 Oct (stale notes are shown). Backfilling needs approved API-Football requests.
3. **Kick-off basis unknown (1,313)** and **Arsenal / Rangers** stay excluded. Arsenal's "Premier League" record mixes league 116; separating it needs a label or key redesign.
4. Form (`form_last5`) still follows the existing pipeline: it counts results across all labels for the same name. Unchanged by design.
5. The running demo keeps writing results. **Deploying requires re-running the recovery on a fresh snapshot** (about 10 min), not copying `run5/recovered.db` over newer state.

## 7. Proposed deployment (only after your approval)

1. Back up `localhost_copy/engine.db` and the demo pids.
2. Stop the demo: backend 13288, vite 13338.
3. Rerun `scripts/recover_cached_results.py` on the stopped DB and check integrity.
4. Start backend + vite from `.worktrees/demo-xcomp` on the new DB. Run the browser regression.
5. Rollback: restore the backup and restart from `demo-fd`.

# Known Issues

Status as of M2.5 (2026-10-13). Severity: P0 blocks the baseline experiment, P1 must be fixed before production use, P2 hygiene. "Gov" = changes production behaviour and needs owner approval (`baseline_contract.md`).

## Open

| ID | Sev | Issue | Where | Planned |
|---|---|---|---|---|
| K5 | P0 Gov | Automatic recalibration/selection drift (P1–P9, P18) still active until the baseline is frozen | `docs/BASELINE_FREEZE_DESIGN.md` §4 | implemented (M2 C); activate at M3 (`docs/CUTOVER_PYTHON311.md` §2) |
| K20 | P1 | Outputs depend on the Python minor version (`sum()` compensated from 3.12): one golden value differs 3.11 vs 3.13. 3.11 is canonical; references versioned, divergence pinned by test. Running app still on 3.13 | `match_analysis_service.py:1372,1425` | cutover prepared (`docs/CUTOVER_PYTHON311.md`); execute at M3 |
| K10 | P1 | No real CLV anywhere: no real odds collected yet (`odds_quotes` empty; budget 0). Price history per bookmaker is now kept, so CLV becomes computable once collection runs | settlement path | M4 |
| K11 | P1 | Football baseline not started (`picks` 0/1,000); scheduler not installed | crontab / F1 | M3 (owner decision) |
| K27 | P2 | Dead module `src/engine/classic_prediction.py` imports nonexistent `src.db.daily_repo` (not imported anywhere); allow-listed in the static import guard | — | documented |
| K28 | P0 | **Football API pid 94530 was started by an Antigravity agent session at 2026-10-11 18:52 from the M2 working tree** (pre-M2 C; no reload). It writes `data/engine.db`, still has every defect fixed in M2.5, and its 03:00 nightly refit rebuilds live league adjustments and calibration from the fabricated rows of K32 (last run 2026-10-12 00:13 UTC) | runtime | owner: stop and restart deliberately (`docs/OPERATIONAL_READINESS.md` §2–3) |
| K32 | P0 Gov | **10,808 of 11,209 `prediction_errors` rows are fabricated 33/33/33 "home" records** (2026-06-04 → 08-31) from a results-verification bug (fixed in `927e402`). League reliability, live league adjustments (7 weighted leagues) and the nightly refit derive from them | live DB | M3: independently confirmed; quarantined and rebuilt on an isolated copy (`docs/DATA_INTEGRITY_REMEDIATION.md`); production activation awaits owner review |
| K33 | P1 Gov | No real odds flow yet: no `ODDS_API_KEY`; budget default 0. API-Football free key verified to serve 2026 pre-match odds, but its odds carry no team names, so they only attach to `api_football` fixtures, not to official (SofaScore) fixtures | `src/odds/` | M3: API-Football fixture identity via `/fixtures?date=` done; live probe 11/12 fixtures quoted; production budget still 0 (owner) |
| K34 | P1 Gov | `ODDS_MAX_AGE_MINUTES` default 60 is a placeholder; must match the chosen provider's refresh cycle | `market_value.odds_max_age` | M3: free API-Football quotes were 193–283 min old; 240 provisional for shadow; final value from shadow p95 (`docs/ODDS_PROVIDER_VALIDATION.md` §3) |
| K35 | P1 Gov | Official pipeline has no correlated-exposure limit (e.g. 1X2 home + Over 2.5 on one match both become picks; characterised by `test_policy_gap_correlated_selections_on_one_match_are_not_limited`) and allows BTTS although the gate disables BTTS for bankroll picks | `pipeline.py` | policy decision |
| K36 | P1 Gov | BILQE: reliability formula peaks near 50 while unknown leagues default to 65 (passes the 60 gate); no market restriction (55 % corners/cards); empty `team_learning_state` counts as volatility 0; live call passes no odds, so nothing can qualify; A/S unreachable without CLV/ROI history | `qualification_engine.py` | policy decision (`docs/BILQE_DIAGNOSTIC_REPORT.md` §7) |
| K37 | P2 | `/api/execution/opportunities` defaults to simulated odds = model probability + margin + random ±8 % noise, so it can show spurious +EV "opportunities". Labelled `odds_source: simulated` and bookmaker `simulated`; not stored; not used by the frontend | `execution_engine.generate_simulated_odds` | remove or gate in M3 |
| K38 | P2 | `auto_settle_picks` settles only status `FT`; picks on matches finishing `AET`/`PEN` stay pending until voided | `picks_repo.get_unsettled_picks` | M3 |
| K39 | P2 | Pipeline probability inputs read team state through the global `get_db()` connection, not the `conn` passed to `run_pipeline` (same DB in production; matters for isolated runs and tests) | `probability_engine.resolve_probability_inputs` | M3 |
| K43 | P0 Gov | API-Football team/league names do not match stored team-state names (e.g. "La Liga" vs "LaLiga"; club spellings): 56/63 forward candidates had data quality < 60 and fallback inputs, yet legacy grading issued A+ picks on 11–38 % "edges" | `team_stats_db.get_team_stats` | M3.2: identity-v1 golden + inactive `PROBABILITY_INPUTS` switch; 31-case registry proposed. M3.1: root causes found (profile key reused as history key, substring substitution, hash stats, label contamination); team-identity-v1 + `resolve_probability_inputs_v2` in shadow; production default switch awaits approval (`docs/TEAM_IDENTITY_REPORT.md`) |
| K44 | P1 | API-Football free plan: `/odds?league=&season=2026` refused ("Free plans do not have access to this season"); ~10 requests/min (HTTP 429); provider update stamps 3–5 h old | `src/odds/providers/api_football.py` | M3: per-fixture mode default, throttle, errors counted as failures |
| K45 | P1 Gov | After remediation the 28 formerly active leagues fall back to BILQE's unknown-league reliability 65 and pass Layer 3 (before: 26–50, rejected) | `qualification_engine.py` | M3.1: evidence classes + v1.1 proposal (`docs/LEAGUE_EVIDENCE_POLICY_PROPOSAL.md`); BILQE change awaits approval |
| K46 | P1 Gov | `prediction_log` (isotonic calibration source) is 100 % retrospective: 0 of 109,871 rows created before matchday; old isotonic maps e.g. `result` 20 % → 60 % | `isotonic_calibrator.fit_all` | M3: candidate uses documented fallback; activation needs review |
| K47 | P2 | `pytest` from the repo root collects root-level scratch scripts (`test_stats.py`, `test_playwright.py`, …) that call the network at import; run `pytest tests` | repo root | add `testpaths = tests` (needs approval of config change) |
| K48 | P0 Gov | Legacy static-table substring fallback substitutes other teams' statistics ("Rangers"→angers, "Gent"→argentina, "Beitar Jerusalem"→usa, …; 23 of 492 tracked teams) and 317 tracked teams get hash-generated statistics | `team_stats_db._lookup_in_db`, priority 5 | fixed in v2 inputs (shadow); production pending approval |
| K49 | P1 Gov | `team_state` labels have no country: "Premier League", "Championship", "Serie A", "Premiership", "Ligue 1", "Pro League" mix competitions; "Arsenal" history contaminated by another country's club | `team_state` schema | v2 verifies provenance by league id; schema-level fix (competition id) needs approval |
| K50 | P1 | Early season: correctly identified teams have 3–8 recorded matches, so data quality < 60 (formula penalises < 15) | data | policy reality; no change proposed |
| K51 | P1 | Scheduled shadow cycles stop while the Mac sleeps (2026-10-16: 15:48–18:27 UTC lost 1 decision snapshot and all closing captures) | host | use a non-sleeping host or `caffeinate`/wake schedule before a live shadow |
| K52 | P2 | `is_neutral_tournament` treats any label containing "euro" (e.g. "UEFA Europa League") as a neutral venue; legacy avoided it only because the profile key "Champions League" is passed | `team_stats_db.is_neutral_tournament` | v2 keeps legacy behaviour; fix needs approval (changes probabilities) |
| K53 | P1 | `odds_quotes` unique key (fixture, provider, bookmaker, market, selection, line, observed_at) has no price: a changed price under an unchanged provider stamp is silently dropped | `src/odds/store.py` | characterised by a test; fix (add price to the key) needs approval; the timestamp experiment keeps raw responses |
| K54 | P0 Gov | `get_team_state` falls back to an unordered `LIKE` substring match within a label: "Inter" -> Internacional (Brazil), "Chelsea U21" -> Chelsea (18 of 492 tracked teams use it) | `src/db/team_state.py` | identity-v1 reads exact records; legacy path unchanged pending approval |
| K55 | P0 Gov | Production "Arsenal" (Premier League) history: 7 of 8 source fixtures belong to another club named Arsenal (competition 116) | `team_state` | identity-v1 excludes it; per-competition rebuild needed (K49) |
| K29 | P2 | Research pickles in `models/real_data/` were saved with scikit-learn 1.9.0; the lock pins 1.8.0 (`InconsistentVersionWarning`); research/shadow only | `models/real_data/*_calibrator.pkl` | re-export or pin at research time |
| K13 | P2 | `/api/debug/backtest-features` uses current team state (look-ahead); carries an explicit warning | `api/services/debug_service.py` | M8 |
| K15 | P2 | `.gitignore` ignores `*.json`; new JSON artifacts are silently untracked unless whitelisted (`.devcontainer/devcontainer.json`, `model_weights.json` are tracked-but-ignored) | `.gitignore` | narrow the pattern |
| K17 | P2 | `suggested_pick` is randomly shuffled on every request | `match_analysis_service.py:1436` | product decision |
| K18 | P2 | Phase 2 report tables are stale vs committed `backtest_results.json` | `REAL_DATA_BACKTEST_REPORT.md` | regenerate |
| K22 | P2 | Tracked `football_predictor_ai-master.zip` (4.2 MB) contains a 2026-03 snapshot DB (no credentials found) | repo root | owner decision |
| K23 | P2 | Several admin GET endpoints have side effects (e.g. `/api/debug/settle-date/{d}`) | `api/routers/debug.py` | convert to POST later |

## Resolved in M1 / M1.1 / M2 / M2.5

| ID | Resolution |
|---|---|
| K3 | No hard-coded admin key; admin API disabled (503) until `ADMIN_API_KEY` is set; constant-time compare; non-development startup refuses keys < 32 chars |
| K4 | CORS from `CORS_ALLOW_ORIGINS`; development default = localhost/127.0.0.1 on the web port; `*` rejected; non-development requires explicit origins |
| K6 | API-compat tests use offline provider fakes (`tests/fakes/providers.py`); no quota used |
| K7 | Tests run on a temp DB, temp HOME, blank provider keys; a session check refuses the production DB path |
| K8 | 9 weak assertions replaced by exact expectations; integrity audit passes |
| K9 | Python 3.11 env (`.venv311`), pinned `requirements.txt`, full `requirements-lock.txt` (verified to reproduce) |
| K14 | Frontend lint: 13 → 0 errors |
| K21 | CI installs `requirements-lock.txt` on 3.11, runs integrity audit and a frontend lint/build job; verified in clean checkout and in Linux amd64 containers (M1.1) |
| K16 | Research `predictions.db` path overridable (`FOOTBALL_PREDICTOR_SNAPSHOT_DB_PATH`); tests and their subprocesses use a temp file |
| K1 | M2 B: synthetic XGBoost weight 0 in `estimate_probabilities`, removed from BILQE consensus and from the live-odds hybrid blend |
| K2 | M2 B: hash-fallback teams report `matches_played=0`; downstream data quality and low-data safeguards apply |
| K19 | Corrected: pid 65948 is `/Users/nikantehrani/cryptopredic`, not this project (see K28) |
| K24–K26 | M2 A: missing imports fixed (competition tracking, pre-warm, admin date endpoints, logo audit, audit_engine league map) |
| K31 | M2 A follow-up: vote-derived odds snapshots crashed on insert (missing `implied_probability`) since 26a8ba4; fixed |
| K12 | M2.5 `97ef889`: BILQE Layer 3 read league reliability from a dict with `getattr`, so it was always 50 and rejected every pick; Layer 4 passed picks without odds. Both fixed with regression tests (`docs/BILQE_DIAGNOSTIC_REPORT.md`) |
| K40 | M2.5 `927e402`: results verification and settle-date stored 33/33/33 instead of the real 1X2 prediction; fixed (`prediction_from_analysis`). Existing live rows untouched (K32) |
| K41 | M2.5 `61bba8c`: crowd votes were the pipeline's only "odds" (a vote alone produced an A+ pick at "odds 10.0"); `find_value` took the all-time maximum price; Odds API rows never matched or stored; no quota control. Canonical real-odds layer, crowd votes stored separately, budget guard (`docs/REAL_ODDS_INTEGRATION.md`) |
| K42 | M2.5 `65738f0`: every pipeline decision recorded with structured reasons; NO ACTIONABLE BET without fresh real odds |
| K30 | M2.5: golden v3-m2.5 verified in a Linux amd64 python:3.11-slim container (9/9) |

# M3 Report — Data Integrity, Controlled Cutover and Shadow Validation

Branch `m3/data-integrity-shadow`, created from the M2.5 completion commit `fe697d4` (tagged `backup/m2.5/complete`). Local only: nothing was pushed, merged, deployed or frozen, and no model weights were changed.

## 1. Commits

| Commit | Phase | Content |
|---|---|---|
| `a19262b` | B | `src/integrity/`: evidence classification, quarantine (hash-checked move, non-overwriting restore), isolated rebuild; `scripts/remediate_evidence.py` |
| `f48c834` | C | API-Football fixture identity (`/fixtures?date=`), wrong-fixture and moved-kickoff rejection, in-play drop, `fetched_at <= decision time`, quote age on candidates and decisions, freshness buckets, throttle, probe script |
| `d1b0afb` | D | `src/policy/baseline_v1.py` (proposal, inactive); comparison harness |
| `22b8534` | E | Operator service hold on the production DB (`src/ops/service_hold.py`; checked in `get_db()` and at API import) |
| `3889d0a` | F | `src/shadow/` cycle and report, league-date odds mode, provider-error accounting, CLIs, launchd/cron templates (not installed) |
| (docs commit) | G | This report and the five companion documents; `KNOWN_ISSUES.md` K32–K34 updated, K43–K47 added |

Changed production code paths:
- `src/odds/*`
- `src/engine/market_value.py` (adds two candidate fields)
- `src/engine/decisions.py` (adds two columns)
- `src/db/database.py` (hold check)
- `api/main.py` (hold check)

Probabilities, calibration code, selection thresholds and grading are unchanged: golden references 9/9 on 3.11 and 3.13.

## 2. Live operational actions taken

| Time (+03) | Action | Verification |
|---|---|---|
| 15:45 | Identified the football API: pid 94530, `Python 3.13 -m uvicorn api.main:app --port 8001`, cwd = repo, parent = Antigravity language server 36411, DB handles on `engine.db`/`-wal`/`-shm` | `lsof`, `ps`, cwd; no launchd, cron, systemd or supervisor; 65948 is `cryptopredic` (port 8000) |
| 15:45 | Online backup (SQLite backup API, source opened read-only) → `~/Desktop/football_predictor_backups/m3_2026-10-16/engine_pre_m3.db` | integrity `ok`; FK violations 0; 290,793 rows; counts equal to source; restore to scratch: schema identical, counts identical |
| 15:45:56 | `kill -TERM 94530` after checking: no client connections; last writes were the 03:03 nightly refit and an Oct 15 fixture insert, both before the backup | Exited in 2 s; port 8001 free; no DB handles; live `.dump` hash = backup `.dump` hash |
| 16:04 | Created `data/SERVICE_HOLD.json` (`m3-hold-20261016-401a4675`; git-ignored; copy in backup folder) | `uvicorn api.main:app` now exits at import; live DB mtime/size unchanged |
| 15:58–16:20 | API-Football requests on the existing free key, into **isolated DBs only** | 16 OK + 3 × HTTP 429 (probe), 19 + 1 (shadow and diagnostic). Lowest provider-reported remaining: 75/100 |
| 17:47–20:17 | Shadow component-verification cycles every 30 min on the isolated shadow DB (budget 60) | See `SHADOW_VALIDATION_REPORT.md` |

Not touched:
- the Vite dev server (pids 46155/46214, frontend only);
- the Antigravity IDE;
- `cryptopredic`.

Not done: no restart, no production schema change, no production budget, no scheduler installed.

## 3. Data integrity (details: `DATA_INTEGRITY_REMEDIATION.md`)

| | Original | Verified | Quarantined |
|---|---|---|---|
| `prediction_errors` | 11,209 | 77 settled + 107 unsettled | 11,025 (10,808 fabricated, 49 retrospective, 168 unverifiable timing) |
| `prediction_log` | 109,871 | 0 | 109,871 (107,636 retrospective, 2,235 unverifiable) |
| `model_confidence_adjustments` | 412 rows, 28 active | 3 rows, 0 active | originals kept as `*_pre_m3` |
| `calibration_models` | 1,049 rows (10 markets) | 0 (documented shrinkage fallback) | originals kept as `*_pre_m3` |

- The fabricated rows inflated league factors up to 1.37: a 55 % favourite became up to 63 %.
- The old isotonic models were fitted on selection-biased retrospective picks. They mapped `result` 20 % → 60 %.
- A full restore round-trip is byte-identical.
- **None of this is active in production.**

## 4. Real odds (details: `ODDS_PROVIDER_VALIDATION.md`)

| Probe (12 fixtures, 2026-10-17) | Result |
|---|---|
| Fixtures with real quotes | 11 / 12 |
| Bookmakers per model market | 1X2: 6; O/U 2.5: 5; BTTS: 5 |
| Quote age at measurement | 0–60: 0; 61–120: 0; 121–240: 9 (75 %); > 240: 2; none: 1 |
| Coverage within 240 / 120 / 60 min | 75 % / 0 % / 0 % |
| Integrity rejections (wrong fixture / moved kickoff / in-play) | 0 / 0 / 0 |

- Free-plan limits: league+season odds queries are refused for 2026, and the plan allows about 10 requests per minute.
- Freshness recommendation: keep 240 min for shadow only. Set the official window from the shadow 95th percentile.
- The official policy uses two clocks:
  - provider price age ≤ 240 min;
  - our fetch ≤ 60 min before the decision.
- If the 95th percentile exceeds 240 min or coverage stays below 95 %, the free feed cannot support the baseline.

## 5. Selection policy (details: `BASELINE_POLICY_V1_PROPOSAL.md`)

- v1 is implemented and tested. It is not active.
- Simulation:
  - with no model edge, both policies lose (legacy −4.6 %, v1 −4.7 % on 8× fewer picks);
  - with a model edge, v1 beats legacy (+13.3 % vs +10.2 %, 0 % negative-EV selections) and removes correlated same-match picks.
- Real-odds forward sample: legacy would issue 12 A/A+ picks on 6 matches from inputs with data quality < 60. v1 issues 0.

## 6. Python 3.11 and cutover preparation (details: `M3_CUTOVER_RUNBOOK.md`)

| Check | Result |
|---|---|
| Migration on restored copy | Additive only: 11 tables added, 0 removed, no pre-existing table content changed, integrity ok |
| `tests/test_schema_migrations.py` on the real copy | 4/4 on 3.11 and 3.13 |
| Old code (M2 `632f8d7`) on migrated copy | 10/10 smoke endpoints 200, 0 tracebacks (rollback-compatible) |
| M3 code on 3.11, migrated copy | 10/10 smoke endpoints 200, 0 tracebacks; background tasks start (nightly 03:00, hourly refresh, live broadcast) |
| Security | `APP_ENV=production` refuses missing or short `ADMIN_API_KEY` and missing or wildcard CORS; admin endpoint 401 without the key or with a wrong key, 200 with it |
| Restart does not activate the baseline | `/api/baseline/status` → `{"frozen": false}` on restart (3.11 and old code) |
| Odds smoke | Worker dry run on probe copy: 12 targets with identity, 0 requests sent |
| Settlement smoke | `auto_settle_picks` on copy: 1 settled, won, +1.2 u |
| Unexpected restart | Blocked by the service hold (verified) |

## 7. Tests

| Suite | Result |
|---|---|
| Backend, Python 3.11 (`.venv311`, `pytest tests`) | **PASS**: 698 passed, 1 skipped (M2.5: 609/1) |
| Backend, Python 3.13 (`.venv`, `pytest tests`) | **PASS**: 697 passed, 2 skipped (M2.5: 608/2) |
| New M3 tests | 89 across 5 files (evidence 22, odds M3 23, policy v1 25, service hold 6, shadow 13; included in the totals above) |
| Golden v3-m2.5 | **PASS**: 9/9 on 3.11 and 3.13 (probabilities unchanged) |
| Integrity audit | **PASS**: 601 real tests, 0 weak, 2 skipped |
| Migrations (fresh + real-DB copy) | **PASS**: 4/4 on 3.11 and 3.13 |
| Frontend lint / build | **PASS** / **PASS** |
| Linux CI-equivalent (python:3.11-slim) | **BLOCKED**: Docker daemon unavailable (UI running, socket absent, waited 2 min; not restarted) |
| Note | One run accidentally used `pytest` from the repo root. It collected the scratch scripts there; `test_stats.py` made one unauthenticated public SofaScore GET (403) at import. Reruns used `pytest tests` (K47). |

## 8. Shadow validation (details: `SHADOW_VALIDATION_REPORT.md`)

The scheduling, monitoring and reporting components are implemented and tested. **The 14-day shadow period has not started.** Its prerequisites, the cutover and the activated remediation, need approval. A short component-verification run on the isolated shadow DB is reported with its actual period.

## 9. Pending approvals

1. **Data remediation activation** in production (and the "apply" step), including review of the isotonic change and the BILQE side-effect (K45).
2. **Cutover:** Python 3.11 runtime, first live migration (additive), `APP_ENV`/`ADMIN_API_KEY`/CORS, and releasing the hold.
3. **Odds:** API-Football daily budget (free plan; recommended 40–80 depending on whether the API's own auto-refresh runs), and whether to buy a faster provider.
4. **Freshness policy:** confirm 240/60 provisional; final value from shadow data.
5. **Policy v1:** each threshold, the league-qualification route (approved list vs verified record), BILQE unknown-league default, stake rule, and wiring into `run_pipeline` before freezing.
6. **K43 name canonicalisation** between API-Football and team state (changes probability inputs).
7. **Shadow scheduler** installation (launchd or cron) and the shadow DB choice (isolated copy recommended).
8. Linux container run once Docker works.
9. Merge or tag of `m3/data-integrity-shadow`.

## 10. Recommendation

**NO-GO for baseline activation.**

Blocking reasons, each with evidence above:
- The corrected artifacts are not active, and production still holds fabricated-derived adjustments. A restart without remediation would rebuild them at 03:00.
- No verified league track record exists. The largest is 9 settled forward predictions.
- API-Football free quotes meet a 240-minute window for only 75 % of probed fixtures and never meet 120 minutes. The 95 % exit criterion is not shown.
- K43: forward inputs for API-Football fixtures are mostly fallback (data quality < 60). Legacy selection turns them into A+ picks.
- 0 days of the 14-day shadow observation have elapsed.
- The Linux check is blocked.

**GO for the next step:** approve items 1–3 and 6–7. Then run the 14-day shadow and re-evaluate against the exit criteria in `M2_5_REPORT.md` §8 and the freshness rule above.

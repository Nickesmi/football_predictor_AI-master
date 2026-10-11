# Known Issues

Status as of M0 (2026-10-11). Severity: P0 blocks the baseline experiment, P1 must be fixed before production use, P2 hygiene. "Gov" = changes production behaviour and needs owner approval (`baseline_contract.md`).

| ID | Sev | Issue | Where | Planned |
|---|---|---|---|---|
| K1 | P0 Gov | Synthetic-trained XGBoost is blended into live 1X2 / O/U 2.5 / BTTS at weight 0.10–0.25 when data quality ≥ 45 | `src/engine/probability_engine.py:91` | M2 (test ready in `tests/pending_m2/`) |
| K2 | P0 Gov | Hash-fallback (unknown) teams report `matches_played=20`, inflating data quality | `src/ml/team_stats_db.py` Priority 4 | M2 (tests ready in `tests/pending_m2/`) |
| K3 | P0 | Admin key falls back to a hard-coded dev value when `ADMIN_API_KEY` is unset; it is unset in `.env` | `src/config.py:52`, `api/dependencies.py` | M1 |
| K4 | P1 | CORS `allow_origins=["*"]` | `api/main.py` | M1 |
| K5 | P1 Gov | API process refits isotonic calibration and league adjustments nightly (03:00), changing production probabilities automatically | API startup tasks | decide before baseline starts |
| K6 | P1 | `test_api_compatibility.py` is not mocked: each run calls live providers and rewrites the shared `.cache/fixtures-*.json` | `tests/test_api_compatibility.py` | M1 |
| K7 | P1 | Tests call the `get_db()` singleton, which opens `data/engine.db` and runs `init_db()` on it unless `FOOTBALL_PREDICTOR_DB_PATH` is set | `src/db/database.py:92` | M1 (test conftest default) |
| K8 | P1 | CI integrity audit fails: 9 weak assertions (`len(rows) > 0`, `is not None`) | `tests/prediction_service/*` | M1 |
| K9 | P1 | Unpinned dependencies; declared Python 3.11 not installed locally (3.13.2 used) | `requirements.txt`, `runtime.txt` | M1 |
| K10 | P1 | No real CLV anywhere; `odds_snapshots` empty | settlement path | M4 |
| K11 | P1 | Football baseline not started (`picks` 0/1,000); scheduler not installed | crontab / F1 | M3 (owner decision) |
| K12 | P1 | BILQE rejects everything observed so far: 3,650 `qualification_log` rows, all tier C | `qualification_engine.py` | diagnose before M3 |
| K13 | P2 | `/api/debug/backtest-features` uses current team state (look-ahead); now carries an explicit warning | `api/services/debug_service.py` | structural fix with point-in-time live features (M8) |
| K14 | P2 | Frontend lint: 13 pre-existing errors (unused vars, React compiler rules, `process` in vite config) | `frontend/` | M7 |
| K15 | P2 | `.gitignore` ignores `*.json`; new JSON artifacts are silently untracked unless whitelisted | `.gitignore` | narrow the pattern |
| K16 | P2 | Running the audit tests writes `data/real_historical/predictions.db` (git-ignored) | `tests/prediction_service/*` | point to tmp dir |
| K17 | P2 | `suggested_pick` is randomly shuffled on every request | `match_analysis_service.py:1436` | product decision |
| K18 | P2 | Phase 2 report tables are stale vs committed `backtest_results.json`; real-data XGB results drift with library versions | `REAL_DATA_BACKTEST_REPORT.md` | regenerate after pinning |
| K19 | P2 | Running `uvicorn` (pid 65948, started 2026-09-21) predates the session 1–4 fixes now committed | runtime | owner restarts when ready |

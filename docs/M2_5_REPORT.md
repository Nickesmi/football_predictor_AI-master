# M2.5 Report — Operational Readiness, Real Odds Integration, Qualification Audit

Branch `m2.5/operational-readiness`, based on M2 `632f8d7` (tag `backup/m2.5/base`). Local only: nothing was pushed, merged, deployed or restarted. No threshold, model weight or calibration was changed. The live database was read only.

## 1. Part 1: M2 result preserved

- The tree was clean at the start. Golden v1 and v2-m2 are kept byte-identical under `tests/golden/history/` with SHA-256 pins.
- Canonical golden is now **v3-m2.5**. Its only differences from v2 are 22 BILQE qualification values from the documented defect fix, with 0 unintended (`docs/M2_5_GOLDEN_COMPARISON.md`). Match probabilities are identical.
- The freeze implementation is unchanged. Its tests now run with real-shaped mocked bookmaker prices instead of crowd votes (§3).
- Before every commit, `precommit.sh` checked for modifications outside the expected files. None appeared during M2.5.

## 2. Commits

| Commit | Change | Regression evidence |
|---|---|---|
| `97ef889` fix(bilqe) | Layer 3 read reliability from a dict with `getattr` and always got 50 (every pick tier C). Layer 4 passed picks without odds (score 50). Structured per-layer outcomes and `rejected_picks`. | 6 tests, all failing on the old code; golden v3 |
| `927e402` fix(data) | Results verification, settle-date and analysis outcome logging stored 33/33/33 (they read a nonexistent key). `prediction_from_analysis` never defaults. | 3 tests; the old code stored (33,33,33) against the real (48.3,26.6,25.1) |
| `61bba8c` feat(odds) | Canonical real-odds layer. Crowd votes separated. Budget guard. Fresh latest prices only. Legacy fetcher guarded. Odds provenance on picks. | 45 tests; the old code gave an **A+ pick from crowd votes alone** |
| `65738f0` feat(pipeline) | `selection_decisions`: NO ACTIONABLE BET / PICK / REJECTED with structured reasons. Same pass/fail logic. | 8 chain tests |
| `93a8b28` feat(odds) | Budgeted `odds_worker.py` with `--dry-run`; league-id scheme per provider; one match stored by two feeds gets the same quotes. | 3 tests |
| (this commit) docs | Reports and `scripts/diagnose_bilqe.py`. | — |

## 3. Corrected defects

1. **BILQE always tier C:** a code defect, not restrictive thresholds (`docs/BILQE_DIAGNOSTIC_REPORT.md`).
2. **BILQE qualified without odds:** a no-odds pick could score into Tier B on probability alone.
3. **Fabricated outcome records:**
   - The code is fixed.
   - The live table still holds **10,808 fabricated rows out of 11,209**.
   - League reliability, the live league adjustments for 7 leagues, and the running app's nightly refit are all computed from these rows.
4. **Crowd votes treated as bookmaker odds.**
   - The official pipeline had no other odds source.
   - In the live app this stayed hidden only because the vote insert crashed (K31).
   - M2's K31 fix made the insert work, so the next pipeline run would have created picks from crowd votes. M2.5 removes this path entirely.
5. **Value from stale or outlier prices** (`max` over all rows and all time). Odds API rows could never match or be stored. No quota control.

## 4. Part 2–4 findings

- **BILQE funnel** (52 matches, 4,134 candidates; full table in the diagnostic report):
  - without real odds, 0 qualify;
  - with a genuine 6 pp edge, 2,207 reach Tier B and none reach A or S;
  - L3 legitimately rejects 968 candidates in 5 weakly measured leagues;
  - L1 rejects 145 candidates (2 low-data matches).
- **Real odds:**
  - The Odds API: no key, nothing requested.
  - API-Football: the existing free key was verified with 1 request (93/100 left that day). It serves 2026 pre-match prices from about 10 real bookmakers, and the parser produced 3,269 canonical quotes from one page.
  - API-Football odds carry no team names, so they cannot yet reach official SofaScore fixtures (K33).
  - Expected The Odds API usage is 360–5,000 credits per month depending on schedule. Paid tiers need approval.
- **Full chain verified** with explicitly synthetic prices in isolated DBs:
  - missing or stale odds give NO ACTIONABLE BET, and the forecast is kept;
  - markets the model does not price are ignored;
  - positive probability without edge is REJECTED NO_POSITIVE_EDGE;
  - small edge is REJECTED EDGE_BELOW_LEAGUE_MINIMUM;
  - decisions reproduce exactly from identical inputs;
  - a shadow pick settles from the final score and never counts toward the baseline;
  - synthetic prices are labelled on display and refused outside isolated test DBs.
- **Policy gaps, reported and not changed:**
  - no correlated-exposure limit (two picks on one match; K35);
  - BTTS allowed in official picks;
  - BILQE reliability scale vs the 65 default, no market restriction, volatility 0 from an empty table (K36);
  - `/api/execution/opportunities` simulated odds with random noise (K37).

## 5. Part 6: verification (all isolated from the live DB and the network)

| Check | Result |
|---|---|
| Python 3.11 full suite | **609 passed, 1 skipped** |
| Python 3.13 full suite | 608 passed, 2 skipped |
| Golden v3-m2.5 | 9/9 on 3.11 and 3.13 |
| Migrations, fresh + copy of real pre-M0 DB | 4/4 (new tables additive, row counts unchanged) |
| API compatibility (offline provider fakes, admin smoke, frontend contract) | included in suite, pass |
| Odds provider mock tests | 48/48 |
| BILQE regression | 6/6 |
| Freeze integrity (tamper, model-file, runtime mismatch, provenance, reproducibility) | pass |
| End-to-end shadow prediction and settlement | pass (`test_decision_chain.py`) |
| Integrity audit | PASS, 0 weak tests |
| pyflakes undefined-name guard | pass |
| Frontend lint / build | PASS / PASS |
| Linux CI-equivalent (python:3.11-slim amd64, lock install) | **591 passed, 19 skipped**; golden 9/9; integrity audit PASS. The 19 skips are identical on a macOS run of the same `git archive` tree: 18 repo-hygiene tests need `.git` and 1 needs `M0_EXISTING_DB_COPY`. No platform drift. |
| Live DB after all work | schema == pre-M0; `picks` 0 |

## 6. Part 5: operations

PID 94530 is uvicorn on Python 3.13, launched by an Antigravity agent session at 2026-10-11 18:52 from the pre-M2 C tree, with no supervisor. It holds `data/engine.db` read-write.

**Its 03:00 nightly refit last rebuilt live league adjustments and 1,013 calibration rows from the fabricated outcome data at 2026-10-12 00:13 UTC, and will do so every night until it is stopped.** Shutdown and controlled-restart procedures are in `docs/OPERATIONAL_READINESS.md`. It was not stopped.

## 7. Decisions needed before M3

1. **Stop PID 94530 now** (§2 of the operational doc). Otherwise it keeps refitting from fabricated data nightly.
2. **Fabricated `prediction_errors` rows (K32):** quarantine (recommended: move to `prediction_errors_quarantine` and back up first), then recompute league adjustments **in shadow** and review before any live use.
3. **Odds provider and budget (K33):**
   - (a) API-Football free key: no cost; needs a teams lookup to map onto official fixtures (M3 work);
   - (b) The Odds API: free tier for one daily sweep, or a paid plan.

   Set `ODDS_REQUEST_BUDGET_<PROVIDER>`.
4. **Freshness window (K34):** `ODDS_MAX_AGE_MINUTES` (60 placeholder) consistent with the provider's refresh cycle.
5. **Selection policy (K35):** a correlated-exposure limit (e.g. at most one pick per match), and BTTS in official picks.
6. **BILQE policy (K36):** reliability scale vs the unknown-league default, market restriction, missing volatility, and whether BILQE consumes real odds.
7. **Live schema change at the first restart** on M2.5 code (additive tables), plus runtime, freeze and `APP_ENV`/`ADMIN_API_KEY` choices.

## 8. M3 readiness recommendation

**Not ready to begin the 1,000-pick baseline. Ready to begin an M3 "data and operations" phase.**

Evidence:
- The pipeline can now only make a pick from a fresh real bookmaker price, and no real price is collected. A frozen baseline started today would produce **zero** picks.
- League adjustments that feed live probabilities are built on 96 % fabricated outcomes. Freezing now would freeze those parameters.
- The running app is on unreviewed, pre-freeze code and mutates calibration nightly.
- On the positive side: the code-level blockers found in M2.5 are fixed and tested. The odds layer is complete and offline-tested. A free real-odds source is verified. Every decision is now auditable.

Proposed M3 sequence, each step needing approval:
1. Stop the running app; back up; quarantine the fabricated rows.
2. Pick the provider and budget. Add the API-Football teams lookup if chosen. Install the scheduler for fixtures and odds only.
3. Run **unfrozen shadow for about 14 days**. Collect real odds and `selection_decisions`; count nothing.
4. Freeze `football-v1.0` and activate F1 only when these exit criteria hold:
   - at least 95 % of tracked fixtures mapped to real quotes within the freshness window;
   - 0 integrity violations;
   - recomputed league adjustments reviewed;
   - policy decisions 5–6 made;
   - Python 3.11 cutover done.

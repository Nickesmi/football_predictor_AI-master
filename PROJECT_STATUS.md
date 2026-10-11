# Project Status

Updated 2026-10-11 after M0. Integration branch: `integration/m0-consolidation` (local). Details: `docs/M0_CONSOLIDATION_REPORT.md`; open issues: `KNOWN_ISSUES.md`.

## Governance counters (live DB, read-only)

| Baseline | Contract | Settled |
|---|---|---|
| Football v1.0 | `baseline_contract.md` | 0 / 1,000 (not started) |
| Tennis v1.0-elo | `tennis_baseline_contract.md` | 16 / 500 |

## Components

| Component | State | Evidence |
|---|---|---|
| FastAPI app (routers/services) | Implemented, tested | 440 backend tests pass |
| Production probability path (Poisson + DC + synthetic-XGB blend + shrink) | Implemented; frozen by golden test; never validated on real out-of-sample data | `tests/golden/` |
| Gated pipeline (picks, settlement, void, experiment counter) | Implemented, tested; not scheduled | session 1–4 tests |
| Four error/team-learning tables | Schema now in code | `tests/test_schema_migrations.py` |
| Real-data research pipeline (openfootball 10,657 matches, walk-forward, champion registry, shadow) | Implemented, tested offline; not wired to the app | `tests/prediction_service`, `tests/real_data` |
| Tennis engine | Implemented, collecting | 16 settled |
| Frontend | Builds; 13 pre-existing lint errors | `vite build` |
| Scheduler / F1 activation | Not installed / not activated | owner decision |
| Security hardening | Not done | K3, K4 |

## Next milestones

1. **M1** — security and reproducibility: required admin key, CORS from env, pinned deps on Python 3.11, mocked API-compat tests, tests default to a temp DB, fix 9 weak assertions.
2. **M2** (governed) — synthetic-XGB weight 0 and hash-fallback `matches_played=0`; move `tests/pending_m2` into the main suite and regenerate the golden file in the same commit.
3. **M3** (governed) — choose the v1.0 baseline model, decide on the nightly refit (K5), diagnose all-tier-C BILQE (K12), install scheduler, activate F1.

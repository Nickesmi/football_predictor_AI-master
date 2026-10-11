# Project Status

Updated 2026-10-11 after M1.1. Branches (local): `integration/m0-consolidation` → `m1/security-reproducibility` → `m1.1/stabilization`. Details: `docs/M0_CONSOLIDATION_REPORT.md`, `docs/M1_REPORT.md`, `docs/M1_1_REPORT.md`; open issues: `KNOWN_ISSUES.md`.

## Governance counters (live DB, read-only)

| Baseline | Contract | Settled |
|---|---|---|
| Football v1.0 | `baseline_contract.md` | 0 / 1,000 (not started) |
| Tennis v1.0-elo | `tennis_baseline_contract.md` | 16 / 500 |

## Components

| Component | State | Evidence |
|---|---|---|
| FastAPI app (routers/services) | Implemented, tested | 473 tests pass on 3.11 (canonical, incl. Linux amd64 with no network) and 3.13 |
| Production probability path (Poisson + DC + synthetic-XGB blend + shrink) | Implemented; frozen by golden test; never validated on real out-of-sample data | `tests/golden/` |
| Gated pipeline (picks, settlement, void, experiment counter) | Implemented, tested; not scheduled | session 1–4 tests |
| Four error/team-learning tables | Schema now in code | `tests/test_schema_migrations.py` |
| Real-data research pipeline (openfootball 10,657 matches, walk-forward, champion registry, shadow) | Implemented, tested offline; not wired to the app | `tests/prediction_service`, `tests/real_data` |
| Tennis engine | Implemented, collecting | 16 settled |
| Frontend | Builds; lint clean | `vite build`, `eslint` |
| Scheduler / F1 activation | Not installed / not activated | owner decision |
| Security hardening | Admin key required, CORS restricted, startup validation | `tests/test_security_config.py` |
| Reproducible env | Python 3.11 canonical; lock verified in clean checkout and Linux; golden versioned per runtime | K20 |
| Baseline freeze | Designed, not implemented | `docs/BASELINE_FREEZE_DESIGN.md` |

## Next milestones

1. **M2** (`docs/M2_PLAN.md`) — A: operational bug fixes (K24–K26); B: governed probability fixes (K1, K2) with golden diff; C: freeze infrastructure (switch off); D: runtime cutover preparation.
2. **M3** (governed) — diagnose all-tier-C BILQE (K12), freeze `football-v1.0`, cut over to 3.11, install scheduler, activate F1.

# Data Integrity Remediation (M3 Phase B)

Branch `m3/data-integrity-shadow`, commit `a19262b`. Code: `src/integrity/evidence.py`, `src/integrity/rebuild.py`, `scripts/remediate_evidence.py`. Tests: `tests/test_evidence_integrity.py` (22).

**Status:** the reconstruction was done on an isolated copy of the verified backup. The live database was **not modified**. Activating the corrected artifacts in production needs owner review (see §8).

## 1. Inputs

| Item | Value |
|---|---|
| Source | `~/Desktop/football_predictor_backups/m3_2026-10-16/engine_pre_m3.db` (online backup of `data/engine.db`, 2026-10-16 15:45 +03) |
| SHA-256 | `37a98bb66ec3e5a8f10643ac2859a19f7a1cff74413992faf6b4a3b7d6809ad6` (re-verified after the remediation; unchanged) |
| Candidate DB | `~/Desktop/football_predictor_backups/m3_2026-10-16/remediation_candidate_v1/engine_m3_candidate.db` |
| Machine-readable result | `…/remediation_candidate_v1/rebuild_report.json` |
| Classifier | `m3-evidence-v1` |

Reproduce:
```bash
.venv/bin/python scripts/remediate_evidence.py \
  --source ~/Desktop/football_predictor_backups/m3_2026-10-16/engine_pre_m3.db \
  --out    <new empty directory>
```
The script refuses `data/engine.db` as the source, refuses a non-empty output directory, and refuses to run with `BASELINE_FREEZE` set.

## 2. Which artifacts depend on which evidence

| Artifact | Built from | Used by |
|---|---|---|
| `model_confidence_adjustments` | `prediction_errors` (`error_intelligence.rebuild_confidence_adjustments`) | 1X2 shrink in match analysis (`apply_league_adjustment`); BILQE Layer 3 league reliability; `performance_gate` |
| `calibration_models` (isotonic) | `prediction_log` (`IsotonicCalibrator.fit_all`) | Market probabilities in `classic_prediction` and the analysis service |
| `pick_calibrator` | settled `picks` (0 rows) | Official pipeline (unfit; unchanged) |
| `league_profiles` | Hard-coded seeds (`database._seed_league_profiles`) | Official pipeline risk control and grading |

The nightly refit (03:00 local) rebuilt both of the first two artifacts. It last ran on **2026-10-16 00:03:48 UTC** before the app was stopped. That run is included in the backup.

## 3. Classification rules (independent of the M2.5 audit)

A row is **verified** only when every condition holds:

1. **Real probability.** It is not the 33/33/33 "home" placeholder.
2. **Provably pre-match.** `created_at` is SQLite UTC, and fixture times are naive local times (the app uses Iran Standard Time, +03:30). The row counts as pre-match only if `created_at <= naive kickoff − 3h30`, which is safe for any offset from UTC to UTC+03:30. When no kickoff is known, the row must be created before the local matchday began.
3. **Internally consistent.**
   - The probabilities sum to 100 ± 1.5.
   - `predicted_result` is the argmax.
   - The result agrees with the goals.
   - `correct` agrees with prediction and result.
4. **First record of the match.** A later duplicate would double count.

Classes:
- **FABRICATED_PLACEHOLDER**
- **RETROSPECTIVE**: created after kickoff under every offset.
- **UNVERIFIABLE_TIMING**
- **DUPLICATE**
- **INCONSISTENT**
- **VERIFIED_SETTLED / VERIFIED_UNSETTLED**

Missing evidence is never treated as verified.

## 4. Original versus classified counts

### `prediction_errors` (11,209 rows)

| Class | Rows | Notes |
|---|---|---|
| FABRICATED_PLACEHOLDER | **10,808** | Exactly 33.0/33.0/33.0 with "home". 10,807 settled. 10,804 were created in the same minute as their settlement (written at verification time, not before the match). No real-probability row is uniform. This **confirms the M2.5 count independently.** |
| RETROSPECTIVE | 49 | Real probabilities, but created after kickoff |
| UNVERIFIABLE_TIMING | 168 | 139 have no kickoff time and were created on matchday; 29 were created within 3h30 of the naive kickoff |
| DUPLICATE | 0 | The 8 duplicate real-row groups all fall into the timing classes above |
| INCONSISTENT | 0 | |
| **VERIFIED_SETTLED** | **77** | Spread over 36 leagues. The most in any league is 9 (USL League Two). |
| VERIFIED_UNSETTLED | 107 | Real pre-match forecasts with no result. They are not evidence of accuracy. |

### `prediction_log` (109,871 rows; the isotonic calibration source)

| Class | Rows |
|---|---|
| RETROSPECTIVE | **107,636** |
| UNVERIFIABLE_TIMING | 2,235 |
| VERIFIED (any) | **0** |

`prediction_log` match dates run from 2014-06-18 to 2026-08-30. Every row was created between 2026-06-04 and 2026-08-31, and none was created before its matchday. These rows are backfilled or replayed predictions. They are not fabricated, but nothing proves they were made without knowledge of the result. The model state used to replay them may already include the match. They are therefore unverifiable as forward evidence.

## 5. Quarantine

- `evidence_classification`: one row per source row (table, original PK, class, reason, classifier version, time).
- `evidence_quarantine`: **120,896 rows**, every non-verified row. Each row stores:
  - source table and original primary key;
  - class and reason;
  - the full original row as JSON and its SHA-256;
  - `quarantined_at`, `moved_at` and `restored_at`.
- The move deletes a source row only if its current content still hashes to the quarantined copy. Otherwise nothing moves.
- In the candidate DB, 11,025 `prediction_errors` rows and 109,871 `prediction_log` rows were moved. 184 `prediction_errors` rows remain: 77 verified settled and 107 verified unsettled.
- Original artifacts are preserved as `model_confidence_adjustments_pre_m3` (412 rows) and `calibration_models_pre_m3` (1,049 rows) inside the candidate DB. The untouched backup also holds them.

### Recovery instructions
```python
import sqlite3
from src.integrity import evidence
conn = sqlite3.connect("<db with evidence_quarantine>")
evidence.restore_quarantined(conn, "prediction_errors")       # or "prediction_log"; pks=[...] for a subset
```
Restores reinsert with the original primary keys. A restore refuses to overwrite a different row that has taken the same id. **Verified:** a full restore on a copy of the candidate gave tables byte-identical to the backup, for both `prediction_errors` and `prediction_log`.

## 6. Rebuilt artifacts (production builders, verified evidence only)

### League adjustments

| | Before | After |
|---|---|---|
| League rows | 412 | 3 (only leagues with at least 5 verified settled rows) |
| Active adjustments (≥ 50 samples) | 28 | **0** |
| Status "developing" | 7 | 0 |

The fabricated placeholder always predicts "home" at 33 %, and home teams win about 44 % of the time. The builder therefore read every league as **underconfident** and **expanded** probabilities away from 33 %. Effect on a raw 55/25/20 forecast:

| League | Samples (old → new) | Factor (old → new) | 55/25/20 becomes (old) | New | Old hit rate | Old reliability |
|---|---|---|---|---|---|---|
| Friendlies Clubs | 1,552 → 0 | 1.3305 → none | 62.2/22.2/15.6 | 55/25/20 | 43.9 % | 49.5 |
| USL League Two | 432 → 9 | 1.2296 → 1.0 (inactive) | 60.0/23.1/16.9 | 55/25/20 | 43.8 % | 46.8 |
| Premier League | 254 → 0 | 1.1693 → none | 58.7/23.6/17.7 | 55/25/20 | 40.9 % | 38.2 |
| 1. Division | 106 → 0 | 1.1106 | 57.4/24.1/18.5 | 55/25/20 | 40.6 % | 30.7 |
| Super League | 105 → 0 | 1.2504 | 60.4/22.9/16.7 | 55/25/20 | 49.5 % | 28.4 |
| International Friendly Games | 102 → 7 | 1.2653 → 1.0 (inactive) | 60.7/22.8/16.5 | 55/25/20 | 53.9 % | 28.2 |
| MLS Next Pro | 102 → 0 | 1.3707 | 63.0/21.9/15.1 | 55/25/20 | 57.8 % | 31.2 |
| 21 further leagues (50–97 samples, weight 0.25) | → 0 | 0.9867–1.1709 | ±0–3.7 pp on the favourite | 55/25/20 | | |

The full per-league table is in `rebuild_report.json` → `league_comparison`.

**Behavioural side-effect to review.** BILQE Layer 3 uses a league's measured reliability only when its adjustment is active, and otherwise uses the "unknown league" default of 65. All 28 formerly active leagues had fabricated-data reliability between 26 and 50, below the Layer 3 gate of 60, so BILQE rejected them. After remediation they become inactive and fall back to 65, which **passes** the gate. Remediation alone therefore makes BILQE more permissive for those leagues. Missing evidence would act as a pass. Policy v1 does not accept that (it requires an owner-approved league or a verified record; see `BASELINE_POLICY_V1_PROPOSAL.md`). The BILQE default itself needs an owner decision (M2.5 K36).

### Isotonic calibration

0 verified rows means no model meets `MIN_SAMPLES_FOR_FIT = 200`. All 10 market models disappear and the documented fallback applies. Calibrated output at sample raw probabilities (old retrospective model → fallback `0.85·p + 0.15·50`):

| Market (old samples) | raw 20 | raw 35 | raw 50 | raw 65 | raw 80 | raw 90 |
|---|---|---|---|---|---|---|
| result (10,970) | 60.0 → 24.5 | 60.0 → 37.2 | 60.0 → 50.0 | 60.5 → 62.8 | 92.0 → 75.5 | 92.0 → 84.0 |
| goals (11,050) | 39.7 → 24.5 | 39.7 → 37.2 | 39.7 → 50.0 | 71.4 → 62.8 | 92.0 → 75.5 | 92.0 → 84.0 |
| btts (10,976) | 56.6 → 24.5 | 56.6 → 37.2 | 56.6 → 50.0 | 77.7 → 62.8 | 82.2 → 75.5 | 82.2 → 84.0 |
| team_goals (10,973) | 68.4 → 24.5 | 68.4 → 37.2 | 68.4 → 50.0 | 73.0 → 62.8 | 92.0 → 75.5 | 92.0 → 84.0 |
| handicap (10,972) | 70.0 → 24.5 | 70.0 → 37.2 | 70.0 → 50.0 | 70.0 → 62.8 | 82.0 → 75.5 | 82.0 → 82.0 |
| half (10,532) | 61.5 → 24.5 | | | 61.5 → 62.8 | 89.8 → 75.5 | 92.0 → 84.0 |
| combo (10,967) | 21.9 → 24.5 | 55.5 → 37.2 | 61.7 → 50.0 | 66.2 → 62.8 | 70.7 → 75.5 | 70.7 → 84.0 |
| corners (3,176) / cards (3,179) | 61.3 / 57.4 → 24.5 | | | | 65.5 / 61.4 → 75.5 | |
| cs (11,003) | 20.0 at every input | | | | | |

The old models are step functions fitted on a selection-biased sample: `prediction_log` stores only offered picks, which are mostly high-probability. They map a 20 % input to 60 % for `result` and flatten most of the range. This is a large change to displayed and analysis probabilities. **It needs review before activation.** It does not touch model weights or the official pipeline's own `pick_calibrator`.

## 7. Conservative fallback (explicit)

| Situation | Fallback |
|---|---|
| League with fewer than 50 verified settled predictions | No league adjustment (raw probabilities) |
| League with no verified record | No row; never "reliable" |
| Market without a fitted isotonic model | `IsotonicCalibrator.calibrate`: `0.85·p + 0.15·50`. `classic_prediction`: `0.80·shrunk + 0.20·50` (existing code) |
| Official selection | Policy v1: the league must be owner-approved or have ≥ 100 verified settled predictions with reliability ≥ 60 |

## 8. Activation (not done; needs owner review)

1. Review §6, in particular the BILQE side-effect and the isotonic change.
2. With the app stopped and a fresh online backup taken, run the same steps on `data/engine.db`:
   - classify;
   - quarantine;
   - archive the artifacts as `*_pre_m3`;
   - move (hash-checked);
   - rebuild.

   That needs a small "apply" entry point (the script currently refuses the live DB by design).
3. Or copy the reviewed candidate tables into production by an approved procedure.
4. Restart only after the activation is verified, so the nightly refit runs on verified data. Otherwise the first 03:00 refit rebuilds the old artifacts from the fabricated rows.

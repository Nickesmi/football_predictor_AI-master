# Shadow Settlement Results — six forward observations of 2026-10-16 (M3.2, Decision 1)

Tool: `scripts/shadow_settle.py` (commit `28bb9d3`). Data:
- `~/Desktop/football_predictor_backups/m3_2026-10-16/m3_2/shadow_settlement.json`
- `m3_2/shadow_report_after_settlement.json`
- pre-settlement backup: `shadow/engine_shadow.pre_settlement.db`

## Request

| Item | Value |
|---|---|
| Requests sent | **1**: `GET /fixtures?date=2026-10-16`, 2026-10-17 15:33 UTC, HTTP 200 |
| Key | the existing API-Football key (configured; not printed) |
| Quota | free plan; the provider reported **99 remaining** after the request (new UTC day) |
| Paid service | none |
| Database written | the isolated shadow DB only. `data/engine.db` untouched (mtime 2026-10-16 15:35, before the stop) |

## Settlement

Only fixtures with final status FT/AET/PEN and a regulation-time score (`score.fulltime`) are settled. Nothing was postponed, abandoned or unresolved.

| Fixture | Kickoff (UTC) | Status | Score | 1X2 forecast H/D/A | Most likely | Result |
|---|---|---|---|---|---|---|
| Çorum FK v Rizespor | 17:00 | FT | 2–1 | 51.1 / 26.5 / 22.4 | home | home ✔ |
| Eintracht Frankfurt v 1. FC Köln | 18:30 | FT | 1–0 | 48.0 / 23.9 / 28.1 | home | home ✔ |
| Frosinone v Sassuolo | 18:45 | FT | 4–2 | 48.2 / 28.4 / 23.5 | home | home ✔ |
| Le Mans v Toulouse | 18:45 | FT | 0–3 | 44.2 / 26.4 / 29.4 | home | away ✘ |
| Burnley v Wolves | 19:00 | FT | 1–2 | 47.7 / 28.1 / 24.1 | home | away ✘ |
| Deportivo La Coruna v Levante | 19:00 | FT | 1–1 | 34.2 / 27.2 / 38.6 | away | draw ✘ |
| Heerenveen v Excelsior (not observed: host asleep) | 18:00 | FT | 4–0 | — | — | outcome stored, not scored |

- **Settled observations:** 6 of 6. Unsettled: 0.
- **Outcome records:** 7 (one fixture had no observation).

## Scoring (n = 6; forecasts used the legacy inputs of `b0d4e7a`)

| Market | Brier | Log loss | Reference |
|---|---|---|---|
| 1X2 | 0.602 | 1.014 | uniform 1/3 each: Brier 0.667, log loss 1.099 |
| O/U 2.5 | 0.316 | 0.828 | 50/50: Brier 0.250, log loss 0.693 |
| BTTS | 0.306 | 0.806 | 50/50: Brier 0.250, log loss 0.693 |

- 1X2 most-likely outcome correct: 3/6.
- Reproducibility: 6/6 forecasts recomputed exactly from their stored inputs.
- Six matches carry no statistical weight. These numbers only show that settlement and scoring work end to end.

## Would-be selections

| Policy | Selections | Result | Flat P&L | CLV |
|---|---|---|---|---|
| Legacy (would-be picks, not stored) | 2: Çorum v Rizespor under 2.5 @ 2.44 and BTTS no @ 2.45 (same match, both A+) | both lost (3 goals, both teams scored) | −2.0 u | not available |
| v1 / v1 frozen-assumed | 0 | — | — | — |
| v1.1 / v1.1 frozen-assumed | 0 | — | — | — |
| BILQE | 0 qualified | — | — | — |

CLV is undefined: no later pre-kickoff price was captured, because the host was asleep during the closing windows.

## Observation kinds

| Kind | Count |
|---|---|
| Genuine forward observations (recorded before kickoff, before results existed) | 6 |
| Settled from verified final results | 6 |
| Synthetic | 0 |
| Retrospective (the M3.1 replay, stored separately) | not counted |

None of this counts toward the 1,000-pick baseline (`counts_toward_baseline` is CHECK-constrained to 0).

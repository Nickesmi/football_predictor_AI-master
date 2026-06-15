# TENNIS BASELINE CONTRACT
# ========================

## Status

```text
WAITING FOR 500 SETTLED TENNIS PREDICTIONS

Settled Predictions: 0 / 500

Calibration: NOT AUTHORIZED
```

---

## Branch

```text
git branch: tennis-prediction-engine
```

---

## Model Version

```text
v1.0-elo
```

## Architecture

```text
Observable Layer           Warehouse Layer
(LIVE UI only)             (Settlement only)

RapidAPI SofaScore    ══════════════════════
  LIVE status              Daily Refresh
  Live score               FT status
  Live minute              tennis_results
  ↓                        ↓
  UI display only          Settlement
  NEVER settles            ALWAYS settles
```

---

## Approved V1 Features

```text
✓ Surface-adjusted Elo    (primary signal)
✓ Ranking difference
✓ Last 5 win rate
✓ Last 10 win rate
✓ Surface win rate
✓ H2H win rate
✓ Fatigue (7-day)
✓ Tournament context
```

---

## Deferred Features (require 500 settled picks + evidence)

```text
✗ Ace rate
✗ Double fault rate
✗ First-serve win %
✗ Break point conversion
✗ Return games won %

See: research_backlog/candidate_tennis_serve_features.md
```

---

## Governance Rules

```text
1. No automatic recalibration before 500 settled tennis predictions.

2. No tennis model promoted to production without baseline comparison.

3. Tennis metrics are NEVER mixed with football metrics.

4. Football baseline_contract.md is untouched by this experiment.

5. RapidAPI SofaScore is LIVE UI ONLY — never used for settlement.

6. Settlement source must always be 'daily_refresh'.
   Any other settlement source is a governance violation.
```

---

## Evaluation Metrics (tracked, not yet acted upon)

```text
Brier Score     (target: < 0.22)
ECE             (target: < 0.05)
Hit Rate        (target: > 55%)
ROI             (measured against fair odds)
```

---

## Acceptance Rule (post 500 picks)

```text
Candidate model (e.g., +serve features) BEATS this baseline
on ALL of:
  - Brier Score
  - ECE
  - Hit Rate

AND preserves:
  - Pick Volume (not shrinking too many picks)
  - Calibration (no degradation)

THEN: candidate is promoted.

Otherwise: baseline survives.
```

---

## Timeline

```text
Created:   2026-06-15
Branch:    tennis-prediction-engine
Locked:    YES — no changes to model or features without evidence
Next check: 500 / 500 Settled Predictions
```

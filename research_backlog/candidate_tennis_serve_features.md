# Candidate: Tennis Serve/Return Feature Engineering

## Status

```text
🛑 NOT AUTHORIZED

Research Sprint: NOT STARTED

Requires: 500 settled tennis predictions
```

---

## Candidate Features

```text
Ace rate (per service game)
Double fault rate
First-serve win %
Break point conversion %
Return games won %
Service games held %
```

---

## Baseline Required

```text
v1.0-elo baseline must be frozen at 500 settled predictions
with measured Brier, ECE, Hit Rate, and ROI.
```

---

## Evaluation

```text
Candidate must beat baseline on:
  - Brier Score
  - ECE
  - Hit Rate

Without degrading:
  - Pick Volume
  - Calibration

Out-of-sample only.
No backtests.
```

---

## Why Deferred

```text
Serve statistics require:
  - At minimum 50 matches per player per surface for signal stability
  - Provider with structured stat access (not currently available in V1)
  - Proven need from baseline weakness

Adding serve stats now would:
  - Inflate complexity without evidence it's needed
  - Risk overfitting on thin data samples
  - Violate the "minimum viable signal" principle
```

---

## When to Re-Evaluate

```text
After 500 settled predictions:
  1. Inspect Brier Score breakdown by confidence tier
  2. If LOW confidence picks are mis-calibrated → serve stats candidate
  3. If HIGH confidence picks are well-calibrated → serve stats not needed
```

---

## Created

```text
Date: 2026-06-15
Author: governance process
Branch: tennis-prediction-engine
```

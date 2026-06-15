# Quantitative Engineering: v1.0 Baseline Contract

## CURRENT STATUS

```text
Infrastructure:
COMPLETE

Observability:
COMPLETE

Recovery:
COMPLETE

Live Validation:
WAITING FOR VALID API KEY

Settled Picks:
0 / 1000

Research Sprint:
NOT AUTHORIZED
```

## THE GOLDEN RULE
> [!CAUTION]
> If Settled Picks < 1000:
>
> **Close the IDE.**
>
> Do not:
> - retrain models
> - add features
> - tune hyperparameters
> - change ensembles
> - add filters
> - alter production probabilities
>
> Collect evidence. Nothing else.

## Baseline Status
- **Current State**: `WAITING FOR 1,000 SETTLED PICKS`
- **Pick Count**: `[TO BE UPDATED BY PRODUCTION AUDIT]`
- **Evaluation Sample**: Matches `#1` to `#1000`

## Core Metrics (To Be Frozen)
When 1,000 settled picks are reached, this section will be permanently frozen with the exact metrics:
- **ROI**: `TBD`
- **CLV (Closing Line Value)**: `TBD`
- **Brier Score**: `TBD`
- **Calibration Error (ECE)**: `TBD`
- **Hit Rate**: `TBD`
- **Pick Volume**: `TBD`
- **Maximum Drawdown**: `TBD`

## Governance & Rules
1. **The Frozen Sample**: The first 1,000 matches belong exclusively to the v1.0 Baseline. 
2. **Data Leakage Prevention**: No future candidate model (e.g., xG models, FootballBin ensembles) may be trained on the baseline sample. Candidate models must be trained on separate time-series splits or strictly tested on match `#1001` and beyond.
3. **The Acceptance Threshold**: A candidate must beat the Baseline ROI and Brier Score without materially decreasing volume or worsening the Maximum Drawdown. If a clever idea fails to beat this contract, it is rejected.
4. **Research Backlog**: All unproven ideas must reside in `/research_backlog/`. No code changes to the production prediction engine may occur until the candidate strictly passes the Acceptance Threshold.

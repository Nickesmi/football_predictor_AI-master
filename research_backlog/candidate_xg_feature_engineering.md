# Candidate: xG Feature Engineering & FootballBin Advisory Signal

## Current Status
- **Status**: FUTURE RESEARCH CANDIDATE (REJECTED FOR CURRENT IMPLEMENTATION)
- **Constraint**: No retraining or code injection is allowed until the v1.0 baseline contract is frozen (requires 1,000 settled picks).

## Description
This candidate aims to enhance the mathematical prediction model's accuracy by:
1. **Expected Goals (xG)**: Injecting historical rolling `xg` and `xga` (Expected Goals Against) from the live `team_state` DB into the XGBoost feature builder (`MatchFeatures`). This aims to reduce variance/luck from raw goals scored.
2. **FootballBin Advisory Signal**: Using the `footballbin-predictions` API to fetch secondary predictions (scores, corners, next goal) to act as an expert advisory layer, specifically for top leagues (Premier League, Champions League).

### Evaluation Plan

### Data Leakage Prevention Rule
> [!CAUTION]
> A candidate model is forbidden from seeing any matches that belong to the frozen baseline.
>
> - **Baseline**: matches #1 - #1000
> - **Research**: matches #1001+
> - OR: Perform strict time-series backtesting.
>
> Under no circumstances may the candidate train on the baseline evaluation sample. This prevents disguised data leakage where testing on training data falsely signals an "improvement."

### Baseline
The current production model frozen at **1,000 settled picks**.

### Candidate Definition
Current Model + xG/xGA Features (`sports-skills` integration) + FootballBin Advisory Signal.

### FootballBin Constraints
- **Advisory Only**: FootballBin data must be stored or logged alongside predictions as an advisory signal.
- **No Overrides**: It must **not** override production probabilities from the XGBoost/Poisson engine under any circumstances.

### Evaluation Metrics
When the backtest and live simulation run, the candidate will be strictly evaluated against the baseline using:
- **ROI** (Return on Investment)
- **CLV** (Closing Line Value)
- **Brier Score**
- **Calibration Error** (ECE)
- **Hit Rate**
- **Pick Volume** (Total executable bets)
- **Maximum Drawdown**

### Acceptance Rule
The candidate can **only** be promoted to production if it beats the baseline without materially reducing bet volume, worsening the calibration error, or worsening the maximum drawdown.

## Prerequisites for Retraining
No retraining or promotion of this candidate will occur until ALL of the following are met:
1. API-Football live feed is restored.
2. Live validation passes.
3. The data warehouse has a sufficient settled sample.
4. The baseline report is completely frozen.

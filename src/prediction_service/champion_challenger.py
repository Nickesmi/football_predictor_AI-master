"""
Champion/Challenger promotion logic (Phase 3 §11).

A challenger may replace the champion ONLY if ALL of these hold — this
function checks every one explicitly and refuses promotion the moment any
single one fails, exactly as specified. None is skippable, and passing
7 of 8 is not "close enough": champion_registry.py's own stability
discipline exists because Phase 2/3 found real cases (see
REAL_DATA_BACKTEST_REPORT.md, tests/prediction_service/test_champion_registry.py)
where a model that "won" a single fold was actually worse everywhere else.

  1. Leakage tests passed on the data used to evaluate the challenger.
  2. Real walk-forward validation (4 chronological folds) was used.
  3. Challenger beats the champion on the frozen holdout's Brier score.
  4. That improvement is statistically credible (95% bootstrap CI on the
     paired Brier difference excludes zero).
  5. Challenger's calibration is acceptable (ECE below a fixed bar).
  6. Challenger beats the champion in >= 3 of the 4 walk-forward folds —
     not just the frozen one (never promote on one lucky fold).
  7. If the challenger is XGBoost-based, it passes
     src.ml.model_provenance's production_validated gate for this market.
  8. The full regression suite passed — this is NOT re-run inside this
     function (running pytest from a library call would be a layering
     violation); the caller must have actually run it and pass the result
     in. Omitting it defaults to False, i.e. promotion is refused, not
     assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from src.ml.model_provenance import real_data_model_provenance
from src.ml.baselines import ALL_BASELINES, add_outcome_columns

_BASELINE_BY_NAME = {cls.name: cls for cls in ALL_BASELINES}
CALIBRATION_ECE_BAR = 0.10

_MARKET_FIELD = {
    "home_win": "p_home", "draw": "p_draw", "away_win": "p_away",
    "over_1_5": "p_over_1_5", "over_2_5": "p_over_2_5", "over_3_5": "p_over_3_5",
    "btts": "p_btts_yes",
}


@dataclass
class PromotionDecision:
    market: str
    champion: str
    challenger: str
    promoted: bool
    criteria: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)


def _brier_for_model(model_name: str, fold: dict, market: str) -> Optional[float]:
    entry = fold["models"].get(model_name, {}).get(market)
    return entry["brier"] if entry else None


def _ece_for_model(model_name: str, fold: dict, market: str) -> Optional[float]:
    entry = fold["models"].get(model_name, {}).get(market)
    return entry.get("ece") if entry else None


def evaluate_promotion(
    market: str,
    champion: str,
    challenger: str,
    backtest: dict,
    leakage_tests_passed: bool,
    full_regression_suite_passed: bool,
) -> PromotionDecision:
    criteria = {}
    reasons = []

    # 1. Leakage tests
    criteria["leakage_tests_passed"] = bool(leakage_tests_passed)
    if not leakage_tests_passed:
        reasons.append("leakage tests were not confirmed passing for the data used")

    # 2. Real walk-forward validation (4 chronological folds)
    folds = backtest.get("folds", [])
    criteria["walk_forward_validation_used"] = len(folds) >= 2 and folds[-1].get("is_final_frozen_holdout") is True
    if not criteria["walk_forward_validation_used"]:
        reasons.append("backtest does not look like a real walk-forward run with a frozen final fold")

    final_fold = folds[-1] if folds else {}
    champion_brier = _brier_for_model(champion, final_fold, market)
    challenger_brier = _brier_for_model(challenger, final_fold, market)

    # 3. Beats champion on the frozen holdout
    criteria["beats_champion_on_frozen_holdout"] = (
        champion_brier is not None and challenger_brier is not None and challenger_brier < champion_brier
    )
    if not criteria["beats_champion_on_frozen_holdout"]:
        reasons.append(f"challenger brier ({challenger_brier}) does not beat champion brier ({champion_brier}) "
                        "on the frozen holdout")

    # 4. Statistically credible improvement
    sig = _bootstrap_significance(market, champion, challenger, backtest)
    criteria["statistically_significant"] = sig is not None and sig["significant_at_95"] and sig["point_estimate"] < 0
    if not criteria["statistically_significant"]:
        reasons.append(f"improvement is not statistically significant at 95% CI (result: {sig})")

    # 5. Calibration acceptable
    challenger_ece = _ece_for_model(challenger, final_fold, market)
    criteria["calibration_acceptable"] = challenger_ece is not None and challenger_ece <= CALIBRATION_ECE_BAR
    if not criteria["calibration_acceptable"]:
        reasons.append(f"challenger ECE ({challenger_ece}) exceeds the {CALIBRATION_ECE_BAR} bar")

    # 6. Stable across folds (challenger beats champion in >= 3 of 4 folds)
    beats_count = 0
    for fold in folds:
        cb = _brier_for_model(champion, fold, market)
        xb = _brier_for_model(challenger, fold, market)
        if cb is not None and xb is not None and xb < cb:
            beats_count += 1
    criteria["stable_across_folds"] = beats_count >= max(3, len(folds) - 1)
    if not criteria["stable_across_folds"]:
        reasons.append(f"challenger only beat the champion in {beats_count}/{len(folds)} folds — "
                        "not stable enough to promote on")

    # 7. Provenance gate (only applies if challenger is XGBoost-based)
    if challenger.startswith("xgboost"):
        prov = real_data_model_provenance(market)
        criteria["provenance_gate_passed"] = bool(prov.get("production_validated"))
        if not criteria["provenance_gate_passed"]:
            reasons.append(f"challenger '{challenger}' failed the provenance gate for market '{market}'")
    else:
        criteria["provenance_gate_passed"] = True  # non-XGBoost baselines aren't subject to this specific gate

    # 8. Full regression suite (caller-attested, never assumed)
    criteria["regression_suite_passed"] = bool(full_regression_suite_passed)
    if not full_regression_suite_passed:
        reasons.append("full regression suite was not confirmed passing — promotion refused by default")

    promoted = all(criteria.values())
    return PromotionDecision(market=market, champion=champion, challenger=challenger,
                              promoted=promoted, criteria=criteria, reasons=reasons)


def _bootstrap_significance(market: str, champion: str, challenger: str, backtest: dict,
                             n_boot: int = 2000, seed: int = 42) -> Optional[dict]:
    """Refits champion and challenger on the frozen fold's actual train/test
    split and bootstraps the paired Brier difference — a real, general
    significance test for ANY two baseline models, not just the
    XGBoost-vs-baseline comparisons scripts/walk_forward_backtest.py
    precomputes."""
    if champion not in _BASELINE_BY_NAME or challenger not in _BASELINE_BY_NAME:
        return None  # can't refit an XGBoost model here; caller should use the precomputed significance table instead

    field = _MARKET_FIELD.get(market)
    if field is None:
        return None

    from pathlib import Path
    matches_path = Path(__file__).resolve().parent.parent.parent / "data" / "real_historical" / "matches.csv"
    if not matches_path.exists():
        return None
    from src.ml.point_in_time import build_point_in_time_features

    matches = pd.read_csv(matches_path).sort_values("date").reset_index(drop=True)
    feats = add_outcome_columns(build_point_in_time_features(matches))

    final_fold_info = backtest["folds"][-1]
    train = feats[feats["season"].isin(final_fold_info["train_seasons"])]
    test = feats[feats["season"] == final_fold_info["test_season"]]

    champ_model = _BASELINE_BY_NAME[champion]().fit(train)
    chal_model = _BASELINE_BY_NAME[challenger]().fit(train)
    p_champ = np.array([getattr(mp, field) for mp in champ_model.predict(test)], dtype=float)
    p_chal = np.array([getattr(mp, field) for mp in chal_model.predict(test)], dtype=float)

    if market in ("home_win",):
        y = (test["result"] == "H").astype(int).values
    elif market == "draw":
        y = (test["result"] == "D").astype(int).values
    elif market == "away_win":
        y = (test["result"] == "A").astype(int).values
    else:
        y = test[market].astype(int).values

    eps = 1e-7
    rng = np.random.default_rng(seed)
    n = len(y)
    brier_chal = (np.clip(p_chal, eps, 1 - eps) - y) ** 2
    brier_champ = (np.clip(p_champ, eps, 1 - eps) - y) ** 2
    diffs = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        diffs[i] = brier_chal[idx].mean() - brier_champ[idx].mean()
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    point = float(brier_chal.mean() - brier_champ.mean())
    return {"point_estimate": round(point, 5), "ci_95": [round(float(lo), 5), round(float(hi), 5)],
            "significant_at_95": bool(lo > 0 or hi < 0)}

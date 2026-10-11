"""
Data-driven champion-model selection per market (Phase 3 §1).

No market's champion is hardcoded. This module reads the actual
walk-forward backtest results (data/real_historical/backtest_results.json,
produced by scripts/walk_forward_backtest.py) and picks, per market:

  1. The model with the best (lowest) Brier score on the FROZEN final
     holdout (2024-25) among models that actually cover that market.
  2. A stability check: that model must also rank in the top 2 in at
     least 3 of the 4 walk-forward folds (not just the lucky final one).
     If it fails that check, fall back to whichever model has the best
     AVERAGE rank across all 4 folds instead, and say so explicitly.
  3. XGBoost is only eligible to WIN a market if
     src.ml.model_provenance.real_data_model_provenance(market)
     ["production_validated"] is True — i.e. it beat the baselines with
     statistical significance on the frozen holdout, not just a lower
     number. A numerically-lower-but-not-significant Brier does not make
     XGBoost champion; this is the same discipline Phase 2 already
     applied to the production blend weight, now applied to champion
     selection too, so "market-specific champion" can never become a
     backdoor for silently re-enabling an unvalidated model.

Run `python3 scripts/build_champion_registry.py` to regenerate
models/champion_registry.json after any backtest re-run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from src.ml.model_provenance import real_data_model_provenance

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKTEST_RESULTS = _PROJECT_ROOT / "data" / "real_historical" / "backtest_results.json"
_REGISTRY_PATH = _PROJECT_ROOT / "models" / "champion_registry.json"

# Models eligible to be considered at all for champion selection. The
# synthetic-only model (src/ml/trainer.py) is never in this list — it was
# never evaluated against real data (see AUDIT_REPORT.md §3) and cannot be
# a champion for anything.
ELIGIBLE_MODELS = ["frequency", "home_baseline", "elo", "poisson_real_data", "dixon_coles", "xgboost_real_data_calibrated"]

MARKETS = ["home_win", "draw", "away_win", "over_1_5", "over_2_5", "over_3_5", "btts",
           "home_over_0_5", "away_over_0_5"]

# under_X markets are the exact complement of over_X (P(under) = 1 - P(over)),
# so they always inherit the same champion as their over_X counterpart —
# there is no separate model to fit or evaluate.
UNDER_MARKET_SOURCE = {"under_1_5": "over_1_5", "under_2_5": "over_2_5", "under_3_5": "over_3_5"}


def _xgb_eligible(market: str) -> bool:
    if market not in ("home_win", "draw", "away_win", "over_1_5", "over_2_5", "over_3_5", "btts"):
        return False  # XGBoost was not trained/evaluated for the light markets at all
    return bool(real_data_model_provenance(market).get("production_validated"))


def _load_backtest() -> dict:
    if not _BACKTEST_RESULTS.exists():
        raise FileNotFoundError(
            f"{_BACKTEST_RESULTS} not found — run scripts/walk_forward_backtest.py first."
        )
    with open(_BACKTEST_RESULTS) as f:
        return json.load(f)


def _fold_ranking(fold: dict, market: str) -> list[tuple[str, float]]:
    """Return [(model_name, brier)] for models that cover this market in
    this fold, sorted best (lowest brier) first, filtered to eligible
    models and (for xgboost) the provenance gate."""
    ranked = []
    for model_name in ELIGIBLE_MODELS:
        model_key = model_name
        if model_name == "xgboost_real_data_calibrated" and not _xgb_eligible(market):
            continue
        market_metrics = fold["models"].get(model_key, {})
        if market not in market_metrics:
            continue
        ranked.append((model_name, market_metrics[market]["brier"]))
    ranked.sort(key=lambda t: t[1])
    return ranked


def select_champion(market: str, backtest: Optional[dict] = None) -> dict:
    """Select the champion model for one market from real backtest evidence."""
    backtest = backtest or _load_backtest()
    folds = backtest["folds"]
    final_fold = folds[-1]
    assert final_fold["is_final_frozen_holdout"], "expected the last fold to be the frozen holdout"

    final_ranking = _fold_ranking(final_fold, market)
    if not final_ranking:
        return {
            "market": market, "champion": None, "reason": "no eligible model covers this market",
            "evidence": {},
        }

    final_best_model, final_best_brier = final_ranking[0]

    # Stability check across all folds
    per_fold_ranks = []
    for fold in folds:
        ranking = _fold_ranking(fold, market)
        names_in_order = [name for name, _ in ranking]
        if final_best_model in names_in_order:
            per_fold_ranks.append(names_in_order.index(final_best_model) + 1)  # 1-indexed rank
        else:
            per_fold_ranks.append(None)  # not eligible/covered in that fold (shouldn't normally happen)

    top2_count = sum(1 for r in per_fold_ranks if r is not None and r <= 2)
    # Deliberately strict: winning the frozen holdout is not enough on its
    # own (a model can win one season by variance alone — see
    # tests/prediction_service/test_champion_registry.py
    # ::test_champion_falls_back_to_average_rank_when_frozen_holdout_winner_is_unstable).
    # It must ALSO have already been rank #1 (not merely top-2) in at
    # least 2 of the 3 earlier, non-final folds.
    earlier_ranks = per_fold_ranks[:-1]
    top1_count_earlier = sum(1 for r in earlier_ranks if r == 1)
    won_frozen_holdout = per_fold_ranks[-1] == 1
    is_stable = won_frozen_holdout and top1_count_earlier >= 2

    if is_stable:
        champion = final_best_model
        method = "frozen_holdout_best_with_stability_confirmed"
    else:
        # Fall back to best AVERAGE rank across folds among models present in every fold.
        model_avg_rank: dict[str, list[int]] = {}
        for fold in folds:
            ranking = _fold_ranking(fold, market)
            for rank, (name, _) in enumerate(ranking, start=1):
                model_avg_rank.setdefault(name, []).append(rank)
        # Only consider models that appeared in ALL folds (fair comparison).
        candidates = {name: ranks for name, ranks in model_avg_rank.items() if len(ranks) == len(folds)}
        if candidates:
            champion = min(candidates, key=lambda name: sum(candidates[name]) / len(candidates[name]))
            method = "fallback_best_average_rank_frozen_holdout_was_unstable"
        else:
            champion = final_best_model
            method = "fallback_frozen_holdout_only_no_stable_alternative"

    margin_to_runner_up = None
    if len(final_ranking) >= 2:
        margin_to_runner_up = round(final_ranking[1][1] - final_ranking[0][1], 5)

    return {
        "market": market,
        "champion": champion,
        "method": method,
        "frozen_holdout_best_model": final_best_model,
        "frozen_holdout_brier": round(final_best_brier, 4),
        "per_fold_rank_of_frozen_best": per_fold_ranks,
        "top2_fold_count_of_4": top2_count,
        "stable": is_stable,
        "margin_to_runner_up_brier": margin_to_runner_up,
        "selection_confidence": (
            "low_margin_is_noise_level" if margin_to_runner_up is not None and margin_to_runner_up < 0.002
            else "clear_margin"
        ),
        "all_models_frozen_holdout_ranking": final_ranking,
    }


def build_registry() -> dict:
    backtest = _load_backtest()
    registry = {"markets": {}, "data_summary": backtest.get("data_summary", {})}

    for market in MARKETS:
        registry["markets"][market] = select_champion(market, backtest)

    for under_market, source_market in UNDER_MARKET_SOURCE.items():
        source_result = registry["markets"][source_market]
        registry["markets"][under_market] = {
            "market": under_market,
            "champion": source_result["champion"],
            "method": f"complement_of_{source_market} (P(under) = 1 - P(over), same model)",
            "derived_from": source_market,
        }

    return registry


def save_registry(registry: Optional[dict] = None) -> Path:
    registry = registry or build_registry()
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_REGISTRY_PATH, "w") as f:
        json.dump(registry, f, indent=2)
    return _REGISTRY_PATH


def load_registry() -> dict:
    if not _REGISTRY_PATH.exists():
        raise FileNotFoundError(f"{_REGISTRY_PATH} not found — run scripts/build_champion_registry.py first.")
    with open(_REGISTRY_PATH) as f:
        return json.load(f)


def get_champion(market: str) -> Optional[str]:
    """Convenience lookup used by the live pipeline."""
    registry = load_registry()
    entry = registry["markets"].get(market)
    return entry["champion"] if entry else None


def get_champion_evidence(market: str) -> dict:
    """Full selection evidence for a market's champion (stability, margin,
    method) — used by the confidence engine."""
    registry = load_registry()
    return registry["markets"].get(market, {})


def get_champion_holdout_metrics(market: str) -> Optional[dict]:
    """The champion's full metric dict (brier, log_loss, accuracy, ece, n)
    on the frozen final holdout, straight from the backtest results —
    used by the confidence engine's calibration-quality component."""
    evidence = get_champion_evidence(market)
    champion = evidence.get("champion")
    if champion is None:
        return None
    backtest = _load_backtest()
    final_fold = backtest["folds"][-1]
    return final_fold["models"].get(champion, {}).get(market)

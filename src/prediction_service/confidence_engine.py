"""
Confidence engine (Phase 3 §9).

Confidence is NOT probability * 100. It is a separate, transparent score
built from how much evidence actually backs the champion model's number
for THIS market and THIS match:

  1. Champion quality (0-35 pts): how strong was the real backtest
     evidence for this market's champion — stability across walk-forward
     folds, margin over the runner-up, and calibration error (ECE) on the
     frozen holdout. A razor-thin, unstable win contributes little here
     even if it's technically "the champion".
  2. Model agreement (0-30 pts): do the other available models roughly
     agree with the champion for this match? High agreement raises
     confidence; models scattered all over raise doubt regardless of what
     the champion says.
  3. Data sufficiency (0-20 pts): how much real history exists for both
     teams (AUDIT_REPORT.md §1.1's fix — never let thin history pass as
     full evidence).
  4. Lineup/injury uncertainty (0 to -15 pts): penalty, see lineup_info.py.
  5. Out-of-distribution penalty: severe OOD hard-caps total confidence at
     20/100 regardless of the other components (an extrapolation is an
     extrapolation no matter how confident the model "feels"); mild OOD
     subtracts a flat 15.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.prediction_service.model_agreement import AgreementResult
from src.prediction_service.ood_detection import OODResult

_AGREEMENT_POINTS = {"high": 30.0, "medium": 18.0, "low": 6.0, "unknown": 12.0}


@dataclass
class ConfidenceResult:
    score: float                  # 0-100
    tier: str                     # "very_low" | "low" | "medium" | "high"
    components: dict = field(default_factory=dict)
    hard_capped_by_ood: bool = False
    notes: list = field(default_factory=list)


def _champion_quality_points(champion_evidence: dict, champion_holdout_metrics: Optional[dict]) -> tuple[float, list[str]]:
    notes = []
    points = 0.0

    if champion_evidence.get("stable"):
        points += 18.0
    else:
        points += 6.0
        notes.append("champion selection used the average-rank fallback (frozen-holdout win was not stable)")

    margin = champion_evidence.get("margin_to_runner_up_brier")
    if margin is not None:
        if champion_evidence.get("selection_confidence") == "low_margin_is_noise_level":
            points += 3.0
            notes.append(f"champion's margin over runner-up is only {margin:.4f} Brier — within noise level")
        else:
            points += 10.0

    if champion_holdout_metrics and "ece" in champion_holdout_metrics:
        ece = champion_holdout_metrics["ece"]
        # ECE near 0 -> full 7 points; ECE >= 0.10 -> 0 points.
        points += max(0.0, 7.0 * (1.0 - min(ece / 0.10, 1.0)))

    return round(min(points, 35.0), 2), notes


def _data_sufficiency_points(data_sufficiency: int) -> float:
    # 50+ prior matches (roughly 1.5 seasons of history for both teams) -> full marks.
    return round(20.0 * min(data_sufficiency / 50.0, 1.0), 2)


def compute_confidence(
    champion_evidence: dict,
    champion_holdout_metrics: Optional[dict],
    agreement: AgreementResult,
    ood: OODResult,
    data_sufficiency: int,
    lineup_uncertainty_penalty: float = 0.0,
) -> ConfidenceResult:
    champion_points, champion_notes = _champion_quality_points(champion_evidence, champion_holdout_metrics)
    agreement_points = _AGREEMENT_POINTS.get(agreement.agreement_level, 12.0)
    sufficiency_points = _data_sufficiency_points(data_sufficiency)

    components = {
        "champion_quality": champion_points,
        "model_agreement": agreement_points,
        "data_sufficiency": sufficiency_points,
        "lineup_uncertainty_penalty": -abs(lineup_uncertainty_penalty),
    }
    notes = list(champion_notes)

    raw_score = champion_points + agreement_points + sufficiency_points - abs(lineup_uncertainty_penalty)

    hard_capped = False
    if ood.severity == "severe":
        raw_score = min(raw_score, 20.0)
        hard_capped = True
        notes.append("HARD-CAPPED at 20/100: severe out-of-distribution match (see ood.reasons)")
    elif ood.severity == "mild":
        raw_score -= 15.0
        notes.append("−15 penalty: mild out-of-distribution signal(s) detected")

    score = round(max(0.0, min(100.0, raw_score)), 1)

    if score >= 70:
        tier = "high"
    elif score >= 50:
        tier = "medium"
    elif score >= 30:
        tier = "low"
    else:
        tier = "very_low"

    return ConfidenceResult(score=score, tier=tier, components=components,
                             hard_capped_by_ood=hard_capped, notes=notes)

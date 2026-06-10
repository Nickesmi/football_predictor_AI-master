"""
Betting Intelligence Layered Qualification Engine (BILQE)

10-Layer sequential pipeline that aggressively rejects bad picks
and maximizes long-term ROI, calibration quality, and edge quality.

Architecture:
    Layer 1  — Data Quality Gate          (HARD GATE)
    Layer 2  — Model Consensus            (HARD GATE on disagreement > 15%)
    Layer 3  — Calibration Verification   (HARD GATE on league_reliability < 60)
    Layer 4  — Market Edge                (HARD GATE on edge < 4%)
    Layer 5  — Volatility Filter          (HARD GATE on volatility > 85)
    Layer 6  — xG Reality Check           (SOFT — applies shrinkage)
    Layer 7  — CLV Tracking               (SOFT — penalizes negative CLV)
    Layer 8  — Historical ROI             (SOFT — penalizes negative ROI)
    Layer 9  — Liquidity Filter           (HARD GATE on illiquid markets)
    Layer 10 — Similar Match Engine       (SOFT — reality check)

Final Score Weights:
    15% Data Quality
    15% Model Consensus
    15% Calibration
    30% Market Edge          ← most important
     5% Volatility
     5% Historical ROI
     5% CLV
     5% Liquidity
     5% Similar Match

Tier Rules:
    S (90+) — Elite Pick
    A (80-89) — Strong Pick
    B (70-79) — Acceptable Pick
    C (<70) — No Pick

Tier S Hard Requirements:
    Edge > 5%
    League Reliability > 70
    Similar Match Hit Rate > 60%
    Volatility < 60

Success Metrics:
    1. ROI
    2. Brier Score
    3. Calibration Error
    4. Closing Line Value
    5. Long-Term Bankroll Growth
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("football_predictor")


# ═══════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class LayerResult:
    """Result from a single qualification layer."""
    layer_name: str
    layer_number: int
    passed: bool
    score: float              # 0-100 normalized
    details: dict = field(default_factory=dict)
    rejection_reason: str = ""


@dataclass
class QualifiedPick:
    """A pick that has passed (or failed) the qualification pipeline."""
    market: str
    probability: float
    pick_quality_score: float
    tier: str                 # S, A, B, C
    layer_scores: dict = field(default_factory=dict)
    rejection_reason: str = ""
    edge: float = 0.0
    implied_prob: float = 0.0
    model_consensus: float = 0.0
    similar_match_hit_rate: float = 0.0


@dataclass
class QualificationResult:
    """Complete qualification result for one match."""
    match_id: str
    home_team: str
    away_team: str
    league: str
    qualified_picks: list[QualifiedPick] = field(default_factory=list)
    rejected_picks: list[QualifiedPick] = field(default_factory=list)
    match_data_quality: float = 0.0
    has_qualified_picks: bool = False
    summary: str = "NO QUALIFIED PICK"
    layer_gate_results: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        tier_counts = {"S": 0, "A": 0, "B": 0}
        for p in self.qualified_picks:
            if p.tier in tier_counts:
                tier_counts[p.tier] += 1

        return {
            "qualified_picks": [
                {
                    "market": p.market,
                    "probability": p.probability,
                    "pick_quality_score": p.pick_quality_score,
                    "tier": p.tier,
                    "tier_label": _TIER_LABELS.get(p.tier, "No Pick"),
                    "edge": round(p.edge, 1),
                    "implied_prob": round(p.implied_prob, 1),
                    "model_consensus": round(p.model_consensus, 1),
                    "similar_match_hit_rate": round(p.similar_match_hit_rate, 1),
                    "layer_scores": p.layer_scores,
                }
                for p in self.qualified_picks
            ],
            "rejected_count": len(self.rejected_picks),
            "match_data_quality": round(self.match_data_quality, 1),
            "has_qualified_picks": self.has_qualified_picks,
            "summary": self.summary,
            "tier_counts": tier_counts,
            "layer_gate_results": self.layer_gate_results,
        }


_TIER_LABELS = {
    "S": "Elite Pick",
    "A": "Strong Pick",
    "B": "Acceptable Pick",
    "C": "No Pick",
}

# ═══════════════════════════════════════════════════════════════════════
# SCORE WEIGHTS
# ═══════════════════════════════════════════════════════════════════════

WEIGHTS = {
    "data_quality":     0.15,
    "model_consensus":  0.15,
    "calibration":      0.15,
    "market_edge":      0.30,   # most important
    "volatility":       0.05,
    "historical_roi":   0.05,
    "clv":              0.05,
    "liquidity":        0.05,
    "similar_match":    0.05,
}

# ═══════════════════════════════════════════════════════════════════════
# TIER S HARD REQUIREMENTS
# ═══════════════════════════════════════════════════════════════════════

TIER_S_MIN_EDGE = 5.0
TIER_S_MIN_RELIABILITY = 70.0
TIER_S_MIN_SIMILAR_HIT_RATE = 60.0
TIER_S_MAX_VOLATILITY = 60.0

# ═══════════════════════════════════════════════════════════════════════
# MODEL CONSENSUS WEIGHTS
# ═══════════════════════════════════════════════════════════════════════

MODEL_WEIGHTS = {
    "poisson":   0.40,
    "xgboost":   0.25,
    "lightgbm":  0.15,
    "catboost":  0.10,
    "ensemble":  0.10,
}


# ═══════════════════════════════════════════════════════════════════════
# LAYER 1 — DATA QUALITY GATE
# ═══════════════════════════════════════════════════════════════════════

def _layer1_data_quality(
    data_quality_score: float,
    league_name: str,
    home_matches: int,
    away_matches: int,
) -> LayerResult:
    """
    Hard gate: reject matches with insufficient data.
    
    Threshold:
        Tier S/A → 70
        Tier B   → 50
    
    We return the raw score; the tier decision uses it downstream.
    """
    details = {
        "raw_score": round(data_quality_score, 1),
        "home_matches": home_matches,
        "away_matches": away_matches,
        "league": league_name,
    }

    # Hard floor: below 50 means no pick at any tier
    if data_quality_score < 50:
        return LayerResult(
            layer_name="Data Quality",
            layer_number=1,
            passed=False,
            score=data_quality_score,
            details=details,
            rejection_reason=f"Data quality {data_quality_score:.0f} < 50 minimum",
        )

    # Between 50-70: only Tier B eligible (passed=True, but score reflects limitation)
    return LayerResult(
        layer_name="Data Quality",
        layer_number=1,
        passed=True,
        score=data_quality_score,
        details=details,
    )


# ═══════════════════════════════════════════════════════════════════════
# LAYER 2 — MODEL CONSENSUS (WEIGHTED)
# ═══════════════════════════════════════════════════════════════════════

def _layer2_model_consensus(
    model_probs: dict[str, float],
) -> LayerResult:
    """
    Weighted consensus using MODEL_WEIGHTS.
    
    Disagreement = weighted standard deviation from weighted mean.
    Hard gate: if raw max-min disagreement > 15%, reject.
    """
    if not model_probs or len(model_probs) < 2:
        # Only one model available — consensus is perfect by default
        score = list(model_probs.values())[0] if model_probs else 50.0
        return LayerResult(
            layer_name="Model Consensus",
            layer_number=2,
            passed=True,
            score=85.0,  # single model gets moderate consensus score
            details={"models_available": len(model_probs), "note": "single model"},
        )

    # Compute weighted mean
    total_weight = 0.0
    weighted_sum = 0.0
    for model_name, prob in model_probs.items():
        w = MODEL_WEIGHTS.get(model_name, 0.05)
        weighted_sum += prob * w
        total_weight += w

    if total_weight == 0:
        weighted_mean = sum(model_probs.values()) / len(model_probs)
    else:
        weighted_mean = weighted_sum / total_weight

    # Compute weighted disagreement (std dev from weighted mean)
    variance_sum = 0.0
    for model_name, prob in model_probs.items():
        w = MODEL_WEIGHTS.get(model_name, 0.05)
        variance_sum += w * (prob - weighted_mean) ** 2

    weighted_std = math.sqrt(variance_sum / total_weight) if total_weight > 0 else 0

    # Raw spread
    raw_spread = max(model_probs.values()) - min(model_probs.values())

    # Hard gate: raw spread > 15% → reject
    if raw_spread > 15.0:
        return LayerResult(
            layer_name="Model Consensus",
            layer_number=2,
            passed=False,
            score=max(0, 100 - raw_spread * 3),
            details={
                "weighted_mean": round(weighted_mean, 1),
                "weighted_std": round(weighted_std, 1),
                "raw_spread": round(raw_spread, 1),
                "models": {k: round(v, 1) for k, v in model_probs.items()},
            },
            rejection_reason=f"Model disagreement {raw_spread:.1f}% > 15% threshold",
        )

    # Score: 100 at perfect consensus, decreasing with spread
    # 0% spread → 100, 15% spread → 55
    consensus_score = max(0, min(100, 100 - raw_spread * 3))

    consensus_grade = "Strong" if raw_spread < 5 else ("Moderate" if raw_spread < 10 else "Weak")

    return LayerResult(
        layer_name="Model Consensus",
        layer_number=2,
        passed=True,
        score=consensus_score,
        details={
            "weighted_mean": round(weighted_mean, 1),
            "weighted_std": round(weighted_std, 1),
            "raw_spread": round(raw_spread, 1),
            "consensus_grade": consensus_grade,
            "models": {k: round(v, 1) for k, v in model_probs.items()},
        },
    )


# ═══════════════════════════════════════════════════════════════════════
# LAYER 3 — CALIBRATION VERIFICATION
# ═══════════════════════════════════════════════════════════════════════

def _layer3_calibration(
    league_reliability: float,
    calibration_gap: float,
) -> LayerResult:
    """
    Hard gate: league_reliability < 60 → reject.
    Penalize overconfident predictions (cal_gap > 10pp).
    """
    details = {
        "league_reliability": round(league_reliability, 1),
        "calibration_gap": round(calibration_gap, 1),
    }

    if league_reliability < 60:
        return LayerResult(
            layer_name="Calibration",
            layer_number=3,
            passed=False,
            score=league_reliability,
            details=details,
            rejection_reason=f"League reliability {league_reliability:.0f} < 60 minimum",
        )

    # Score: reliability itself is a good 0-100 metric
    score = league_reliability

    # Penalty for overconfidence
    if abs(calibration_gap) > 10:
        penalty = min(20, (abs(calibration_gap) - 10) * 2)
        score = max(0, score - penalty)
        details["overconfidence_penalty"] = round(penalty, 1)

    return LayerResult(
        layer_name="Calibration",
        layer_number=3,
        passed=True,
        score=min(100, score),
        details=details,
    )


# ═══════════════════════════════════════════════════════════════════════
# LAYER 4 — MARKET EDGE
# ═══════════════════════════════════════════════════════════════════════

def _layer4_market_edge(
    model_prob: float,
    implied_prob: float,
) -> LayerResult:
    """
    Hard gate: edge < 4% → reject.
    
    edge = model_prob - implied_prob
    
    If no odds available (implied_prob=0), pass through with neutral score.
    """
    if implied_prob <= 0:
        return LayerResult(
            layer_name="Market Edge",
            layer_number=4,
            passed=True,
            score=50.0,  # neutral — no odds data
            details={"note": "No bookmaker odds available", "edge": 0},
        )

    edge = model_prob - implied_prob

    details = {
        "model_prob": round(model_prob, 1),
        "implied_prob": round(implied_prob, 1),
        "edge": round(edge, 1),
    }

    if edge < 4.0:
        return LayerResult(
            layer_name="Market Edge",
            layer_number=4,
            passed=False,
            score=max(0, edge * 10),  # edge=4 → 40, edge=0 → 0
            details=details,
            rejection_reason=f"Edge {edge:.1f}% < 4% minimum",
        )

    # Score: edge 4% → 60, edge 8% → 80, edge 12%+ → 100
    score = min(100, 40 + edge * 5)

    return LayerResult(
        layer_name="Market Edge",
        layer_number=4,
        passed=True,
        score=score,
        details=details,
    )


# ═══════════════════════════════════════════════════════════════════════
# LAYER 5 — VOLATILITY FILTER
# ═══════════════════════════════════════════════════════════════════════

def _layer5_volatility(
    home_volatility: float,
    away_volatility: float,
) -> LayerResult:
    """
    Hard gate: max volatility > 85 → reject.
    Soft penalty: volatility > 70 → reduce confidence.
    """
    max_vol = max(home_volatility, away_volatility)

    details = {
        "home_volatility": round(home_volatility, 1),
        "away_volatility": round(away_volatility, 1),
        "max_volatility": round(max_vol, 1),
    }

    if max_vol > 85:
        return LayerResult(
            layer_name="Volatility",
            layer_number=5,
            passed=False,
            score=max(0, 100 - max_vol),
            details=details,
            rejection_reason=f"Volatility {max_vol:.0f} > 85 threshold",
        )

    # Score: 0 volatility → 100, 85 volatility → 15
    score = max(0, 100 - max_vol * 1.1)

    if max_vol > 70:
        details["confidence_reduction"] = "10% applied"

    return LayerResult(
        layer_name="Volatility",
        layer_number=5,
        passed=True,
        score=score,
        details=details,
    )


# ═══════════════════════════════════════════════════════════════════════
# LAYER 6 — xG REALITY CHECK
# ═══════════════════════════════════════════════════════════════════════

def _layer6_xg_reality(
    predicted_home_xg: float,
    predicted_away_xg: float,
    historical_home_avg: float,
    historical_away_avg: float,
) -> LayerResult:
    """
    Soft layer: apply shrinkage if predicted xG > 2× historical average.
    No hard rejection.
    """
    home_ratio = predicted_home_xg / max(0.3, historical_home_avg)
    away_ratio = predicted_away_xg / max(0.3, historical_away_avg)
    max_ratio = max(home_ratio, away_ratio)

    shrinkage_applied = max_ratio > 2.0

    details = {
        "predicted_home_xg": round(predicted_home_xg, 2),
        "predicted_away_xg": round(predicted_away_xg, 2),
        "historical_home_avg": round(historical_home_avg, 2),
        "historical_away_avg": round(historical_away_avg, 2),
        "max_ratio": round(max_ratio, 2),
        "shrinkage_applied": shrinkage_applied,
    }

    if shrinkage_applied:
        # Penalty proportional to how extreme the projection is
        penalty = min(30, (max_ratio - 2.0) * 15)
        score = max(40, 90 - penalty)
    else:
        # Good reality check → high score
        score = min(100, 95 - (max_ratio - 1.0) * 10)

    return LayerResult(
        layer_name="xG Reality Check",
        layer_number=6,
        passed=True,  # never hard-rejects
        score=max(0, score),
        details=details,
    )


# ═══════════════════════════════════════════════════════════════════════
# LAYER 7 — CLV TRACKING
# ═══════════════════════════════════════════════════════════════════════

def _layer7_clv(
    historical_clv: float,
    clv_sample_count: int,
) -> LayerResult:
    """
    Soft gate: penalize if historical CLV is negative over 30+ samples.
    """
    details = {
        "historical_clv": round(historical_clv, 2),
        "sample_count": clv_sample_count,
    }

    if clv_sample_count < 30:
        # Insufficient data — neutral pass
        return LayerResult(
            layer_name="CLV Tracking",
            layer_number=7,
            passed=True,
            score=50.0,  # neutral
            details={**details, "note": "Insufficient CLV data"},
        )

    # Positive CLV → great, Negative CLV → penalty
    if historical_clv >= 0:
        score = min(100, 70 + historical_clv * 10)
    else:
        # Negative CLV: -1% → 65, -5% → 25
        score = max(0, 70 + historical_clv * 10)
        details["confidence_reduction"] = "5% applied"

    return LayerResult(
        layer_name="CLV Tracking",
        layer_number=7,
        passed=True,  # never hard-rejects
        score=score,
        details=details,
    )


# ═══════════════════════════════════════════════════════════════════════
# LAYER 8 — HISTORICAL ROI
# ═══════════════════════════════════════════════════════════════════════

def _layer8_historical_roi(
    league_roi: float,
    league_roi_samples: int,
    market_roi: float,
    market_roi_samples: int,
) -> LayerResult:
    """
    Soft gate: penalize if ROI is negative over 500+ picks.
    """
    details = {
        "league_roi": round(league_roi, 2),
        "league_samples": league_roi_samples,
        "market_roi": round(market_roi, 2),
        "market_samples": market_roi_samples,
    }

    # Need 500+ samples to be meaningful
    has_league_signal = league_roi_samples >= 500
    has_market_signal = market_roi_samples >= 500

    if not has_league_signal and not has_market_signal:
        return LayerResult(
            layer_name="Historical ROI",
            layer_number=8,
            passed=True,
            score=50.0,  # neutral
            details={**details, "note": "Insufficient ROI data"},
        )

    # Blend league and market ROI
    if has_league_signal and has_market_signal:
        blended_roi = league_roi * 0.6 + market_roi * 0.4
    elif has_league_signal:
        blended_roi = league_roi
    else:
        blended_roi = market_roi

    # ROI score: +10% → 100, 0% → 60, -10% → 0
    score = max(0, min(100, 60 + blended_roi * 4))

    if blended_roi < 0 and (has_league_signal or has_market_signal):
        details["confidence_reduction"] = "10% applied"

    return LayerResult(
        layer_name="Historical ROI",
        layer_number=8,
        passed=True,  # never hard-rejects
        score=score,
        details=details,
    )


# ═══════════════════════════════════════════════════════════════════════
# LAYER 9 — LIQUIDITY FILTER
# ═══════════════════════════════════════════════════════════════════════

def _layer9_liquidity(
    bookmaker_count: int,
    odds_movement_stable: bool,
    has_sharp_bookmaker: bool,
) -> LayerResult:
    """
    Hard gate on illiquid markets.
    
    Reject if:
        - bookmaker_count < 2
        - odds movement unstable AND no sharp bookmaker
    """
    details = {
        "bookmaker_count": bookmaker_count,
        "odds_stable": odds_movement_stable,
        "has_sharp_bookmaker": has_sharp_bookmaker,
    }

    if bookmaker_count == 0:
        # No odds at all — pass through (odds not available)
        return LayerResult(
            layer_name="Liquidity",
            layer_number=9,
            passed=True,
            score=50.0,  # neutral
            details={**details, "note": "No odds data available"},
        )

    if bookmaker_count < 2 and not has_sharp_bookmaker:
        return LayerResult(
            layer_name="Liquidity",
            layer_number=9,
            passed=False,
            score=20.0,
            details=details,
            rejection_reason=f"Only {bookmaker_count} bookmaker(s), no sharp book",
        )

    if not odds_movement_stable and not has_sharp_bookmaker:
        return LayerResult(
            layer_name="Liquidity",
            layer_number=9,
            passed=False,
            score=30.0,
            details=details,
            rejection_reason="Odds movement unstable, no sharp bookmaker anchor",
        )

    # Score based on market depth
    score = min(100, 50 + bookmaker_count * 8)
    if has_sharp_bookmaker:
        score = min(100, score + 15)
    if odds_movement_stable:
        score = min(100, score + 5)

    return LayerResult(
        layer_name="Liquidity",
        layer_number=9,
        passed=True,
        score=score,
        details=details,
    )


# ═══════════════════════════════════════════════════════════════════════
# LAYER 10 — SIMILAR MATCH ENGINE
# ═══════════════════════════════════════════════════════════════════════

def _layer10_similar_match(
    similar_match_hit_rate: float,
    similar_match_count: int,
    market_name: str,
) -> LayerResult:
    """
    Reality check: compare against historical similar matches.
    Soft layer — penalizes overconfidence but doesn't reject.
    """
    details = {
        "hit_rate": round(similar_match_hit_rate, 1),
        "sample_count": similar_match_count,
        "market": market_name,
    }

    if similar_match_count < 20:
        return LayerResult(
            layer_name="Similar Match Engine",
            layer_number=10,
            passed=True,
            score=50.0,  # neutral
            details={**details, "note": "Insufficient similar matches"},
        )

    # Score: hit_rate 70% → 90, 50% → 60, 30% → 30
    score = max(0, min(100, similar_match_hit_rate * 1.3))

    return LayerResult(
        layer_name="Similar Match Engine",
        layer_number=10,
        passed=True,
        score=score,
        details=details,
    )


# ═══════════════════════════════════════════════════════════════════════
# TIER ASSIGNMENT
# ═══════════════════════════════════════════════════════════════════════

def _assign_tier(
    pick_quality_score: float,
    edge: float,
    league_reliability: float,
    similar_hit_rate: float,
    max_volatility: float,
    data_quality: float,
) -> str:
    """
    Assign tier with hard requirements for S.
    
    Tier S requires ALL of:
        Edge > 5%
        Reliability > 70
        Similar Match Hit Rate > 60%
        Volatility < 60
        
    Tier B requires data_quality >= 50 (relaxed threshold).
    Tier S/A requires data_quality >= 70.
    """
    if pick_quality_score >= 90:
        # Check Tier S hard requirements
        if (edge > TIER_S_MIN_EDGE
                and league_reliability > TIER_S_MIN_RELIABILITY
                and similar_hit_rate > TIER_S_MIN_SIMILAR_HIT_RATE
                and max_volatility < TIER_S_MAX_VOLATILITY
                and data_quality >= 70):
            return "S"
        # Downgrade to A if hard requirements not met
        return "A" if data_quality >= 70 else "B"

    if pick_quality_score >= 80:
        return "A" if data_quality >= 70 else "B"

    if pick_quality_score >= 70:
        return "B"

    return "C"


# ═══════════════════════════════════════════════════════════════════════
# MAIN QUALIFICATION PIPELINE
# ═══════════════════════════════════════════════════════════════════════

def qualify_picks(
    match_analysis: dict,
    home_name: str,
    away_name: str,
    league_name: str,
    match_id: str = "",
    bookmaker_odds: list[dict] | None = None,
    conn=None,
) -> QualificationResult:
    """
    Run the 10-layer BILQE pipeline on a match analysis.

    Args:
        match_analysis: output of _compute_match_analysis() from api/main.py
        home_name: home team name
        away_name: away team name
        league_name: league name
        match_id: fixture ID
        bookmaker_odds: optional list of {market, odds, bookmaker} dicts
        conn: optional sqlite3 connection for historical queries

    Returns:
        QualificationResult with qualified and rejected picks
    """
    result = QualificationResult(
        match_id=match_id,
        home_team=home_name,
        away_team=away_name,
        league=league_name,
    )

    # ── Extract analysis data ──
    data_quality = match_analysis.get("data_quality_score", 0)
    result.match_data_quality = data_quality

    poisson = match_analysis.get("poisson", {})
    xgb_preds = match_analysis.get("xgboost_predictions", [])
    averages = match_analysis.get("averages", {})

    # Volatility from team intelligence
    home_vol = 0.0
    away_vol = 0.0
    try:
        if conn:
            from src.db.team_intelligence import get_team_rating
            _, _, home_vol, _ = get_team_rating(conn, home_name, league_name)
            _, _, away_vol, _ = get_team_rating(conn, away_name, league_name)
    except Exception:
        pass

    # League reliability
    league_reliability = 50.0  # default
    calibration_gap = 0.0
    try:
        if conn:
            from src.db.error_intelligence import get_league_adjustment
            adj = get_league_adjustment(conn, league_name)
            if adj:
                league_reliability = getattr(adj, 'reliability', 50.0)
                calibration_gap = getattr(adj, 'calibration_gap', 0.0)
    except Exception:
        pass

    # Home/away match counts
    home_matches = averages.get("home", {}).get("avg_goals_scored", 1.2)  # proxy
    away_matches = averages.get("away", {}).get("avg_goals_scored", 1.0)  # proxy

    # ══════════════════════════════════════════════════════
    # LAYER 1 — DATA QUALITY GATE (match-level)
    # ══════════════════════════════════════════════════════
    l1 = _layer1_data_quality(data_quality, league_name, 10, 10)
    result.layer_gate_results["data_quality"] = {
        "passed": l1.passed, "score": round(l1.score, 1),
        "rejection": l1.rejection_reason,
    }

    if not l1.passed:
        # Entire match rejected — no picks qualify
        result.summary = f"REJECTED: {l1.rejection_reason}"
        return result

    # ══════════════════════════════════════════════════════
    # LAYER 5 — VOLATILITY (match-level gate)
    # ══════════════════════════════════════════════════════
    l5 = _layer5_volatility(home_vol, away_vol)
    result.layer_gate_results["volatility"] = {
        "passed": l5.passed, "score": round(l5.score, 1),
        "rejection": l5.rejection_reason,
    }

    if not l5.passed:
        result.summary = f"REJECTED: {l5.rejection_reason}"
        return result

    # ══════════════════════════════════════════════════════
    # LAYER 6 — xG REALITY CHECK (match-level)
    # ══════════════════════════════════════════════════════
    pred_home_xg = poisson.get("xg", {}).get("home", 1.3)
    pred_away_xg = poisson.get("xg", {}).get("away", 1.1)
    hist_home_avg = averages.get("home", {}).get("avg_goals_scored", 1.3)
    hist_away_avg = averages.get("away", {}).get("avg_goals_scored", 1.0)

    l6 = _layer6_xg_reality(pred_home_xg, pred_away_xg, hist_home_avg, hist_away_avg)
    result.layer_gate_results["xg_reality"] = {
        "passed": l6.passed, "score": round(l6.score, 1),
        "shrinkage_applied": l6.details.get("shrinkage_applied", False),
    }

    # ══════════════════════════════════════════════════════
    # LAYER 3 — CALIBRATION (match-level gate)
    # ══════════════════════════════════════════════════════
    l3 = _layer3_calibration(league_reliability, calibration_gap)
    result.layer_gate_results["calibration"] = {
        "passed": l3.passed, "score": round(l3.score, 1),
        "rejection": l3.rejection_reason,
    }

    # ══════════════════════════════════════════════════════
    # CLV + ROI (match-level, soft)
    # ══════════════════════════════════════════════════════
    historical_clv = 0.0
    clv_samples = 0
    league_roi = 0.0
    league_roi_samples = 0

    try:
        if conn:
            # CLV from picks table
            clv_row = conn.execute(
                "SELECT AVG(clv_pct), COUNT(*) FROM picks WHERE clv_pct IS NOT NULL"
            ).fetchone()
            if clv_row and clv_row[1] > 0:
                historical_clv = clv_row[0] or 0.0
                clv_samples = clv_row[1]

            # ROI from picks table by league
            roi_row = conn.execute(
                """SELECT SUM(pnl_units), COUNT(*) FROM picks
                   WHERE result IS NOT NULL AND pnl_units IS NOT NULL""",
            ).fetchone()
            if roi_row and roi_row[1] > 0:
                league_roi = (roi_row[0] / roi_row[1]) * 100 if roi_row[1] > 0 else 0
                league_roi_samples = roi_row[1]
    except Exception:
        pass

    l7 = _layer7_clv(historical_clv, clv_samples)
    l8 = _layer8_historical_roi(league_roi, league_roi_samples, 0.0, 0)

    result.layer_gate_results["clv"] = {
        "passed": l7.passed, "score": round(l7.score, 1),
    }
    result.layer_gate_results["historical_roi"] = {
        "passed": l8.passed, "score": round(l8.score, 1),
    }

    # ══════════════════════════════════════════════════════
    # PER-MARKET QUALIFICATION
    # ══════════════════════════════════════════════════════

    # Build odds lookup
    odds_lookup = {}
    if bookmaker_odds:
        for o in bookmaker_odds:
            odds_lookup[o.get("market", "")] = o

    # Collect all markets from top_picks (Layer 2 candidates)
    all_picks = match_analysis.get("top_picks", [])
    if not all_picks:
        # Fallback: collect from categories
        for cat in match_analysis.get("categories", []):
            all_picks.extend(cat.get("picks", []))

    # Build XGBoost probability lookup for consensus
    xgb_lookup = {}
    for xp in xgb_preds:
        market_key = xp.get("market_key", "")
        xgb_lookup[market_key] = xp.get("probability", 0)

    # Similar match engine (loaded once)
    similar_engine_cache = {}
    try:
        if conn:
            from src.engine.similar_match_engine import find_similar_match_stats
            similar_engine_cache = find_similar_match_stats(
                conn, home_name, away_name, league_name,
                pred_home_xg, pred_away_xg,
            )
    except Exception as e:
        logger.debug(f"Similar match engine unavailable: {e}")

    for pick in all_picks:
        market_name = pick.get("market", "")
        probability = pick.get("probability", 0)

        # ── Layer 2: Model Consensus ──
        model_probs = {"poisson": probability}

        # Map pick to XGBoost market key
        xgb_key = _market_to_xgb_key(market_name)
        if xgb_key and xgb_key in xgb_lookup:
            model_probs["xgboost"] = xgb_lookup[xgb_key]

        l2 = _layer2_model_consensus(model_probs)

        # ── Layer 4: Market Edge ──
        implied_prob = 0.0
        odds_info = odds_lookup.get(market_name, {})
        odds_val = odds_info.get("odds", 0)
        if odds_val > 0:
            implied_prob = 100.0 / odds_val

        l4 = _layer4_market_edge(probability, implied_prob)
        edge = probability - implied_prob if implied_prob > 0 else 0

        # ── Layer 9: Liquidity ──
        bk_count = len(set(o.get("bookmaker", "") for o in (bookmaker_odds or [])
                          if o.get("market") == market_name))
        has_sharp = any(
            o.get("bookmaker", "").lower() in ("pinnacle", "betfair_exchange", "matchbook")
            for o in (bookmaker_odds or []) if o.get("market") == market_name
        )
        l9 = _layer9_liquidity(bk_count, True, has_sharp)

        # ── Layer 10: Similar Match ──
        sim_hit_rate = similar_engine_cache.get(market_name, {}).get("hit_rate", 50.0)
        sim_count = similar_engine_cache.get(market_name, {}).get("count", 0)
        l10 = _layer10_similar_match(sim_hit_rate, sim_count, market_name)

        # ── Check hard gates ──
        layers = [l1, l2, l3, l4, l5, l6, l7, l8, l9, l10]
        failed_layer = None
        for layer in layers:
            if not layer.passed:
                failed_layer = layer
                break

        # ── Compute weighted score ──
        layer_scores = {
            "data_quality": l1.score,
            "model_consensus": l2.score,
            "calibration": l3.score,
            "market_edge": l4.score,
            "volatility": l5.score,
            "xg_reality": l6.score,
            "clv": l7.score,
            "historical_roi": l8.score,
            "liquidity": l9.score,
            "similar_match": l10.score,
        }

        pick_quality_score = sum(
            layer_scores[k] * WEIGHTS.get(k, 0)
            for k in WEIGHTS
        )

        # ── Assign tier ──
        tier = _assign_tier(
            pick_quality_score,
            edge=edge,
            league_reliability=league_reliability,
            similar_hit_rate=sim_hit_rate,
            max_volatility=max(home_vol, away_vol),
            data_quality=data_quality,
        )

        qp = QualifiedPick(
            market=market_name,
            probability=probability,
            pick_quality_score=round(pick_quality_score, 1),
            tier=tier if not failed_layer else "C",
            layer_scores={k: round(v, 1) for k, v in layer_scores.items()},
            rejection_reason=failed_layer.rejection_reason if failed_layer else "",
            edge=round(edge, 1),
            implied_prob=round(implied_prob, 1),
            model_consensus=round(l2.details.get("raw_spread", 0), 1),
            similar_match_hit_rate=round(sim_hit_rate, 1),
        )

        if failed_layer or tier == "C":
            qp.tier = "C"
            if not qp.rejection_reason:
                qp.rejection_reason = f"Score {pick_quality_score:.0f} below Tier B threshold"
            result.rejected_picks.append(qp)
        else:
            result.qualified_picks.append(qp)

    # Sort qualified picks by score descending
    result.qualified_picks.sort(key=lambda p: p.pick_quality_score, reverse=True)
    result.has_qualified_picks = len(result.qualified_picks) > 0

    # Build summary
    if result.has_qualified_picks:
        tier_counts = {"S": 0, "A": 0, "B": 0}
        for p in result.qualified_picks:
            if p.tier in tier_counts:
                tier_counts[p.tier] += 1
        parts = []
        for t, label in [("S", "Elite"), ("A", "Strong"), ("B", "Acceptable")]:
            if tier_counts[t] > 0:
                parts.append(f"{tier_counts[t]} {label}")
        result.summary = ", ".join(parts) if parts else "NO QUALIFIED PICK"
    else:
        result.summary = "NO QUALIFIED PICK"

    return result


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _market_to_xgb_key(market_name: str) -> str | None:
    """Map an internal market name to XGBoost market key."""
    mapping = {
        "Home Win": "home_win",
        "Draw": "draw",
        "Away Win": "away_win",
        "BTTS - Yes": "btts",
        "Over 1.5 Goals": "over_1_5",
        "Over 2.5 Goals": "over_2_5",
        "Over 3.5 Goals": "over_3_5",
        "FH Over 0.5 Goals": "ht_over_0_5",
    }
    return mapping.get(market_name)


# ═══════════════════════════════════════════════════════════════════════
# QUALIFICATION LOGGING
# ═══════════════════════════════════════════════════════════════════════

def log_qualification(
    conn,
    match_id: str,
    match_date: str,
    home_team: str,
    away_team: str,
    league_name: str,
    result: QualificationResult,
) -> int:
    """Log all qualification decisions to the qualification_log table."""
    if conn is None:
        return 0

    count = 0
    all_picks = result.qualified_picks + result.rejected_picks

    for pick in all_picks:
        try:
            conn.execute(
                """INSERT INTO qualification_log
                   (match_id, match_date, home_team, away_team, league_name,
                    market, pick_quality_score, tier,
                    data_quality_score, model_consensus_score, calibration_score,
                    edge_score, volatility_score, xg_reality_score,
                    clv_adjustment, roi_adjustment, rejection_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    match_id, match_date, home_team, away_team, league_name,
                    pick.market, pick.pick_quality_score, pick.tier,
                    pick.layer_scores.get("data_quality", 0),
                    pick.layer_scores.get("model_consensus", 0),
                    pick.layer_scores.get("calibration", 0),
                    pick.layer_scores.get("market_edge", 0),
                    pick.layer_scores.get("volatility", 0),
                    pick.layer_scores.get("xg_reality", 0),
                    pick.layer_scores.get("clv", 0),
                    pick.layer_scores.get("historical_roi", 0),
                    pick.rejection_reason or None,
                ),
            )
            count += 1
        except Exception as e:
            logger.debug(f"Failed to log qualification for {pick.market}: {e}")

    conn.commit()
    return count

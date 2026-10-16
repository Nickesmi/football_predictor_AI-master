"""The legacy official selection (pipeline steps 4-5 and grading), evaluated without storing picks."""
from __future__ import annotations

from collections import Counter

LEGACY_POLICY_VERSION = "legacy-m2.5-pipeline"


def legacy_select(conn, candidates: list[dict]) -> tuple[list[dict], Counter]:
    """Return (would-be picks, rejection reason counts). Thresholds are the unchanged production ones."""
    from src.engine.risk_control import apply_risk_filter, get_league_profile, score_confidence
    from src.models.pick import assign_grade

    positive = [dict(c) for c in candidates if c["edge"] > 0.0]
    reasons: Counter = Counter()
    if len(positive) < len(candidates):
        reasons["NO_POSITIVE_EDGE"] += len(candidates) - len(positive)
    for c in positive:
        score_confidence(c, get_league_profile(conn, c.get("league_id", 0)), c.get("model_source", "fallback"))
    passed = apply_risk_filter(positive)
    for c in positive:
        for r in c.get("risk_rejections", []):
            reasons[r["code"]] += 1
    picks = []
    for c in passed:
        grade, stake = assign_grade(c["edge"], c["confidence"], c["league_reliability"], c["model_prob"], c["odds"])
        if grade == "Pass":
            reasons["GRADE_PASS"] += 1
            continue
        picks.append({**c, "grade": grade, "stake_units": min(stake, c.get("max_stake_units", 2.0))})
    return picks, reasons

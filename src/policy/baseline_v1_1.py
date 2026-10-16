"""Baseline selection policy v1.1 — PROPOSAL (M3.1). Not active.

v1 plus two changes:

1. Data quality and demonstrated performance are separate requirements.
   * Data quality (per candidate): both team identities resolved by
     team-identity-v1 (src/identity) and data_quality >= min_data_quality.
     Unresolved identity -> TEAM_IDENTITY_UNRESOLVED.
   * Demonstrated performance (per competition, src/policy/league_evidence.py):
     only VERIFIED_SUFFICIENT evidence with reliability >= min_league_reliability
     counts as a track record. Nothing else is given a reliability value; in
     particular missing evidence is not 65.

2. A defensible initial forward baseline without an invented track record.
   A competition without demonstrated performance can be selected only if the
   owner lists it in provisional_competitions. Provisional selections:
     * need a higher risk-adjusted EV (min_ev_provisional);
     * carry evidence_status "provisional_no_track_record" on every decision;
     * are exactly the forward record that later turns the competition into
       VERIFIED_SUFFICIENT (or not). No retrospective or contaminated data is
       used to qualify them.
   The provisional list is empty by default, so v1.1 selects nothing until the
   owner approves competitions.

Rejection codes for competitions: LEAGUE_UNKNOWN, LEAGUE_NO_VERIFIED_EVIDENCE,
LEAGUE_EVIDENCE_INSUFFICIENT, LEAGUE_EVIDENCE_CONTAMINATED, LEAGUE_UNRELIABLE.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime

from src.policy import baseline_v1 as v1
from src.policy.league_evidence import (CONTAMINATED_OR_UNVERIFIABLE, NO_VERIFIED_EVIDENCE, VERIFIED_INSUFFICIENT,
                                        VERIFIED_SUFFICIENT, LeagueEvidence)

POLICY_NAME = "baseline-policy-v1.1-proposal"

PROVISIONAL = "provisional_no_track_record"
DEMONSTRATED = "demonstrated_track_record"

_CLASS_REASON = {
    NO_VERIFIED_EVIDENCE: "LEAGUE_NO_VERIFIED_EVIDENCE",
    VERIFIED_INSUFFICIENT: "LEAGUE_EVIDENCE_INSUFFICIENT",
    CONTAMINATED_OR_UNVERIFIABLE: "LEAGUE_EVIDENCE_CONTAMINATED",
}


@dataclass(frozen=True)
class PolicyV11Config:
    base: v1.PolicyConfig = v1.PolicyConfig()
    min_league_reliability: float = 60.0
    provisional_competitions: tuple[str, ...] = ()
    min_ev_provisional: float = 0.04
    provisional_stake_units: float = 0.5
    require_identity: bool = True

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:12]


DEFAULT_CONFIG = PolicyV11Config()


def version(config: PolicyV11Config = DEFAULT_CONFIG) -> str:
    return f"{POLICY_NAME}.{config.digest()}"


def evidence_status(competition_key: str | None, evidence: dict[str, LeagueEvidence],
                    config: PolicyV11Config) -> tuple[str | None, dict | None]:
    """(evidence_status, rejection reason). Exactly one of the two is None."""
    if not competition_key:
        return None, {"code": "LEAGUE_UNKNOWN"}
    ev = evidence.get(competition_key) or LeagueEvidence(competition_key, NO_VERIFIED_EVIDENCE)
    if ev.evidence_class == VERIFIED_SUFFICIENT:
        if ev.reliability is not None and ev.reliability >= config.min_league_reliability:
            return DEMONSTRATED, None
        return None, {"code": "LEAGUE_UNRELIABLE", "reliability": ev.reliability,
                      "required": config.min_league_reliability, "verified_settled": ev.verified_settled}
    if competition_key in config.provisional_competitions:
        return PROVISIONAL, None
    return None, {"code": _CLASS_REASON[ev.evidence_class], "evidence_class": ev.evidence_class,
                  "verified_settled": ev.verified_settled, "quarantined": ev.quarantined}


def evaluate(candidates: list[dict], decided_at: datetime, provenance: dict,
             evidence: dict[str, LeagueEvidence], config: PolicyV11Config = DEFAULT_CONFIG) -> list[v1.Decision]:
    tag = version(config)
    decisions: list[v1.Decision] = []
    statuses: dict[int, str | None] = {}
    for i, c in enumerate(candidates):
        status, league_reason = evidence_status(c.get("competition_key"), evidence, config)
        statuses[i] = status
        reasons = v1.candidate_reasons(
            c, decided_at, provenance, {}, config.base,
            league_check=lambda _c, r=league_reason: r,
            min_ev=config.min_ev_provisional if status == PROVISIONAL else None,
        )
        if config.require_identity and not c.get("identity_resolved"):
            reasons.append({"code": "TEAM_IDENTITY_UNRESOLVED", "identity_version": c.get("identity_version")})
        odds = float(c.get("odds") or 0.0)
        ev_value = v1.risk_adjusted_ev(float(c.get("model_prob") or 0.0), odds, config.base) if odds > 0 else None
        d = v1.Decision(str(c["match_id"]), c.get("market", ""), c.get("selection", ""), selected=not reasons,
                        reasons=reasons, risk_adjusted_ev=ev_value, policy_version=tag)
        d.evidence_status = status  # type: ignore[attr-defined]
        decisions.append(d)

    by_match: dict[str, list[int]] = {}
    for i, d in enumerate(decisions):
        if d.selected:
            by_match.setdefault(d.match_id, []).append(i)
    for idx in by_match.values():
        idx.sort(key=lambda i: (-(decisions[i].risk_adjusted_ev or 0.0), decisions[i].market, decisions[i].selection))
        keep = decisions[idx[0]]
        keep.stake_units = (config.provisional_stake_units if statuses[idx[0]] == PROVISIONAL
                            else config.base.flat_stake_units)
        for i in idx[1:]:
            decisions[i].selected = False
            decisions[i].reasons.append({"code": "CORRELATED_EXPOSURE", "kept_market": keep.market,
                                         "kept_selection": keep.selection})
    return decisions


def decision_dict(d: v1.Decision) -> dict:
    out = d.to_dict()
    out["evidence_status"] = getattr(d, "evidence_status", None)
    return out

"""Baseline selection policy v1 — PROPOSAL (M3 Phase D). Not active.

Evaluates value candidates (the dicts produced by market_value.find_value and
scored by risk_control.score_confidence) and returns one decision per
candidate with every failed rule. Nothing here places bets, writes picks or
changes the legacy pipeline. The shadow runner records its decisions next to
the legacy ones so the two can be compared.

Rules, applied to every candidate (all failures are reported, not just the first):

  PROVENANCE        model_version and calibration_version present and the
                    calibration artifacts frozen (baseline manifest active).
  MARKET            1X2, O/U 2.5 or BTTS only. Corners, cards, player props,
                    handicaps and every other market are excluded.
  ODDS_TYPE         a genuine bookmaker price (odds_type 'bookmaker').
  ODDS_AGE          provider price age known and <= max_quote_age_minutes.
  ODDS_FETCH_AGE    this system fetched the price <= max_fetch_age_minutes
                    before the decision (the price was re-confirmed recently).
  ODDS_RANGE        min_odds <= odds <= max_odds.
  INPUT_QUALITY     data_quality >= min_data_quality and a non-fallback model.
  LEAGUE            the league is qualified: on the owner-approved list, or
                    with at least min_league_settled verified forward
                    predictions and verified reliability >= min_league_reliability.
                    Missing history never qualifies a league.
  EDGE_SANITY       model edge over the price <= max_edge (larger edges are
                    more likely model error than market error).
  RISK_ADJUSTED_EV  (model_prob - uncertainty_margin) * odds - 1 >= min_ev.

Then at most one selection per match: the passing candidate with the highest
risk-adjusted EV; the others are rejected as CORRELATED_EXPOSURE.

Thresholds live in a frozen PolicyConfig whose hash is part of the version
tag. Changing any threshold produces a different version; there is no
runtime relaxation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime

from src.odds.freshness import age_minutes

POLICY_NAME = "baseline-policy-v1-proposal"

ALLOWED_MARKETS = ("1X2", "O/U 2.5", "BTTS")


@dataclass(frozen=True)
class PolicyConfig:
    max_quote_age_minutes: int = 240
    max_fetch_age_minutes: int = 60
    min_odds: float = 1.30
    max_odds: float = 6.00
    min_data_quality: float = 60.0
    min_league_settled: int = 100
    min_league_reliability: float = 60.0
    approved_leagues: tuple[str, ...] = ()
    max_edge: float = 0.15
    uncertainty_margin: float = 0.03
    min_ev: float = 0.02
    flat_stake_units: float = 1.0

    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


DEFAULT_CONFIG = PolicyConfig()


def version(config: PolicyConfig = DEFAULT_CONFIG) -> str:
    return f"{POLICY_NAME}.{config.digest()}"


@dataclass(frozen=True)
class LeagueRecord:
    """Verified forward track record of a league (from evidence-classified prediction_errors)."""
    settled: int = 0
    reliability: float | None = None


@dataclass
class Decision:
    match_id: str
    market: str
    selection: str
    selected: bool
    reasons: list[dict] = field(default_factory=list)
    risk_adjusted_ev: float | None = None
    stake_units: float = 0.0
    policy_version: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _reason(code: str, **detail) -> dict:
    return {"code": code, **detail}


def league_qualified(league: str, record: LeagueRecord | None, config: PolicyConfig) -> tuple[bool, dict]:
    if league in config.approved_leagues:
        return True, {}
    record = record or LeagueRecord()
    if record.settled < config.min_league_settled:
        return False, _reason("LEAGUE_UNPROVEN", settled=record.settled, required=config.min_league_settled)
    if record.reliability is None or record.reliability < config.min_league_reliability:
        return False, _reason("LEAGUE_UNRELIABLE", reliability=record.reliability,
                              required=config.min_league_reliability)
    return True, {}


def risk_adjusted_ev(model_prob: float, odds: float, config: PolicyConfig) -> float:
    return round((model_prob - config.uncertainty_margin) * odds - 1.0, 4)


def candidate_reasons(c: dict, decided_at: datetime, provenance: dict,
                      league_records: dict[str, LeagueRecord], config: PolicyConfig) -> list[dict]:
    reasons: list[dict] = []
    if not (provenance.get("model_version") and provenance.get("calibration_version")
            and provenance.get("calibration_frozen")):
        reasons.append(_reason("PROVENANCE_NOT_FROZEN", **{k: provenance.get(k) for k in (
            "model_version", "calibration_version", "calibration_frozen")}))
    if c.get("market") not in ALLOWED_MARKETS:
        reasons.append(_reason("MARKET_NOT_ALLOWED", market=c.get("market")))
    if c.get("odds_type") != "bookmaker":
        reasons.append(_reason("ODDS_NOT_BOOKMAKER", odds_type=c.get("odds_type")))

    observed = c.get("odds_observed_at")
    if not observed:
        reasons.append(_reason("ODDS_AGE_UNKNOWN"))
    else:
        quote_age = round(age_minutes(decided_at, observed), 1)
        if quote_age < 0 or quote_age > config.max_quote_age_minutes:
            reasons.append(_reason("ODDS_STALE", age_minutes=quote_age, max=config.max_quote_age_minutes))
    fetched = c.get("odds_fetched_at")
    if not fetched:
        reasons.append(_reason("ODDS_FETCH_TIME_UNKNOWN"))
    else:
        fetch_age = round(age_minutes(decided_at, fetched), 1)
        if fetch_age < 0 or fetch_age > config.max_fetch_age_minutes:
            reasons.append(_reason("ODDS_NOT_RECONFIRMED", fetch_age_minutes=fetch_age,
                                   max=config.max_fetch_age_minutes))

    odds = float(c.get("odds") or 0.0)
    if not (config.min_odds <= odds <= config.max_odds):
        reasons.append(_reason("ODDS_OUT_OF_RANGE", odds=odds, min=config.min_odds, max=config.max_odds))

    data_quality = c.get("data_quality")
    if data_quality is None or float(data_quality) < config.min_data_quality:
        reasons.append(_reason("INPUT_QUALITY_LOW", data_quality=data_quality, min=config.min_data_quality))
    if c.get("model_source") in (None, "fallback", "unknown"):
        reasons.append(_reason("MODEL_FALLBACK", model_source=c.get("model_source")))

    ok, why = league_qualified(c.get("league_name", ""), league_records.get(c.get("league_name", "")), config)
    if not ok:
        reasons.append(why)

    edge = float(c.get("edge") or 0.0)
    if edge > config.max_edge:
        reasons.append(_reason("EDGE_IMPLAUSIBLE", edge=edge, max=config.max_edge))
    ev = risk_adjusted_ev(float(c.get("model_prob") or 0.0), odds, config) if odds > 0 else None
    if ev is None or ev < config.min_ev:
        reasons.append(_reason("RISK_ADJUSTED_EV_TOO_LOW", value=ev, min=config.min_ev,
                               uncertainty_margin=config.uncertainty_margin))
    return reasons


def evaluate(candidates: list[dict], decided_at: datetime, provenance: dict,
             league_records: dict[str, LeagueRecord] | None = None,
             config: PolicyConfig = DEFAULT_CONFIG) -> list[Decision]:
    """One Decision per candidate; at most one selected per match."""
    league_records = league_records or {}
    tag = version(config)
    decisions: list[Decision] = []
    for c in candidates:
        reasons = candidate_reasons(c, decided_at, provenance, league_records, config)
        odds = float(c.get("odds") or 0.0)
        ev = risk_adjusted_ev(float(c.get("model_prob") or 0.0), odds, config) if odds > 0 else None
        decisions.append(Decision(str(c["match_id"]), c.get("market", ""), c.get("selection", ""),
                                  selected=not reasons, reasons=reasons, risk_adjusted_ev=ev, policy_version=tag))

    by_match: dict[str, list[Decision]] = {}
    for d in decisions:
        if d.selected:
            by_match.setdefault(d.match_id, []).append(d)
    for passing in by_match.values():
        passing.sort(key=lambda d: (-(d.risk_adjusted_ev or 0.0), d.market, d.selection))
        keep = passing[0]
        keep.stake_units = config.flat_stake_units
        for other in passing[1:]:
            other.selected = False
            other.reasons.append(_reason("CORRELATED_EXPOSURE", kept_market=keep.market,
                                         kept_selection=keep.selection))
    return decisions


def funnel(decisions: list[Decision]) -> dict:
    """Reason-code counts plus totals (a candidate can fail several rules)."""
    counts: dict[str, int] = {}
    for d in decisions:
        for r in d.reasons:
            counts[r["code"]] = counts.get(r["code"], 0) + 1
    return {
        "policy_version": decisions[0].policy_version if decisions else version(),
        "candidates": len(decisions),
        "selected": sum(d.selected for d in decisions),
        "matches_with_selection": len({d.match_id for d in decisions if d.selected}),
        "rejections_by_reason": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
    }


def load_league_records(conn) -> dict[str, LeagueRecord]:
    """Verified forward track record per league, from the evidence classification.

    Uses only prediction_errors rows classified VERIFIED_SETTLED by the
    current classifier. Without an evidence_classification table every league
    is unproven (an empty dict), never assumed reliable.
    """
    from src.db.error_intelligence import _reliability_score
    from src.integrity.evidence import CLASSIFIER_VERSION, VERIFIED_SETTLED

    try:
        rows = conn.execute(
            """SELECT p.league_name, p.confidence, p.correct
               FROM prediction_errors p JOIN evidence_classification e
                 ON e.source_table = 'prediction_errors' AND e.original_pk = p.id
                AND e.classifier_version = ? AND e.classification = ?
               WHERE p.correct IS NOT NULL""",
            (CLASSIFIER_VERSION, VERIFIED_SETTLED),
        ).fetchall()
    except Exception:
        return {}
    grouped: dict[str, list[tuple[float, int]]] = {}
    for league, confidence, correct in rows:
        grouped.setdefault(league, []).append((float(confidence), int(correct)))
    out = {}
    for league, items in grouped.items():
        n = len(items)
        avg_conf = sum(c for c, _ in items) / n
        hit = sum(k for _, k in items) / n * 100.0
        brier = sum((max(1e-7, min(1 - 1e-7, c / 100.0)) - k) ** 2 for c, k in items) / n
        out[league] = LeagueRecord(settled=n, reliability=_reliability_score(n, hit, round(avg_conf - hit, 2), brier))
    return out

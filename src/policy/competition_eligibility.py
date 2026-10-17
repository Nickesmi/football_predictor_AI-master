"""Competition eligibility for research-provisional selections (M3.2). Proposal; not active.

Eligibility says whether a competition's *inputs and prices* are good enough to
run a provisional forward experiment on it. It is never evidence of predictive
performance (that is league_evidence.VERIFIED_SUFFICIENT) and never yields a
reliability score.

Criteria (measured from shadow observations; thresholds in EligibilityCriteria):
  E0 SAMPLE        at least min_observed_fixtures observed fixtures in the competition
  E1 IDENTITY      share of observed fixtures with both teams resolved (team-identity-v1)
  E2 INPUT_QUALITY share of identity-resolved fixtures with data quality >= min_data_quality
  E3 ODDS          share of observed fixtures with a genuine pre-match quote no older than
                   max_quote_age_minutes at decision time (fetched before the decision)
  E4 MARKETS       share of quoted fixtures where 1X2, O/U 2.5 and BTTS are each priced by
                   >= min_bookmakers bookmakers
  E5 CALIBRATION   every observation records model and calibration versions; official
                   (validated) use additionally requires frozen calibration
A competition is RESEARCH_ELIGIBLE only if E0-E5 all hold. Anything else is
NOT_ELIGIBLE with the failed criteria, or INSUFFICIENT_SAMPLE.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass

from src.identity.competitions import COMPETITIONS, competition_for_fixture

RESEARCH_ELIGIBLE = "RESEARCH_ELIGIBLE"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
MODEL_MARKETS = (("1X2", ""), ("TOTALS", "2.5"), ("BTTS", ""))


@dataclass(frozen=True)
class EligibilityCriteria:
    min_observed_fixtures: int = 20
    min_identity_share: float = 0.90
    min_data_quality: float = 60.0
    min_input_quality_share: float = 0.60
    max_quote_age_minutes: int = 240
    min_odds_share: float = 0.80
    min_bookmakers: int = 3
    min_market_share: float = 0.80


@dataclass
class CompetitionEligibility:
    competition_key: str
    status: str
    observed: int
    metrics: dict
    failed: list

    def to_dict(self) -> dict:
        return asdict(self)


def _share(n: int, d: int) -> float | None:
    return round(n / d, 3) if d else None


def assess(conn: sqlite3.Connection, criteria: EligibilityCriteria = EligibilityCriteria()) -> dict[str, CompetitionEligibility]:
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(shadow_observations)")}
    if not cols:
        return {c.key: CompetitionEligibility(c.key, INSUFFICIENT_SAMPLE, 0, {}, ["E0_SAMPLE"]) for c in COMPETITIONS}
    identity_col = "o.identity_json" if "identity_json" in cols else "NULL"
    rows = conn.execute(
        f"""SELECT o.fixture_id, o.decided_at, o.data_quality, o.quote_age_minutes, o.model_version,
                   o.calibration_version, {identity_col} AS identity_json, m.provider, m.league_id
            FROM shadow_observations o JOIN matches m ON m.id = o.fixture_id"""
    ).fetchall()
    grouped: dict[str, list] = {}
    for r in rows:
        comp = competition_for_fixture(r["provider"], r["league_id"])
        if comp:
            grouped.setdefault(comp.key, []).append(r)

    out = {}
    for comp in COMPETITIONS:
        obs = grouped.get(comp.key, [])
        n = len(obs)
        resolved = [o for o in obs if o["identity_json"] and json.loads(o["identity_json"]).get("resolved")]
        good_inputs = [o for o in resolved if (o["data_quality"] or 0) >= criteria.min_data_quality]
        fresh = [o for o in obs if o["quote_age_minutes"] is not None
                 and 0 <= o["quote_age_minutes"] <= criteria.max_quote_age_minutes]
        markets_ok = 0
        for o in fresh:
            ok = True
            for market, line in MODEL_MARKETS:
                books = conn.execute(
                    """SELECT COUNT(DISTINCT bookmaker) FROM odds_quotes WHERE fixture_id = ? AND market = ?
                       AND line = ? AND odds_type = 'bookmaker' AND fetched_at <= ? AND observed_at < commence_time""",
                    (o["fixture_id"], market, line, o["decided_at"]),
                ).fetchone()[0]
                ok = ok and books >= criteria.min_bookmakers
            markets_ok += ok
        provenance_ok = all(o["model_version"] and o["calibration_version"] for o in obs) if obs else False
        metrics = {
            "identity_share": _share(len(resolved), n),
            "input_quality_share": _share(len(good_inputs), len(resolved)),
            "odds_share": _share(len(fresh), n),
            "market_share": _share(markets_ok, len(fresh)),
            "calibration_provenance_recorded": provenance_ok,
        }
        failed = []
        if n < criteria.min_observed_fixtures:
            failed.append("E0_SAMPLE")
        if (metrics["identity_share"] or 0) < criteria.min_identity_share:
            failed.append("E1_IDENTITY")
        if (metrics["input_quality_share"] or 0) < criteria.min_input_quality_share:
            failed.append("E2_INPUT_QUALITY")
        if (metrics["odds_share"] or 0) < criteria.min_odds_share:
            failed.append("E3_ODDS")
        if (metrics["market_share"] or 0) < criteria.min_market_share:
            failed.append("E4_MARKETS")
        if not provenance_ok:
            failed.append("E5_CALIBRATION_PROVENANCE")
        status = INSUFFICIENT_SAMPLE if "E0_SAMPLE" in failed else (NOT_ELIGIBLE if failed else RESEARCH_ELIGIBLE)
        out[comp.key] = CompetitionEligibility(comp.key, status, n, metrics, failed)
    return out

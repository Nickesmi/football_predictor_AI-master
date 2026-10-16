"""League (competition) evidence classes for selection policies (M3.1). Missing evidence is never reliability.

Evidence is counted per competition (provider league id via the fixture), not per
league label: a "Premier League" row may belong to another country's league.
Only prediction_errors rows classified VERIFIED_SETTLED by the evidence
classifier (src/integrity/evidence.py) count as demonstrated performance.

Classes:
  VERIFIED_SUFFICIENT     >= min_settled verified settled forward predictions;
                          reliability is computed from them alone.
  VERIFIED_INSUFFICIENT   1 .. min_settled-1 verified settled predictions;
                          reliability is reported but not usable.
  NO_VERIFIED_EVIDENCE    no verified settled predictions and no quarantined ones.
  CONTAMINATED_OR_UNVERIFIABLE  no verified settled predictions, but quarantined
                          (fabricated, retrospective, unverifiable) rows exist for
                          the competition. They are neither positive nor negative
                          evidence.
Only VERIFIED_SUFFICIENT carries a reliability value a policy may use.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from src.identity.competitions import COMPETITIONS, competition_for_fixture

VERIFIED_SUFFICIENT = "VERIFIED_SUFFICIENT"
VERIFIED_INSUFFICIENT = "VERIFIED_INSUFFICIENT"
NO_VERIFIED_EVIDENCE = "NO_VERIFIED_EVIDENCE"
CONTAMINATED_OR_UNVERIFIABLE = "CONTAMINATED_OR_UNVERIFIABLE"
DEFAULT_MIN_SETTLED = 100


@dataclass(frozen=True)
class LeagueEvidence:
    competition_key: str
    evidence_class: str
    verified_settled: int = 0
    quarantined: int = 0
    reliability: float | None = None  # only for VERIFIED_SUFFICIENT

    @property
    def demonstrated(self) -> bool:
        return self.evidence_class == VERIFIED_SUFFICIENT


def classify(verified_settled: int, quarantined: int, reliability: float | None,
             competition_key: str, min_settled: int = DEFAULT_MIN_SETTLED) -> LeagueEvidence:
    if verified_settled >= min_settled:
        return LeagueEvidence(competition_key, VERIFIED_SUFFICIENT, verified_settled, quarantined, reliability)
    if verified_settled > 0:
        return LeagueEvidence(competition_key, VERIFIED_INSUFFICIENT, verified_settled, quarantined, None)
    if quarantined > 0:
        return LeagueEvidence(competition_key, CONTAMINATED_OR_UNVERIFIABLE, 0, quarantined, None)
    return LeagueEvidence(competition_key, NO_VERIFIED_EVIDENCE, 0, 0, None)


def load_league_evidence(conn: sqlite3.Connection, min_settled: int = DEFAULT_MIN_SETTLED) -> dict[str, LeagueEvidence]:
    """Evidence class for every tracked competition (every competition gets a class, never a default score)."""
    from src.db.error_intelligence import _reliability_score
    from src.integrity.evidence import CLASSIFIER_VERSION, VERIFIED, VERIFIED_SETTLED

    verified: dict[str, list[tuple[float, int]]] = {}
    quarantined: dict[str, int] = {}
    try:
        rows = conn.execute(
            """SELECT e.classification, m.provider, m.league_id,
                      COALESCE(p.confidence, json_extract(q.original_row_json, '$.confidence')),
                      COALESCE(p.correct, json_extract(q.original_row_json, '$.correct'))
               FROM evidence_classification e
               LEFT JOIN prediction_errors p ON p.id = e.original_pk
               LEFT JOIN evidence_quarantine q ON q.source_table = e.source_table AND q.original_pk = e.original_pk
               JOIN matches m ON m.id = COALESCE(p.fixture_id, json_extract(q.original_row_json, '$.fixture_id'))
               WHERE e.source_table = 'prediction_errors' AND e.classifier_version = ?""",
            (CLASSIFIER_VERSION,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for cls, provider, league_id, confidence, correct in rows:
        comp = competition_for_fixture(provider, league_id)
        if comp is None:
            continue
        if cls == VERIFIED_SETTLED and correct is not None:
            verified.setdefault(comp.key, []).append((float(confidence), int(correct)))
        elif cls not in VERIFIED:
            quarantined[comp.key] = quarantined.get(comp.key, 0) + 1
    out = {}
    for comp in COMPETITIONS:
        items = verified.get(comp.key, [])
        reliability = None
        if len(items) >= min_settled:
            n = len(items)
            avg = sum(c for c, _ in items) / n
            hit = sum(k for _, k in items) / n * 100
            brier = sum((max(1e-7, min(1 - 1e-7, c / 100)) - k) ** 2 for c, k in items) / n
            reliability = _reliability_score(n, hit, round(avg - hit, 2), brier)
        out[comp.key] = classify(len(items), quarantined.get(comp.key, 0), reliability, comp.key, min_settled)
    return out

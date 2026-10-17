"""M3.1: league evidence classes and policy v1.1 (missing evidence is never reliability)."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.policy import baseline_v1_1 as v11
from src.policy.league_evidence import (CONTAMINATED_OR_UNVERIFIABLE, NO_VERIFIED_EVIDENCE, VERIFIED_INSUFFICIENT,
                                        VERIFIED_SUFFICIENT, LeagueEvidence, classify, load_league_evidence)

NOW = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
FROZEN = {"model_version": "m", "calibration_version": "c", "calibration_frozen": True}
EPL = "england-premier-league"


def _eligible():
    from src.policy.competition_eligibility import RESEARCH_ELIGIBLE, CompetitionEligibility

    return {EPL: CompetitionEligibility(EPL, RESEARCH_ELIGIBLE, 30, {}, [])}


def _c(match_id="m1", market="1X2", selection="home", prob=0.55, odds=2.10, **kw):
    base = {"match_id": match_id, "market": market, "selection": selection, "model_prob": prob, "odds": odds,
            "edge": round(prob - 1 / odds, 4), "odds_type": "bookmaker",
            "odds_observed_at": (NOW - timedelta(minutes=100)).isoformat(),
            "odds_fetched_at": (NOW - timedelta(minutes=10)).isoformat(), "data_quality": 80.0,
            "model_source": "poisson", "league_name": "Premier League", "competition_key": EPL,
            "identity_resolved": True}
    base.update(kw)
    return base


def _codes(d):
    return {r["code"] for r in d.reasons}


@pytest.mark.parametrize("verified, quarantined, expected", [
    (150, 0, VERIFIED_SUFFICIENT), (99, 0, VERIFIED_INSUFFICIENT), (1, 500, VERIFIED_INSUFFICIENT),
    (0, 500, CONTAMINATED_OR_UNVERIFIABLE), (0, 0, NO_VERIFIED_EVIDENCE),
])
def test_classes(verified, quarantined, expected):
    ev = classify(verified, quarantined, 70.0, EPL)
    assert ev.evidence_class == expected
    assert (ev.reliability is not None) == (expected == VERIFIED_SUFFICIENT)  # never a default score


@pytest.mark.parametrize("evidence, code", [
    ({}, "LEAGUE_NO_VERIFIED_EVIDENCE"),
    ({EPL: classify(0, 900, None, EPL)}, "LEAGUE_EVIDENCE_CONTAMINATED"),
    ({EPL: classify(40, 0, None, EPL)}, "LEAGUE_EVIDENCE_INSUFFICIENT"),
    ({EPL: classify(200, 0, 59.9, EPL)}, "LEAGUE_UNRELIABLE"),
])
def test_without_demonstrated_record_nothing_is_selected_by_default(evidence, code):
    [d] = v11.evaluate([_c()], NOW, FROZEN, evidence)
    assert not d.selected and code in _codes(d)


def test_demonstrated_record_selects_with_full_stake():
    from src.policy.baseline_v1_1 import CalibrationEvidence

    [d] = v11.evaluate([_c()], NOW, FROZEN, {EPL: classify(200, 0, 61.0, EPL)},
                       calibration=CalibrationEvidence(True, 500, 0.21, "test"))
    assert d.selected and d.stake_units == 1.0 and d.evidence_status == v11.DEMONSTRATED


def test_provisional_competition_needs_higher_ev_and_is_labelled():
    cfg = v11.PolicyV11Config(provisional_competitions=(EPL,))
    [ok] = v11.evaluate([_c(prob=0.55, odds=2.10)], NOW, FROZEN, {}, cfg, _eligible())  # risk-adj EV 0.092
    [low] = v11.evaluate([_c(prob=0.52, odds=2.05)], NOW, FROZEN, {}, cfg, _eligible())  # 0.0045 < 0.04
    assert ok.selected and ok.evidence_status == v11.PROVISIONAL and ok.stake_units == 0.5
    assert not low.selected and "RISK_ADJUSTED_EV_TOO_LOW" in _codes(low)


def test_provisional_list_does_not_override_contaminated_data_quality_or_identity():
    cfg = v11.PolicyV11Config(provisional_competitions=(EPL,))
    [d] = v11.evaluate([_c(identity_resolved=False, data_quality=55)], NOW, FROZEN, {}, cfg, _eligible())
    assert {"TEAM_IDENTITY_UNRESOLVED", "INPUT_QUALITY_LOW"} <= _codes(d)


def test_unknown_competition_and_version_tag():
    [d] = v11.evaluate([_c(competition_key=None)], NOW, FROZEN, {})
    assert "LEAGUE_UNKNOWN" in _codes(d)
    assert v11.version() != v11.version(v11.PolicyV11Config(provisional_competitions=(EPL,)))


def test_one_selection_per_match():
    cfg = v11.PolicyV11Config(provisional_competitions=(EPL,))
    ds = v11.evaluate([_c(), _c(market="BTTS", selection="yes", prob=0.62, odds=1.95)], NOW, FROZEN, {}, cfg,
                      _eligible())
    assert sum(d.selected for d in ds) == 1


def test_load_league_evidence_counts_by_competition_id_not_label(tmp_path):
    from src.db.database import init_db
    from src.integrity import evidence

    conn = sqlite3.connect(tmp_path / "e.db")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    # Same label "Premier League": one English (39) verified row, one Egyptian (233) placeholder row.
    rows = [("1", 39, (60.0, 25.0, 15.0)), ("2", 233, (33.0, 33.0, 33.0))]
    for fid, lid, probs in rows:
        conn.execute("INSERT INTO matches (id, date, kickoff, home_team, away_team, league_name, league_id, provider) "
                     "VALUES (?, '2026-07-01', '20:00', ?, 'B', 'Premier League', ?, 'api_football')",
                     (fid, f"A{fid}", lid))
        conn.execute(
            """INSERT INTO prediction_errors (fixture_id, match_date, league_name, home_team, away_team,
                   predicted_result, home_win_pct, draw_pct, away_win_pct, confidence, confidence_bucket,
                   actual_result, home_goals, away_goals, correct, created_at)
               VALUES (?, '2026-07-01', 'Premier League', ?, 'B', 'home', ?, ?, ?, ?, 'x', 'home', 1, 0, 1,
                       '2026-07-01 08:00:00')""", (fid, f"A{fid}", *probs, max(probs)))
    conn.commit()
    classified = evidence.classify_all(conn)
    evidence.record_classification(conn, classified)
    evidence.quarantine(conn, classified)
    ev = load_league_evidence(conn)
    assert ev[EPL].evidence_class == VERIFIED_INSUFFICIENT and ev[EPL].verified_settled == 1
    assert ev[EPL].quarantined == 0                          # the Egyptian placeholder is not England's
    assert all(e.reliability is None for e in ev.values())
    assert len(ev) == 14


def test_provisional_requires_research_eligibility():
    from src.policy.competition_eligibility import NOT_ELIGIBLE, RESEARCH_ELIGIBLE, CompetitionEligibility

    cfg = v11.PolicyV11Config(provisional_competitions=(EPL,))
    [none] = v11.evaluate([_c()], NOW, FROZEN, {}, cfg)
    assert "COMPETITION_NOT_RESEARCH_ELIGIBLE" in _codes(none)
    bad = {EPL: CompetitionEligibility(EPL, NOT_ELIGIBLE, 30, {}, ["E3_ODDS"])}
    [d] = v11.evaluate([_c()], NOW, FROZEN, {}, cfg, bad)
    assert not d.selected
    assert next(r for r in d.reasons if r["code"] == "COMPETITION_NOT_RESEARCH_ELIGIBLE")["failed"] == ["E3_ODDS"]
    ok = {EPL: CompetitionEligibility(EPL, RESEARCH_ELIGIBLE, 30, {}, [])}
    [d] = v11.evaluate([_c()], NOW, FROZEN, {}, cfg, ok)
    assert d.selected and d.evidence_status == v11.PROVISIONAL and d.stake_units == 0.5


def test_eligibility_never_outranks_missing_performance_evidence():
    from src.policy.competition_eligibility import RESEARCH_ELIGIBLE, CompetitionEligibility

    ok = {EPL: CompetitionEligibility(EPL, RESEARCH_ELIGIBLE, 30, {}, [])}
    [d] = v11.evaluate([_c()], NOW, FROZEN, {}, v11.DEFAULT_CONFIG, ok)   # eligible but not on the owner list
    assert not d.selected and "LEAGUE_NO_VERIFIED_EVIDENCE" in _codes(d)


def test_eligibility_assessment_from_shadow_observations(tmp_path):
    import json as _json

    from src.db.database import init_db
    from src.policy.competition_eligibility import (INSUFFICIENT_SAMPLE, NOT_ELIGIBLE, RESEARCH_ELIGIBLE,
                                                    EligibilityCriteria, assess)
    from src.shadow.store import ensure_shadow_tables
    from src.shadow import cycle

    conn = sqlite3.connect(tmp_path / "s.db")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    ensure_shadow_tables(conn)
    cycle._ensure(conn)
    for i in range(4):
        fid = f"f{i}"
        conn.execute("INSERT INTO matches (id, date, home_team, away_team, league_name, league_id, status, provider) "
                     "VALUES (?, '2030-01-01', 'A', 'B', 'Premier League', 39, 'NS', 'api_football')", (fid,))
        conn.execute(
            """INSERT INTO shadow_observations (cycle_id, fixture_id, decided_at, kickoff, minutes_to_kickoff,
                   legacy_policy_version, v1_policy_version, candidates_json, legacy_json, v1_json,
                   v1_assuming_frozen_json, data_quality, quote_age_minutes, model_version, calibration_version,
                   identity_json)
               VALUES ('c', ?, '2030-01-01T10:00:00+00:00', '2030-01-01T12:00:00+00:00', 120, 'l', 'v', '[]',
                       '{}', '{}', '{}', 75, 100, 'm', 'cal', ?)""", (fid, _json.dumps({"resolved": True})))
        for market, sel, line in (("1X2", "home", ""), ("TOTALS", "over", "2.5"), ("BTTS", "yes", "")):
            for book in ("a", "b", "c"):
                conn.execute(
                    """INSERT INTO odds_quotes (fixture_id, odds_type, provider, provider_event_id, bookmaker,
                           market, selection, line, decimal_odds, implied_probability, observed_at, commence_time,
                           fetched_at) VALUES (?, 'bookmaker', 'p', 'e', ?, ?, ?, ?, 2.0, 0.5,
                           '2030-01-01T08:20:00+00:00', '2030-01-01T12:00:00+00:00', '2030-01-01T09:30:00+00:00')""",
                    (fid, book, market, sel, line))
    conn.commit()
    small = assess(conn)
    assert small["england-premier-league"].status == INSUFFICIENT_SAMPLE
    assert small["spain-la-liga"].status == INSUFFICIENT_SAMPLE
    ok = assess(conn, EligibilityCriteria(min_observed_fixtures=4))
    assert ok["england-premier-league"].status == RESEARCH_ELIGIBLE
    strict_books = assess(conn, EligibilityCriteria(min_observed_fixtures=4, min_bookmakers=4))
    assert strict_books["england-premier-league"].status == NOT_ELIGIBLE
    assert strict_books["england-premier-league"].failed == ["E4_MARKETS"]


def test_validated_needs_verified_calibration_as_well_as_performance():
    from src.policy.baseline_v1_1 import CalibrationEvidence

    ev = {EPL: classify(200, 0, 61.0, EPL)}
    [d] = v11.evaluate([_c()], NOW, FROZEN, ev)                                  # calibration unverified
    assert not d.selected and d.layers["selection_status"] == "REJECTED"
    assert "CALIBRATION_NOT_VERIFIED" in _codes(d)
    assert d.layers["calibration_evidence"] == "UNVERIFIED" and d.layers["forward_performance"] == VERIFIED_SUFFICIENT
    [d] = v11.evaluate([_c()], NOW, FROZEN, ev, calibration=CalibrationEvidence(True, 500, 0.21, "test"))
    assert d.selected and d.layers["selection_status"] == "VALIDATED"


def test_layers_keep_the_five_questions_apart():
    cfg = v11.PolicyV11Config(provisional_competitions=(EPL,))
    [d] = v11.evaluate([_c()], NOW, FROZEN, {}, cfg, _eligible())
    assert d.layers == {"input_quality": "PASS", "calibration_evidence": "UNVERIFIED",
                        "forward_performance": NO_VERIFIED_EVIDENCE, "market_value": "POSITIVE_RISK_ADJUSTED_EV",
                        "selection_status": "RESEARCH_PROVISIONAL"}
    out = v11.decision_dict(d)
    assert "not a validated selection" in out["disclaimer"] and "profitab" in out["disclaimer"]
    [bad] = v11.evaluate([_c(data_quality=40, prob=0.50, odds=2.06)], NOW, FROZEN, {}, cfg, _eligible())
    assert (bad.layers["input_quality"], bad.layers["market_value"], bad.layers["selection_status"]) == \
        ("FAIL", "FAIL", "REJECTED")


def test_missing_evidence_never_becomes_a_score():
    [d] = v11.evaluate([_c()], NOW, FROZEN, {})
    assert d.layers["forward_performance"] == NO_VERIFIED_EVIDENCE
    assert all(isinstance(v, str) for v in d.layers.values())                      # statuses, never numbers


def test_deselected_correlated_candidate_is_rejected_in_layers():
    cfg = v11.PolicyV11Config(provisional_competitions=(EPL,))
    ds = v11.evaluate([_c(), _c(market="BTTS", selection="yes", prob=0.62, odds=1.95)], NOW, FROZEN, {}, cfg,
                      _eligible())
    assert sorted(d.layers["selection_status"] for d in ds) == ["REJECTED", "RESEARCH_PROVISIONAL"]

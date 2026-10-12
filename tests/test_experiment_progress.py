"""
Regression tests for the authoritative baseline_contract.md 1,000-pick
experiment counter (src.db.picks_repo.get_experiment_progress).

Confirms every known contamination source is excluded:
- manual picks (api/services/portfolio_service.py::place_pick)
- collection_worker.py's raw "NO PICK" placeholder row / any unrecognized
  model_source (an allowlist, not a blocklist, so an unanticipated tag like a
  future "test" marker is excluded by default too)
- legacy prediction_log rows (can't appear at all — different table)
- qualification_log rows (can't appear at all — different table)
- unsettled (pending) picks
- voided picks
- picks with missing provenance or produced under another model/calibration/policy version
"""
import json

import pytest

from src import config
from src.db.picks_repo import get_experiment_progress, insert_pick

MANIFEST = {
    "baseline_version": "football-test-v1",
    "model_version": "model-aaaaaaaaaaaa",
    "calibration_version": "cal-bbbbbbbbbbbb",
    "selection_policy_version": "sel-cccccccccccc",
}
PROVENANCE = {
    **{k: MANIFEST[k] for k in ("baseline_version", "model_version", "calibration_version", "selection_policy_version")},
    "code_revision": "abc123",
    "runtime": '{"python": "3.11"}',
    "input_data_timestamp": "2026-01-01T12:00:00+00:00",
    "inputs_json": "{}",
}


@pytest.fixture(autouse=True)
def active_baseline(tmp_path, monkeypatch):
    manifest_dir = tmp_path / "baselines"
    (manifest_dir / MANIFEST["baseline_version"]).mkdir(parents=True)
    (manifest_dir / MANIFEST["baseline_version"] / "manifest.json").write_text(json.dumps(MANIFEST))
    monkeypatch.setattr(config, "BASELINE_FREEZE", MANIFEST["baseline_version"])
    monkeypatch.setattr(config, "BASELINE_MANIFEST_DIR", manifest_dir)


def _insert_match(conn, match_id):
    conn.execute(
        """INSERT INTO matches (id, date, home_team, away_team, league_name, status)
           VALUES (?, '2026-01-01', 'Home FC', 'Away FC', 'Test League', 'FT')""",
        (match_id,),
    )


def _insert_settled_pick(conn, match_id, model_source, result, market="1X2", selection="home", provenance=None):
    pick_id = insert_pick(conn, {
        **(PROVENANCE if provenance is None else provenance),
        "match_id": match_id,
        "market": market,
        "selection": selection,
        "model_prob": 0.6,
        "implied_prob": 0.5,
        "edge": 0.1,
        "odds_at_pick": 2.0,
        "confidence": 0.7,
        "league_reliability": 8.0,
        "grade": "A",
        "stake_units": 1.0,
        "model_source": model_source,
    })
    if result is not None:
        conn.execute("UPDATE picks SET result = ? WHERE id = ?", (result, pick_id))
    conn.commit()
    return pick_id


def test_automated_pipeline_pick_counts(engine_db):
    _insert_match(engine_db, "m1")
    _insert_settled_pick(engine_db, "m1", "poisson", "won")

    progress = get_experiment_progress(engine_db)
    assert progress["settled_eligible"] == 1


def test_hybrid_and_fallback_sources_also_count(engine_db):
    _insert_match(engine_db, "m1")
    _insert_match(engine_db, "m2")
    _insert_settled_pick(engine_db, "m1", "hybrid", "won", selection="home")
    _insert_settled_pick(engine_db, "m2", "fallback", "lost", selection="away")

    progress = get_experiment_progress(engine_db)
    assert progress["settled_eligible"] == 2


def test_manual_pick_does_not_count(engine_db):
    _insert_match(engine_db, "m1")
    _insert_settled_pick(engine_db, "m1", "manual", "won")

    progress = get_experiment_progress(engine_db)
    assert progress["settled_eligible"] == 0
    assert progress["excluded_non_pipeline"] == 1


def test_unrecognized_model_source_does_not_count(engine_db):
    """Allowlist behavior: a novel/unanticipated tag (e.g. a future 'test'
    marker, or collection_worker.py's raw NO-PICK placeholder which sets no
    model_source at all and falls back to the schema default 'unknown') is
    excluded by default, not accidentally counted."""
    _insert_match(engine_db, "m1")
    _insert_settled_pick(engine_db, "m1", "test", "won")
    _insert_match(engine_db, "m2")
    _insert_settled_pick(engine_db, "m2", "unknown", "won", selection="draw")

    progress = get_experiment_progress(engine_db)
    assert progress["settled_eligible"] == 0
    assert progress["excluded_non_pipeline"] == 2


def test_no_pick_placeholder_row_does_not_count(engine_db):
    """Reproduces collection_worker.py's exact raw INSERT for a low-data-quality
    fixture (bypasses insert_pick entirely, sets no model_source)."""
    _insert_match(engine_db, "m1")
    engine_db.execute(
        """INSERT INTO picks (match_id, market, selection, model_prob, implied_prob,
                               edge, odds_at_pick, grade, result)
           VALUES ('m1', 'NO PICK', 'Quality < 60', 0, 0, 0, 0, 'F', 'NO PICK')"""
    )
    engine_db.commit()

    progress = get_experiment_progress(engine_db)
    assert progress["settled_eligible"] == 0


def test_legacy_prediction_log_row_does_not_count(engine_db):
    engine_db.execute(
        """INSERT INTO prediction_log
               (match_id, match_date, home_team, away_team, league_name,
                market, market_type, predicted_prob, actual_outcome)
           VALUES ('legacy_m1', '2026-01-01', 'H', 'A', 'League', '1X2', 'result', 60.0, 1)"""
    )
    engine_db.commit()

    progress = get_experiment_progress(engine_db)
    assert progress["settled_eligible"] == 0


def test_qualification_log_row_does_not_count(engine_db):
    engine_db.execute(
        """INSERT INTO qualification_log
               (match_id, match_date, home_team, away_team, league_name, market,
                pick_quality_score, tier)
           VALUES ('m_q1', '2026-01-01', 'H', 'A', 'League', '1X2', 90.0, 'S')"""
    )
    engine_db.commit()

    progress = get_experiment_progress(engine_db)
    assert progress["settled_eligible"] == 0


def test_unsettled_pending_pick_does_not_count(engine_db):
    _insert_match(engine_db, "m1")
    _insert_settled_pick(engine_db, "m1", "poisson", result=None)

    progress = get_experiment_progress(engine_db)
    assert progress["settled_eligible"] == 0
    assert progress["pending_eligible"] == 1


def test_voided_pick_does_not_count(engine_db):
    _insert_match(engine_db, "m1")
    _insert_settled_pick(engine_db, "m1", "poisson", "void")

    progress = get_experiment_progress(engine_db)
    assert progress["settled_eligible"] == 0
    assert progress["excluded_voided"] == 1


def test_mixed_population_counts_only_genuine_settled_automated_picks(engine_db):
    """One of everything at once — the composite scenario."""
    _insert_match(engine_db, "m_auto")
    _insert_settled_pick(engine_db, "m_auto", "poisson", "won")

    _insert_match(engine_db, "m_manual")
    _insert_settled_pick(engine_db, "m_manual", "manual", "won", selection="draw")

    _insert_match(engine_db, "m_void")
    _insert_settled_pick(engine_db, "m_void", "hybrid", "void", selection="away")

    _insert_match(engine_db, "m_pending")
    _insert_settled_pick(engine_db, "m_pending", "fallback", None, market="O/U 2.5", selection="over")

    engine_db.execute(
        """INSERT INTO prediction_log
               (match_id, match_date, home_team, away_team, league_name,
                market, market_type, predicted_prob, actual_outcome)
           VALUES ('legacy_m1', '2026-01-01', 'H', 'A', 'League', '1X2', 'result', 60.0, 1)"""
    )
    engine_db.commit()

    progress = get_experiment_progress(engine_db)
    assert progress["settled_eligible"] == 1
    assert progress["excluded_non_pipeline"] == 1
    assert progress["excluded_voided"] == 1
    assert progress["pending_eligible"] == 1


def test_pick_without_provenance_is_excluded(engine_db):
    _insert_match(engine_db, "m1")
    _insert_settled_pick(engine_db, "m1", "poisson", "won", provenance={})

    progress = get_experiment_progress(engine_db)
    assert progress["settled_eligible"] == 0
    assert progress["excluded_missing_or_mismatched_provenance"] == 1


@pytest.mark.parametrize("field, value", [
    ("baseline_version", "football-other"),
    ("model_version", "model-changed00000"),
    ("calibration_version", "cal-changed000000"),
    ("selection_policy_version", "sel-changed000000"),
    ("input_data_timestamp", None),
    ("code_revision", None),
])
def test_pick_with_inconsistent_provenance_is_excluded(engine_db, field, value):
    _insert_match(engine_db, "m1")
    _insert_settled_pick(engine_db, "m1", "poisson", "won", provenance={**PROVENANCE, field: value})

    progress = get_experiment_progress(engine_db)
    assert progress["settled_eligible"] == 0
    assert progress["excluded_missing_or_mismatched_provenance"] == 1


def test_nothing_counts_without_an_active_baseline(engine_db, monkeypatch):
    _insert_match(engine_db, "m1")
    _insert_settled_pick(engine_db, "m1", "poisson", "won")
    monkeypatch.setattr(config, "BASELINE_FREEZE", None)

    progress = get_experiment_progress(engine_db)
    assert progress["baseline_version"] is None
    assert progress["settled_eligible"] == 0

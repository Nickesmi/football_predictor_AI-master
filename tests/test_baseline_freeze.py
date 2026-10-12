"""Frozen football baseline: snapshot, integrity, routing, provenance and reproducibility."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import src.db.database as database
from src import config
from src.baseline import fingerprint as fp
from src.baseline import state as baseline
from src.baseline.freeze import create_baseline
from src.db.database import init_db
from src.db.picks_repo import get_experiment_progress
from src.engine.calibration import ProbabilityCalibrator
from src.engine.pipeline import _pick_calibrator, reproduce_probabilities, run_pipeline

VERSION = "football-test-v1"


def _seed(conn):
    conn.execute(
        "INSERT INTO calibration_models (market_type, fitted_json, samples, brier_score, log_loss) VALUES (?, ?, ?, ?, ?)",
        ("btts", json.dumps({"X_thresholds": [0.1, 0.9], "y_thresholds": [0.2, 0.8], "X_min": 0.1, "X_max": 0.9}), 500, 0.2, 0.6),
    )
    conn.execute(
        """INSERT INTO model_confidence_adjustments
               (league_name, sample_count, avg_confidence, actual_hit_rate, adjustment_factor,
                overconfidence, brier_score, reliability_score, status)
           VALUES ('Premier League', 254, 62.0, 55.0, 1.169, 7.0, 0.21, 38.2, 'developing')"""
    )
    for i in range(5):
        conn.execute(
            """INSERT INTO prediction_log (match_id, match_date, home_team, away_team, league_name,
                                           market, market_type, predicted_prob, actual_outcome)
               VALUES (?, '2026-01-01', 'A', 'B', 'Premier League', 'Over 1.5 Goals', 'goals', 70.0, ?)""",
            (f"p{i}", i % 2),
        )
    conn.commit()


@pytest.fixture
def db(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    _seed(conn)
    monkeypatch.setattr(database, "_connection", conn)
    yield conn
    conn.close()


@pytest.fixture
def frozen(db, tmp_path, monkeypatch):
    manifest_dir = tmp_path / "baselines"
    manifest = create_baseline(db, VERSION, manifest_dir, approved_by="test")
    monkeypatch.setattr(config, "BASELINE_MANIFEST_DIR", manifest_dir)
    monkeypatch.setattr(config, "BASELINE_FREEZE", VERSION)
    return manifest


def _event(event_id, vote=(10, 45, 45)):
    return {
        "id": event_id,
        "tournament": {"uniqueTournament": {"id": 17, "name": "Premier League"}},
        "homeTeam": {"name": "Liverpool"}, "awayTeam": {"name": "Ipswich Town"},
        "status": {"type": "notstarted"}, "homeScore": {}, "awayScore": {},
        "startTimestamp": 1893456000,
        "vote": {"vote1": vote[0], "votex": vote[1], "vote2": vote[2]},
    }


def _table_rows(conn, table):
    return [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()]


# ── Snapshot and manifest ────────────────────────────────────────────────────

def test_freeze_writes_manifest_and_snapshot_and_is_immutable(db, frozen, tmp_path):
    manifest_file = tmp_path / "baselines" / VERSION / "manifest.json"
    assert json.loads(manifest_file.read_text()) == frozen
    assert frozen["model_version"].startswith("model-")
    assert frozen["calibration_version"].startswith("cal-")
    assert frozen["selection_policy_version"].startswith("sel-")
    assert frozen["runtime"] == fp.runtime_fingerprint()
    assert set(frozen["files"]["model"]) == set(fp.MODEL_FILES)
    assert db.execute("SELECT COUNT(*) FROM calibration_models_frozen WHERE baseline_version = ?", (VERSION,)).fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM model_confidence_adjustments_frozen WHERE baseline_version = ?", (VERSION,)).fetchone()[0] == 1
    with pytest.raises(FileExistsError):
        create_baseline(db, VERSION, tmp_path / "baselines", approved_by="test")


def test_integrity_ok_after_freeze_and_status_endpoint(db, frozen):
    report = baseline.integrity_report(db)
    assert report["ok"] is True
    assert report["problems"] == []

    from api.main import app
    status = TestClient(app).get("/api/baseline/status").json()
    assert status["frozen"] is True
    assert status["ok"] is True
    assert status["manifest"]["model_version"] == frozen["model_version"]


def test_status_endpoint_when_not_frozen(db):
    from api.main import app
    assert TestClient(app).get("/api/baseline/status").json() == {"frozen": False, "baseline_version": None}


# ── Accumulating results cannot move frozen parameters or rules ─────────────

def _accumulate_results(conn, n=40):
    conn.execute(
        """INSERT INTO matches (id, date, home_team, away_team, league_name, status, home_goals, away_goals)
           VALUES ('settled-m', '2026-01-02', 'Liverpool', 'Ipswich Town', 'Premier League', 'FT', 2, 0)"""
    )
    for i in range(n):
        conn.execute(
            """INSERT INTO picks (match_id, market, selection, model_prob, implied_prob, edge, odds_at_pick,
                                  confidence, league_reliability, grade, stake_units, model_source, result, pnl_units, clv_pct)
               VALUES ('settled-m', '1X2', ?, 0.55, 0.5, 0.05, 2.0, 0.6, 9.0, 'A', 1.0, 'poisson', ?, ?, 2.5)""",
            (f"sel{i}", "won" if i % 3 else "lost", 1.0 if i % 3 else -1.0),
        )
    for i in range(300):
        prob = 55.0 + (i % 40)  # 55-94 %, outcome rate rising with probability
        conn.execute(
            """INSERT INTO prediction_log (match_id, match_date, home_team, away_team, league_name,
                                           market, market_type, predicted_prob, actual_outcome)
               VALUES (?, '2026-01-02', 'A', 'B', 'Premier League', 'Over 1.5 Goals', 'goals', ?, ?)""",
            (f"grow{i}", prob, 1 if (i * 7) % 100 < prob else 0),
        )
    for i in range(120):
        conn.execute(
            """INSERT INTO prediction_errors (fixture_id, match_date, league_name, home_team, away_team,
                   predicted_result, home_win_pct, draw_pct, away_win_pct, confidence, confidence_bucket,
                   actual_result, correct)
               VALUES (?, '2026-01-02', 'Premier League', 'A', 'B', 'home', 70, 20, 10, 70, '70-75%', 'away', 0)""",
            (f"err{i}",),
        )
    conn.commit()


def test_accumulating_results_cannot_change_frozen_parameters_or_rules(db, frozen):
    from api.services.system_service import run_nightly_refit
    from src.db.error_intelligence import get_league_adjustment
    from src.engine.isotonic_calibrator import IsotonicCalibrator
    from src.engine.performance_gate import load_market_performance
    from src.engine.qualification_engine import _pick_history_for_qualification

    before = {
        "calibrator": _pick_calibrator(db).to_state(),
        "gate": load_market_performance(db),
        "league_adj": get_league_adjustment(db, "Premier League"),
        "bilqe_history": _pick_history_for_qualification(db),
        "prod_calibration": _table_rows(db, "calibration_models"),
        "prod_adjustments": _table_rows(db, "model_confidence_adjustments"),
    }
    _accumulate_results(db)
    assert run_nightly_refit(db) == "shadow"

    assert _pick_calibrator(db).to_state() == before["calibrator"]
    assert before["calibrator"]["fitted"] is False  # 0 settled picks at freeze time
    assert load_market_performance(db) == before["gate"]
    assert get_league_adjustment(db, "Premier League") == before["league_adj"]
    assert _pick_history_for_qualification(db) == before["bilqe_history"]
    assert _table_rows(db, "calibration_models") == before["prod_calibration"]
    assert _table_rows(db, "model_confidence_adjustments") == before["prod_adjustments"]
    shadow_markets = {r[0] for r in db.execute("SELECT market_type FROM calibration_models_shadow")}
    assert shadow_markets == {"goals"}
    assert db.execute(
        "SELECT sample_count FROM model_confidence_adjustments_shadow WHERE league_name = 'Premier League'"
    ).fetchone()[0] == 120
    loaded = IsotonicCalibrator(production=True)
    loaded.load_from_db(db)
    assert set(loaded._models) == {"btts"}
    assert baseline.integrity_report(db)["ok"] is True

    # The same accumulated data WOULD have moved the unfrozen system:
    config.BASELINE_FREEZE = None
    try:
        assert _pick_calibrator(db).to_state()["fitted"] is True
        assert load_market_performance(db) != before["gate"]
    finally:
        config.BASELINE_FREEZE = VERSION


# ── Controlled rejection of production model changes ───────────────────────

def test_production_isotonic_refit_is_refused_while_frozen(db, frozen):
    from src.engine.isotonic_calibrator import get_isotonic_calibrator
    import src.engine.isotonic_calibrator as iso

    iso._calibrator_instance = None
    try:
        with pytest.raises(baseline.BaselineFrozenError):
            get_isotonic_calibrator(db).fit_all(db)
    finally:
        iso._calibrator_instance = None


def test_admin_retrain_endpoint_returns_409_while_frozen(db, frozen, monkeypatch):
    from api.main import app
    import src.engine.isotonic_calibrator as iso

    monkeypatch.setattr(iso, "_calibrator_instance", None)
    monkeypatch.setattr(config, "ADMIN_API_KEY", "k" * 40)
    response = TestClient(app).post("/api/admin/retrain-engine", headers={"X-API-Key": "k" * 40})
    assert response.status_code == 409
    assert "frozen" in response.json()["detail"]


# ── Official picks: provenance and point-in-time reproducibility ────────────

def test_frozen_pipeline_records_full_provenance_and_reproduces_exactly(db, frozen):
    result = run_pipeline("2026-01-10", [_event("off-1")], db)
    assert result["final_picks"] == 1
    pick = dict(db.execute("SELECT * FROM picks WHERE match_id = 'off-1'").fetchone())

    for key in ("baseline_version", "model_version", "calibration_version", "selection_policy_version"):
        assert pick[key] == frozen[key]
    assert json.loads(pick["runtime"]) == frozen["runtime"]
    assert pick["code_revision"] == fp.code_revision()
    assert pick["input_data_timestamp"].endswith("+00:00")
    record = json.loads(pick["inputs_json"])
    assert record["probability_inputs"]["home_team"] == "Liverpool"

    calibrator = ProbabilityCalibrator.from_state(baseline.load_artifact(db, "pick_calibrator"))
    reproduced = reproduce_probabilities(pick["inputs_json"], calibrator)
    assert reproduced["raw"] == record["raw_probabilities"]
    assert reproduced["calibrated"] == record["calibrated_probabilities"]
    assert reproduced["calibrated"][pick["market"]][pick["selection"]] == pytest.approx(pick["model_prob"], abs=1e-4)

    db.execute("UPDATE picks SET result = 'won', pnl_units = 1.0 WHERE match_id = 'off-1'")
    db.commit()
    progress = get_experiment_progress(db)
    assert progress["settled_eligible"] == 1
    assert progress["baseline_version"] == VERSION


def _corrupt_frozen_table(conn):
    conn.execute("UPDATE model_confidence_adjustments_frozen SET adjustment_factor = 2.0")
    conn.commit()


def _corrupt_artifact(conn):
    conn.execute("UPDATE baseline_frozen_artifacts SET payload_json = '{}' WHERE artifact = 'gate_market_stats'")
    conn.commit()


def _corrupt_league_profiles(conn):
    conn.execute("UPDATE league_profiles SET min_edge_threshold = 0.0")
    conn.commit()


@pytest.mark.parametrize("tamper, expected_problem", [
    (_corrupt_frozen_table, "frozen table modified: model_confidence_adjustments_frozen"),
    (_corrupt_artifact, "frozen artifact modified: gate_market_stats"),
    (_corrupt_league_profiles, "league_profiles modified"),
])
def test_tampered_frozen_state_blocks_official_picks(db, frozen, tamper, expected_problem):
    tamper(db)
    result = run_pipeline("2026-01-10", [_event("off-2")], db)
    assert expected_problem in result["baseline_integrity_error"]
    assert result["final_picks"] == 0
    assert db.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 0


def test_modified_model_file_blocks_official_picks(db, frozen, monkeypatch):
    real = fp.file_hashes

    def tampered(paths):
        hashes = real(paths)
        if "src/engine/probability_engine.py" in hashes:
            hashes["src/engine/probability_engine.py"] = "0" * 64
        return hashes

    monkeypatch.setattr(fp, "file_hashes", tampered)
    result = run_pipeline("2026-01-10", [_event("off-3")], db)
    assert "model file modified: src/engine/probability_engine.py" in result["baseline_integrity_error"]
    assert db.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 0


def test_runtime_mismatch_blocks_official_picks(db, frozen, monkeypatch):
    monkeypatch.setattr(fp, "runtime_fingerprint", lambda: {"python": "3.13", "requirements_lock_sha256": "x"})
    result = run_pipeline("2026-01-10", [_event("off-4")], db)
    assert result["baseline_integrity_error"].startswith("runtime mismatch")
    assert db.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 0


# ── Inputs keep evolving: Elo and form update while parameters stay frozen ──

def test_elo_and_form_update_while_frozen(db, frozen):
    from src.db.team_state import get_team_state
    from src.engine.live_updater import on_match_finished

    def elo():
        state = get_team_state(db, "Liverpool", "Premier League", "overall")
        return (state.elo, state.matches_played) if state else (None, 0)

    before = elo()
    on_match_finished(db, "res-1", "2026-01-09", "Premier League", "Liverpool", "Ipswich Town", 3, 0)
    after = elo()
    assert after[1] == before[1] + 1
    assert after[0] != before[0]
    assert baseline.integrity_report(db)["ok"] is True


# ── Unfrozen behaviour unchanged ────────────────────────────────────────────

def test_unfrozen_picks_record_provenance_but_never_count(db):
    result = run_pipeline("2026-01-10", [_event("unf-1")], db)
    assert result["final_picks"] == 1
    pick = dict(db.execute("SELECT * FROM picks WHERE match_id = 'unf-1'").fetchone())
    assert pick["baseline_version"] is None
    assert pick["calibration_version"] == "unfrozen"
    assert pick["model_version"].startswith("model-")
    db.execute("UPDATE picks SET result = 'won' WHERE match_id = 'unf-1'")
    db.commit()
    assert get_experiment_progress(db)["settled_eligible"] == 0

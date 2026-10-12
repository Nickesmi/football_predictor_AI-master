"""
Regression tests for gated-pipeline safeguards found during the F1-readiness
review (not part of the original F1-F17 list):

- F19: run_pipeline() must never generate a pick for a fixture that has already
  kicked off (status != NS) — otherwise a re-run mid-day could produce a
  "pre-match" pick using team state that already reflects that match's result.
- run_investment_pipeline_service() must refuse any date_str other than today
  (UTC) — otherwise the pipeline could be used to backfill "picks" for past
  fixtures using current (already-updated) team state.
- F18: picks now carry model_source provenance instead of discarding it.
"""
from datetime import datetime, timezone

from src.engine.pipeline import run_pipeline
from src.db.picks_repo import insert_pick
from src.models.pick import assign_grade


def _sofascore_event(event_id: str, status_type: str, league_id: int = 17):
    return {
        "id": event_id,
        "tournament": {"uniqueTournament": {"id": league_id, "name": "Premier League"}},
        "homeTeam": {"name": "Home FC"},
        "awayTeam": {"name": "Away FC"},
        "status": {"type": status_type},
        "homeScore": {"current": 1} if status_type == "finished" else {},
        "awayScore": {"current": 0} if status_type == "finished" else {},
        "startTimestamp": 1234567890,
    }


def test_pipeline_skips_already_started_fixtures(engine_db):
    events = [
        _sofascore_event("m_ns", "notstarted"),
        _sofascore_event("m_live", "inprogress"),
        _sofascore_event("m_ft", "finished"),
    ]

    result = run_pipeline("2026-01-01", events, engine_db)

    assert result["tracked_matches"] == 1  # only the NS fixture proceeds
    stored_statuses = {
        row["id"]: row["status"]
        for row in engine_db.execute("SELECT id, status FROM matches").fetchall()
    }
    assert stored_statuses == {"m_ns": "NS", "m_live": "LIVE", "m_ft": "FT"}


def test_ns_safeguard_survives_a_rerun_after_kickoff(engine_db):
    """The NS filter reads the status carried on each freshly-fetched event, not
    a stale DB value — so a second run_pipeline() call after the same fixture
    has kicked off must exclude it, even though the first call already wrote a
    'NS' row for that match_id."""
    first_run = run_pipeline("2026-01-01", [_sofascore_event("m_x", "notstarted")], engine_db)
    assert first_run["tracked_matches"] == 1

    second_run = run_pipeline("2026-01-01", [_sofascore_event("m_x", "inprogress")], engine_db)
    assert second_run["tracked_matches"] == 0

    status = engine_db.execute("SELECT status FROM matches WHERE id = 'm_x'").fetchone()["status"]
    assert status == "LIVE"


def test_post_match_leakage_scenario_produces_zero_picks_and_never_consults_team_state(
    engine_db, monkeypatch
):
    """Stronger than the plain NS->LIVE rerun test: the fixture reaches FT with a
    real result, and team_state for the home team is mutated to reflect having
    played (and won) that very match — exactly as a real settlement update would
    do. A second run_pipeline() call for the same match must produce zero picks
    for it, and must never even invoke the probability engine for that match —
    proving the leak is cut off at fixture filtering, not merely absent from the
    final output by luck (e.g. because the probability happened not to move)."""
    import src.engine.pipeline as pipeline_module

    # Run 1: fixture not yet started.
    run_pipeline("2026-01-01", [_sofascore_event("m_leak", "notstarted")], engine_db)

    # Simulate settlement having already happened: the match is FT with a real
    # result, and the home team's rolling state now reflects having played (and
    # won) this exact match — the same team_state row a live settlement update
    # would produce.
    engine_db.execute(
        "UPDATE matches SET status='FT', home_goals=3, away_goals=0 WHERE id='m_leak'"
    )
    engine_db.execute(
        """INSERT INTO team_state
               (team_name, team_name_lower, league, venue, elo, rolling_scored,
                matches_played, last_match_id)
           VALUES ('Home FC', 'home fc', 'Premier League', 'home',
                   1650.0, 3.0, 1, 'm_leak')"""
    )
    engine_db.commit()

    calls = []
    original = pipeline_module.resolve_probability_inputs  # first step that reads team_state

    def spy(match):
        calls.append(match["id"])
        return original(match)

    monkeypatch.setattr(pipeline_module, "resolve_probability_inputs", spy)

    # Run 2: same match, now FT, with post-match team_state already updated.
    result = run_pipeline("2026-01-01", [_sofascore_event("m_leak", "finished")], engine_db)

    assert result["tracked_matches"] == 0
    assert "m_leak" not in calls  # probability engine never even consulted for it
    picks_for_match = engine_db.execute(
        "SELECT COUNT(*) FROM picks WHERE match_id = 'm_leak'"
    ).fetchone()[0]
    assert picks_for_match == 0


def test_pipeline_run_service_rejects_non_today_date():
    from api.services import results_service

    result = results_service.run_investment_pipeline_service("2000-01-01")

    assert result["picks"] == []
    assert "error" in result
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert today not in "2000-01-01"  # sanity: definitely not today


def test_pick_persists_model_source_provenance(engine_db):
    engine_db.execute(
        """INSERT INTO matches (id, date, home_team, away_team, league_name, status)
           VALUES ('m1', '2026-01-01', 'Home FC', 'Away FC', 'Test League', 'NS')"""
    )
    pick_id = insert_pick(engine_db, {
        "match_id": "m1",
        "market": "1X2",
        "selection": "home",
        "model_prob": 0.6,
        "implied_prob": 0.5,
        "edge": 0.1,
        "odds_at_pick": 2.0,
        "confidence": 0.7,
        "league_reliability": 8.0,
        "grade": "A",
        "stake_units": 1.0,
        "model_source": "hybrid",
    })

    row = engine_db.execute(
        "SELECT model_source FROM picks WHERE id = ?", (pick_id,)
    ).fetchone()
    assert row["model_source"] == "hybrid"


def test_pick_model_source_defaults_to_unknown_when_omitted(engine_db):
    engine_db.execute(
        """INSERT INTO matches (id, date, home_team, away_team, league_name, status)
           VALUES ('m2', '2026-01-01', 'Home FC', 'Away FC', 'Test League', 'NS')"""
    )
    pick_id = insert_pick(engine_db, {
        "match_id": "m2",
        "market": "1X2",
        "selection": "away",
        "model_prob": 0.4,
        "implied_prob": 0.35,
        "edge": 0.05,
        "odds_at_pick": 2.5,
        "confidence": 0.5,
        "league_reliability": 7.0,
        "grade": "B",
        "stake_units": 0.5,
    })

    row = engine_db.execute(
        "SELECT model_source FROM picks WHERE id = ?", (pick_id,)
    ).fetchone()
    assert row["model_source"] == "unknown"


def test_pipeline_own_grade_filter_is_the_actual_pick_qualification_mechanism():
    """pipeline.py does NOT call qualification_engine.qualify_picks() at all —
    the thing that actually decides what reaches `picks` is assign_grade()
    (edge/confidence/league_reliability -> A+/A/B/Pass), and pipeline.py drops
    every "Pass" candidate before insert_pick (`if grade == "Pass": continue`).
    This is the real "qualification" gate for the picks table; qualify_picks()
    is a separate, currently-unconnected system that only writes to
    qualification_log (F16)."""
    no_edge_grade, no_edge_stake = assign_grade(
        edge=0.0, confidence=0.9, league_reliability=9.0, model_prob=0.6, odds=2.0
    )
    assert no_edge_grade == "Pass"
    assert no_edge_stake == 0.0

    strong_grade, strong_stake = assign_grade(
        edge=0.10, confidence=0.8, league_reliability=9.0, model_prob=0.6, odds=2.0
    )
    assert strong_grade in {"A+", "A", "B"}
    assert strong_stake > 0.0

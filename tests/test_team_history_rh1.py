"""M3.3 Package A: point-in-time team history reconstructed per competition id (rh1)."""
import json
from datetime import datetime, timezone

import pytest

from src.history import reconstruct as rh
from src.identity.competitions import competition_for_fixture

EPL = competition_for_fixture("api_football", 39)
SERIE_A = competition_for_fixture("api_football", 135)
_seq = [0]


def _fx(conn, home, away, hg, ag, date, kickoff="15:00", league_id=39, label="Premier League",
        provider="api_football", fid=None, status="FT"):
    _seq[0] += 1
    conn.execute("INSERT INTO matches (id, date, kickoff, home_team, away_team, league_name, league_id, status, "
                 "provider, home_goals, away_goals) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (fid or f"x{_seq[0]}", date, kickoff, home, away, label, league_id, status, provider, hg, ag))


def _at(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def test_same_mathematics_as_the_production_updater(engine_db, tmp_path):
    """Data-only: on clean single-competition data the rebuild equals on_match_finished exactly."""
    import sqlite3

    from src.db.database import init_db
    from src.engine.live_updater import on_match_finished

    games = [("Alpha", "Beta", 2, 1, "2026-08-01"), ("Beta", "Alpha", 0, 0, "2026-08-08"),
             ("Alpha", "Beta", 3, 2, "2026-08-15"), ("Beta", "Alpha", 1, 4, "2026-08-22"),
             ("Alpha", "Beta", 1, 1, "2026-08-29"), ("Beta", "Alpha", 2, 0, "2026-09-05")]
    live = sqlite3.connect(tmp_path / "live.db")
    live.row_factory = sqlite3.Row
    init_db(live)
    for i, (h, a, hg, ag, d) in enumerate(games):
        _fx(engine_db, h, a, hg, ag, d)
        on_match_finished(live, f"m{i}", d, "Premier League", h, a, hg, ag)
    rh.reconstruct(engine_db)
    for team in ("Alpha", "Beta"):
        s = rh.state_at(engine_db, EPL, team, _at("2026-12-01T00:00:00"))
        ts = live.execute("SELECT rolling_scored, rolling_conceded, matches_played, form_last5 FROM team_state "
                          "WHERE team_name = ? AND venue = 'overall'", (team,)).fetchone()
        assert (s.rolling_scored, s.rolling_conceded, s.matches_played, s.form_last5) == tuple(ts)


def test_other_competitions_never_enter_the_history(engine_db):
    _fx(engine_db, "Arsenal", "Chelsea", 2, 0, "2026-09-01")
    for i in range(7):  # another country's "Premier League" club named Arsenal
        _fx(engine_db, "Arsenal", f"Belarus {i}", 0, 3, f"2026-08-{10 + i:02d}", league_id=116)
    rh.reconstruct(engine_db)
    s = rh.state_at(engine_db, EPL, "Arsenal", _at("2026-10-01T00:00:00"))
    assert s.matches_played == 1 and s.rolling_scored == round(0.85 * 1.2 + 0.15 * 2, 3)


def test_no_substring_substitution_and_distinct_youth_teams(engine_db):
    _fx(engine_db, "Internacional", "Gremio", 2, 1, "2026-09-01", league_id=71, label="Serie A")
    _fx(engine_db, "Chelsea", "Everton", 1, 0, "2026-09-01")
    rh.reconstruct(engine_db)
    assert rh.state_at(engine_db, SERIE_A, "Inter", _at("2026-10-01T00:00:00")).status == "UNRESOLVED_NO_HISTORY"
    assert rh.state_at(engine_db, EPL, "Chelsea U21", _at("2026-10-01T00:00:00")).status == "UNRESOLVED_NO_HISTORY"
    assert rh.state_at(engine_db, EPL, "Chelsea W", _at("2026-10-01T00:00:00")).matches_played == 0


def test_point_in_time_excludes_later_and_same_window_results(engine_db):
    _fx(engine_db, "Alpha", "Beta", 3, 0, "2026-09-01", kickoff="15:00")
    _fx(engine_db, "Alpha", "Beta", 0, 2, "2026-09-08", kickoff="15:00")
    rh.reconstruct(engine_db)
    before_second = rh.state_at(engine_db, EPL, "Alpha", _at("2026-09-08T17:59:00"))   # < kickoff + 3 h
    after_second = rh.state_at(engine_db, EPL, "Alpha", _at("2026-09-08T18:00:00"))
    assert before_second.matches_played == 1 and after_second.matches_played == 2
    assert rh.state_at(engine_db, EPL, "Alpha", _at("2026-09-01T12:00:00")).status == "NO_HISTORY_BEFORE_DECISION"


def test_unknown_kickoff_counts_from_the_end_of_the_matchday(engine_db):
    _fx(engine_db, "Alpha", "Beta", 1, 0, "2026-09-01", kickoff=None)
    rh.reconstruct(engine_db)
    assert rh.state_at(engine_db, EPL, "Alpha", _at("2026-09-02T02:58:00")).matches_played == 0
    assert rh.state_at(engine_db, EPL, "Alpha", _at("2026-09-02T02:59:00")).matches_played == 1


def test_one_match_stored_by_two_feeds_counts_once_and_keeps_both_ids(engine_db):
    _fx(engine_db, "Alpha", "Beta", 1, 0, "2026-09-01", provider="api_football", fid="af1")
    _fx(engine_db, "Alpha", "Beta", 1, 0, "2026-09-01", provider="matches_db", fid="md1")
    summary = rh.reconstruct(engine_db)
    assert summary["fixtures_used"] == 1 and summary["source_rows_merged"] == 2
    row = engine_db.execute("SELECT source_fixture_ids, matches_played FROM team_history_rh1 "
                            "WHERE team_key = 'alpha'").fetchone()
    assert json.loads(row[0]) == ["api_football:af1", "matches_db:md1"] and row[1] == 1


def test_collision_of_two_clubs_on_one_key_is_excluded(engine_db):
    _fx(engine_db, "FC Alpha", "Beta", 1, 0, "2026-09-01")
    _fx(engine_db, "Alpha", "Gamma", 2, 2, "2026-09-01")     # same key, another fixture, same day
    rh.reconstruct(engine_db)
    assert rh.state_at(engine_db, EPL, "Alpha", _at("2026-10-01T00:00:00")).status == rh.COLLISION
    assert engine_db.execute("SELECT COUNT(*) FROM team_history_rh1 WHERE team_key = 'alpha'").fetchone()[0] == 0


def test_unfinished_or_postponed_fixtures_are_ignored(engine_db):
    _fx(engine_db, "Alpha", "Beta", 1, 0, "2026-09-01", status="PST")
    _fx(engine_db, "Alpha", "Beta", None, None, "2026-09-08", status="NS")
    rh.reconstruct(engine_db)
    assert rh.state_at(engine_db, EPL, "Alpha", _at("2026-10-01T00:00:00")).matches_played == 0


def test_rebuild_is_deterministic(engine_db):
    for i in range(5):
        _fx(engine_db, "Alpha", "Beta", i % 3, (i + 1) % 2, f"2026-09-{i + 1:02d}")
    rh.reconstruct(engine_db)
    first = engine_db.execute("SELECT * FROM team_history_rh1 ORDER BY competition_key, team_key, sequence").fetchall()
    rh.reconstruct(engine_db)
    second = engine_db.execute("SELECT * FROM team_history_rh1 ORDER BY competition_key, team_key, sequence").fetchall()
    assert [tuple(r)[1:] for r in first] == [tuple(r)[1:] for r in second]

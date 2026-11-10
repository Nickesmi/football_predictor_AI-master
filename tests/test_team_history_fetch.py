"""On-demand team history (2026-11-10): off by default, one request per team, finished results stored by
provider team id, refusals back off for 24 h. HTTP is a stub; no network."""
import json

import pytest

from src.ops import app_mode
from src.ops import team_history_fetch as thf


def _raw(fid, home, hid, away, aid, hg, ag, status="FT", league=("Serie A", 135, "Italy"), d="2026-10-20T18:00:00+00:00"):
    return {"fixture": {"id": fid, "date": d, "status": {"short": status}},
            "league": {"id": league[1], "name": league[0], "country": league[2]},
            "teams": {"home": {"id": hid, "name": home}, "away": {"id": aid, "name": away}},
            "goals": {"home": hg, "away": ag}, "score": {"halftime": {"home": 0, "away": 0}}}


@pytest.fixture
def db(engine_db):
    from src.engine.live_updater import TEAM_STATE_IDS_SCHEMA
    engine_db.execute(TEAM_STATE_IDS_SCHEMA)
    return engine_db


def test_off_unless_live_mode_and_explicitly_on():
    assert not thf.enabled({})
    assert not thf.enabled({"TEAM_HISTORY_FETCH": "on"})                     # demo mode
    assert not thf.enabled({"APP_MODE": "live"})
    assert thf.enabled({"APP_MODE": "live", "TEAM_HISTORY_FETCH": "on"})


def test_one_request_by_team_id_stores_finished_results_under_the_bound_record(db):
    db.execute("INSERT INTO team_state (team_name, team_name_lower, league, venue, matches_played) "
               "VALUES ('Internacional', 'internacional', 'Serie A', 'overall', 12)")      # Brazil, same label
    calls = []

    def get(endpoint, params):
        calls.append((endpoint, params))
        return {"errors": [], "response": [
            _raw(1, "Venezia", 517, "Inter", 505, 0, 2),
            _raw(2, "Inter", 505, "Napoli", 492, 1, 1, d="2026-10-25T18:00:00+00:00"),
            _raw(3, "Inter", 505, "Roma", 497, None, None, status="NS", d="2026-11-20T18:00:00+00:00")]}

    r = thf.fetch_team(db, 505, "Inter", http_get=get)
    assert calls == [("fixtures", {"team": "505", "last": 20})]
    assert (r["outcome"], r["fixtures"], r["finished"], r["inserted"]) == ("ok", 3, 2, 2)
    rows = db.execute("SELECT home_team, away_team FROM match_history ORDER BY match_id").fetchall()
    assert [tuple(x) for x in rows] == [("Venezia", "Inter"), ("Inter", "Napoli")]          # never Internacional
    assert db.execute("SELECT team_name FROM team_state_ids WHERE provider_team_id = '505'").fetchone()[0] == "Inter"
    assert thf.fetch_team(db, 505, "Inter", http_get=get)["already_stored"] == 2           # nothing duplicated
    assert thf.recently_fetched(db, 505)
    assert thf.stored_count(db, 505, "2026-11-01") == 2


def test_a_refusal_backs_off_and_the_prefetch_stops(db, tmp_path):
    fixtures = [{"id": "9", "source": "api_football", "kickoff_utc": "2030-01-02T18:00:00Z",
                 "home_team": {"id": 11, "name": "A"}, "away_team": {"id": 12, "name": "B"}}]
    (tmp_path / "fixtures-2030-01-02.json").write_text(json.dumps(fixtures))
    calls = []

    def refuse(endpoint, params):
        calls.append(params["team"])
        return {"errors": {"plan": "Free plans do not have access to this season"}, "response": []}

    rep = thf.prefetch_upcoming(db, tmp_path, "2030-01-01", http_get=refuse)
    assert calls == ["11"] and rep["stopped"] == "provider_refused" and rep["requests"] == 1
    rep = thf.prefetch_upcoming(db, tmp_path, "2030-01-01", http_get=refuse)
    assert calls == ["11"] and rep["stopped"] == "provider_refused_within_24h"


def test_prefetch_skips_teams_with_enough_history_and_respects_the_request_cap(db, tmp_path):
    fixtures = [{"id": str(i), "source": "api_football", "kickoff_utc": "2030-01-03T18:00:00Z",
                 "home_team": {"id": 100 + i, "name": f"H{i}"}, "away_team": {"id": 200 + i, "name": f"A{i}"}}
                for i in range(5)]
    (tmp_path / "fixtures-2030-01-03.json").write_text(json.dumps(fixtures))
    seen = []

    def ok(endpoint, params):
        seen.append(params["team"])
        return {"errors": [], "response": []}

    rep = thf.prefetch_upcoming(db, tmp_path, "2030-01-01", max_requests=3, http_get=ok)
    assert len(seen) == 3 and rep["candidates"] == 10 and rep["ok"] == 3


def test_a_failed_request_is_recorded_without_raising(db):
    def boom(endpoint, params):
        raise RuntimeError("daily budget reached")
    r = thf.fetch_team(db, 7, "X", http_get=boom)
    assert r == {"team_id": "7", "outcome": "error", "error": "RuntimeError"}
    assert not thf.recently_fetched(db, 7)


def test_live_fixture_days_ahead_defaults_to_tomorrow_and_allows_seven():
    from datetime import datetime, timezone
    now = datetime(2030, 1, 5, 12, tzinfo=timezone.utc)
    s = app_mode.settings({"APP_MODE": "live"})
    assert s.days_ahead == 1 and not app_mode.refresh_due(s, "2030-01-07", "2030-01-05", None, None, now)[0]
    s = app_mode.settings({"APP_MODE": "live", "LIVE_FIXTURE_DAYS_AHEAD": "7"})
    assert app_mode.refresh_due(s, "2030-01-12", "2030-01-05", None, None, now)[0]
    assert not app_mode.refresh_due(s, "2030-01-13", "2030-01-05", None, None, now)[0]
    with pytest.raises(ValueError):
        app_mode.settings({"APP_MODE": "live", "LIVE_FIXTURE_DAYS_AHEAD": "8"})

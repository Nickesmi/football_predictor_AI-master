"""Match analysis with team-identity-v1 (2026-11-01): split labels, provider spellings, any API-Football league,
football-data.org competition codes, fail-closed when a team cannot be identified. Fixtures: tests/test_team_identity.py."""
from src.identity.analysis_inputs import competition_for, resolve_for_analysis
from tests.test_team_identity import _fixture, _state



def _resolve(conn, **kw):
    base = dict(provider="api_football", league_id="140", league_name="La Liga", profile_key="LaLiga",
                home="Real Madrid", away="Elche", match_date="2026-09-25")
    return resolve_for_analysis(conn, **{**base, **kw})


def _history(conn, label, league_id, n=40, d="2026-09-10"):
    """n stored results between other clubs: the fallback chain's league baseline (home 2, away 1)."""
    for i in range(n):
        fid = f"{league_id}-{i}"
        conn.execute("INSERT INTO matches (id, date, home_team, away_team, league_name, league_id, status, provider) "
                     "VALUES (?, ?, ?, ?, ?, ?, 'FT', 'api_football')", (fid, d, f"H{i}", f"A{i}", label, league_id))
        conn.execute("INSERT INTO match_history (match_id, match_date, league, home_team, away_team, home_goals, "
                     "away_goals) VALUES (?, ?, ?, ?, ?, 2, 1)", (fid, d, label, f"H{i}", f"A{i}"))


def _laliga(conn):
    _state(conn, "Real Madrid", "LaLiga", 1)                     # stale label the legacy lookup reads
    _state(conn, "Real Madrid", "La Liga", 4, scored=2.5)        # the current history
    _state(conn, "Elche", "La Liga", 4)
    _fixture(conn, "Real Madrid", "Elche", "La Liga", 140)
    _fixture(conn, "Real Madrid", "Elche", "LaLiga", 140)
    conn.execute("UPDATE team_state SET last_match_date = '2026-09-20'")
    _history(conn, "La Liga", 140)


def test_split_labels_use_the_verified_record_with_the_most_matches(engine_db):
    _laliga(engine_db)
    out = _resolve(engine_db)
    assert out["available"] and out["identity"]["home"]["history_league"] == "La Liga"
    # 4 matches (< FULL_HISTORY): the chain; the stale "LaLiga" record is never one of its sources
    h = out["basis"]["home"]
    assert h["path"] == "chain" and h["goals"]["baseline_level"] == "league" and h["records"] == ["Real Madrid | La Liga"]
    assert out["confidence"]["level"] == "Low"


def test_provider_spellings_resolve_by_normalised_name_only(engine_db):
    _laliga(engine_db)
    out = _resolve(engine_db, provider="football_data", league_id="fd-2014", league_code="PD", home="Real Madrid CF",
                   away="Real Racing Club de Santander")
    assert out["identity"]["home"]["team_name_lower"] == "real madrid"
    assert out["available"] and out["basis"]["away"]["identity"].startswith("UNRESOLVED_NO_HISTORY")
    assert out["basis"]["away"]["own_matches"] == 0 and out["confidence"]["level"] == "Low"


def test_any_api_football_league_uses_its_own_id_and_label_and_detects_contamination(engine_db):
    _state(engine_db, "SC Villa", "Premier League", 8)
    _fixture(engine_db, "SC Villa", "Vipers", "Premier League", 585)       # Uganda
    _fixture(engine_db, "SC Villa", "Bantu", "Premier League", 386)        # another country's "Premier League"
    _state(engine_db, "Vipers", "Premier League", 8)
    _fixture(engine_db, "Vipers", "Express", "Premier League", 585)
    _history(engine_db, "Premier League", 585)
    out = _resolve(engine_db, league_id="585", league_name="Premier League", country="Uganda", home="Vipers",
                   away="SC Villa", profile_key="Premier League")
    assert out["identity"]["competition"] == "api-football-585"
    assert out["identity"]["home"]["status"] == "RESOLVED"
    assert out["identity"]["away"]["status"] == "CONTAMINATED_HISTORY"
    # the contaminated record is never read; SC Villa is predicted from Uganda's league averages, labelled Low
    a = out["basis"]["away"]
    assert out["available"] and a["path"] == "chain" and a["records"] == [] and a["identity"].startswith("CONTAMINATED")
    assert out["confidence"]["baseline"]["source"] == "Premier League" and out["confidence"]["level"] == "Low"


def test_women_and_youth_teams_never_take_the_senior_record(engine_db):
    _state(engine_db, "Brisbane Roar", "A-League Women", 6)
    _fixture(engine_db, "Brisbane Roar", "Perth", "A-League Women", 190)
    out = _resolve(engine_db, league_id="190", league_name="A-League Women", home="Brisbane Roar FC W",
                   away="Brisbane Roar", profile_key="A-League Women")
    assert out["identity"]["home"]["status"] == "UNRESOLVED_NO_HISTORY"
    assert out["identity"]["away"]["status"] == "RESOLVED"


def test_old_history_is_reported_and_unknown_competitions_keep_the_legacy_path(engine_db):
    _laliga(engine_db)
    engine_db.execute("UPDATE team_state SET matches_played = 12 WHERE team_name = 'Real Madrid' AND league = 'La Liga'")
    out = _resolve(engine_db, match_date="2026-11-01")
    assert "home_team 'Real Madrid' latest stored result is 42 days before this match; newer results are not " \
           "in the database" in out["warnings"]
    assert out["basis"]["home"]["path"] == "record"
    assert competition_for("football_data", "fd-2013", league_code="BSA") is None
    assert competition_for("sofascore", 8) is None and competition_for(None, None) is None
    assert competition_for("api_football", "x") is None

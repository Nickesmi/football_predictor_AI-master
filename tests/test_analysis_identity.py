"""Match analysis with team-identity-v1 (2026-11-01): split labels, provider spellings, any API-Football league,
football-data.org competition codes, fail-closed when a team cannot be identified. Fixtures: tests/test_team_identity.py."""
from src.identity.analysis_inputs import competition_for, resolve_for_analysis
from tests.test_team_identity import _fixture, _state



def _resolve(conn, **kw):
    base = dict(provider="api_football", league_id="140", league_name="La Liga", profile_key="LaLiga",
                home="Real Madrid", away="Elche", match_date="2026-09-25")
    return resolve_for_analysis(conn, **{**base, **kw})


def _laliga(conn):
    _state(conn, "Real Madrid", "LaLiga", 1)                     # stale label the legacy lookup reads
    _state(conn, "Real Madrid", "La Liga", 4, scored=2.5)        # the current history
    _state(conn, "Elche", "La Liga", 4)
    _fixture(conn, "Real Madrid", "Elche", "La Liga", 140)
    _fixture(conn, "Real Madrid", "Elche", "LaLiga", 140)
    conn.execute("UPDATE team_state SET last_match_date = '2026-09-20'")


def test_split_labels_use_the_verified_record_with_the_most_matches(engine_db):
    _laliga(engine_db)
    out = _resolve(engine_db)
    assert out["available"] and out["identity"]["home"]["history_league"] == "La Liga"
    assert out["home_stats"].matches_played == 4 and out["home_stats"].scored == round(2.5 * 1.12, 2)
    assert out["warnings"] == ["home_team 'Real Madrid' has only 4 matches in its 'La Liga' history",
                               "away_team 'Elche' has only 4 matches in its 'La Liga' history"]


def test_provider_spellings_resolve_by_normalised_name_only(engine_db):
    _laliga(engine_db)
    out = _resolve(engine_db, provider="football_data", league_id="fd-2014", league_code="PD", home="Real Madrid CF",
                   away="Real Racing Club de Santander")
    assert out["identity"]["home"]["team_name_lower"] == "real madrid"
    assert not out["available"] and out["away_stats"] is None
    assert out["blocking"][0].startswith("away_team 'Real Racing Club de Santander' has NO real data — identity "
                                         "UNRESOLVED_NO_HISTORY")


def test_any_api_football_league_uses_its_own_id_and_label_and_detects_contamination(engine_db):
    _state(engine_db, "SC Villa", "Premier League", 8)
    _fixture(engine_db, "SC Villa", "Vipers", "Premier League", 585)       # Uganda
    _fixture(engine_db, "SC Villa", "Bantu", "Premier League", 386)        # another country's "Premier League"
    _state(engine_db, "Vipers", "Premier League", 8)
    _fixture(engine_db, "Vipers", "Express", "Premier League", 585)
    out = _resolve(engine_db, league_id="585", league_name="Premier League", country="Uganda", home="Vipers",
                   away="SC Villa", profile_key="Premier League")
    assert out["identity"]["competition"] == "api-football-585"
    assert out["identity"]["home"]["status"] == "RESOLVED"
    assert out["identity"]["away"]["status"] == "CONTAMINATED_HISTORY" and not out["available"]


def test_women_and_youth_teams_never_take_the_senior_record(engine_db):
    _state(engine_db, "Brisbane Roar", "A-League Women", 6)
    _fixture(engine_db, "Brisbane Roar", "Perth", "A-League Women", 190)
    out = _resolve(engine_db, league_id="190", league_name="A-League Women", home="Brisbane Roar FC W",
                   away="Brisbane Roar", profile_key="A-League Women")
    assert out["identity"]["home"]["status"] == "UNRESOLVED_NO_HISTORY"
    assert out["identity"]["away"]["status"] == "RESOLVED"


def test_old_history_is_reported_and_unknown_competitions_keep_the_legacy_path(engine_db):
    _laliga(engine_db)
    out = _resolve(engine_db, match_date="2026-11-01")
    assert "home_team 'Real Madrid' history ends 2026-09-20 (42 days before this match); newer results are not " \
           "in the database" in out["warnings"]
    assert competition_for("football_data", "fd-2013", league_code="BSA") is None
    assert competition_for("sofascore", 8) is None and competition_for(None, None) is None
    assert competition_for("api_football", "x") is None

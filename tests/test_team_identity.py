"""M3.1 K43: deterministic team identity resolution. Regression fixtures are the real M3 cases."""
import pytest

from src.engine.probability_engine import estimate_probabilities_from_inputs, resolve_probability_inputs
from src.identity import teams
from src.identity.competitions import competition_for_fixture
from src.identity.inputs import UnresolvedTeamIdentity, resolve_identity, resolve_probability_inputs_v2
from src.identity.teams import (AMBIGUOUS, CONTAMINATED, NO_HISTORY, RESOLVED, STALE_MAPPING, UNKNOWN_COMPETITION,
                                UNVERIFIED, normalise_team, resolve_team)

EPL = competition_for_fixture("api_football", 39)
CHAMP = competition_for_fixture("api_football", 40)
LALIGA = competition_for_fixture("api_football", 140)
BUNDES = competition_for_fixture("api_football", 78)
UEL = competition_for_fixture("api_football", 3)


def _state(conn, name, league, played, scored=1.5, conceded=1.0):
    conn.execute(
        """INSERT INTO team_state (team_name, team_name_lower, league, venue, rolling_scored, rolling_conceded,
                                   matches_played, form_last5)
           VALUES (?, ?, ?, 'overall', ?, ?, ?, 0.6)""",
        (name, name.lower().strip(), league, scored, conceded, played),
    )


_fixture_seq = [0]


def _fixture(conn, home, away, label, league_id, provider="api_football", status="FT", fid=None):
    _fixture_seq[0] += 1
    fid = fid or f"t{_fixture_seq[0]}"
    conn.execute("INSERT INTO matches (id, date, home_team, away_team, league_name, league_id, status, provider) "
                 "VALUES (?, '2026-09-01', ?, ?, ?, ?, ?, ?)", (fid, home, away, label, league_id, status, provider))
    return fid


def _match(home, away, league_name, league_id, provider="api_football"):
    return {"id": "x", "home_team": home, "away_team": away, "league_name": league_name, "league_id": league_id,
            "provider": provider}


class TestNormalisation:
    @pytest.mark.parametrize("a, b", [("1. FC Union Berlin", "Union Berlin"), ("SV Werder Bremen", "Werder Bremen"),
                                      ("Atlético Madrid", "Atletico Madrid"), ("Gençlerbirliği S.K.", "Genclerbirligi S K")])
    def test_same_club_spellings_normalise_equal(self, a, b):
        assert normalise_team(a) == normalise_team(b)

    @pytest.mark.parametrize("a, b", [("Chelsea U21", "Chelsea"), ("Werder Bremen II", "Werder Bremen"),
                                      ("Arsenal W", "Arsenal"), ("Union Berlin W", "Union Berlin"),
                                      ("Rangers", "Angers"), ("Inter Turku", "Inter")])
    def test_distinct_teams_never_normalise_equal(self, a, b):
        assert normalise_team(a) != normalise_team(b)


class TestM3RegressionCases:
    def test_championship_team_uses_its_own_history_not_hash_stats(self, engine_db):
        _state(engine_db, "Middlesbrough", "Championship", 6)
        _fixture(engine_db, "Middlesbrough", "Cardiff", "Championship", 40)
        r = resolve_team(engine_db, CHAMP, "Middlesbrough", "api_football")
        assert (r.status, r.history_league, r.matches_played) == (RESOLVED, "Championship", 6)

    def test_la_liga_label_is_found_although_profile_key_is_laliga(self, engine_db):
        _state(engine_db, "Espanyol", "La Liga", 4)
        _fixture(engine_db, "Espanyol", "Getafe", "La Liga", 140)
        assert resolve_team(engine_db, LALIGA, "Espanyol").history_league == "La Liga"

    @pytest.mark.parametrize("stored, provider_name", [("SV Werder Bremen", "Werder Bremen"),
                                                       ("1. FC Union Berlin", "Union Berlin")])
    def test_feed_spellings_resolve_with_competition_verified_history(self, engine_db, stored, provider_name):
        _state(engine_db, stored, "Bundesliga", 4)
        _fixture(engine_db, provider_name, "FC Augsburg", "Bundesliga", 78)
        r = resolve_team(engine_db, BUNDES, provider_name)
        assert (r.status, r.team_name_lower) == (RESOLVED, stored.lower())

    def test_two_records_for_one_name_are_ambiguous_never_merged(self, engine_db):
        _state(engine_db, "Atletico Madrid", "La Liga", 4)
        _state(engine_db, "Atlético Madrid", "LaLiga", 1)
        _fixture(engine_db, "Atletico Madrid", "Sevilla", "La Liga", 140)
        r = resolve_team(engine_db, LALIGA, "Atletico Madrid")
        assert r.status == AMBIGUOUS and r.team_name_lower is None
        assert r.evidence["review"] == "distinct or unverified records"

    def test_label_shared_with_another_country_is_contaminated(self, engine_db):
        _state(engine_db, "Arsenal", "Premier League", 9)
        _fixture(engine_db, "Arsenal", "Chelsea", "Premier League", 39)
        _fixture(engine_db, "Arsenal", "Dinamo Minsk", "Premier League", 116)  # another country's "Premier League"
        r = resolve_team(engine_db, EPL, "Arsenal")
        assert r.status == CONTAMINATED and "116" in r.reason

    def test_history_without_source_fixtures_is_unverified(self, engine_db):
        _state(engine_db, "Rangers", "Scottish Premiership", 1)
        assert resolve_team(engine_db, competition_for_fixture("api_football", 179), "Rangers").status == UNVERIFIED

    def test_no_substring_substitution(self, engine_db):
        _state(engine_db, "Angers", "Premiership", 8)  # legacy matched "Rangers" -> "angers"
        _fixture(engine_db, "Angers", "X", "Premiership", 179)
        assert resolve_team(engine_db, competition_for_fixture("api_football", 179), "Rangers").status == NO_HISTORY

    @pytest.mark.parametrize("name", ["Chelsea U21", "Chelsea W", "Chelsea II"])
    def test_youth_women_reserve_never_take_the_senior_record(self, engine_db, name):
        _state(engine_db, "Chelsea", "Premier League", 6)
        _fixture(engine_db, "Chelsea", "Everton", "Premier League", 39)
        assert resolve_team(engine_db, EPL, name).status == NO_HISTORY

    def test_untracked_competition_is_unknown(self, engine_db):
        assert resolve_team(engine_db, None, "Anyone").status == UNKNOWN_COMPETITION
        assert competition_for_fixture("api_football", 235) is None  # Russian Premier League
        assert competition_for_fixture("sofascore", 39) is None       # ids are per provider space


class TestExplicitMappings:
    def test_resolved_identity_is_remembered_by_provider_id_and_revalidated(self, engine_db):
        _state(engine_db, "SV Werder Bremen", "Bundesliga", 4)
        _fixture(engine_db, "Werder Bremen", "FC Augsburg", "Bundesliga", 78)
        first = resolve_team(engine_db, BUNDES, "Werder Bremen", "api_football", 162, remember=True)
        assert first.source == "normalised_name_in_competition"
        again = resolve_team(engine_db, BUNDES, "Bremen (renamed)", "api_football", 162)
        assert (again.status, again.source, again.team_name_lower) == (RESOLVED, "explicit:auto-verified",
                                                                       "sv werder bremen")
        row = engine_db.execute("SELECT mapping_version FROM team_identity_map").fetchone()
        assert row[0] == teams.TEAM_IDENTITY_VERSION
        engine_db.execute("DELETE FROM team_state WHERE team_name_lower = 'sv werder bremen'")
        assert resolve_team(engine_db, BUNDES, "x", "api_football", 162).status == STALE_MAPPING

    def test_ambiguous_identity_is_never_remembered(self, engine_db):
        _state(engine_db, "Ajax", "Eredivisie", 5)
        _state(engine_db, "AFC Ajax", "VriendenLoterij Eredivisie", 2)
        resolve_team(engine_db, competition_for_fixture("api_football", 88), "Ajax", "api_football", 194, remember=True)
        teams.ensure_tables(engine_db)
        assert engine_db.execute("SELECT COUNT(*) FROM team_identity_map").fetchone()[0] == 0


class TestInputs:
    def test_unresolved_team_fails_closed(self, engine_db):
        _state(engine_db, "Middlesbrough", "Championship", 6)
        _fixture(engine_db, "Middlesbrough", "Cardiff", "Championship", 40)
        with pytest.raises(UnresolvedTeamIdentity) as exc:
            resolve_probability_inputs_v2(engine_db, _match("Middlesbrough", "Nowhere FC", "Championship", 40))
        assert exc.value.resolutions["away"].status == NO_HISTORY

    def test_inputs_carry_identity_provenance(self, engine_db):
        for n in ("Millwall", "Lincoln"):
            _state(engine_db, n, "Championship", 7)
        _fixture(engine_db, "Millwall", "Lincoln", "Championship", 40)
        inputs = resolve_probability_inputs_v2(engine_db, _match("Millwall", "Lincoln", "Championship", 40))
        assert inputs["identity"]["version"] == teams.TEAM_IDENTITY_VERSION
        assert inputs["identity"]["home"]["history_league"] == "Championship"
        assert inputs["home_stats"]["matches_played"] == 7 and inputs["league_key"] == "Premier League"
        assert estimate_probabilities_from_inputs(inputs)["data_quality"] > 50

    def test_same_math_as_legacy_when_legacy_found_the_same_record(self):
        from src.db.database import get_db

        conn = get_db()  # the legacy resolver reads the global connection (K39)
        _state(conn, "Parity Home", "Premier League", 6, 1.7, 0.9)
        _state(conn, "Parity Away", "Premier League", 6, 1.1, 1.4)
        _fixture(conn, "Parity Home", "Parity Away", "Premier League", 39)
        conn.commit()
        m = _match("Parity Home", "Parity Away", "Premier League", 39)
        legacy = resolve_probability_inputs(m)
        v2 = resolve_probability_inputs_v2(conn, m)
        assert v2["home_stats"] == legacy["home_stats"] and v2["away_stats"] == legacy["away_stats"]
        assert estimate_probabilities_from_inputs(v2) == estimate_probabilities_from_inputs(legacy)

    def test_europa_league_keeps_the_legacy_venue_treatment(self, engine_db):
        # "UEFA Europa League" contains "euro" (neutral-venue keyword); the legacy path used the
        # profile key "Champions League" (not neutral). v2 must not silently drop home advantage.
        for n in ("Home UEL", "Away UEL"):
            _state(engine_db, n, "UEFA Europa League", 3, 1.0, 1.0)
        _fixture(engine_db, "Home UEL", "Away UEL", "UEFA Europa League", 3)
        inputs = resolve_probability_inputs_v2(engine_db, _match("Home UEL", "Away UEL", "UEFA Europa League", 3))
        assert inputs["home_stats"]["scored"] == 1.12 and inputs["away_stats"]["scored"] == 0.88

    def test_resolve_identity_uses_provider_team_ids_when_present(self, engine_db):
        _state(engine_db, "Watford", "Championship", 5)
        _state(engine_db, "Charlton", "Championship", 5)
        _fixture(engine_db, "Watford", "Charlton", "Championship", 40)
        m = {**_match("Watford", "Charlton", "Championship", 40), "home_team_id": 38, "away_team_id": 1335}
        res = resolve_identity(engine_db, m, remember=True)
        assert (res["home"].provider_team_id, res["away"].provider_team_id) == ("38", "1335")

"""Result ingestion files each team under the right record (2026-11-05): provider team id first, then exact or
exact normalised name, never a substring. Regression: Inter's Serie A results were filed under the Brazilian
"Internacional" (Italy's and Brazil's top flights share the label "Serie A"; the old state lookup matched
"Inter" with LIKE '%inter%')."""
from src.db.team_state import get_team_state
from src.engine.live_updater import ingest_day_results, on_match_finished, resolve_record


def _state(conn, name, league, played=3):
    conn.execute("INSERT INTO team_state (team_name, team_name_lower, league, venue, matches_played) "
                 "VALUES (?, ?, ?, 'overall', ?)", (name, name.lower(), league, played))


def _played(conn, name, league="Serie A"):
    row = conn.execute("SELECT matches_played FROM team_state WHERE team_name = ? AND league = ? AND venue = "
                       "'overall'", (name, league)).fetchone()
    return row[0] if row else None


def test_inter_is_no_longer_filed_under_internacional(engine_db):
    _state(engine_db, "Internacional", "Serie A", 12)                     # Brazil, same label
    assert get_team_state(engine_db, "Inter", "Serie A", "overall").team_name == "Internacional"   # the old lookup
    on_match_finished(engine_db, "1550176", "2026-10-28", "Serie A", "Venezia", "Inter", 0, 2,
                      provider="api_football", home_team_id="517", away_team_id="505")
    assert _played(engine_db, "Inter") == 1 and _played(engine_db, "Internacional") == 12
    h = engine_db.execute("SELECT home_team, away_team FROM match_history WHERE match_id = '1550176'").fetchone()
    assert tuple(h) == ("Venezia", "Inter")
    on_match_finished(engine_db, "x2", "2026-10-30", "Serie A", "Inter", "Napoli", 1, 0)   # no id: exact name
    assert _played(engine_db, "Inter") == 2 and _played(engine_db, "Internacional") == 12


def test_provider_id_binding_follows_the_club_across_spellings(engine_db):
    _state(engine_db, "FC Bayern München", "Bundesliga", 2)
    rec, how = resolve_record(engine_db, "Bayern München", "Bundesliga", "api_football", 157)
    assert (rec, how) == ("FC Bayern München", "normalised_name")
    assert resolve_record(engine_db, "Bayern Munich", "Bundesliga", "api_football", 157) == ("FC Bayern München",
                                                                                           "provider_id")


def test_two_clubs_with_one_name_in_a_shared_label_never_mix(engine_db):
    on_match_finished(engine_db, "u1", "2026-09-01", "Premier League", "Police", "Vipers", 1, 0,
                      provider="api_football", home_team_id="100", away_team_id="101")     # Uganda
    on_match_finished(engine_db, "r1", "2026-09-02", "Premier League", "Police", "Rayon", 0, 3,
                      provider="api_football", home_team_id="200", away_team_id="201")     # Rwanda
    assert _played(engine_db, "Police", "Premier League") == 1
    assert _played(engine_db, "Police #200", "Premier League") == 1


def test_youth_side_and_ambiguous_names_never_join_another_record(engine_db):
    _state(engine_db, "Inter", "Serie A")
    assert resolve_record(engine_db, "Inter U19", "Serie A")[1] == "new_record"
    _state(engine_db, "Atlético Madrid", "La Liga")
    _state(engine_db, "Atletico Madrid", "La Liga")                       # two records, one normalised name
    assert resolve_record(engine_db, "Atlético de Madrid", "La Liga") == ("Atlético de Madrid", "new_record")
    assert resolve_record(engine_db, "Atletico Madrid", "La Liga") == ("Atletico Madrid", "exact_name")


def test_both_sides_on_one_record_are_not_ingested(engine_db):
    _state(engine_db, "Real Madrid", "La Liga")
    out = on_match_finished(engine_db, "s1", "2026-09-01", "La Liga", "Real Madrid", "Real Madrid CF", 1, 1)
    assert out["skipped"] and _played(engine_db, "Real Madrid", "La Liga") == 3


def test_day_ingestion_passes_provider_ids(engine_db):
    _state(engine_db, "Internacional", "Serie A", 12)
    ingest_day_results(engine_db, [{"match_id": "d1", "match_date": "2026-10-28", "league": "Serie A",
                                    "home_team": "Venezia", "away_team": "Inter", "home_goals": 0, "away_goals": 2,
                                    "provider": "api_football", "home_team_id": "517", "away_team_id": "505"}])
    row = engine_db.execute("SELECT team_name FROM team_state_ids WHERE provider_team_id = '505'").fetchone()
    assert row[0] == "Inter" and _played(engine_db, "Internacional") == 12

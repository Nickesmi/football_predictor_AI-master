"""Cross-competition club history and cached-result recovery (2026-11-03, demo branch). No network.

Regression case: Feyenoord vs Inter (UEFA Champions League, 2026-11-03) was shown as "Prediction unavailable":
the page-load precompute cached a name-only legacy analysis, and the identity path only searched the
Champions League history, where Feyenoord had 2 matches and Inter none, although both clubs have verified
domestic records (Eredivisie, Serie A)."""
import importlib.util
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from src.db.database import init_db
from src.identity import club_membership
from src.identity.analysis_inputs import resolve_for_analysis
from tests.test_team_identity import _fixture, _state

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("recover", ROOT / "scripts" / "recover_cached_results.py")
recover = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recover)

UCL = dict(provider="api_football", league_id="2", league_name="UEFA Champions League", country="World",
           profile_key="Champions League", match_date="2026-11-03")


def _member(conn, tid, lid, lname, country, name, n=8, rounds=None, first="2026-08-09", last="2026-10-10"):
    conn.executescript(club_membership.SCHEMA)
    conn.execute("INSERT INTO provider_team_competitions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'test', 'v')",
                 ("api_football", str(tid), lid, lname, country, name, n, n if rounds is None else rounds, first, last))


def _clubs(conn):
    """Feyenoord (209): 8 Eredivisie + 2 UCL. Inter (505): 5 Serie A, 0 UCL. Another 'Inter' (1150) in
    Colombia's Primera A, and Feyenoord U19 (7981) in the UEFA Youth League."""
    _state(conn, "Feyenoord", "Eredivisie", 8, scored=2.4, conceded=1.0)
    _state(conn, "Feyenoord", "UEFA Champions League", 2, scored=2.0, conceded=3.0)
    _state(conn, "Inter", "Serie A", 5, scored=1.9, conceded=0.9)
    _state(conn, "Inter", "Primera A", 11, scored=1.1, conceded=1.4)
    _state(conn, "Feyenoord U19", "UEFA Youth League", 2)
    for _ in range(3):
        _fixture(conn, "Feyenoord", "Utrecht", "Eredivisie", 88)
        _fixture(conn, "Inter", "Napoli", "Serie A", 135)
        _fixture(conn, "Inter", "Millonarios", "Primera A", 239)
    _fixture(conn, "Feyenoord", "Como", "UEFA Champions League", 2)
    _fixture(conn, "Feyenoord U19", "Inter U19", "UEFA Youth League", 14)
    conn.execute("UPDATE team_state SET last_match_date = '2026-10-10'")
    _member(conn, 209, 88, "Eredivisie", "Netherlands", "Feyenoord")
    _member(conn, 209, 2, "UEFA Champions League", "World", "Feyenoord", n=3, rounds=0)
    _member(conn, 209, 90, "KNVB Beker", "Netherlands", "Feyenoord", n=1, rounds=0, last="2026-10-29")
    _member(conn, 505, 135, "Serie A", "Italy", "Inter")
    _member(conn, 505, 2, "UEFA Champions League", "World", "Inter", n=3, rounds=0)
    _member(conn, 1150, 239, "Primera A", "Colombia", "Inter", n=11, last="2026-10-31")
    _member(conn, 7981, 14, "UEFA Youth League", "World", "Feyenoord U19", n=3, rounds=0)


def _ucl(conn, **kw):
    return resolve_for_analysis(conn, **{**UCL, "home": "Feyenoord", "away": "Inter", "home_id": "209",
                                         "away_id": "505", **kw})


# 1. Feyenoord in the Eredivisie and in the Champions League
def test_feyenoord_uses_its_eredivisie_record_in_the_champions_league(engine_db):
    _clubs(engine_db)
    out = _ucl(engine_db)
    assert out["available"], out["blocking"]
    h = out["identity"]["home_history"]
    assert h["source"] == "domestic_league" and h["record"]["history_league"] == "Eredivisie"
    assert out["identity"]["home"]["matches_played"] == 2          # own UCL record is still reported
    assert out["home_stats"].matches_played == 8 and out["home_stats"].scored == round(2.4 * 1.12, 2)
    assert ("home_team 'Feyenoord' uses its domestic 'Eredivisie' history (8 matches); it has 2 verified "
            "'uefa-champions-league' matches") in out["warnings"]
    dom = resolve_for_analysis(engine_db, provider="api_football", league_id="88", league_name="Eredivisie",
                               country="Netherlands", profile_key="Eredivisie", match_date="2026-11-03",
                               home="Feyenoord", away="Feyenoord", home_id="209", away_id="209")
    assert dom["identity"]["home_history"]["source"] == "competition"
    assert dom["identity"]["home_history"]["record"]["history_league"] == "Eredivisie"


# 2. Inter in Serie A and in the Champions League
def test_inter_uses_serie_a_by_provider_id_never_the_colombian_inter(engine_db):
    _clubs(engine_db)
    out = _ucl(engine_db)
    a = out["identity"]["away_history"]
    assert a["source"] == "domestic_league" and a["record"]["history_league"] == "Serie A"
    assert a["domestic"]["league_id"] == 135
    assert out["away_stats"].matches_played == 5
    assert out["identity"]["away"]["status"] == "UNRESOLVED_NO_HISTORY"   # no UCL record of its own
    assert out["warnings"][-1] == ("home and away statistics come from different competitions ('Eredivisie', "
                                   "'Serie A'); the model applies no league-strength adjustment")


# 3. Cross-provider identity: ids belong to one provider
def test_provider_ids_are_never_shared_between_providers(engine_db):
    _clubs(engine_db)
    out = resolve_for_analysis(engine_db, provider="football_data", league_id="fd-2001", league_code="CL",
                               league_name="UEFA Champions League", profile_key="Champions League",
                               match_date="2026-11-03", home="Feyenoord Rotterdam", away="FC Internazionale Milano",
                               home_id="675", away_id="108")
    assert not out["available"]
    assert out["identity"]["away_history"]["domestic"]["status"] == "NOT_FOUND"
    assert "listed in no domestic league" in out["blocking"][-1]


# 4. Competition aliases
def test_domestic_aliases_of_one_competition_are_one_competition(engine_db):
    _clubs(engine_db)
    _state(engine_db, "Feyenoord", "VriendenLoterij Eredivisie", 1)
    _fixture(engine_db, "Feyenoord", "AZ Alkmaar", "VriendenLoterij Eredivisie", 88)
    out = _ucl(engine_db)
    h = out["identity"]["home_history"]
    assert h["record"]["history_league"] == "Eredivisie"
    assert h["domestic"]["resolution"]["evidence"]["split_history"] == {"Eredivisie": 8, "VriendenLoterij Eredivisie": 1}


# 6. Mixed or ambiguous identities
def test_youth_side_never_links_to_the_senior_club(engine_db):
    _clubs(engine_db)
    dl = club_membership.domestic_league(engine_db, "api_football", "7981", "2026-11-03")
    assert not dl.found and "no domestic league" in dl.reason


def test_two_equal_domestic_leagues_or_two_names_are_ambiguous(engine_db):
    _clubs(engine_db)
    _member(engine_db, 209, 89, "Eerste Divisie", "Netherlands", "Feyenoord")
    assert "two domestic leagues" in club_membership.domestic_league(engine_db, "api_football", "209", "2026-11-03").reason
    _member(engine_db, 505, 135, "Serie A", "Italy", "Internazionale")
    assert "several names" in club_membership.domestic_league(engine_db, "api_football", "505", "2026-11-03").reason


def test_cups_and_friendlies_are_not_domestic_leagues(engine_db):
    _clubs(engine_db)
    dl = club_membership.domestic_league(engine_db, "api_football", "209", "2026-11-03")
    assert dl.found and dl.competition.key == "netherlands-eredivisie"     # not the later KNVB Beker (90)


# 7. Sparse coverage
def test_sparse_domestic_history_is_used_with_a_warning_and_none_blocks(engine_db):
    _clubs(engine_db)
    _state(engine_db, "Bodo/Glimt", "Eliteserien", 1)
    _fixture(engine_db, "Bodo/Glimt", "Molde", "Eliteserien", 103)
    engine_db.execute("UPDATE team_state SET last_match_date = '2026-10-10'")
    _member(engine_db, 327, 103, "Eliteserien", "Norway", "Bodo/Glimt", n=1)
    out = _ucl(engine_db, home="Bodo/Glimt", home_id="327", away="Lille", away_id="79")
    assert out["identity"]["home_history"]["record"]["history_league"] == "Eliteserien"
    assert "home_team 'Bodo/Glimt' has only 1 matches in its 'Eliteserien' history" in out["warnings"]
    assert out["blocking"] == ["away_team 'Lille' has NO real data — identity UNRESOLVED_NO_HISTORY: no "
                               "uefa-champions-league history for normalised 'lille'; domestic league: provider "
                               "team 79 is listed in no domestic league before this match"]


def test_a_sufficient_competition_record_is_kept(engine_db):
    _clubs(engine_db)
    engine_db.execute("UPDATE team_state SET matches_played = 6 WHERE team_name = 'Feyenoord' AND league = "
                      "'UEFA Champions League'")
    out = _ucl(engine_db)
    assert out["identity"]["home_history"]["source"] == "competition"
    assert out["identity"]["home_history"]["domestic"] is None


# 10. No future-data leakage
def test_records_with_results_on_or_after_kickoff_are_not_used(engine_db):
    _clubs(engine_db)
    engine_db.execute("UPDATE team_state SET last_match_date = '2026-11-03' WHERE team_name = 'Inter' AND "
                      "league = 'Serie A'")
    out = _ucl(engine_db)
    assert not out["available"] and out["away_stats"] is None
    assert "already includes results dated 2026-11-03" in out["blocking"][0]


def test_membership_evidence_only_from_before_the_match(engine_db):
    _clubs(engine_db)
    assert not club_membership.domestic_league(engine_db, "api_football", "209", "2026-08-09").found
    assert club_membership.domestic_league(engine_db, "api_football", "209", "2026-08-10").found


# 11. Concurrent analysis requests
def test_concurrent_resolutions_on_separate_read_only_connections(tmp_path):
    path = tmp_path / "engine.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    _clubs(conn)
    conn.commit()
    conn.close()
    results, errors = [], []

    def work():
        ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        try:
            out = _ucl(ro)
            results.append((out["available"], out["home_stats"], out["away_stats"]))
        except Exception as e:  # noqa: BLE001
            errors.append(e)
        finally:
            ro.close()

    threads = [threading.Thread(target=work) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(results) == 16 and len(set(map(repr, results))) == 1 and results[0][0]


def test_precompute_uses_the_same_identity_inputs_as_the_endpoint(monkeypatch):
    from api.services import fixtures_service

    calls = []
    monkeypatch.setattr(fixtures_service, "build_match_analysis", lambda fid, **kw: calls.append((fid, kw)) or {})
    fixtures_service._PREDICTION_STATUS.pop("1635662", None)
    fx = {"id": "1635662", "source": "api_football", "provider": "api_football", "kickoff_utc": "2026-11-03T20:00:00Z",
          "league_id_apifootball": "2", "league": {"id": "2", "name": "UEFA Champions League", "country": "World"},
          "home_team": {"id": "209", "name": "Feyenoord"}, "away_team": {"id": "505", "name": "Inter"}}
    fixtures_service._precompute_predictions_for_date("2026-11-03", [fx])
    assert calls == [("1635662", {"home": "Feyenoord", "away": "Inter", "league": "UEFA Champions League",
                                  "provider": "api_football", "league_id": "2", "league_code": None,
                                  "country": "World", "home_id": "209", "away_id": "505",
                                  "kickoff_utc": "2026-11-03T20:00:00Z", "persist_qualification": False})]
    fixtures_service._PREDICTION_STATUS.pop("1635662", None)


def test_endpoint_serves_the_precomputed_identity_result(monkeypatch):
    from api.services import match_analysis_service as svc

    svc._ANALYSIS_CACHE["xc-1"] = {"prediction_available": False, "missing_inputs": ["x"], "identity": {}}
    try:
        assert svc.get_match_analysis("xc-1", home="A", away="B", provider="api_football")["identity"] == {}
    finally:
        svc._ANALYSIS_CACHE.pop("xc-1", None)


# ── Recovery from the cached fixture lists (scripts/recover_cached_results.py) ──

def _db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _played(conn, mid, d, home, away, hg, ag, label="Serie A", league_id=135):
    from src.engine.live_updater import on_match_finished

    conn.execute("INSERT INTO matches (id, date, home_team, away_team, league_name, league_id, status, home_goals, "
                 "away_goals, provider) VALUES (?, ?, ?, ?, ?, ?, 'FT', ?, ?, 'api_football')",
                 (mid, d, home, away, label, league_id, hg, ag))
    on_match_finished(conn, mid, d, label, home, away, hg, ag)


def _fx(fid, ko, home, away, hg, ag, hid, aid, status="FT", league_id=135, league="Serie A", country="Italy"):
    return {"id": str(fid), "source": "api_football", "provider": "api_football", "status": status,
            "kickoff_utc": ko, "date": ko[:10], "time": ko[11:16], "league_id_apifootball": str(league_id),
            "round": "Regular Season - 1", "league": {"id": str(league_id), "name": league, "country": country},
            "home_team": {"id": str(hid), "name": home}, "away_team": {"id": str(aid), "name": away},
            "home_goals": hg, "away_goals": ag}


def _run(tmp_path, files, before="2026-11-03"):
    src = tmp_path / "src" / "engine.db"
    src.parent.mkdir(exist_ok=True)
    conn = _db(src)
    _played(conn, "100", "2026-09-05", "Napoli", "Roma", 1, 0)
    _played(conn, "101", "2026-09-19", "Roma", "Napoli", 2, 2)
    conn.commit()
    conn.close()
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    for day, fixtures in files.items():
        (cache / f"fixtures-{day}.json").write_text(json.dumps(fixtures))
    out = tmp_path / "out"
    import sys
    argv = sys.argv
    sys.argv = ["x", "--source", str(src), "--cache", str(cache), "--out", str(out), "--import-before", before]
    try:
        assert recover.main() == 0
    finally:
        sys.argv = argv
    return out, json.loads((out / "recovery_report.json").read_text()), src


INTER, NAPOLI, ROMA, MONZA = 505, 492, 497, 1579


# 5. Duplicate results; 8. missing statistics; 9. chronological reconstruction
def test_recovery_dedupes_creates_id_verified_records_and_replays_in_order(tmp_path):
    files = {
        "2026-08-22": [_fx(1550092, "2026-08-22T18:45:00Z", "Inter", "Monza", 4, 1, INTER, MONZA)],
        "2026-08-23": [_fx(1550092, "2026-08-22T18:45:00Z", "Inter", "Monza", 4, 1, INTER, MONZA)],   # same id
        "2026-09-05": [_fx(9001, "2026-09-05T18:45:00Z", "Napoli", "Roma", 1, 0, NAPOLI, ROMA)],      # stored as 100
        "2026-09-12": [_fx(1550113, "2026-09-12T18:45:00Z", "Inter", "Napoli", 3, 2, INTER, NAPOLI),
                       _fx(9002, "2026-09-12T16:00:00Z", "Monza", "Roma", 0, 0, MONZA, ROMA, status="NS")],
        "2026-09-19": [_fx(9003, "2026-09-19T18:45:00Z", "Roma", "Napoli", 3, 3, ROMA, NAPOLI)],      # conflicts with 101
    }
    out, rep, src = _run(tmp_path, files)
    assert rep["accepted"] == 2 and rep["accepted_new_record_sides"] == 3
    assert rep["deduplicated"] == {"same_match_stored_under_another_id": 1}
    assert rep["rejected"] == {"conflicts_with_stored_score": 1}
    c = sqlite3.connect(out / "recovered.db")
    inter = c.execute("SELECT matches_played, last_match_date, rolling_scored, rolling_corners FROM team_state WHERE "
                      "team_name='Inter' AND league='Serie A' AND venue='overall'").fetchone()
    assert inter[:2] == (2, "2026-09-12")
    assert inter[2] == round(round(0.85 * 1.2 + 0.15 * 4, 3) * 0.85 + 0.15 * 3, 3)
    assert inter[3] == 5.0                                      # unknown corners are not estimated
    assert c.execute("SELECT COUNT(*) FROM match_history WHERE home_corners IS NOT NULL OR home_xg IS NOT NULL").fetchone()[0] == 0
    order = [r[0] for r in c.execute("SELECT match_id FROM match_history ORDER BY id")]
    assert order == ["1550092", "100", "1550113", "101"]        # oldest first, imported ones interleaved
    prov = c.execute("SELECT home_identity, away_identity, source_files FROM recovered_results WHERE match_id='1550092'").fetchone()
    assert prov[0] == "new:provider_id:505" and set(json.loads(prov[2])) == {"fixtures-2026-08-22.json", "fixtures-2026-08-23.json"}
    assert c.execute("SELECT COUNT(*) FROM provider_team_competitions WHERE provider_team_id='505'").fetchone()[0] == 1
    s = sqlite3.connect(src)
    assert s.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 2         # source untouched


def test_recovery_rejects_unverifiable_new_records_and_future_results(tmp_path):
    files = {"2026-09-12": [_fx(1, "2026-09-12T18:45:00Z", "Inter", "Monza", 1, 0, INTER, MONZA)],
             "2026-09-13": [_fx(3, "2026-09-13T18:45:00Z", "Inter", "Roma", 2, 0, 7777, ROMA)],   # 'Inter' under another id
             "2026-11-03": [_fx(4, "2026-11-03T18:45:00Z", "Roma", "Napoli", 1, 0, ROMA, NAPOLI)]}
    out, rep, _ = _run(tmp_path, files)
    assert rep["accepted"] == 0
    assert rep["rejected"]["team_identity_unresolved"] == 2
    assert rep["rejected"]["kickoff_not_before_cutoff"] == 1
    bad = json.loads((out / "rejected.json").read_text())
    assert {d["id"] for d in bad} == {"1", "3"}
    assert all("new_record_name_shared_by_several_ids" in " ".join(d["detail"]) for d in bad)


@pytest.mark.parametrize("out_dir", ["src", "cache"])
def test_recovery_refuses_to_write_next_to_the_source_or_into_the_cache(tmp_path, out_dir):
    src = tmp_path / "src" / "engine.db"
    src.parent.mkdir()
    _db(src).close()
    (tmp_path / "cache").mkdir()
    import sys
    argv = sys.argv
    sys.argv = ["x", "--source", str(src), "--cache", str(tmp_path / "cache"), "--out", str(tmp_path / out_dir)]
    try:
        with pytest.raises(SystemExit):
            recover.main()
    finally:
        sys.argv = argv


# 5/6. Fragmented records: spelling splits merged only with one provider id
def test_spelling_split_is_merged_only_when_provider_ids_agree(tmp_path):
    from src.engine.live_updater import on_match_finished

    conn = _db(tmp_path / "engine.db")
    # Seeded through the live pipeline: its substring state lookup folds "Bayern München" into the
    # "FC Bayern München" record, and "FC Köln" into "1. FC Köln" although they are different ids here.
    _played(conn, "1575140", "2026-08-28", "FC Bayern München", "VfB Stuttgart", 5, 1, "Bundesliga", 78)
    _played(conn, "1575156", "2026-09-05", "FC Schalke 04", "Bayern München", 0, 0, "Bundesliga", 78)
    _played(conn, "1575200", "2026-09-06", "1. FC Köln", "Union Berlin", 1, 1, "Bundesliga", 78)
    _played(conn, "1575201", "2026-09-13", "FC Köln", "Mainz", 2, 0, "Bundesliga", 78)
    on_match_finished(conn, "12499528", "2025-05-10", "Bundesliga", "Bayern Munich", "Hertha", 2, 0)  # older feed
    conn.commit()
    sides = {"1575140": (78, "157", "172"), "1575156": (78, "174", "157"),
             "1575200": (78, "192", "182"), "1575201": (78, "999", "164")}     # two different 'Köln' ids
    mapping, rows, outcome = recover.verified_merges(conn, sides)
    assert mapping[("Bundesliga", "bayern münchen")] == "FC Bayern München"
    assert ("Bundesliga", "bayern munich") not in mapping                          # no provider id: never merged
    assert ("Bundesliga", "fc köln") not in mapping and ("Bundesliga", "1. fc köln") not in mapping
    assert outcome["merged_id_groups"] == 1 and outcome["not_merged_name_shared_by_several_ids"] == 2
    assert outcome["not_merged_spelling_without_provider_id"] == 1
    assert rows == [("Bundesliga", "Bayern München", "FC Bayern München", "api_football", "157", 2, recover.IMPORTER_VERSION)]


def test_imported_results_count_as_provider_id_evidence(tmp_path):
    from src.engine.live_updater import on_match_finished

    conn = _db(tmp_path / "engine.db")
    on_match_finished(conn, "12499528", "2025-05-10", "Bundesliga", "FC Bayern München", "Hertha", 2, 0)  # older feed
    _played(conn, "1575156", "2026-09-05", "FC Schalke 04", "Bayern München", 0, 0, "Bundesliga", 78)
    conn.commit()
    mapping, rows, _ = recover.verified_merges(conn, {"1575156": (78, "174", "157")},
                                               [("Bundesliga", "157", "FC Bayern München")] * 3)
    assert mapping[("Bundesliga", "bayern münchen")] == "FC Bayern München"


def test_two_clubs_never_share_one_new_record_under_a_common_label(tmp_path):
    ug = dict(league_id=585, league="Premier League", country="Uganda")
    rw = dict(league_id=762, league="Premier League", country="Rwanda")
    files = {"2026-09-12": [_fx(1, "2026-09-12T13:00:00Z", "Police", "Vipers", 1, 0, 100, 101, **ug),
                            _fx(2, "2026-09-12T13:00:00Z", "Police", "Rayon", 2, 2, 200, 201, **rw)]}
    out, rep, _ = _run(tmp_path, files)
    assert rep["accepted"] == 0 and rep["rejected"]["new_record_claimed_by_two_clubs"] == 2

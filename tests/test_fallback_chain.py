"""Fallback chain and data confidence (2026-11-10). Every fixture is predicted from the best real data level and
labelled; no number comes from a hard-coded default. No network."""
import pytest

from src.identity import fallback_chain as fc
from src.identity.analysis_inputs import resolve_for_analysis
from tests.test_team_identity import _state

_seq = [0]


def _result(conn, label, league_id, home, away, hg, ag, d="2026-09-10", corners=None, cards=None, provider="api_football"):
    _seq[0] += 1
    mid = f"r{_seq[0]}"
    conn.execute("INSERT INTO matches (id, date, home_team, away_team, league_name, league_id, status, provider) "
                 "VALUES (?, ?, ?, ?, ?, ?, 'FT', ?)", (mid, d, home, away, label, league_id, provider))
    hc, ac = corners or (None, None)
    hk, ak = cards or (None, None)
    conn.execute("INSERT INTO match_history (match_id, match_date, league, home_team, away_team, home_goals, away_goals, "
                 "home_corners, away_corners, home_cards, away_cards) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (mid, d, label, home, away, hg, ag, hc, ac, hk, ak))
    return mid


def _league(conn, label, league_id, n=40, hg=2, ag=1, d="2026-09-10", corners=(6, 4), cards=(2, 3)):
    for i in range(n):
        _result(conn, label, league_id, f"{label}-H{i}", f"{label}-A{i}", hg, ag, d, corners, cards)


def _bind(conn, tid, league, name):
    conn.execute("INSERT INTO team_state_ids (provider, provider_team_id, league, team_name_lower, team_name, how, "
                 "created_at) VALUES ('api_football', ?, ?, ?, ?, 'test', '2026-01-01')", (str(tid), league, name.lower(), name))


def _resolve(conn, **kw):
    base = dict(provider="api_football", league_id="999", league_name="Liga Uno", country="Narnia",
                profile_key="Liga Uno", home="Alpha", away="Beta", home_id="1", away_id="2", match_date="2026-10-01")
    return resolve_for_analysis(conn, **{**base, **kw})


@pytest.fixture
def db(engine_db):
    from src.engine.live_updater import TEAM_STATE_IDS_SCHEMA
    engine_db.execute(TEAM_STATE_IDS_SCHEMA)
    return engine_db


# ── competition types ───────────────────────────────────────────────────────

@pytest.mark.parametrize("label, country, kind, gender, age", [
    ("Friendlies", "World", "national_friendly", "men", "senior"),
    ("Friendlies Clubs", "World", "club_friendly", "men", "senior"),
    ("Friendlies Women", "World", "national_friendly", "women", "senior"),
    ("World Cup - Qualification Europe", "World", "national_competitive", "men", "senior"),
    ("UEFA Champions League", "World", "continental_club", "men", "senior"),
    ("Copa del Rey", "Spain", "cup", "men", "senior"),
    ("Serie A", "Italy", "league", "men", "senior"),
    ("Primavera 1", "Italy", "league", "men", "youth"),
    ("Liga F", "Spain", "league", "women", "senior"),
    ("Paraense U20", "Brazil", "league", "men", "youth"),
])
def test_competition_types(label, country, kind, gender, age):
    t = fc.classify(label, country)
    assert (t.kind, t.gender, t.age) == (kind, gender, age)


# ── a team with no history gets the league's real averages, labelled Low ─────

def test_unknown_teams_get_the_leagues_own_averages(db):
    _league(db, "Liga Uno", 999)
    out = _resolve(db)
    assert out["available"] and out["blocking"] == []
    h, a = out["home_stats"], out["away_stats"]
    assert (h.scored, h.conceded, a.scored, a.conceded) == (2.0, 1.0, 1.0, 2.0)
    assert (h.corners, a.corners, h.cards, a.cards) == (6.0, 4.0, 2.0, 3.0)
    assert out["confidence"]["level"] == "Low"
    assert out["confidence"]["note"] == ("Low confidence: Alpha has no stored results; Beta has no stored results; "
                                         "league averages used (Liga Uno, 40 matches).")
    assert out["league_profile"] == {"avg_home_goals": 2.0, "avg_away_goals": 1.0, "level": "league",
                                     "source": "Liga Uno", "matches": 40}


def test_a_thin_team_is_blended_toward_the_league_by_its_own_results(db):
    _league(db, "Liga Uno", 999)
    for i in range(3):                                      # Alpha: 3 home wins 4-0
        _result(db, "Liga Uno", 999, "Alpha", f"Opp{i}", 4, 0)
    _bind(db, 1, "Liga Uno", "Alpha")
    out = _resolve(db)
    b = out["basis"]["home"]
    assert b["path"] == "chain" and b["own_matches"] == 3 and b["goals"]["level"] == "team+league"
    mu = (2 * 40 + 1 * 40 + 4 * 3 + 0 * 3) / 86          # league per-team mean incl. Alpha's games
    home_mean = (2 * 40 + 4 * 3) / 43
    s = (3 * 4.0 + 5 * mu) / 8
    assert out["home_stats"].scored == round(s * home_mean / mu, 3)
    assert "Alpha has 3 recent matches" in out["confidence"]["note"]


def test_league_averages_come_from_the_league_id_never_a_shared_label(db):
    _league(db, "Serie A", 135, hg=3, ag=1)                 # Italy
    _league(db, "Serie A", 71, hg=1, ag=1)                  # Brazil, same label
    out = _resolve(db, league_id="135", league_name="Serie A", country="Italy", profile_key="Serie A")
    assert out["confidence"]["baseline"]["home_goals"] == 3.0 and out["confidence"]["baseline"]["matches"] == 40


def test_country_then_competition_type_when_the_league_is_new(db):
    _league(db, "Liga Dos", 998, n=70, hg=1, ag=1)          # same country, other league
    db.execute("CREATE TABLE IF NOT EXISTS provider_team_competitions (provider TEXT, provider_team_id TEXT, "
               "league_id INTEGER, league_name TEXT, country TEXT, team_name TEXT, fixtures INTEGER, league_rounds "
               "INTEGER, first_date TEXT, last_date TEXT, source TEXT, version TEXT)")
    db.execute("INSERT INTO provider_team_competitions VALUES ('api_football', '7', 998, 'Liga Dos', 'Narnia', 'X', "
               "1, 1, '2026-01-01', '2026-01-02', 't', 'v')")
    out = _resolve(db)
    assert out["confidence"]["baseline"]["level"] == "country"
    assert out["confidence"]["baseline"]["source"] == "Narnia league"
    out = _resolve(db, country="Elsewhere")
    assert out["confidence"]["baseline"]["level"] == "competition_type"


def test_womens_and_youth_football_never_use_mens_senior_averages(db):
    _league(db, "Liga Uno", 999, n=80, hg=3, ag=2)          # men
    _league(db, "Liga Uno Women", 997, n=80, hg=1, ag=1)
    out = _resolve(db, league_id="996", league_name="Copa Women", country="Elsewhere")
    assert out["confidence"]["baseline"]["home_goals"] == 1.0          # women's results only
    assert out["confidence"]["baseline"]["level"] == "gender_age"


def test_results_on_or_after_kickoff_are_never_read(db):
    _league(db, "Liga Uno", 999, d="2026-09-10")
    _league(db, "Liga Uno", 999, n=40, hg=9, ag=9, d="2026-10-01")   # match day: excluded
    out = _resolve(db)
    assert out["confidence"]["baseline"]["home_goals"] == 2.0 and out["confidence"]["baseline"]["matches"] == 40


def test_no_stored_results_at_any_level_is_unavailable(db):
    out = _resolve(db)
    assert not out["available"]
    assert out["blocking"] == ["no stored results before 2026-10-01 at any level of the fallback chain for goals "
                               "(league)"]


def test_corners_and_cards_without_any_stored_data_are_dropped_not_defaulted(db):
    _league(db, "Liga Uno", 999, corners=(None, None), cards=(None, None))
    out = _resolve(db)
    assert out["available"] and out["unavailable_markets"] == ["Corners", "Cards"]
    assert "no stored corners data before 2026-10-01" in out["warnings"][0]


def test_corners_fall_back_to_wider_levels_independently_of_goals(db):
    _league(db, "Liga Uno", 999, corners=(None, None), cards=(None, None))
    _league(db, "Liga Tres", 995, n=30, corners=(7, 3), cards=(1, 1))
    out = _resolve(db)
    assert out["basis"]["home"]["goals"]["baseline_level"] == "league"
    assert out["basis"]["home"]["corners"]["level"] != "league" and out["home_stats"].corners == 7.0


def test_confidence_high_medium_low(db):
    _league(db, "Liga Uno", 999)
    for name, n in (("Alpha", 12), ("Beta", 12)):
        _state(db, name, "Liga Uno", n)
        for i in range(n):
            _result(db, "Liga Uno", 999, name, f"{name}-o{i}", 1, 1, d="2026-09-20")
    db.execute("UPDATE team_state SET last_match_date = '2026-09-20'")
    out = _resolve(db)
    assert out["confidence"]["level"] == "High" and out["basis"]["home"]["path"] == "record"
    db.execute("UPDATE team_state SET matches_played = 6 WHERE team_name = 'Beta'")
    db.execute("DELETE FROM match_history WHERE home_team = 'Beta' AND match_id IN (SELECT match_id FROM "
               "match_history WHERE home_team = 'Beta' LIMIT 6)")
    out = _resolve(db)
    assert out["confidence"]["level"] == "Medium" and out["basis"]["away"]["own_matches"] == 6


def test_national_teams_use_elo_from_international_results(db):
    from datetime import date, timedelta
    teams = [f"T{i}" for i in range(8)]                     # T0 strongest ... T7 weakest
    d = date(2025, 1, 1)
    for _ in range(4):
        for i in range(8):
            for j in range(i + 1, 8):
                d += timedelta(days=1)
                _result(db, "Friendlies", 10, teams[i], teams[j], 1 + (j - i) // 3, 1 if j - i < 3 else 0, d=d.isoformat())
    out = _resolve(db, league_id="10", league_name="Friendlies", country="World", home="T0", away="T7",
                   home_id=None, away_id=None, profile_key="Friendlies", match_date="2025-08-01")
    elo = out["confidence"]["elo"]
    assert elo and elo["home"] > elo["away"] and elo["slope_goals_per_100"] > 0
    assert out["home_stats"].scored > out["away_stats"].scored
    assert "national-team Elo from" in out["confidence"]["note"]
    # the inputs reproduce the target expected goals through the model's product (no double counting)
    h, a, p = out["home_stats"], out["away_stats"], out["league_profile"]
    assert abs(h.scored * a.conceded / p["avg_home_goals"] - elo["target_home"]) < 0.01
    assert abs(a.scored * h.conceded / p["avg_away_goals"] - elo["target_away"]) < 0.01


# ── service: Low keeps every pick, but none is a best bet ───────────────────

def test_low_confidence_picks_are_shown_but_not_bankroll_qualified():
    from api.services.match_analysis_service import apply_confidence
    pick = {"market": "Over 1.5", "probability": 85.0, "bankroll_qualified": True, "section": "Goals"}
    analysis = {"top_picks": [pick], "tiers": [{"picks": [pick], "bankroll_qualified_count": 1}],
                "categories": [{"category": "Goals", "picks": [pick]}]}
    apply_confidence(analysis, {"confidence": {"level": "Low", "note": "n"}, "basis": {}})
    assert analysis["top_picks"] == [pick] and pick["probability"] == 85.0
    assert pick["bankroll_qualified"] is False and pick["gate_qualified_before_confidence"] is True
    assert analysis["tiers"][0]["bankroll_qualified_count"] == 0


def test_best_bets_leave_out_low_confidence_unless_included(tmp_path, monkeypatch):
    import json
    from api.services import fixtures_service as fs
    monkeypatch.setattr(fs, "_FIXTURE_CACHE_DIR", tmp_path)
    fixtures = [{"id": "f1", "status": "NS", "home_team": {"name": "A"}, "away_team": {"name": "B"}, "league": {}},
                {"id": "f2", "status": "NS", "home_team": {"name": "C"}, "away_team": {"name": "D"}, "league": {}},
                {"id": "f3", "status": "FT", "home_team": {"name": "E"}, "away_team": {"name": "F"}, "league": {}}]
    (tmp_path / "fixtures-2030-01-01.json").write_text(json.dumps(fixtures))
    high = {"confidence": {"level": "High"}, "top_picks": [{"market": "1", "probability": 82, "bankroll_qualified": True}]}
    low = {"confidence": {"level": "Low"}, "top_picks": [{"market": "2", "probability": 90, "bankroll_qualified": False,
                                                          "gate_qualified_before_confidence": True}]}
    monkeypatch.setitem(fs._ANALYSIS_CACHE, "f1", high)
    monkeypatch.setitem(fs._ANALYSIS_CACHE, "f2", low)
    out = fs.best_bets_service("2030-01-01")
    assert [p["fixture_id"] for p in out["picks"]] == ["f1"] and out["excluded_low_confidence_matches"] == 1
    out = fs.best_bets_service("2030-01-01", include_low=True)
    assert [(p["fixture_id"], p["confidence"]) for p in out["picks"]] == [("f2", "Low"), ("f1", "High")]

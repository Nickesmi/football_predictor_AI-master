"""M3 Phase C: API-Football fixture identity, as-of availability, in-play rejection, quote age buckets."""
from datetime import datetime, timedelta, timezone

import pytest

from src.engine import decisions
from src.engine.market_value import find_value
from src.odds import collector, freshness, store
from src.odds.model import Quote, iso_utc
from src.odds.providers import api_football
from tests.fakes.odds import transport_returning

NOW = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
KICKOFF = "2030-01-01T15:00:00+00:00"
HOST = "v3.football.api-sports.io"


def _ago(minutes):
    return iso_utc(NOW - timedelta(minutes=minutes))


def _fixture(conn, fixture_id="sofa-1", home="Liverpool", away="Ipswich Town", provider="sofascore",
             kickoff=str(int(datetime.fromisoformat(KICKOFF).timestamp()))):
    conn.execute(
        """INSERT INTO matches (id, date, kickoff, home_team, away_team, league_name, league_id, status, provider)
           VALUES (?, '2030-01-01', ?, ?, ?, 'Premier League', 17, 'NS', ?)""",
        (fixture_id, kickoff, home, away, provider),
    )
    conn.commit()


def _fixture_list(*items):
    return {"errors": [], "response": [
        {"fixture": {"id": fid, "date": date}, "teams": {"home": {"name": h}, "away": {"name": a}}}
        for fid, h, a, date in items
    ]}


def _odds_payload(fixture_id=777, date=KICKOFF, update=None, home="1.30"):
    return {"errors": [], "response": [{
        "fixture": {"id": fixture_id, "date": date}, "update": update or _ago(5),
        "bookmakers": [{"name": "Bet365", "bets": [{"name": "Match Winner", "values": [
            {"value": "Home", "odd": home}, {"value": "Draw", "odd": "5.50"}, {"value": "Away", "odd": "9.00"}]}]}],
    }]}


def _rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM matches").fetchall()]


class TestFixtureListMapping:
    def test_parse_fixture_list_requires_identity_and_names(self):
        events, skipped = api_football.parse_fixture_list({"errors": [], "response": [
            {"fixture": {"id": 1, "date": KICKOFF}, "teams": {"home": {"name": "A"}, "away": {"name": "B"}}},
            {"fixture": {"id": 2, "date": KICKOFF}, "teams": {"home": {"name": ""}, "away": {"name": "B"}}},
            {"fixture": {"date": KICKOFF}, "teams": {"home": {"name": "A"}, "away": {"name": "B"}}},
        ]})
        assert [e.event_id for e in events] == ["1"] and len(skipped) == 2
        assert api_football.parse_fixture_list({"errors": {"token": "bad"}})[0] == []

    def test_maps_api_football_id_onto_pipeline_fixture(self, engine_db, monkeypatch):
        _fixture(engine_db)
        monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "1")
        transport = transport_returning(_fixture_list(
            (777, "Liverpool FC", "Ipswich", "2030-01-01T15:00:00+00:00"),
            (778, "Arsenal", "Chelsea", "2030-01-01T15:00:00+00:00"),
        ))
        report = collector.map_api_football_fixtures(engine_db, ["2030-01-01"], "key", HOST, transport, NOW)
        assert transport.calls == [f"https://{HOST}/fixtures?date=2030-01-01"]
        assert (report["mapped"], report["unmapped"]) == (1, 1)
        row = engine_db.execute("SELECT provider_event_id, fixture_id FROM fixture_identity").fetchone()
        assert tuple(row) == ("777", "sofa-1")

    def test_kickoff_far_from_stored_fixture_is_not_mapped(self, engine_db, monkeypatch):
        _fixture(engine_db)
        monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "1")
        transport = transport_returning(_fixture_list((777, "Liverpool", "Ipswich Town", "2030-01-01T19:30:00+00:00")))
        report = collector.map_api_football_fixtures(engine_db, ["2030-01-01"], "key", HOST, transport, NOW)
        assert report["mapped"] == 0
        assert engine_db.execute("SELECT COUNT(*) FROM fixture_identity").fetchone()[0] == 0

    def test_mapping_respects_budget(self, engine_db, monkeypatch):
        monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "0")
        transport = transport_returning(_fixture_list())
        report = collector.map_api_football_fixtures(engine_db, ["2030-01-01"], "key", HOST, transport, NOW)
        assert report["requests_refused"] == 1 and transport.calls == []


class TestApiFootballCollection:
    def _mapped(self, engine_db, monkeypatch, budget="5"):
        _fixture(engine_db)
        monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", budget)
        collector.map_api_football_fixtures(
            engine_db, ["2030-01-01"], "key", HOST,
            transport_returning(_fixture_list((777, "Liverpool", "Ipswich Town", KICKOFF))), NOW)

    def test_odds_reach_pipeline_fixture_through_identity(self, engine_db, monkeypatch):
        self._mapped(engine_db, monkeypatch)
        transport = transport_returning(_odds_payload())
        report = collector.collect_api_football(engine_db, _rows(engine_db), "key", HOST, transport, NOW)
        assert transport.calls == [f"https://{HOST}/odds?fixture=777"]
        assert (report["mapped"], report["quotes_new"]) == (1, 3)
        assert {r[0] for r in engine_db.execute("SELECT fixture_id FROM odds_quotes")} == {"sofa-1"}

    def test_response_for_a_different_fixture_is_rejected(self, engine_db, monkeypatch):
        self._mapped(engine_db, monkeypatch)
        transport = transport_returning(_odds_payload(fixture_id=999))
        report = collector.collect_api_football(engine_db, _rows(engine_db), "key", HOST, transport, NOW)
        assert report["quotes_new"] == 0 and report["rejected_events"]
        assert engine_db.execute("SELECT COUNT(*) FROM odds_quotes").fetchone()[0] == 0

    def test_moved_kickoff_requires_remap(self, engine_db, monkeypatch):
        self._mapped(engine_db, monkeypatch)
        transport = transport_returning(_odds_payload(date="2030-01-03T15:00:00+00:00"))
        report = collector.collect_api_football(engine_db, _rows(engine_db), "key", HOST, transport, NOW)
        assert report["quotes_new"] == 0 and "remap" in report["rejected_events"][0]

    def test_prices_observed_after_kickoff_are_not_stored(self, engine_db, monkeypatch):
        self._mapped(engine_db, monkeypatch)
        transport = transport_returning(_odds_payload(update="2030-01-01T15:10:00+00:00"))
        report = collector.collect_api_football(engine_db, _rows(engine_db), "key", HOST, transport, NOW)
        assert (report["quotes_new"], report["quotes_after_kickoff"]) == (0, 3)

    def test_unmapped_pipeline_fixture_costs_nothing(self, engine_db, monkeypatch):
        _fixture(engine_db)
        monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "5")
        transport = transport_returning(_odds_payload())
        report = collector.collect_api_football(engine_db, _rows(engine_db), "key", HOST, transport, NOW)
        assert transport.calls == [] and report["requests_ok"] == 0


def _store(conn, observed, fetched, home="2.0", fixture_id="sofa-1"):
    q = Quote(provider="api_football", provider_event_id="777", bookmaker="bet365", market="1X2",
              selection="home", line="", decimal_odds=float(home), observed_at=observed, commence_time=KICKOFF)
    store.store_quotes(conn, fixture_id, [q], fetched)
    conn.commit()


class TestAsOfAvailability:
    def test_price_fetched_after_decision_time_is_invisible(self, engine_db):
        _fixture(engine_db)
        _store(engine_db, observed=_ago(30), fetched=_ago(-30))  # observed before, but fetched after NOW
        assert store.actionable_quotes(engine_db, "sofa-1", NOW, timedelta(minutes=240)) == []
        later = NOW + timedelta(minutes=31)
        assert len(store.actionable_quotes(engine_db, "sofa-1", later, timedelta(minutes=240))) == 1

    def test_replay_uses_the_quote_known_then_not_a_later_one(self, engine_db):
        _fixture(engine_db)
        _store(engine_db, observed=_ago(60), fetched=_ago(59), home="2.10")
        _store(engine_db, observed=_ago(-10), fetched=_ago(-9), home="1.50")  # arrives after NOW
        [q] = store.actionable_quotes(engine_db, "sofa-1", NOW, timedelta(minutes=240))
        assert q["decimal_odds"] == 2.10

    def test_candidate_records_quote_age_and_fetch_time(self, engine_db, monkeypatch):
        _fixture(engine_db)
        monkeypatch.setenv("ODDS_MAX_AGE_MINUTES", "240")
        _store(engine_db, observed=_ago(150), fetched=_ago(149))
        match = dict(engine_db.execute("SELECT * FROM matches").fetchone())
        [c] = find_value(match, {"1X2": {"home": 0.6, "draw": 0.2, "away": 0.2}}, engine_db, now=NOW)
        assert c["odds_age_minutes"] == 150.0 and c["odds_fetched_at"] == _ago(149)

    def test_decision_table_gains_age_columns_in_place(self, engine_db):
        engine_db.execute("DROP TABLE selection_decisions")
        engine_db.execute("CREATE TABLE selection_decisions (id INTEGER PRIMARY KEY, run_id TEXT, match_id TEXT)")
        decisions.ensure_decision_tables(engine_db)
        cols = {r[1] for r in engine_db.execute("PRAGMA table_info(selection_decisions)")}
        assert {"odds_fetched_at", "odds_age_minutes"} <= cols


class TestFreshnessBuckets:
    @pytest.mark.parametrize("minutes, expected", [(0, "0-60"), (60, "0-60"), (60.5, "61-120"), (120, "61-120"),
                                                   (121, "121-240"), (240, "121-240"), (241, ">240")])
    def test_bucket_edges(self, minutes, expected):
        assert freshness.bucket(minutes) == expected

    def test_coverage_report_counts_buckets_and_rejections(self, engine_db):
        for fid, age in (("a", 30), ("b", 90), ("c", 200), ("d", 300)):
            _fixture(engine_db, fid, home=f"H{fid}", away=f"A{fid}")
            engine_db.execute("INSERT INTO fixture_identity VALUES ('api_football', ?, ?, '', '', ?, 'x', ?)",
                              (fid, fid, KICKOFF, _ago(500)))
            _store(engine_db, observed=_ago(age), fetched=_ago(age), fixture_id=fid)
        _fixture(engine_db, "e", home="He", away="Ae")                                   # never mapped
        _fixture(engine_db, "f", home="Hf", away="Af")
        engine_db.execute("INSERT INTO fixture_identity VALUES ('api_football', 'f', 'f', '', '', ?, 'x', ?)",
                          (KICKOFF, _ago(500)))                                           # mapped, no quotes
        report = freshness.coverage_report(engine_db, ["a", "b", "c", "d", "e", "f"], NOW, 240)
        assert {b: v["fixtures"] for b, v in report["by_bucket"].items()} == \
            {"0-60": 1, "61-120": 1, "121-240": 1, ">240": 1}
        assert report["by_bucket"][">240"]["accepted"] == 0
        assert report["by_status"] == {"QUOTED": 4, "UNMAPPED": 1, "NO_QUOTES": 1}
        assert (report["covered_within_window"], report["coverage_pct"]) == (3, 50.0)


def test_api_football_requests_are_spaced_for_the_rate_limit(engine_db, monkeypatch):
    for fid, home in (("1", "A"), ("2", "B")):
        _fixture(engine_db, fid, home=home, provider="api_football")
    monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "5")
    monkeypatch.setenv("API_FOOTBALL_MIN_INTERVAL_SECONDS", "7")
    slept = []
    collector.collect_api_football(engine_db, _rows(engine_db), "key", HOST,
                                   transport_returning({"errors": [], "response": []}), NOW, sleep=slept.append)
    assert slept == [7.0]


def test_league_date_odds_follow_paging_and_ignore_untracked_fixtures(engine_db, monkeypatch):
    import json as _json

    _fixture(engine_db, "777", provider="api_football", kickoff=KICKOFF)
    monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "5")
    pages = {1: [_odds_payload(fixture_id=555)["response"][0]], 2: [_odds_payload()["response"][0]]}
    calls = []

    def transport(url, headers, timeout):
        calls.append(url)
        page = int(url.rsplit("page=", 1)[1])
        body = {"errors": [], "paging": {"current": page, "total": 2}, "response": pages[page]}
        return 200, {"x-ratelimit-requests-remaining": "90"}, _json.dumps(body).encode()

    report = collector.collect_api_football_by_league(engine_db, _rows(engine_db), {"777": 2029}, "key", HOST,
                                                      transport, NOW, sleep=lambda s: None)
    assert [c.rsplit("?", 1)[1] for c in calls] == ["league=17&season=2029&date=2030-01-01&page=1",
                                                    "league=17&season=2029&date=2030-01-01&page=2"]
    assert (report["requests_ok"], report["untracked_events"], report["quotes_new"]) == (2, 1, 3)
    assert {r[0] for r in engine_db.execute("SELECT fixture_id FROM odds_quotes")} == {"777"}


def test_k53_same_provider_stamp_with_new_price_is_dropped_by_the_store(engine_db):
    """Characterises K53 (not a fix): the unique key has no price, so a price change under an
    unchanged provider stamp is ignored. The timestamp experiment therefore keeps raw responses."""
    _fixture(engine_db)
    _store(engine_db, observed=_ago(100), fetched=_ago(90), home="2.00")
    _store(engine_db, observed=_ago(100), fetched=_ago(10), home="1.80")
    prices = [r[0] for r in engine_db.execute("SELECT decimal_odds FROM odds_quotes")]
    assert prices == [2.0]

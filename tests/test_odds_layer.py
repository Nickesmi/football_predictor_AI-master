"""Real-odds layer: canonical quotes, provider parsing, fixture identity, storage, freshness, quota, crowd votes."""
import math
from datetime import datetime, timedelta, timezone

import pytest

from src.engine.fixture_collector import collect_fixtures
from src.engine.market_value import find_value
from src.engine.pipeline import run_pipeline
from src.odds import collector, identity, quota, store
from src.odds.model import InvalidQuote, OddsType, Quote, decimal_odds, format_line, iso_utc, model_fair_odds
from src.odds.providers import api_football, the_odds_api
from tests.fakes.odds import ingest_odds_api, odds_api_event, synthetic_quote, transport_returning

NOW = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
KICKOFF = "2030-01-01T15:00:00+00:00"


def _ago(minutes):
    return iso_utc(NOW - timedelta(minutes=minutes))


def _fixture(conn, fixture_id="fx-1", home="Liverpool", away="Ipswich Town", date="2030-01-01",
             kickoff=str(int(datetime.fromisoformat(KICKOFF).timestamp())), league_id=17, provider="sofascore"):
    conn.execute(
        """INSERT INTO matches (id, date, kickoff, home_team, away_team, league_name, league_id, status, provider)
           VALUES (?, ?, ?, ?, ?, 'Premier League', ?, 'NS', ?)""",
        (fixture_id, date, kickoff, home, away, league_id, provider),
    )
    conn.commit()


def _quote(selection="home", price=2.0, observed=None, bookmaker="book-a", market="1X2", line="", commence=KICKOFF):
    return Quote(provider="the_odds_api", provider_event_id="ev-1", bookmaker=bookmaker, market=market,
                 selection=selection, line=line, decimal_odds=price, observed_at=observed or _ago(5),
                 commence_time=commence)


# ── Canonical values ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [1.0, 0, -2, "abc", None, math.nan, math.inf, 1000.5])
def test_invalid_prices_are_rejected_not_repaired(bad):
    with pytest.raises(InvalidQuote):
        decimal_odds(bad)


def test_valid_prices_and_lines_are_normalised():
    assert decimal_odds("1.8500") == 1.85
    assert [format_line(v) for v in (2.5, "-0.25", 0, None)] == ["2.5", "-0.25", "0", ""]
    with pytest.raises(InvalidQuote):
        format_line(2.3)
    assert model_fair_odds(0.5) == 2.0 and model_fair_odds(0.0) is None


@pytest.mark.parametrize("kind", [OddsType.CROWD_VOTE, OddsType.MODEL_FAIR])
def test_votes_and_model_fair_odds_can_never_be_quotes(kind):
    with pytest.raises(InvalidQuote, match="not a price"):
        Quote("p", "e", "b", "1X2", "home", "", 2.0, _ago(1), KICKOFF, odds_type=kind)


@pytest.mark.parametrize("market, selection, line, bookmaker", [
    ("1X2", "over", "", "b"), ("TOTALS", "over", "", "b"), ("1X2", "home", "2.5", "b"), ("1X2", "home", "", ""),
])
def test_non_canonical_quotes_are_rejected(market, selection, line, bookmaker):
    with pytest.raises(InvalidQuote):
        Quote("p", "e", bookmaker, market, selection, line, 2.0, _ago(1), KICKOFF)


# ── Provider parsing ─────────────────────────────────────────────────────────

def test_the_odds_api_payload_maps_to_canonical_quotes():
    event = odds_api_event("ev-1", "Liverpool", "Ipswich Town", "2030-01-01T15:00:00Z",
                           h2h=(1.3, 5.5, 9.0), totals={2.5: (1.8, 2.05)}, observed="2030-01-01T11:55:00Z",
                           bookmaker="Pinnacle")
    event["bookmakers"][0]["markets"].append({"key": "spreads", "outcomes": [
        {"name": "Liverpool", "price": 1.9, "point": -1.5}, {"name": "Ipswich Town", "price": 1.95, "point": 1.5},
        {"name": "Liverpool FC", "price": 2.0, "point": -1}, {"name": "Draw", "price": 1.0},
    ]})
    [(ev, quotes)], skipped = the_odds_api.parse_events([event])
    assert ev == identity.ProviderEvent("the_odds_api", "ev-1", "Liverpool", "Ipswich Town", KICKOFF)
    assert {(q.market, q.selection, q.line, q.decimal_odds) for q in quotes} == {
        ("1X2", "home", "", 1.3), ("1X2", "draw", "", 5.5), ("1X2", "away", "", 9.0),
        ("TOTALS", "over", "2.5", 1.8), ("TOTALS", "under", "2.5", 2.05),
        ("AH", "home", "-1.5", 1.9), ("AH", "away", "1.5", 1.95),
    }
    assert {q.bookmaker for q in quotes} == {"pinnacle"}
    assert {q.observed_at for q in quotes} == {"2030-01-01T11:55:00+00:00"}
    assert len(skipped) == 2  # a team name spelled differently and a 'Draw' handicap are never guessed


def test_api_football_payload_maps_to_canonical_quotes():
    payload = {"errors": [], "response": [{
        "fixture": {"id": 777, "date": "2030-01-01T15:00:00+00:00"}, "update": "2030-01-01T11:50:00+00:00",
        "bookmakers": [{"id": 8, "name": "Bet365", "bets": [
            {"name": "Match Winner", "values": [{"value": "Home", "odd": "1.30"}, {"value": "Draw", "odd": "5.50"},
                                                 {"value": "Away", "odd": "9.00"}]},
            {"name": "Goals Over/Under", "values": [{"value": "Over 2.5", "odd": "1.80"}, {"value": "Under 2.5", "odd": "2.00"}]},
            {"name": "Asian Handicap", "values": [{"value": "Home -1", "odd": "1.95"}]},
            {"name": "Double Chance", "values": [{"value": "Home/Draw", "odd": "1.05"}]},
            {"name": "Corners Over Under", "values": [{"value": "Over 9.5", "odd": "1.9"}]},
            {"name": "Both Teams Score", "values": [{"value": "Yes", "odd": "1.00"}]},
        ]}],
    }]}
    [(ev, quotes)], skipped = api_football.parse_fixture_odds(payload, "Liverpool", "Ipswich Town")
    assert (ev.provider, ev.event_id, ev.commence_time) == ("api_football", "777", KICKOFF)
    assert {(q.market, q.selection, q.line, q.decimal_odds) for q in quotes} == {
        ("1X2", "home", "", 1.3), ("1X2", "draw", "", 5.5), ("1X2", "away", "", 9.0),
        ("TOTALS", "over", "2.5", 1.8), ("TOTALS", "under", "2.5", 2.0),
        ("AH", "home", "-1", 1.95), ("DC", "home_draw", "", 1.05),
    }
    assert {q.bookmaker for q in quotes} == {"bet365"}
    assert len(skipped) == 2  # unsupported market and an impossible price


def test_api_football_error_payload_yields_no_quotes():
    parsed, skipped = api_football.parse_fixture_odds({"errors": {"plan": "Free plans do not have access"}, "response": []},
                                                      "A", "B")
    assert parsed == [] and "provider error" in skipped[0]


# ── Fixture identity ─────────────────────────────────────────────────────────

def test_team_names_normalise_across_providers():
    assert identity.normalise_team("Brighton & Hove Albion") == identity.normalise_team("Brighton")
    assert identity.normalise_team("FC Bayern München") == identity.normalise_team("Bayern Munich")
    assert identity.normalise_team("Paris Saint-Germain") == identity.normalise_team("PSG")
    assert identity.normalise_team("Manchester City") != identity.normalise_team("Manchester United")


def _event(home="Liverpool", away="Ipswich Town", commence=KICKOFF, event_id="ev-1"):
    return identity.ProviderEvent("the_odds_api", event_id, home, away, commence)


def test_event_maps_to_the_unique_fixture_and_is_remembered(engine_db):
    _fixture(engine_db)
    mapping = identity.resolve_fixture(engine_db, _event())
    assert mapping == identity.Mapping(("fx-1",), "names+kickoff")
    identity.remember(engine_db, _event(), mapping, _ago(0))
    engine_db.execute("UPDATE matches SET home_team = 'Renamed' WHERE id = 'fx-1'")
    assert identity.resolve_fixture(engine_db, _event()) == identity.Mapping(("fx-1",), "stored")


@pytest.mark.parametrize("event, reason", [
    (_event(commence="2030-01-01T18:30:00+00:00"), "no fixture"),   # kickoff 3.5 h away
    (_event(home="Manchester City"), "no fixture"),                 # different team, never fuzzy
    (_event(home="Ipswich Town", away="Liverpool"), "no fixture"),  # home/away reversed
])
def test_events_that_do_not_match_stay_unmapped(engine_db, event, reason):
    _fixture(engine_db)
    mapping = identity.resolve_fixture(engine_db, event)
    assert not mapping.mapped and reason in mapping.reason


def test_time_only_kickoffs_map_by_names_and_date_and_ambiguity_is_refused(engine_db):
    _fixture(engine_db, "fx-1", kickoff="15:00")
    assert identity.resolve_fixture(engine_db, _event()) == identity.Mapping(("fx-1",), "names+date")
    _fixture(engine_db, "fx-2", kickoff="15:00")
    mapping = identity.resolve_fixture(engine_db, _event())
    assert not mapping.mapped and mapping.reason == "ambiguous: 2 fixtures match (sofascore)"


# ── Storage and freshness ────────────────────────────────────────────────────

def test_duplicate_deliveries_are_stored_once(engine_db):
    _fixture(engine_db)
    assert store.store_quotes(engine_db, "fx-1", [_quote()], _ago(0)) == 1
    assert store.store_quotes(engine_db, "fx-1", [_quote()], _ago(0)) == 0
    assert engine_db.execute("SELECT COUNT(*) FROM odds_quotes").fetchone()[0] == 1


def test_the_table_itself_refuses_non_price_kinds(engine_db):
    _fixture(engine_db)
    with pytest.raises(Exception, match="CHECK constraint"):
        engine_db.execute(
            """INSERT INTO odds_quotes (fixture_id, odds_type, provider, provider_event_id, bookmaker, market,
                   selection, decimal_odds, implied_probability, observed_at, commence_time, fetched_at)
               VALUES ('fx-1', 'crowd_vote', 'sofascore', 'x', 'sofascore_vote', '1X2', 'home', 2.0, 0.5, ?, ?, ?)""",
            (_ago(1), KICKOFF, _ago(0)),
        )


def test_latest_fresh_price_per_bookmaker_is_used_never_the_historic_maximum(engine_db):
    _fixture(engine_db)
    store.store_quotes(engine_db, "fx-1", [
        _quote(price=3.0, observed=_ago(50), bookmaker="book-a"),   # superseded by book-a's later price
        _quote(price=2.0, observed=_ago(10), bookmaker="book-a"),
        _quote(price=2.1, observed=_ago(5), bookmaker="book-b"),
        _quote(price=4.0, observed=_ago(90), bookmaker="book-c"),   # stale: older than the 60 min limit
    ], _ago(0))
    quotes = store.actionable_quotes(engine_db, "fx-1", NOW, timedelta(minutes=60))
    assert [(q["bookmaker"], q["decimal_odds"]) for q in quotes] == [("book-b", 2.1), ("book-a", 2.0)]


def test_prices_after_kickoff_or_for_started_matches_are_not_actionable(engine_db):
    _fixture(engine_db)
    store.store_quotes(engine_db, "fx-1", [_quote(observed="2030-01-01T15:05:00+00:00")], _ago(0))
    assert store.actionable_quotes(engine_db, "fx-1", datetime(2030, 1, 1, 15, 30, tzinfo=timezone.utc),
                                   timedelta(minutes=60)) == []
    store.store_quotes(engine_db, "fx-1", [_quote(observed="2030-01-01T14:50:00+00:00")], _ago(0))
    assert store.actionable_quotes(engine_db, "fx-1", datetime(2030, 1, 1, 15, 1, tzinfo=timezone.utc),
                                   timedelta(minutes=60)) == []


def test_synthetic_test_prices_are_labelled_and_only_read_on_request(engine_db):
    _fixture(engine_db)
    store.store_quotes(engine_db, "fx-1", [synthetic_quote("1X2", "home", 2.0, KICKOFF, observed=_ago(5))], _ago(0))
    assert store.actionable_quotes(engine_db, "fx-1", NOW, timedelta(minutes=60)) == []
    [row] = store.actionable_quotes(engine_db, "fx-1", NOW, timedelta(minutes=60), include_synthetic=True)
    assert (row["odds_type"], row["bookmaker"]) == ("synthetic_test", "synthetic_test")


@pytest.mark.parametrize("environment", ["outside_test_env", "frozen", "production_db"])
def test_synthetic_test_prices_are_refused_outside_an_isolated_test_database(engine_db, monkeypatch, tmp_path, environment):
    _fixture(engine_db)
    if environment == "outside_test_env":
        monkeypatch.setenv("APP_ENV", "production")
    elif environment == "frozen":
        from src.baseline import state as baseline
        monkeypatch.setattr(baseline, "is_frozen", lambda: True)
    else:
        import sqlite3
        from src.db.database import init_db
        path = tmp_path / "engine.db"
        engine_db = sqlite3.connect(path)
        engine_db.row_factory = sqlite3.Row
        init_db(engine_db)
        _fixture(engine_db)
        monkeypatch.setattr(store, "_LIVE_DB", path.resolve())
    with pytest.raises(store.SyntheticOddsRefused):
        store.store_quotes(engine_db, "fx-1", [synthetic_quote("1X2", "home", 2.0, KICKOFF)], _ago(0))
    assert engine_db.execute("SELECT COUNT(*) FROM odds_quotes").fetchone()[0] == 0


# ── Quota guard ──────────────────────────────────────────────────────────────

def _fetch(conn, transport, cost=1):
    return quota.fetch_json(conn, "the_odds_api", "/sports/x/odds?markets=h2h", "https://example.invalid/?apiKey=SECRET",
                            {}, cost=cost, remaining_header="x-requests-remaining", used_header="x-requests-used",
                            cost_header="x-requests-last", transport=transport, now=NOW)


def _log(conn):
    return [tuple(r) for r in conn.execute(
        "SELECT status, cost, http_status, requests_remaining, endpoint FROM provider_quota_log ORDER BY id")]


def test_no_request_is_sent_without_an_approved_budget(engine_db, monkeypatch):
    monkeypatch.delenv("ODDS_REQUEST_BUDGET", raising=False)
    transport = transport_returning([])
    with pytest.raises(quota.BudgetExceeded, match="0 approved"):
        _fetch(engine_db, transport)
    assert transport.calls == []
    assert _log(engine_db) == [("refused", 1, None, None, "/sports/x/odds?markets=h2h")]


def test_budget_counts_actual_cost_and_stops_at_the_limit(engine_db, monkeypatch):
    monkeypatch.setenv("ODDS_REQUEST_BUDGET_THE_ODDS_API", "3")
    transport = transport_returning([], headers={"x-requests-last": "2"})
    assert _fetch(engine_db, transport) == []
    with pytest.raises(quota.BudgetExceeded):
        _fetch(engine_db, transport, cost=2)
    assert len(transport.calls) == 1
    assert [row[:4] for row in _log(engine_db)] == [("ok", 2, 200, "480"), ("refused", 2, None, None)]
    assert all("SECRET" not in row[4] for row in _log(engine_db))


def test_low_provider_quota_is_protected(engine_db, monkeypatch):
    monkeypatch.setenv("ODDS_REQUEST_BUDGET", "50")
    monkeypatch.setenv("ODDS_MIN_REMAINING", "20")
    _fetch(engine_db, transport_returning([], headers={"x-requests-remaining": "20"}))
    with pytest.raises(quota.BudgetExceeded, match="20 remaining"):
        _fetch(engine_db, transport_returning([]))


@pytest.mark.parametrize("transport, logged_status", [
    (transport_returning({"message": "Unauthorized"}, status=401), 401),
    (lambda url, headers, timeout: (_ for _ in ()).throw(TimeoutError()), None),
])
def test_provider_failures_produce_no_data_and_are_logged(engine_db, monkeypatch, transport, logged_status):
    monkeypatch.setenv("ODDS_REQUEST_BUDGET", "5")
    with pytest.raises(quota.ProviderUnavailable):
        _fetch(engine_db, transport)
    [(status, _, http_status, _, _)] = _log(engine_db)
    assert (status, http_status) == ("error", logged_status)


# ── Collection end to end (mocked provider) ──────────────────────────────────

def test_collection_maps_stores_dedupes_and_reports_unmapped_events(engine_db, monkeypatch):
    _fixture(engine_db)
    events = [
        odds_api_event("ev-1", "Liverpool", "Ipswich Town", KICKOFF, h2h=(1.3, 5.5, 9.0), observed=_ago(5)),
        odds_api_event("ev-2", "Arsenal", "Chelsea", KICKOFF, h2h=(2.0, 3.4, 3.8), observed=_ago(5)),
    ]
    first = ingest_odds_api(engine_db, monkeypatch, events, now=NOW)
    again = ingest_odds_api(engine_db, monkeypatch, events, now=NOW)
    assert (first["requests_ok"], first["events"], first["mapped"], first["quotes_new"]) == (1, 2, 1, 3)
    assert first["unmapped"] == [f"Arsenal v Chelsea {KICKOFF}: no fixture with these teams near this kickoff"]
    assert (again["quotes_new"], again["quotes_duplicate"]) == (0, 3)
    assert engine_db.execute("SELECT COUNT(*) FROM odds_quotes").fetchone()[0] == 3


def test_refusal_stops_collection_without_any_quotes(engine_db, monkeypatch):
    _fixture(engine_db)
    monkeypatch.setenv("ODDS_REQUEST_BUDGET", "0")
    transport = transport_returning([])
    report = collector.collect_the_odds_api(engine_db, [{"league_id": 17}, {"league_id": 8}], "key",
                                            transport=transport, now=NOW)
    assert (report["requests_refused"], report["requests_ok"], transport.calls) == (1, 0, [])
    assert engine_db.execute("SELECT COUNT(*) FROM odds_quotes").fetchone()[0] == 0


# ── Value detection uses only fresh real prices ─────────────────────────────

MATCH = {"id": "fx-1", "home_team": "Liverpool", "away_team": "Ipswich Town", "league_name": "Premier League", "league_id": 17}
PROBS = {"1X2": {"home": 0.6, "draw": 0.25, "away": 0.15}, "O/U 2.5": {"over": 0.55, "under": 0.45},
         "BTTS": {"yes": 0.5, "no": 0.5}}


def test_value_uses_best_fresh_price_and_the_matching_line_only(engine_db):
    _fixture(engine_db)
    store.store_quotes(engine_db, "fx-1", [
        _quote("home", 1.9, _ago(10), "book-a"), _quote("home", 2.0, _ago(10), "book-b"),
        _quote("home", 9.0, _ago(600), "book-c"),
        _quote("over", 2.2, _ago(10), "book-a", market="TOTALS", line="3.5"),
        _quote("over", 1.95, _ago(10), "book-a", market="TOTALS", line="2.5"),
    ], _ago(0))
    candidates = {(c["market"], c["selection"]): c for c in find_value(MATCH, PROBS, engine_db, now=NOW)}
    assert set(candidates) == {("1X2", "home"), ("O/U 2.5", "over")}
    home = candidates[("1X2", "home")]
    assert (home["odds"], home["bookmaker"], home["odds_type"], home["edge"]) == (2.0, "book-b", "bookmaker", 0.1)
    assert candidates[("O/U 2.5", "over")]["odds"] == 1.95


def test_no_fresh_real_price_means_no_candidate(engine_db, monkeypatch):
    # The window is set explicitly: the shadow host exports ODDS_MAX_AGE_MINUTES=240, under which a
    # 120-minute-old quote is legitimately fresh.
    monkeypatch.setenv("ODDS_MAX_AGE_MINUTES", "60")
    _fixture(engine_db)
    assert find_value(MATCH, PROBS, engine_db, now=NOW) == []
    store.store_quotes(engine_db, "fx-1", [_quote("home", 2.0, _ago(120))], _ago(0))
    assert find_value(MATCH, PROBS, engine_db, now=NOW) == []
    monkeypatch.setenv("ODDS_MAX_AGE_MINUTES", "240")       # the shadow's window: older than 240 min is not fresh
    store.store_quotes(engine_db, "fx-1", [_quote("home", 2.1, _ago(300))], _ago(0))
    assert all(c["odds"] != 2.1 for c in find_value(MATCH, PROBS, engine_db, now=NOW))


# ── Crowd votes are kept apart and never become prices ──────────────────────

VOTE_EVENT = {
    "id": "vote-1", "tournament": {"uniqueTournament": {"id": 17, "name": "Premier League"}},
    "homeTeam": {"name": "Liverpool"}, "awayTeam": {"name": "Ipswich Town"},
    "status": {"type": "notstarted"}, "homeScore": {}, "awayScore": {},
    "startTimestamp": 1893456000, "vote": {"vote1": 10, "votex": 45, "vote2": 45},
}


def test_crowd_votes_are_stored_as_shares_not_odds(engine_db):
    collect_fixtures([VOTE_EVENT], "2030-01-01", engine_db)
    rows = engine_db.execute(
        "SELECT source, selection, votes, share FROM crowd_vote_signals ORDER BY selection").fetchall()
    assert [tuple(r) for r in rows] == [("sofascore_vote", "away", 45, 0.45), ("sofascore_vote", "draw", 45, 0.45),
                                        ("sofascore_vote", "home", 10, 0.1)]
    assert engine_db.execute("SELECT COUNT(*) FROM odds_quotes").fetchone()[0] == 0
    assert engine_db.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone()[0] == 0


def test_crowd_votes_alone_never_produce_a_pick(engine_db):
    # Before M2.5 these votes became pseudo-odds of 10.0 for the home side and an official pick.
    result = run_pipeline("2030-01-01", [VOTE_EVENT], engine_db)
    assert (result["tracked_matches"], result["candidates_found"], result["final_picks"]) == (1, 0, 0)
    assert engine_db.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 0


def test_api_football_collection_attaches_quotes_by_provider_fixture_id(engine_db, monkeypatch):
    _fixture(engine_db, "777", provider="api_football")
    monkeypatch.setenv("ODDS_REQUEST_BUDGET_API_FOOTBALL", "1")
    payload = {"errors": [], "response": [{
        "fixture": {"id": 777, "date": KICKOFF}, "update": _ago(5),
        "bookmakers": [{"name": "Bet365", "bets": [{"name": "Match Winner", "values": [
            {"value": "Home", "odd": "1.30"}, {"value": "Draw", "odd": "5.50"}, {"value": "Away", "odd": "9.00"}]}]}],
    }]}
    transport = transport_returning(payload, headers={"x-ratelimit-requests-remaining": "90"})
    report = collector.collect_api_football(engine_db, [dict(engine_db.execute("SELECT * FROM matches").fetchone())],
                                            "key", "v3.football.api-sports.io", transport=transport, now=NOW)
    assert (report["requests_ok"], report["mapped"], report["quotes_new"]) == (1, 1, 3)
    assert transport.calls == ["https://v3.football.api-sports.io/odds?fixture=777"]
    assert engine_db.execute("SELECT method FROM fixture_identity").fetchone()[0] == "provider_id"


def test_legacy_odds_fetcher_sends_nothing_without_a_budget(engine_db, monkeypatch):
    from src.data.odds_fetcher import TheOddsAPIProvider

    monkeypatch.setenv("ODDS_API_KEY", "configured-but-no-budget")
    monkeypatch.delenv("ODDS_REQUEST_BUDGET", raising=False)
    assert TheOddsAPIProvider(engine_db)._fetch_from_api("soccer_epl") == []
    assert engine_db.execute("SELECT status FROM provider_quota_log").fetchone()[0] == "refused"


def test_sport_keys_follow_the_fixture_providers_league_id_scheme():
    assert the_odds_api.sport_for({"provider": "sofascore", "league_id": 17}) == "soccer_epl"
    assert the_odds_api.sport_for({"provider": "api_football", "league_id": 39}) == "soccer_epl"
    assert the_odds_api.sport_for({"provider": "matches_db", "league_id": 17}) is None
    assert the_odds_api.sport_for({"provider": "matches_db", "league_id": 71}) is None  # Brazil Serie A


def test_odds_worker_dry_run_reports_cost_and_sends_nothing(engine_db, monkeypatch, capsys):
    import json as _json
    import scripts.odds_worker as worker

    _fixture(engine_db, "a", date=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    _fixture(engine_db, "b", home="Arsenal", away="Chelsea", date=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    _fixture(engine_db, "c", home="Inter", away="Milan", league_id=23,
             date=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    monkeypatch.setattr(worker, "get_db", lambda: engine_db)
    monkeypatch.setattr("sys.argv", ["odds_worker.py", "--dry-run"])
    monkeypatch.setenv("ODDS_API_MARKETS", "h2h,totals")
    worker.main()
    plan = _json.loads(capsys.readouterr().out)["plan"]
    assert (plan["requests"], plan["estimated_cost"], plan["approved_daily_budget"]) == (2, 4, 0)
    assert plan["competitions"] == ["soccer_epl", "soccer_italy_serie_a"]
    assert engine_db.execute("SELECT COUNT(*) FROM provider_quota_log").fetchone()[0] == 0


def test_one_real_match_stored_by_two_providers_receives_the_same_quotes(engine_db, monkeypatch):
    _fixture(engine_db, "fx-sofa")                                         # official pipeline row
    _fixture(engine_db, "1545382", kickoff="15:00", provider="matches_db")  # same match, another feed
    report = ingest_odds_api(engine_db, monkeypatch, [
        odds_api_event("ev-1", "Liverpool", "Ipswich Town", KICKOFF, h2h=(1.3, 5.5, 9.0), observed=_ago(5)),
    ], now=NOW)
    assert (report["mapped"], report["quotes_new"]) == (1, 6)
    assert [r[0] for r in engine_db.execute("SELECT fixture_id FROM fixture_identity ORDER BY 1")] == ["1545382", "fx-sofa"]

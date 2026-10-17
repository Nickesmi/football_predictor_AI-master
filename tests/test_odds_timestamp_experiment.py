"""M3.2: minimal odds-timestamp experiment (plan and analysis are offline)."""
import subprocess
import sys
from pathlib import Path

from src.odds.timestamp_experiment import analyse, classify_pair, plan

ROOT = Path(__file__).resolve().parent.parent


def _fx(i, league, kickoff):
    return {"id": str(i), "league_id": league, "kickoff": kickoff}


def test_plan_picks_two_leagues_two_fixtures_three_fetches():
    fixtures = [_fx(1, 39, "2030-01-05T15:00:00+00:00"), _fx(2, 39, "2030-01-05T15:00:00+00:00"),
                _fx(3, 78, "2030-01-05T14:30:00+00:00"), _fx(4, 78, "2030-01-05T17:30:00+00:00"),
                _fx(5, 61, "2030-01-05T20:00:00+00:00")]
    p = plan(fixtures)
    assert len(p["fixtures"]) == 4 and p["leagues"] == [39, 78]
    assert len(p["fetches"]) == 12 and p["requests"] == 13
    assert p["fetches"][0]["at"] == "2030-01-05T08:30:00+00:00"   # 14:30 kickoff - 6 h


def _item(update, home="1.50"):
    return {"update": update, "bookmakers": [{"name": "Bet365", "bets": [{"name": "Match Winner", "values": [
        {"value": "Home", "odd": home}, {"value": "Draw", "odd": "4.0"}, {"value": "Away", "odd": "6.0"}]}]}]}


def _fetch(at, update, home="1.50"):
    return {"fixture_id": "1", "fetched_at": at, "kickoff": "2030-01-05T15:00:00+00:00",
            "payload": {"response": [_item(update, home)]}}


def test_pair_classification():
    a = {"update": "t1", "prices": "p1"}
    assert classify_pair(a, {"update": "t2", "prices": "p2"}) == "CHANGE"
    assert classify_pair(a, {"update": "t2", "prices": "p1"}) == "CHECK"
    assert classify_pair(a, {"update": "t1", "prices": "p1"}) == "NO_REFRESH"
    assert classify_pair(a, {"update": "t1", "prices": "p2"}) == "INCONSISTENT"


def test_verdicts():
    check = [_fetch("2030-01-05T09:00:00+00:00", "2030-01-05T08:00:00+00:00"),
             _fetch("2030-01-05T12:30:00+00:00", "2030-01-05T11:00:00+00:00")]
    assert analyse(check)["verdict"] == "STAMP_IS_LAST_CHECK"
    change = [_fetch("2030-01-05T09:00:00+00:00", "2030-01-05T08:00:00+00:00"),
              _fetch("2030-01-05T12:30:00+00:00", "2030-01-05T11:00:00+00:00", home="1.45")]
    assert analyse(change)["verdict"] == "STAMP_IS_LAST_CHANGE_OR_CHECK"
    stale = [_fetch("2030-01-05T09:00:00+00:00", "2030-01-05T08:00:00+00:00"),
             _fetch("2030-01-05T12:30:00+00:00", "2030-01-05T08:00:00+00:00")]
    r = analyse(stale)
    assert r["verdict"] == "UNDETERMINED" and r["stamp_age_within"][240] == (1, 2)
    bad = [_fetch("2030-01-05T09:00:00+00:00", "2030-01-05T08:00:00+00:00"),
           _fetch("2030-01-05T12:30:00+00:00", "2030-01-05T08:00:00+00:00", home="1.40")]
    assert analyse(bad)["verdict"] == "STAMP_UNRELIABLE"


def test_fetch_refuses_without_approval_and_live_db(tmp_path):
    script = str(ROOT / "scripts" / "odds_timestamp_experiment.py")
    r = subprocess.run([sys.executable, script, "fetch", "--db", str(ROOT / "data" / "engine.db"), "--fixture", "1",
                        "--approved-requests", "5", "--out-dir", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 2
    db = tmp_path / "x.db"
    db.write_bytes(b"")
    r = subprocess.run([sys.executable, script, "fetch", "--db", str(db), "--fixture", "1",
                        "--approved-requests", "0", "--out-dir", str(tmp_path / "o")], capture_output=True, text=True)
    assert r.returncode == 2 and "approved" in r.stderr

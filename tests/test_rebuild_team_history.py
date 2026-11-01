"""History rebuild on a copy (scripts/rebuild_team_history.py, 2026-11-01): verified imports only, duplicates
skipped, exact state lookup, oldest-first replay, as-of builds without later matches. No network."""
import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from src.db.database import init_db

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("rebuild", ROOT / "scripts" / "rebuild_team_history.py")
rebuild = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rebuild)


def _db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _played(conn, mid, d, home, away, hg, ag, label="La Liga", league_id=140):
    from src.engine.live_updater import on_match_finished

    conn.execute("INSERT INTO matches (id, date, home_team, away_team, league_name, league_id, status, home_goals, "
                 "away_goals, provider) VALUES (?, ?, ?, ?, ?, ?, 'FT', ?, ?, 'api_football')",
                 (mid, d, home, away, label, league_id, hg, ag))
    on_match_finished(conn, mid, d, label, home, away, hg, ag)


def _fx(fid, ko, home, away, hg, ag, status="FT", league_id=140):
    return {"id": str(fid), "source": "api_football", "provider": "api_football", "status": status,
            "kickoff_utc": ko, "date": ko[:10], "time": ko[11:16], "league_id_apifootball": str(league_id),
            "league": {"id": str(league_id), "name": "La Liga", "country": "Spain"},
            "home_team": {"id": "1", "name": home}, "away_team": {"id": "2", "name": away},
            "home_goals": hg, "away_goals": ag}


def _source(tmp_path):
    src = tmp_path / "src" / "engine.db"
    src.parent.mkdir()
    conn = _db(src)
    _played(conn, "100", "2026-09-01", "Real Madrid", "Elche", 2, 0)
    _played(conn, "101", "2026-09-08", "Elche", "Getafe", 1, 1)
    _played(conn, "102", "2026-09-15", "Getafe", "Real Madrid", 0, 3)
    conn.commit()
    conn.close()
    return src


def _cache(tmp_path, files: dict):
    cache = tmp_path / "cache"
    cache.mkdir()
    for day, fixtures in files.items():
        (cache / f"fixtures-{day}.json").write_text(json.dumps(fixtures))
    return cache


def test_selection_accepts_only_verified_unique_results(tmp_path):
    src = _source(tmp_path)
    cache = _cache(tmp_path, {
        "2026-09-22": [_fx(200, "2026-09-22T19:00:00Z", "Real Madrid CF", "Elche CF", 1, 0),        # accepted
                       _fx(201, "2026-09-22T19:00:00Z", "Getafe", "Elche", None, None),             # no score
                       _fx(202, "2026-09-22T19:00:00Z", "Getafe", "Elche", 0, 0, status="NS"),      # not finished
                       _fx(203, "2026-09-22T19:00:00Z", "Unknown CF", "Elche", 2, 2),               # no identity
                       _fx(100, "2026-09-01T19:00:00Z", "Real Madrid", "Elche", 2, 0),              # stored id
                       _fx(204, "2026-09-08T19:00:00Z", "Elche", "Getafe", 1, 1),                   # stored, other id
                       _fx(205, "2026-09-29T19:00:00Z", "Elche", "Real Madrid", 0, 1),
                       _fx(206, "2026-10-31T19:00:00Z", "Getafe", "Elche", 1, 0)],                  # after cutoff
        "2026-09-29": [_fx(205, "2026-09-29T19:00:00Z", "Elche", "Real Madrid", 0, 2)],             # score conflict
    })
    cands, pre = rebuild.candidates(cache, "2026-10-31")
    conn = sqlite3.connect(src)
    accepted, rejected, details, dup = rebuild.select(conn, cands)
    rejected.update(pre)
    assert [c["id"] for c in accepted] == ["200"]
    assert accepted[0]["home_record"] == "Real Madrid" and accepted[0]["label"] == "La Liga"
    assert dict(rejected) == {"team_identity_unresolved": 1, "conflicting_scores_in_cache": 1, "no_final_score": 1,
                              "kickoff_not_before_cutoff": 1}
    assert dict(dup) == {"same_match_id_already_stored": 1, "same_match_stored_under_another_id": 1}


def test_exact_replay_never_loads_another_teams_state(tmp_path):
    db = tmp_path / "x.db"
    _db(db).close()
    rows = [dict(match_id="1", match_date="2026-09-01", league="La Liga", home_team="Real Madrid II",
                 away_team="Elche", home_goals=5, away_goals=0),
            dict(match_id="2", match_date="2026-09-02", league="La Liga", home_team="Real Madrid",
                 away_team="Getafe", home_goals=0, away_goals=1)]
    rebuild.replay(db, rows, exact=True)
    c = sqlite3.connect(db)
    got = dict(c.execute("SELECT team_name, matches_played FROM team_state WHERE venue='overall'").fetchall())
    assert got == {"Real Madrid II": 1, "Elche": 1, "Real Madrid": 1, "Getafe": 1}
    rebuild.replay(db, rows, exact=False)              # the module's own lookup: substring fallback
    got = dict(c.execute("SELECT team_name, matches_played FROM team_state WHERE venue='overall'").fetchall())
    assert got.get("Real Madrid II") == 2 and "Real Madrid" not in got   # documents the defect avoided above


def test_end_to_end_copy_only_oldest_first_and_as_of_has_no_later_matches(tmp_path):
    src = _source(tmp_path)
    before = src.read_bytes()
    cache = _cache(tmp_path, {"2026-09-22": [_fx(200, "2026-09-22T19:00:00Z", "Real Madrid", "Elche", 1, 0)],
                              "2026-09-05": [_fx(201, "2026-09-05T19:00:00Z", "Getafe", "Real Madrid", 2, 2)]})
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "rebuild_team_history.py"), "--source", str(src),
                        "--cache", str(cache), "--out", str(out), "--as-of", "2026-09-10"],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert src.read_bytes() == before                                      # source untouched
    rep = json.loads((out / "import_report.json").read_text())
    assert rep["accepted"] == 2 and rep["reproduction"]["identical"] == rep["reproduction"]["rows_a"]
    full = sqlite3.connect(out / "rebuilt.db")
    order = [r[0] for r in full.execute("SELECT match_id FROM match_history ORDER BY id")]
    assert order == ["100", "201", "101", "102", "200"]                    # oldest first
    i = rep["rebuilt_integrity"]
    assert i["integrity_check"] == "ok" and i["matches_played_consistent"] == i["state_rows_with_history"]
    asof = sqlite3.connect(out / "rebuilt_asof.db")
    assert asof.execute("SELECT MAX(match_date) FROM match_history").fetchone()[0] < "2026-09-10"
    assert asof.execute("SELECT MAX(last_match_date) FROM team_state").fetchone()[0] < "2026-09-10"
    rm = full.execute("SELECT matches_played FROM team_state WHERE team_name='Real Madrid' AND venue='overall'").fetchone()
    assert rm[0] == 4

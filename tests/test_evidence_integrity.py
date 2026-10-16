"""M3 Phase B: evidence classification, quarantine, move and restore (src/integrity)."""
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from src.db.database import init_db
from src.integrity import evidence
from src.integrity.evidence import (
    DUPLICATE, FABRICATED_PLACEHOLDER, INCONSISTENT, RETROSPECTIVE,
    UNVERIFIABLE_TIMING, VERIFIED_SETTLED, VERIFIED_UNSETTLED, timing_class,
)

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "evidence.db")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


def _pe(conn, fid, *, probs=(50.0, 30.0, 20.0), pred="home", actual=None, goals=(None, None),
        correct=None, created="2026-07-01 08:00:00", date="2026-07-01", home="A", away="B"):
    h, d, a = probs
    conn.execute(
        """INSERT INTO prediction_errors (fixture_id, match_date, league_name, home_team, away_team,
               predicted_result, home_win_pct, draw_pct, away_win_pct, confidence, confidence_bucket,
               actual_result, home_goals, away_goals, correct, created_at)
           VALUES (?, ?, 'L', ?, ?, ?, ?, ?, ?, ?, '45-50%', ?, ?, ?, ?, ?)""",
        (fid, date, home, away, pred, h, d, a, max(probs), actual, goals[0], goals[1], correct, created),
    )


def _match(conn, mid, date="2026-07-01", kickoff="20:00"):
    conn.execute("INSERT INTO matches (id, date, kickoff, home_team, away_team, league_name) "
                 "VALUES (?, ?, ?, 'A', 'B', 'L')", (mid, date, kickoff))


def _classes(conn):
    return {c.pk: c.classification for c in evidence.classify_all(conn) if c.source_table == "prediction_errors"}


class TestTiming:
    K = datetime(2026, 7, 1, 20, 0)

    def test_pre_match_needs_full_offset_margin(self):
        assert timing_class("2026-07-01 16:30:00", "2026-07-01", self.K)[0] == "pre_match"
        assert timing_class("2026-07-01 16:31:00", "2026-07-01", self.K)[0] == "unverifiable"

    def test_after_naive_kickoff_is_retrospective(self):
        assert timing_class("2026-07-01 20:00:00", "2026-07-01", self.K)[0] == "retrospective"

    def test_no_kickoff_uses_matchday_bounds(self):
        assert timing_class("2026-06-30 20:00:00", "2026-07-01", None)[0] == "pre_match"
        assert timing_class("2026-07-01 12:00:00", "2026-07-01", None)[0] == "unverifiable"
        assert timing_class("2026-07-02 00:00:00", "2026-07-01", None)[0] == "retrospective"

    def test_missing_created_at_is_never_verified(self):
        assert timing_class(None, "2026-07-01", self.K)[0] == "unverifiable"


class TestClassification:
    def test_placeholder_is_fabricated_even_if_settled_before_kickoff(self, conn):
        _match(conn, "1")
        _pe(conn, "1", probs=(33.0, 33.0, 33.0), actual="away", goals=(0, 1), correct=0)
        assert _classes(conn) == {1: FABRICATED_PLACEHOLDER}

    def test_real_33_33_34_is_not_the_placeholder(self, conn):
        _match(conn, "1")
        _pe(conn, "1", probs=(33.0, 33.0, 34.0), pred="away")
        assert _classes(conn) == {1: VERIFIED_UNSETTLED}

    def test_verified_settled_and_unsettled(self, conn):
        _match(conn, "1")
        _match(conn, "2")
        _pe(conn, "1", actual="home", goals=(2, 1), correct=1, home="C")
        _pe(conn, "2")
        assert _classes(conn) == {1: VERIFIED_SETTLED, 2: VERIFIED_UNSETTLED}

    def test_retrospective_and_unverifiable(self, conn):
        _match(conn, "1")
        _match(conn, "2")
        _pe(conn, "1", created="2026-07-01 21:00:00", home="C")
        _pe(conn, "2", created="2026-07-01 18:00:00")
        assert _classes(conn) == {1: RETROSPECTIVE, 2: UNVERIFIABLE_TIMING}

    @pytest.mark.parametrize("kwargs", [
        dict(actual="away", goals=(2, 1), correct=0),          # result disagrees with goals
        dict(actual="home", goals=(2, 1), correct=0),          # correct flag wrong
        dict(actual="home", goals=(None, None), correct=1),    # settled without goals
        dict(probs=(50.0, 30.0, 30.0)),                        # sums to 110
        dict(pred="away"),                                     # not the argmax
    ])
    def test_inconsistent_rows(self, conn, kwargs):
        _match(conn, "1")
        _pe(conn, "1", **kwargs)
        assert _classes(conn) == {1: INCONSISTENT}

    def test_second_record_of_same_match_is_duplicate(self, conn):
        _match(conn, "1")
        _match(conn, "2")
        _pe(conn, "1", created="2026-07-01 08:00:00")
        _pe(conn, "2", created="2026-07-01 09:00:00")
        assert _classes(conn) == {1: VERIFIED_UNSETTLED, 2: DUPLICATE}

    def test_prediction_log_after_matchday_is_retrospective(self, conn):
        conn.execute("""INSERT INTO prediction_log (match_id, match_date, home_team, away_team, league_name,
                        market, market_type, predicted_prob, actual_outcome, created_at)
                        VALUES ('9', '2020-01-01', 'A', 'B', 'L', 'Over 2.5', 'goals', 70.0, 1,
                                '2026-06-04 07:00:00')""")
        got = [c for c in evidence.classify_all(conn) if c.source_table == "prediction_log"]
        assert [c.classification for c in got] == [RETROSPECTIVE]


class TestQuarantine:
    def _setup(self, conn):
        _match(conn, "1")
        _match(conn, "2")
        _pe(conn, "1", probs=(33.0, 33.0, 33.0), actual="away", goals=(0, 1), correct=0)
        _pe(conn, "2", actual="home", goals=(1, 0), correct=1, home="C")
        conn.commit()
        classified = evidence.classify_all(conn)
        evidence.record_classification(conn, classified)
        return classified

    def test_quarantine_copies_without_touching_source(self, conn):
        classified = self._setup(conn)
        assert evidence.quarantine(conn, classified) == 1
        assert conn.execute("SELECT COUNT(*) FROM prediction_errors").fetchone()[0] == 2
        row = conn.execute("SELECT * FROM evidence_quarantine").fetchone()
        assert (row["source_table"], row["original_pk"], row["classification"]) == \
            ("prediction_errors", 1, FABRICATED_PLACEHOLDER)
        assert json.loads(row["original_row_json"])["home_win_pct"] == 33.0
        assert conn.execute("SELECT COUNT(*) FROM evidence_classification").fetchone()[0] == 2

    def test_move_then_restore_round_trips_exactly(self, conn):
        classified = self._setup(conn)
        before = [tuple(r) for r in conn.execute("SELECT * FROM prediction_errors ORDER BY id")]
        evidence.quarantine(conn, classified)
        assert evidence.move_quarantined(conn, "prediction_errors") == 1
        assert [r[0] for r in conn.execute("SELECT id FROM prediction_errors")] == [2]
        assert evidence.restore_quarantined(conn, "prediction_errors") == 1
        after = [tuple(r) for r in conn.execute("SELECT * FROM prediction_errors ORDER BY id")]
        assert after == before

    def test_move_refuses_when_row_changed_after_quarantine(self, conn):
        evidence.quarantine(conn, self._setup(conn))
        conn.execute("UPDATE prediction_errors SET league_name = 'edited' WHERE id = 1")
        conn.commit()
        with pytest.raises(RuntimeError, match="differ"):
            evidence.move_quarantined(conn, "prediction_errors")
        assert conn.execute("SELECT COUNT(*) FROM prediction_errors").fetchone()[0] == 2

    def test_restore_never_overwrites_a_different_row(self, conn):
        evidence.quarantine(conn, self._setup(conn))
        evidence.move_quarantined(conn, "prediction_errors")
        _pe(conn, "1", home="X")  # a new legitimate row takes the freed fixture... with a new id
        conn.execute("UPDATE prediction_errors SET id = 1 WHERE home_team = 'X'")
        conn.commit()
        with pytest.raises(RuntimeError, match="occupied"):
            evidence.restore_quarantined(conn, "prediction_errors")
        assert conn.execute("SELECT home_team FROM prediction_errors WHERE id = 1").fetchone()[0] == "X"


class TestRemediationScript:
    def _run(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "remediate_evidence.py"), *args],
                              capture_output=True, text=True, cwd=ROOT)

    def test_refuses_live_database(self, tmp_path):
        r = self._run("--source", str(ROOT / "data" / "engine.db"), "--out", str(tmp_path / "o"))
        assert r.returncode == 2 and "live database" in r.stderr

    def test_refuses_non_empty_output(self, tmp_path):
        (tmp_path / "x").write_text("x")
        r = self._run("--source", str(tmp_path / "src.db"), "--out", str(tmp_path))
        assert r.returncode == 2 and "not empty" in r.stderr

    def test_isolated_rebuild_leaves_source_untouched(self, tmp_path, conn):
        _match(conn, "1")
        _pe(conn, "1", probs=(33.0, 33.0, 33.0), actual="away", goals=(0, 1), correct=0)
        conn.execute("INSERT INTO model_confidence_adjustments (league_name, sample_count, adjustment_factor) "
                     "VALUES ('L', 600, 1.3)")
        conn.commit()
        src = Path(conn.execute("PRAGMA database_list").fetchone()[2])
        before = src.read_bytes()
        r = self._run("--source", str(src), "--out", str(tmp_path / "out"))
        assert r.returncode == 0, r.stderr
        assert src.read_bytes() == before
        report = json.loads((tmp_path / "out" / "rebuild_report.json").read_text())
        assert report["moved_rows"]["prediction_errors"] == 1
        assert report["counts_after"]["model_confidence_adjustments"] == 0
        league = report["league_comparison"][0]
        assert league["old_active"] and not league["new_active"]
        cand = sqlite3.connect(tmp_path / "out" / "engine_m3_candidate.db")
        assert cand.execute("SELECT COUNT(*) FROM model_confidence_adjustments_pre_m3").fetchone()[0] == 1
        assert cand.execute("SELECT COUNT(*) FROM evidence_quarantine").fetchone()[0] == 1

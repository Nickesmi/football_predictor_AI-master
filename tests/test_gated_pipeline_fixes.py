"""
Regression tests for the gated pipeline audit fixes (AUDIT_REPORT_2026-09-13.md):

- F4:  unique constraints prevent duplicate picks / qualification_log rows
- F6:  UTC-consistent time handling (config.now_utc/parse_utc, leakage guard)
- F10: isotonic calibrator's chronological split orders by match_date, not id
- F11: stale (never-finished) matches get their picks voided, not stuck pending
- Settlement: auto_settle_picks grades picks with the vocabulary/convention
  pipeline.py and get_portfolio_summary actually use
"""
import sqlite3
from datetime import timedelta

import pytest

from src.config import now_utc, parse_utc
from src.db.picks_repo import insert_pick, get_portfolio_summary
from api.services.portfolio_service import auto_settle_picks, void_stale_picks
from src.engine.isotonic_calibrator import _fetch_settled_predictions


def _insert_match(conn, match_id, status="FT", home_goals=None, away_goals=None, date="2026-01-01"):
    conn.execute(
        """INSERT INTO matches (id, date, home_team, away_team, league_name, status, home_goals, away_goals)
           VALUES (?, ?, 'Home FC', 'Away FC', 'Test League', ?, ?, ?)""",
        (match_id, date, status, home_goals, away_goals),
    )
    conn.commit()


def _insert_pick(conn, match_id, market, selection, odds=2.0, stake=1.0):
    return insert_pick(conn, {
        "match_id": match_id,
        "market": market,
        "selection": selection,
        "model_prob": 0.6,
        "implied_prob": 0.5,
        "edge": 0.1,
        "odds_at_pick": odds,
        "confidence": 0.7,
        "league_reliability": 8.0,
        "grade": "A",
        "stake_units": stake,
    })


# ── F4: unique constraints ──────────────────────────────────────────

def test_duplicate_pick_rejected_by_unique_index(engine_db):
    _insert_match(engine_db, "m1")
    _insert_pick(engine_db, "m1", "1X2", "home")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_pick(engine_db, "m1", "1X2", "home")


def test_distinct_selections_for_same_match_market_are_not_treated_as_duplicates(engine_db):
    """Guards against a repeat of F17 (the legacy dedup key was coarse enough to
    collapse genuinely distinct markets) — picks' unique index must key on the
    exact (match_id, market, selection), not a coarser grouping."""
    _insert_match(engine_db, "m1")
    _insert_pick(engine_db, "m1", "1X2", "home")
    _insert_pick(engine_db, "m1", "1X2", "draw")
    _insert_pick(engine_db, "m1", "1X2", "away")

    rows = engine_db.execute("SELECT COUNT(*) FROM picks WHERE match_id = 'm1'").fetchone()[0]
    assert rows == 3


def test_duplicate_qualification_log_rejected_by_unique_index(engine_db):
    engine_db.execute(
        """INSERT INTO qualification_log
           (match_id, match_date, home_team, away_team, league_name, market,
            pick_quality_score, tier)
           VALUES ('m1', '2026-01-01', 'Home FC', 'Away FC', 'Test League', '1X2', 80.0, 'A')"""
    )
    engine_db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        engine_db.execute(
            """INSERT INTO qualification_log
               (match_id, match_date, home_team, away_team, league_name, market,
                pick_quality_score, tier)
               VALUES ('m1', '2026-01-01', 'Home FC', 'Away FC', 'Test League', '1X2', 55.0, 'C')"""
        )


# ── Settlement: vocabulary + convention ─────────────────────────────

def test_auto_settle_picks_grades_1x2_home_win_correctly(engine_db):
    _insert_match(engine_db, "m1", status="FT", home_goals=2, away_goals=0)
    pick_id = _insert_pick(engine_db, "m1", "1X2", "home", odds=2.0, stake=1.0)

    result = auto_settle_picks(engine_db)

    assert result["settled"] == 1
    row = engine_db.execute("SELECT result, pnl_units FROM picks WHERE id = ?", (pick_id,)).fetchone()
    assert row["result"] == "won"
    assert row["pnl_units"] == pytest.approx(1.0)  # stake(1.0) * (odds(2.0) - 1)


def test_auto_settle_picks_grades_ou_and_btts(engine_db):
    _insert_match(engine_db, "m1", status="FT", home_goals=2, away_goals=1)
    over_id = _insert_pick(engine_db, "m1", "O/U 2.5", "over", odds=1.9)
    btts_id = _insert_pick(engine_db, "m1", "BTTS", "yes", odds=1.8)

    auto_settle_picks(engine_db)

    over_row = engine_db.execute("SELECT result FROM picks WHERE id = ?", (over_id,)).fetchone()
    btts_row = engine_db.execute("SELECT result FROM picks WHERE id = ?", (btts_id,)).fetchone()
    assert over_row["result"] == "won"   # total goals 3 > 2.5
    assert btts_row["result"] == "won"   # both teams scored


def test_settled_picks_counted_by_get_portfolio_summary(engine_db):
    """Regression guard: a settlement path that wrote result='W'/'L' (as the old
    settlement_worker.py inline branch did) would settle picks that
    get_portfolio_summary silently never counts as a win or a loss."""
    _insert_match(engine_db, "m1", status="FT", home_goals=2, away_goals=0)
    _insert_pick(engine_db, "m1", "1X2", "home", odds=2.0, stake=1.0)

    auto_settle_picks(engine_db)
    summary = get_portfolio_summary(engine_db)

    assert summary["wins"] == 1
    assert summary["losses"] == 0
    assert summary["pending"] == 0


# ── F11: stale matches get voided, not stuck pending forever ───────

def test_void_stale_picks_closes_picks_for_never_finished_matches(engine_db):
    old_date = (now_utc() - timedelta(hours=100)).strftime("%Y-%m-%d")
    _insert_match(engine_db, "m_stale", status="NS", date=old_date)
    pick_id = _insert_pick(engine_db, "m_stale", "1X2", "home")

    result = void_stale_picks(engine_db, max_age_hours=48)

    assert result["voided"] == 1
    row = engine_db.execute("SELECT result, pnl_units FROM picks WHERE id = ?", (pick_id,)).fetchone()
    assert row["result"] == "void"
    assert row["pnl_units"] == 0.0


def test_void_stale_picks_leaves_recent_pending_matches_alone(engine_db):
    recent_date = now_utc().strftime("%Y-%m-%d")
    _insert_match(engine_db, "m_recent", status="NS", date=recent_date)
    pick_id = _insert_pick(engine_db, "m_recent", "1X2", "home")

    result = void_stale_picks(engine_db, max_age_hours=48)

    assert result["voided"] == 0
    row = engine_db.execute("SELECT result FROM picks WHERE id = ?", (pick_id,)).fetchone()
    assert row["result"] is None


# ── F10: isotonic calibrator orders by match_date, not row id ───────

def test_fetch_settled_predictions_orders_by_match_date_not_id(engine_db):
    # Insert rows so that autoincrement id order is the REVERSE of match_date
    # order — this is exactly the scenario (e.g. a backfill) that would break
    # the walk-forward split if sorting fell back to row id.
    rows = [
        ("m3", "2026-03-01", 70.0, 1),
        ("m2", "2026-02-01", 60.0, 0),
        ("m1", "2026-01-01", 50.0, 1),
    ]
    for match_id, match_date, prob, outcome in rows:
        engine_db.execute(
            """INSERT INTO prediction_log
               (match_id, match_date, home_team, away_team, league_name,
                market, market_type, predicted_prob, actual_outcome)
               VALUES (?, ?, 'H', 'A', 'League', 'goals', 'goals', ?, ?)""",
            (match_id, match_date, prob, outcome),
        )
    engine_db.commit()

    fetched = _fetch_settled_predictions(engine_db)

    dates_in_result_order = [r["predicted_prob"] for r in fetched]
    assert dates_in_result_order == [50.0, 60.0, 70.0]  # chronological, not insertion order


# ── F6: UTC time helpers ─────────────────────────────────────────────

def test_now_utc_is_timezone_aware():
    now = now_utc()
    assert now.tzinfo is not None


def test_parse_utc_assumes_utc_for_naive_timestamps():
    dt = parse_utc("2026-01-01T12:00:00")
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0

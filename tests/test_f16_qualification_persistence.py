"""
Regression test for F16: qualify_picks() runs live daily but log_qualification()
had zero callers anywhere, so qualification_log stayed empty regardless of what
the qualification engine actually decided. _compute_match_analysis() now accepts
a fixture_id and persists the verdict when one is provided.

_compute_match_analysis() is not connection-injectable (it calls the real
src.db.database.get_db() singleton internally), so this test exercises the real
DB with a clearly test-scoped match_id and cleans up after itself rather than
polluting production data.
"""
import uuid

from src.db.database import get_db
from api.services.match_analysis_service import _compute_match_analysis


def test_qualification_verdict_is_persisted_when_fixture_id_given():
    fixture_id = f"test_f16_{uuid.uuid4().hex}"
    conn = get_db()

    try:
        analysis = _compute_match_analysis(
            "Arsenal", "Chelsea", "Premier League",
            shuffle_tiers=False, fixture_id=fixture_id,
        )

        rows = conn.execute(
            "SELECT market, tier, pick_quality_score FROM qualification_log WHERE match_id = ?",
            (fixture_id,),
        ).fetchall()

        qualification = analysis["qualification"]
        expected_count = len(qualification["qualified_picks"]) + qualification["rejected_count"]

        assert expected_count > 0, "test match produced no qualification verdicts to check"
        assert len(rows) == expected_count

        logged_markets = {r["market"] for r in rows}
        qualified_markets = {p["market"] for p in qualification["qualified_picks"]}
        assert qualified_markets.issubset(logged_markets)
    finally:
        conn.execute("DELETE FROM qualification_log WHERE match_id = ?", (fixture_id,))
        conn.commit()


def test_no_fixture_id_means_no_persistence_and_no_crash():
    """Ad-hoc callers (tests, backtesting snippets) that don't pass a fixture_id
    must keep working exactly as before — persistence is opt-in, not required."""
    conn = get_db()
    before = conn.execute("SELECT COUNT(*) FROM qualification_log").fetchone()[0]

    analysis = _compute_match_analysis("Arsenal", "Chelsea", "Premier League", shuffle_tiers=False)

    after = conn.execute("SELECT COUNT(*) FROM qualification_log").fetchone()[0]
    assert after == before
    assert "qualification" in analysis

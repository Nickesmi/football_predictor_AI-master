"""
Regression tests for the "no forced prediction on blind data" contract.

The two tests asserting the hash-fallback fix (matches_played=0) live in
tests/pending_m2/ until that governed change is applied in M2.
"""

from __future__ import annotations

from src.ml.team_stats_db import get_team_stats


def test_known_hardcoded_team_still_reports_nonzero_matches_played():
    """Sanity check: the fix must not regress known teams with real season priors."""
    stats = get_team_stats("Arsenal", "home", league="Premier League")
    assert stats.matches_played > 0

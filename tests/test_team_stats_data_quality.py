"""
Regression tests for the "no forced prediction on blind data" contract.

Bug: get_team_stats() Priority-4 (hash-based placeholder for a team with
zero real history) used to return the TeamVenueStats dataclass default of
matches_played=20, which fooled FeatureBuilder.compute_data_quality() into
scoring a genuinely blind guess as if it had a near-full season of real
data. That silently defeated the risk_control / performance_gate "NO BET"
safeguards for any team not in the hardcoded league tables and not yet
seen by the live team_state DB (new teams, lower leagues, etc.).
"""

from __future__ import annotations

from src.ml.team_stats_db import get_team_stats
from src.ml.feature_builder import FeatureBuilder, TeamProfile


def test_unknown_team_hash_fallback_reports_zero_matches_played():
    """A team with no hardcoded and no live-DB history must not claim history."""
    stats = get_team_stats("Totally Unknown FC 12345", "home", league="Nonexistent League")
    assert stats.matches_played == 0


def test_unknown_team_hash_fallback_is_penalized_as_low_quality():
    """compute_data_quality must not score a blind hash guess as high quality."""
    home_stats = get_team_stats("Totally Unknown FC 12345", "home", league="Nonexistent League")
    away_stats = get_team_stats("Another Unknown FC 67890", "away", league="Nonexistent League")

    home_profile = TeamProfile(
        team_name="Totally Unknown FC 12345",
        matches_played=home_stats.matches_played,
        avg_scored=home_stats.scored,
        avg_conceded=home_stats.conceded,
        avg_total_goals=home_stats.scored + home_stats.conceded,
        btts_rate=0.5, clean_sheet_rate=0.3, failed_to_score_rate=0.3,
        over_1_5_rate=0.6, over_2_5_rate=0.5, over_0_5_ht_rate=0.6,
        form_last5=home_stats.form_last5, goal_diff=0.0,
    )
    away_profile = TeamProfile(
        team_name="Another Unknown FC 67890",
        matches_played=away_stats.matches_played,
        avg_scored=away_stats.scored,
        avg_conceded=away_stats.conceded,
        avg_total_goals=away_stats.scored + away_stats.conceded,
        btts_rate=0.5, clean_sheet_rate=0.3, failed_to_score_rate=0.3,
        over_1_5_rate=0.6, over_2_5_rate=0.5, over_0_5_ht_rate=0.6,
        form_last5=away_stats.form_last5, goal_diff=0.0,
    )

    dq = FeatureBuilder.compute_data_quality(
        home_profile, away_profile, league_name="Nonexistent League", country=""
    )

    # Two teams with zero real observations must never be scored as
    # trustworthy ("high"/"medium" tiers start at 60+ in audit_engine).
    assert dq < 60.0


def test_known_hardcoded_team_still_reports_nonzero_matches_played():
    """Sanity check: the fix must not regress known teams with real season priors."""
    stats = get_team_stats("Arsenal", "home", league="Premier League")
    assert stats.matches_played > 0

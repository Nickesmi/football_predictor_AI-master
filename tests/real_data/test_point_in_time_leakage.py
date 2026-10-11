"""
Regression tests for src/ml/point_in_time.py — the leakage-safety core of
the Phase-2 real-data pipeline.

Uses small, explicitly hand-crafted match tables (NOT synthetic training
data — this is test fixture data used only to verify code correctness,
the same way any unit test uses small fixtures).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.ml.point_in_time import build_point_in_time_features, assert_no_leakage, compute_elo_before_each_match


def _make_matches(rows: list[tuple]) -> pd.DataFrame:
    """rows: (match_id, date, league, season, home, away, hg, ag)"""
    df = pd.DataFrame(rows, columns=[
        "match_id", "date", "league", "season", "home_team", "away_team", "home_goals", "away_goals",
    ])
    return df.sort_values("date").reset_index(drop=True)


def test_rejects_unsorted_input_instead_of_silently_computing_wrong_features():
    df = _make_matches([
        ("m2", "2024-01-10", "L", "S", "A", "B", 1, 0),
        ("m1", "2024-01-01", "L", "S", "B", "A", 0, 1),
    ])
    # deliberately break the sort the module requires
    shuffled = df.iloc[::-1].reset_index(drop=True)
    with pytest.raises(ValueError, match="chronologically sorted"):
        build_point_in_time_features(shuffled)


def test_first_ever_match_for_both_teams_has_zero_prior_matches():
    df = _make_matches([("m1", "2024-01-01", "L", "S", "A", "B", 2, 1)])
    feats = build_point_in_time_features(df)
    row = feats.iloc[0]
    assert row["home_matches_played_before"] == 0
    assert row["away_matches_played_before"] == 0
    assert row["home_is_cold_start"] and row["away_is_cold_start"]


def test_rolling_scored_average_uses_only_strictly_prior_matches():
    """Team A scores 4, then 0, then plays a third match. The third
    match's home_scored_avg_5 must be exactly mean([4, 0]) = 2.0 — NOT
    influenced by whatever A scores in this (the third) match itself."""
    df = _make_matches([
        ("m1", "2024-01-01", "L", "S", "A", "X", 4, 0),
        ("m2", "2024-01-08", "L", "S", "A", "Y", 0, 0),
        ("m3", "2024-01-15", "L", "S", "A", "Z", 9, 9),  # extreme score to prove it's excluded
    ])
    feats = build_point_in_time_features(df)
    m3 = feats[feats["match_id"] == "m3"].iloc[0]
    assert m3["home_scored_avg_5"] == pytest.approx(2.0)
    assert m3["home_matches_played_before"] == 2


def test_a_matchs_own_result_never_appears_in_its_own_features_elo():
    """Elo entering a match must equal Elo computed only from matches
    strictly before it — verified independently for a 2-match head-to-head
    where the second match's outcome is deliberately extreme."""
    df = _make_matches([
        ("m1", "2024-01-01", "L", "S", "A", "B", 1, 1),   # draw, small elo movement
        ("m2", "2024-01-08", "L", "S", "A", "B", 9, 0),   # blowout — must not affect m1's elo values
    ])
    with_elo = compute_elo_before_each_match(df)
    m1 = with_elo[with_elo["match_id"] == "m1"].iloc[0]
    # Before any match, both teams start at the default 1500 rating.
    assert m1["home_elo_before"] == pytest.approx(1500.0)
    assert m1["away_elo_before"] == pytest.approx(1500.0)

    m2 = with_elo[with_elo["match_id"] == "m2"].iloc[0]
    # m2's pre-match elo must reflect m1's (small, draw) update, not be 1500 anymore.
    assert m2["home_elo_before"] != pytest.approx(1500.0)


def test_future_match_never_leaks_into_an_earlier_matchs_features():
    """Directly construct the forbidden scenario the audit calls out: a
    team's FUTURE match result must not be visible to an earlier
    prediction. We plant an impossible/extreme future result and confirm
    it has zero effect on the earlier match's features."""
    df = _make_matches([
        ("early", "2024-01-01", "L", "S", "A", "B", 1, 0),
        ("later", "2024-06-01", "L", "S", "A", "C", 1, 0),
    ])
    feats_with_normal_future = build_point_in_time_features(df)
    early_normal = feats_with_normal_future[feats_with_normal_future["match_id"] == "early"].iloc[0]

    df_extreme_future = df.copy()
    df_extreme_future.loc[df_extreme_future["match_id"] == "later", ["home_goals", "away_goals"]] = [20, 0]
    feats_with_extreme_future = build_point_in_time_features(df_extreme_future)
    early_extreme = feats_with_extreme_future[feats_with_extreme_future["match_id"] == "early"].iloc[0]

    for col in ["home_elo_before", "home_scored_avg_5", "home_conceded_avg_5", "home_matches_played_before"]:
        assert early_normal[col] == pytest.approx(early_extreme[col]), (
            f"{col} changed when a FUTURE match's score changed — this is data leakage."
        )


def test_assert_no_leakage_self_check_passes_on_larger_synthetic_fixture():
    """Sanity: the leakage self-check utility itself runs cleanly on a
    fixture large enough to have matches with >=30 prior appearances."""
    rng = np.random.default_rng(0)
    teams = [f"Team{i}" for i in range(6)]
    rows = []
    day = 0
    for round_ in range(40):
        rng.shuffle(teams)
        for i in range(0, len(teams), 2):
            day += 1
            rows.append((
                f"m{round_}_{i}", pd.Timestamp("2020-01-01") + pd.Timedelta(days=day),
                "L", "S", teams[i], teams[i + 1],
                int(rng.integers(0, 4)), int(rng.integers(0, 4)),
            ))
    df = pd.DataFrame(rows, columns=["match_id", "date", "league", "season", "home_team", "away_team", "home_goals", "away_goals"])
    df["date"] = df["date"].astype(str)
    df = df.sort_values("date").reset_index(drop=True)

    feats = build_point_in_time_features(df)
    assert_no_leakage(feats, df, min_prior_matches=10)

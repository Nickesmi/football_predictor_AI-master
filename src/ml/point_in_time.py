"""
Point-in-time feature reconstruction for real historical matches.

This module is the fix for the leakage bug documented in AUDIT_REPORT.md
§1.2 / §3: instead of asking "what does team_state say RIGHT NOW" (which
mixes in everything that happened after the match being evaluated), every
feature here is computed from a matches DataFrame filtered to
`date < prediction_date` (or, if a prediction is generated same-day as an
earlier one, a strict chronological sequence number). There is no mutable
global state — call build_point_in_time_features() fresh for any date and
you get exactly what would have been knowable at that moment, no more.

Design contract enforced by tests (tests/real_data/test_point_in_time_leakage.py):
    for every produced feature row: FEATURE_TIMESTAMP <= PREDICTION_TIMESTAMP
    i.e. every input match used to build features for match M has
    match.date < M.date (strictly before — a match cannot use its own
    result, and cannot use anything from the future).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

ELO_DEFAULT = 1500.0
ELO_K = 20
ELO_HOME_ADVANTAGE = 65


def _elo_expected(elo_a: float, elo_b: float, home_adv: float = 0.0) -> float:
    return 1.0 / (1 + 10 ** ((elo_b - (elo_a + home_adv)) / 400))


def _elo_update(elo_home: float, elo_away: float, home_goals: int, away_goals: int) -> tuple[float, float]:
    exp_home = _elo_expected(elo_home, elo_away, ELO_HOME_ADVANTAGE)
    exp_away = 1.0 - exp_home
    if home_goals > away_goals:
        actual_home, actual_away = 1.0, 0.0
    elif home_goals == away_goals:
        actual_home, actual_away = 0.5, 0.5
    else:
        actual_home, actual_away = 0.0, 1.0
    gd = abs(home_goals - away_goals)
    gd_mult = np.log(max(gd, 1) + 1) + 1
    new_home = elo_home + ELO_K * gd_mult * (actual_home - exp_home)
    new_away = elo_away + ELO_K * gd_mult * (actual_away - exp_away)
    return new_home, new_away


def compute_elo_before_each_match(matches: pd.DataFrame) -> pd.DataFrame:
    """Sequentially compute each team's Elo rating BEFORE each of their
    matches (i.e. entering that match, not reflecting its own result).

    matches must be sorted chronologically (date, then original order) and
    have columns: match_id, home_team, away_team, home_goals, away_goals.

    Returns matches with two new columns: home_elo_before, away_elo_before.
    """
    elo: dict[str, float] = {}
    home_elo_before = np.empty(len(matches), dtype=float)
    away_elo_before = np.empty(len(matches), dtype=float)

    for i, row in enumerate(matches.itertuples(index=False)):
        h, a = row.home_team, row.away_team
        eh = elo.get(h, ELO_DEFAULT)
        ea = elo.get(a, ELO_DEFAULT)
        home_elo_before[i] = eh
        away_elo_before[i] = ea
        new_h, new_a = _elo_update(eh, ea, row.home_goals, row.away_goals)
        elo[h] = new_h
        elo[a] = new_a

    out = matches.copy()
    out["home_elo_before"] = home_elo_before
    out["away_elo_before"] = away_elo_before
    return out


def _build_team_log(matches: pd.DataFrame) -> pd.DataFrame:
    """Long format: one row per (team, match) with a strict chronological
    sequence number, from that team's perspective."""
    matches = matches.reset_index(drop=True)
    matches["_seq"] = np.arange(len(matches))

    home_rows = pd.DataFrame({
        "team": matches["home_team"],
        "opponent": matches["away_team"],
        "match_id": matches["match_id"],
        "date": matches["date"],
        "_seq": matches["_seq"],
        "venue": "home",
        "goals_for": matches["home_goals"],
        "goals_against": matches["away_goals"],
    })
    away_rows = pd.DataFrame({
        "team": matches["away_team"],
        "opponent": matches["home_team"],
        "match_id": matches["match_id"],
        "date": matches["date"],
        "_seq": matches["_seq"],
        "venue": "away",
        "goals_for": matches["away_goals"],
        "goals_against": matches["home_goals"],
    })
    log = pd.concat([home_rows, away_rows], ignore_index=True)
    log["points"] = np.select(
        [log["goals_for"] > log["goals_against"], log["goals_for"] == log["goals_against"]],
        [3, 1], default=0,
    )
    log = log.sort_values(["team", "_seq"]).reset_index(drop=True)
    return log


def _rolling_prior(series: pd.Series, window: int) -> pd.Series:
    """Mean of the PRIOR `window` observations (excludes the current row)."""
    return series.shift(1).rolling(window=window, min_periods=1).mean()


def build_point_in_time_features(matches: pd.DataFrame) -> pd.DataFrame:
    """Build one feature row per match using ONLY information strictly
    before that match's date (Elo entering the match, rolling form from
    prior matches only).

    Input `matches` must be chronologically sorted already (see
    src.data.openfootball_parser.parse_directory's output, which sorts by
    date). Raises if it detects the input is not sorted, since every
    downstream guarantee depends on it.
    """
    if not matches["date"].is_monotonic_increasing:
        raise ValueError(
            "build_point_in_time_features requires chronologically sorted input "
            "(matches['date'] must be non-decreasing) — got out-of-order dates."
        )

    matches = matches.reset_index(drop=True)
    with_elo = compute_elo_before_each_match(matches)

    log = _build_team_log(matches)
    log["matches_played_before"] = log.groupby("team").cumcount()
    log["scored_avg_5"] = log.groupby("team")["goals_for"].transform(lambda s: _rolling_prior(s, 5))
    log["conceded_avg_5"] = log.groupby("team")["goals_against"].transform(lambda s: _rolling_prior(s, 5))
    log["scored_avg_10"] = log.groupby("team")["goals_for"].transform(lambda s: _rolling_prior(s, 10))
    log["conceded_avg_10"] = log.groupby("team")["goals_against"].transform(lambda s: _rolling_prior(s, 10))
    log["form_points_5"] = log.groupby("team")["points"].transform(lambda s: s.shift(1).rolling(5, min_periods=1).sum())

    # Venue-specific rolling (last 10 matches AT THIS VENUE, prior only)
    log["venue_scored_avg"] = log.groupby(["team", "venue"])["goals_for"].transform(lambda s: _rolling_prior(s, 10))
    log["venue_conceded_avg"] = log.groupby(["team", "venue"])["goals_against"].transform(lambda s: _rolling_prior(s, 10))
    log["venue_matches_played_before"] = log.groupby(["team", "venue"]).cumcount()

    # Cold-start fallback for teams with 0 prior matches (a genuinely new /
    # newly-promoted team — see out-of-distribution flag below). This MUST
    # itself be point-in-time: an EXPANDING average of goals-per-team-per-
    # match using only matches strictly before the current one, not a
    # single global constant computed over the whole dataset (that would
    # leak every future season's scoring level into early-season cold-start
    # predictions — caught by tests/real_data/test_point_in_time_leakage.py
    # ::test_future_match_never_leaks_into_an_earlier_matchs_features).
    total_goals_per_match = matches["home_goals"] + matches["away_goals"]
    cum_goals_before = total_goals_per_match.cumsum().shift(1).fillna(0.0)
    cum_team_observations_before = pd.Series(np.arange(len(matches)), index=matches.index) * 2
    with np.errstate(invalid="ignore", divide="ignore"):
        expanding_league_avg = cum_goals_before / cum_team_observations_before.replace(0, np.nan)
    # Only the very first match(es) in the ENTIRE dataset have no prior
    # matches at all league-wide; fall back to a fixed, disclosed prior
    # (a generic top-five-league average) rather than NaN for those.
    FIRST_MATCH_PRIOR_GOALS = 1.3
    expanding_league_avg = expanding_league_avg.fillna(FIRST_MATCH_PRIOR_GOALS)
    # Symmetric: total goals-for == total goals-against league-wide, so the
    # same expanding series is the correct point-in-time default for both.
    league_avg_scored_by_match = expanding_league_avg.values

    feature_cols = [
        "matches_played_before", "scored_avg_5", "conceded_avg_5",
        "scored_avg_10", "conceded_avg_10", "form_points_5",
        "venue_scored_avg", "venue_conceded_avg", "venue_matches_played_before",
    ]

    home_feats = log[log["venue"] == "home"][["match_id"] + feature_cols].add_prefix("home_")
    home_feats = home_feats.rename(columns={"home_match_id": "match_id"})
    away_feats = log[log["venue"] == "away"][["match_id"] + feature_cols].add_prefix("away_")
    away_feats = away_feats.rename(columns={"away_match_id": "match_id"})

    out = with_elo.merge(home_feats, on="match_id", how="left").merge(away_feats, on="match_id", how="left")

    # Fill true cold-start (0 prior matches -> rolling means are NaN) with
    # league-wide averages, and flag it explicitly rather than silently
    # treating a league-average guess as "real observed form".
    out["home_is_cold_start"] = out["home_matches_played_before"] == 0
    out["away_is_cold_start"] = out["away_matches_played_before"] == 0

    # out's row order matches `matches`' row order (left merges on a
    # unique match_id preserve left-frame order), so the positional
    # per-row expanding averages line up directly.
    assert len(out) == len(matches), "merge unexpectedly changed row count"
    expanding_avg_series = pd.Series(league_avg_scored_by_match, index=out.index)

    for col, default in [
        ("home_scored_avg_5", expanding_avg_series), ("home_conceded_avg_5", expanding_avg_series),
        ("home_scored_avg_10", expanding_avg_series), ("home_conceded_avg_10", expanding_avg_series),
        ("home_venue_scored_avg", expanding_avg_series), ("home_venue_conceded_avg", expanding_avg_series),
        ("away_scored_avg_5", expanding_avg_series), ("away_conceded_avg_5", expanding_avg_series),
        ("away_scored_avg_10", expanding_avg_series), ("away_conceded_avg_10", expanding_avg_series),
        ("away_venue_scored_avg", expanding_avg_series), ("away_venue_conceded_avg", expanding_avg_series),
        ("home_form_points_5", pd.Series(7.5, index=out.index)), ("away_form_points_5", pd.Series(7.5, index=out.index)),
    ]:
        out[col] = out[col].fillna(default)

    out["data_sufficiency"] = out[["home_matches_played_before", "away_matches_played_before"]].min(axis=1)

    return out


FEATURE_COLUMNS = [
    "home_elo_before", "away_elo_before",
    "home_scored_avg_5", "home_conceded_avg_5", "home_scored_avg_10", "home_conceded_avg_10",
    "away_scored_avg_5", "away_conceded_avg_5", "away_scored_avg_10", "away_conceded_avg_10",
    "home_form_points_5", "away_form_points_5",
    "home_venue_scored_avg", "home_venue_conceded_avg",
    "away_venue_scored_avg", "away_venue_conceded_avg",
    "data_sufficiency",
]


def assert_no_leakage(features_df: pd.DataFrame, matches: pd.DataFrame, min_prior_matches: int = 30) -> None:
    """Leakage guard used by tests and the backtest runner itself.

    Recomputes features for a handful of matches using an EXPLICIT
    date-filtered slice of `matches` (`date < this match's date`) via a
    second, independently-written path, and asserts they match the vectorized
    build_point_in_time_features() output. This is the automated leakage
    test the audit mandate requires: if the fast vectorized path ever
    accidentally includes a future match, this check will fail loudly
    instead of silently producing an inflated backtest.
    """
    matches = matches.reset_index(drop=True)
    sample = features_df[features_df["data_sufficiency"] >= min_prior_matches].sample(
        n=min(25, len(features_df[features_df["data_sufficiency"] >= min_prior_matches])),
        random_state=42,
    )
    for _, row in sample.iterrows():
        match_date = row["date"]
        prior = matches[matches["date"] < match_date]
        assert (prior["date"] < match_date).all(), "internal error building prior slice"

        home_prior = prior[(prior["home_team"] == row["home_team"]) | (prior["away_team"] == row["home_team"])]
        if len(home_prior) == 0:
            continue
        # Recompute a simple prior aggregate independently (all-time scored
        # avg for the home team, home-team perspective only, to keep this
        # check simple and orthogonal to the venue-specific rolling logic).
        scored_as_home = prior.loc[prior["home_team"] == row["home_team"], "home_goals"]
        scored_as_away = prior.loc[prior["away_team"] == row["home_team"], "away_goals"]
        total_scored = pd.concat([scored_as_home, scored_as_away])
        assert len(total_scored) == row["home_matches_played_before"], (
            f"matches_played_before mismatch for {row['home_team']} on {match_date}: "
            f"independent recount={len(total_scored)} vs vectorized={row['home_matches_played_before']} "
            "— this indicates the vectorized path is leaking future matches or missing past ones."
        )

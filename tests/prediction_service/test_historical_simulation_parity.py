"""
Regression test for the same-day-multi-match parity bug caught by
scripts/historical_simulation.py during Phase 3 development (Como 1907 vs
Bologna FC 1909, 2024-09-14): the cold-start expanding-league-average
fallback in src/ml/point_in_time.py used to be computed by ROW POSITION
(tie-broken by incidental file/concat order for same-day matches across
different leagues), while src/prediction_service/feature_engine.py's live
path filters by calendar DATE only (all same-day matches are simultaneous,
none is "before" another). That mismatch showed up as a tiny but real
feature discrepancy for a newly-promoted team's first fixture on a day
with other same-day fixtures already in the table.

Fixed by grouping the expanding average by date instead of row position,
so both paths agree by construction.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.ml.point_in_time import build_point_in_time_features
from src.prediction_service.feature_engine import generate_features


def test_same_day_matches_do_not_create_positional_tie_break_drift():
    """Two OTHER matches share a date with the match under test. A cold-start
    team's fallback features must be identical whether those same-day
    matches are listed before or after it in the input table — only
    STRICTLY EARLIER dates may count as 'prior'."""
    common_rows = [
        # An established team with real history, on an earlier date.
        ("early1", "2024-08-01", "L", "S", "Established A", "Established B", 2, 1),
        ("early2", "2024-08-08", "L", "S", "Established B", "Established A", 0, 0),
    ]
    same_day_others = [
        ("sameday1", "2024-09-14", "L", "S", "Other Team X", "Other Team Y", 3, 1),
        ("sameday2", "2024-09-14", "L", "S", "Other Team Z", "Other Team W", 1, 1),
    ]
    cold_start_match = ("target", "2024-09-14", "L", "S", "Brand New Team", "Established A", 0, 0)

    cols = ["match_id", "date", "league", "season", "home_team", "away_team", "home_goals", "away_goals"]

    # Order A: same-day "others" appear BEFORE the cold-start match in the raw table.
    order_a = pd.DataFrame(common_rows + same_day_others + [cold_start_match], columns=cols)
    order_a = order_a.sort_values("date", kind="stable").reset_index(drop=True)

    # Order B: same-day "others" appear AFTER the cold-start match (reversed
    # relative ordering within the same date).
    order_b = pd.DataFrame(common_rows + [cold_start_match] + same_day_others, columns=cols)
    order_b = order_b.sort_values("date", kind="stable").reset_index(drop=True)

    feats_a = build_point_in_time_features(order_a)
    feats_b = build_point_in_time_features(order_b)

    row_a = feats_a[feats_a["match_id"] == "target"].iloc[0]
    row_b = feats_b[feats_b["match_id"] == "target"].iloc[0]

    for col in ["home_scored_avg_5", "home_conceded_avg_5", "home_venue_scored_avg", "home_venue_conceded_avg"]:
        assert row_a[col] == pytest.approx(row_b[col]), (
            f"{col} differs based on same-day match ordering — this is the exact bug "
            "scripts/historical_simulation.py caught on real data (Como vs Bologna, 2024-09-14)."
        )


def test_live_feature_engine_matches_batch_computation_for_a_cold_start_team_on_a_multi_match_day():
    """Direct production-path regression test: generate_features() for a
    cold-start team on a day with other fixtures must match
    build_point_in_time_features() computed on the same full table."""
    cols = ["match_id", "date", "league", "season", "home_team", "away_team", "home_goals", "away_goals"]
    rows = [
        ("early1", "2024-08-01", "L", "S", "Established A", "Established B", 2, 1),
        ("sameday1", "2024-09-14", "L", "S", "Other Team X", "Other Team Y", 3, 1),
        ("target", "2024-09-14", "L", "S", "Brand New Team", "Established A", 0, 0),
    ]
    history = pd.DataFrame(rows, columns=cols).sort_values("date", kind="stable").reset_index(drop=True)

    live_snapshot = generate_features("Brand New Team", "Established A", "2024-09-14", "L", history, match_id="target")

    reference = build_point_in_time_features(history)
    ref_row = reference[reference["match_id"] == "target"].iloc[0]

    for col in live_snapshot.features:
        assert live_snapshot.features[col] == pytest.approx(float(ref_row[col]), abs=1e-9), col

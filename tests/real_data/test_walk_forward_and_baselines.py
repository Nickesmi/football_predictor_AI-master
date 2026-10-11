"""
Regression tests for train/test chronology (walk-forward folds) and for
baseline models never being influenced by the test split they're scored
against.
"""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.walk_forward_backtest import FOLDS, SEASON_ORDER
from src.ml.baselines import FrequencyBaseline, EloBaseline, add_outcome_columns


def test_walk_forward_folds_never_train_on_the_future():
    """For every fold: every train season must precede val_season, and
    val_season must precede test_season, in real chronological order."""
    for train_seasons, val_season, test_season in FOLDS:
        train_idx_max = max(SEASON_ORDER.index(s) for s in train_seasons)
        val_idx = SEASON_ORDER.index(val_season)
        test_idx = SEASON_ORDER.index(test_season)
        assert train_idx_max < val_idx < test_idx, (
            f"fold violates chronology: train up to {SEASON_ORDER[train_idx_max]}, "
            f"val={val_season}, test={test_season}"
        )


def test_walk_forward_folds_roll_forward_and_final_fold_is_last_season():
    """The last fold's test season must be the dataset's final season —
    this is the frozen holdout and must never be an earlier season."""
    assert FOLDS[-1][2] == SEASON_ORDER[-1]
    # Each fold's test season must be strictly later than the previous fold's.
    test_seasons = [f[2] for f in FOLDS]
    assert test_seasons == sorted(test_seasons, key=SEASON_ORDER.index)
    assert len(set(test_seasons)) == len(test_seasons), "a season must not be used as 'test' twice"


def _toy_matches(n_train=200, n_test=50):
    rows = []
    mid = 0
    for i in range(n_train):
        rows.append((f"tr{mid}", f"2020-{1 + i % 9:02d}-01", "L", "train_season",
                      f"T{i % 8}", f"T{(i + 1) % 8}", (i % 3), (i % 2)))
        mid += 1
    for i in range(n_test):
        rows.append((f"te{mid}", f"2021-{1 + i % 9:02d}-01", "L", "test_season",
                      f"T{i % 8}", f"T{(i + 3) % 8}", (i % 4), (i % 3)))
        mid += 1
    df = pd.DataFrame(rows, columns=["match_id", "date", "league", "season", "home_team", "away_team", "home_goals", "away_goals"])
    return df.sort_values("date").reset_index(drop=True)


def test_frequency_baseline_predictions_are_identical_regardless_of_test_set_contents():
    """Fitting on the same TRAIN split must yield the same predictions no
    matter what TEST data is later passed to .predict() — proves the
    model cannot see the test set during fit()."""
    from src.ml.point_in_time import build_point_in_time_features

    df = _toy_matches()
    feats = build_point_in_time_features(df)
    train = feats[feats["season"] == "train_season"]
    test_a = feats[feats["season"] == "test_season"].iloc[:10]
    test_b = feats[feats["season"] == "test_season"].iloc[10:]

    model = FrequencyBaseline().fit(train)
    preds_a = model.predict(test_a)
    preds_b = model.predict(test_b)
    # Same fitted probabilities regardless of which test rows we predict on.
    assert preds_a[0].p_home == preds_b[0].p_home == model.p_home


def test_elo_baseline_predictions_unaffected_by_mutating_test_split_after_fit():
    """Mutating the TEST split's outcomes AFTER fit() must not change
    EloBaseline's predictions on it — proves predict() re-derives
    probabilities purely from the (leakage-safe, point-in-time) elo
    features passed in, never from the outcome columns."""
    from src.ml.point_in_time import build_point_in_time_features

    df = _toy_matches()
    feats = build_point_in_time_features(df)
    train = feats[feats["season"] == "train_season"].copy()
    test = feats[feats["season"] == "test_season"].copy()

    model = EloBaseline().fit(train)
    preds_before = model.predict(test)

    corrupted_test = add_outcome_columns(test)
    corrupted_test["result"] = "H"          # falsify every outcome
    corrupted_test["btts"] = True
    preds_after = model.predict(corrupted_test)

    assert preds_before[0].p_home == pytest.approx(preds_after[0].p_home)
    assert preds_before[0].p_btts_yes == pytest.approx(preds_after[0].p_btts_yes)


def test_calibrator_is_never_fit_on_the_frozen_test_split():
    """Structural guard: RealDataTrainer.calibrate() must be a distinct
    call from .fit(), and the walk-forward script must invoke fit(train)
    then calibrate(val) then evaluate(test) — never calibrate(test)."""
    import inspect
    from src.ml.real_data_trainer import RealDataTrainer

    source = inspect.getsource(RealDataTrainer)
    assert "def fit(self, train" in source
    assert "def calibrate(self, validation" in source
    assert "def evaluate(self, test" in source

    # And in the orchestration script, calibrate() must be called with the
    # `val` dataframe, never `test`.
    script_source = inspect.getsource(__import__("scripts.walk_forward_backtest", fromlist=["run_fold"]).run_fold)
    assert "trainer.calibrate(val)" in script_source
    assert "trainer.calibrate(test)" not in script_source

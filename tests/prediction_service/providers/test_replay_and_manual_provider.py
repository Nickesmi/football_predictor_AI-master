"""
Regression tests for ReplayProvider (Phase 4 §15's blindness guarantee)
and ManualProvider (Phase 4 §17's validation discipline).
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.prediction_service.providers.replay_provider import ReplayProvider
from src.prediction_service.providers.manual_provider import ManualProvider, ManualDataRejected


@pytest.fixture()
def real_matches():
    return pd.read_csv("data/real_historical/matches.csv")


# ── ReplayProvider ───────────────────────────────────────────────────

def test_replay_fetch_fixtures_never_includes_the_result(real_matches):
    provider = ReplayProvider(real_matches)
    fixtures = provider.fetch_fixtures(as_of="2025-05-25")
    assert len(fixtures) > 0
    for f in fixtures:
        assert f.home_goals is None and f.away_goals is None
        assert f.data_mode.value == "REPLAY"


def test_replay_fetch_result_is_blind_before_the_match_happens(real_matches):
    provider = ReplayProvider(real_matches)
    some_fixture = provider.fetch_fixtures(as_of="2025-05-25")[0]

    result_before = provider.fetch_result(some_fixture.fixture_id, as_of="2025-05-24T00:00:00Z")
    assert result_before is None, "a result must not be revealed before its match's real date"


def test_replay_fetch_result_reveals_the_real_result_once_the_date_has_passed(real_matches):
    provider = ReplayProvider(real_matches)
    some_fixture = provider.fetch_fixtures(as_of="2025-05-25")[0]

    result_after = provider.fetch_result(some_fixture.fixture_id, as_of="2025-05-25T23:59:59Z")
    assert result_after is not None
    assert result_after.status == "finished"
    assert result_after.home_goals is not None and result_after.away_goals is not None


def test_replay_fetch_result_unknown_fixture_id_returns_none(real_matches):
    provider = ReplayProvider(real_matches)
    assert provider.fetch_result("does_not_exist", as_of="2099-01-01") is None


def test_replay_odds_and_lineup_are_honestly_unavailable_not_fabricated(real_matches):
    provider = ReplayProvider(real_matches)
    odds = provider.fetch_odds("anything", as_of="2025-01-01")
    lineup = provider.fetch_lineup("anything", as_of="2025-01-01")
    assert odds.status.value == "UNAVAILABLE"
    assert lineup.status.value == "UNKNOWN"


# ── ManualProvider ───────────────────────────────────────────────────

def _valid_fixture(**overrides):
    base = dict(fixture_id="man1", competition="Test League", season="2025-26",
                home_team="Team A", away_team="Team B", scheduled_kickoff="2025-06-01T15:00:00Z")
    base.update(overrides)
    return base


def test_manual_add_fixture_succeeds_with_complete_data():
    provider = ManualProvider()
    record = provider.add_fixture(_valid_fixture())
    assert record.data_mode.value == "MANUAL"
    assert record.status == "scheduled"


def test_manual_add_fixture_rejects_missing_required_field():
    provider = ManualProvider()
    with pytest.raises(ManualDataRejected, match="missing required"):
        provider.add_fixture(_valid_fixture(home_team=None))


def test_manual_add_fixture_rejects_same_team_twice():
    provider = ManualProvider()
    with pytest.raises(ManualDataRejected, match="home_team == away_team"):
        provider.add_fixture(_valid_fixture(away_team="Team A"))


def test_manual_add_fixture_rejects_invalid_timestamp():
    provider = ManualProvider()
    with pytest.raises(ManualDataRejected, match="not a valid timestamp"):
        provider.add_fixture(_valid_fixture(scheduled_kickoff="not-a-date"))


def test_manual_add_fixture_rejects_implausible_score():
    provider = ManualProvider()
    with pytest.raises(ManualDataRejected, match="implausible score"):
        provider.add_fixture(_valid_fixture(home_goals=99, away_goals=0))


def test_manual_add_fixture_rejects_partial_score():
    provider = ManualProvider()
    with pytest.raises(ManualDataRejected, match="both be present or both be absent"):
        provider.add_fixture(_valid_fixture(home_goals=2))


def test_manual_add_fixture_rejects_duplicate_fixture_id():
    provider = ManualProvider()
    provider.add_fixture(_valid_fixture())
    with pytest.raises(ManualDataRejected, match="duplicate fixture_id"):
        provider.add_fixture(_valid_fixture())


def test_manual_add_fixture_rejects_duplicate_content_under_a_different_id():
    provider = ManualProvider()
    provider.add_fixture(_valid_fixture(fixture_id="man1"))
    with pytest.raises(ManualDataRejected, match="duplicate fixture content"):
        provider.add_fixture(_valid_fixture(fixture_id="man2"))  # same teams+date, different id


def test_manual_fetch_result_respects_the_clock_even_for_manually_entered_scores():
    provider = ManualProvider()
    provider.add_fixture(_valid_fixture(home_goals=2, away_goals=1))
    before = provider.fetch_result("man1", as_of="2025-05-01T00:00:00Z")   # before kickoff
    after = provider.fetch_result("man1", as_of="2025-06-02T00:00:00Z")    # after kickoff
    assert before is None
    assert after is not None and after.home_goals == 2

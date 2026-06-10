"""
Tests for the Pattern Analyzer (Issue #2).

Uses hand-crafted MatchResult fixtures to validate every
pattern computation: BTTS, O/U goals, corners, cards,
W/D/L, team scoring, and first-half events.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.models.match import (
    CardEvent,
    GoalEvent,
    MatchResult,
    MatchStatistics,
    TeamMatchSet,
)
from src.models.patterns import PatternStat, TeamPatternReport
from src.processing.pattern_analyzer import PatternAnalyzer


# ==================================================================
# Test fixtures
# ==================================================================

def _match(
    mid: str,
    home_ft: int, away_ft: int,
    home_ht: int = None, away_ht: int = None,
    corners_h: int = 0, corners_a: int = 0,
    yc_h: int = 0, yc_a: int = 0,
    rc_h: int = 0, rc_a: int = 0,
    goals: list = None,
    cards: list = None,
    home_id: str = "33", away_id: str = "49",
) -> MatchResult:
    """Helper to quickly build a MatchResult with common fields."""
    stats = MatchStatistics(
        corners_home=corners_h, corners_away=corners_a,
        yellow_cards_home=yc_h, yellow_cards_away=yc_a,
        red_cards_home=rc_h, red_cards_away=rc_a,
    )
    return MatchResult(
        match_id=mid,
        match_date=date(2024, 10, int(mid)),
        league_id="39",
        league_name="Premier League",
        season="2024",
        home_team_id=home_id,
        home_team_name="Team A",
        away_team_id=away_id,
        away_team_name="Team B",
        home_score_ft=home_ft,
        away_score_ft=away_ft,
        home_score_ht=home_ht,
        away_score_ht=away_ht,
        goals=goals or [],
        cards=cards or [],
        statistics=stats,
    )


def _home_match_set(matches: list[MatchResult]) -> TeamMatchSet:
    return TeamMatchSet(
        team_id="33", team_name="Team A",
        league_id="39", league_name="Premier League",
        season="2024", context="home",
        matches=matches,
    )


def _away_match_set(matches: list[MatchResult]) -> TeamMatchSet:
    return TeamMatchSet(
        team_id="49", team_name="Team B",
        league_id="39", league_name="Premier League",
        season="2024", context="away",
        matches=matches,
    )


# 5 sample home matches for Team A
SAMPLE_HOME_MATCHES = [
    _match("1", 2, 1, 1, 0, corners_h=5, corners_a=3, yc_h=1, yc_a=2,
           goals=[
               GoalEvent(23, "Player1", is_home=True, half="1st Half"),
               GoalEvent(55, "Player2", is_home=True, half="2nd Half"),
               GoalEvent(78, "Player3", is_home=False, half="2nd Half"),
           ],
           cards=[
               CardEvent(30, "PlayerA", "yellow", is_home=True, half="1st Half"),
               CardEvent(40, "PlayerB", "yellow", is_home=False, half="1st Half"),
               CardEvent(65, "PlayerC", "yellow", is_home=False, half="2nd Half"),
           ]),
    _match("2", 0, 0, 0, 0, corners_h=4, corners_a=6, yc_h=3, yc_a=1),
    _match("3", 3, 2, 2, 1, corners_h=7, corners_a=5, yc_h=2, yc_a=3,
           goals=[
               GoalEvent(10, "Player1", is_home=True, half="1st Half"),
               GoalEvent(25, "Player4", is_home=False, half="1st Half"),
               GoalEvent(35, "Player2", is_home=True, half="1st Half"),
               GoalEvent(60, "Player1", is_home=True, half="2nd Half"),
               GoalEvent(80, "Player5", is_home=False, half="2nd Half"),
           ],
           cards=[
               CardEvent(5, "PlayerD", "yellow", is_home=True, half="1st Half"),
               CardEvent(20, "PlayerE", "yellow", is_home=False, half="1st Half"),
               CardEvent(50, "PlayerF", "yellow", is_home=True, half="2nd Half"),
               CardEvent(70, "PlayerG", "yellow", is_home=False, half="2nd Half"),
               CardEvent(85, "PlayerH", "yellow", is_home=False, half="2nd Half"),
           ]),
    _match("4", 1, 0, 0, 0, corners_h=3, corners_a=2, yc_h=0, yc_a=1,
           goals=[
               GoalEvent(88, "Player2", is_home=True, half="2nd Half"),
           ],
           cards=[
               CardEvent(44, "PlayerI", "yellow", is_home=False, half="1st Half"),
           ]),
    _match("5", 2, 2, 1, 1, corners_h=6, corners_a=4, yc_h=2, yc_a=2,
           goals=[
               GoalEvent(15, "Player1", is_home=True, half="1st Half"),
               GoalEvent(30, "Player6", is_home=False, half="1st Half"),
               GoalEvent(55, "Player3", is_home=True, half="2nd Half"),
               GoalEvent(75, "Player7", is_home=False, half="2nd Half"),
           ],
           cards=[
               CardEvent(10, "PlayerJ", "yellow", is_home=True, half="1st Half"),
               CardEvent(25, "PlayerK", "yellow", is_home=False, half="1st Half"),
               CardEvent(60, "PlayerL", "yellow", is_home=True, half="2nd Half"),
               CardEvent(80, "PlayerM", "yellow", is_home=False, half="2nd Half"),
           ]),
]


# ==================================================================
# PatternStat model tests
# ==================================================================


class TestPatternStat:
    def test_confidence_tiers(self):
        assert PatternStat("A", 90, 100, 90.0).confidence == "Very High"
        assert PatternStat("B", 70, 100, 70.0).confidence == "High"
        assert PatternStat("C", 60, 100, 60.0).confidence == "Medium"
        assert PatternStat("D", 50, 100, 50.0).confidence == "Low"
        assert PatternStat("E", 2, 10, 20.0).confidence == "Very Low"

    def test_repr(self):
        s = PatternStat("BTTS", 99, 100, 99.0)
        assert "BTTS" in repr(s)
        assert "99.0%" in repr(s)
        


# ==================================================================
# Goals pattern tests
# ==================================================================


class TestGoalsPattern:
    def setup_method(self):
        self.analyzer = PatternAnalyzer()
        self.match_set = _home_match_set(SAMPLE_HOME_MATCHES)
        self.report = self.analyzer.analyze(self.match_set)

    def test_btts(self):
        # Matches with BTTS: 1 (2-1), 3 (3-2), 5 (2-2) = 3/5
        # No BTTS: 2 (0-0), 4 (1-0) = 2/5
        assert self.report.goals.btts_yes.count == 3
        assert self.report.goals.btts_yes.percentage == 60.0
        assert self.report.goals.btts_no.count == 2

    def test_over_under_ft(self):
        # Goals: 3, 0, 5, 1, 4
        # O0.5: 4/5, O1.5: 3/5, O2.5: 3/5, O3.5: 2/5, O4.5: 1/5
        assert self.report.goals.over_0_5_ft.count == 4
        assert self.report.goals.over_1_5_ft.count == 3
        assert self.report.goals.over_2_5_ft.count == 3
        assert self.report.goals.over_3_5_ft.count == 2
        assert self.report.goals.over_4_5_ft.count == 1

    def test_under_ft(self):
        # U2.5: 2/5 (matches 2 and 4)
        assert self.report.goals.under_2_5_ft.count == 2
        assert self.report.goals.under_2_5_ft.percentage == 40.0

    def test_over_under_ht(self):
        # HT goals: 1+0=1, 0+0=0, 2+1=3, 0+0=0, 1+1=2
        # O0.5 HT: 3/5, O1.5 HT: 2/5, O2.5 HT: 1/5
        assert self.report.goals.over_0_5_ht.count == 3
        assert self.report.goals.over_1_5_ht.count == 2
        assert self.report.goals.over_2_5_ht.count == 1

    def test_averages(self):
        # FT goals: 3, 0, 5, 1, 4 → avg = 2.6
        assert self.report.goals.avg_goals_ft == 2.6
        # HT goals: 1, 0, 3, 0, 2 → avg = 1.2
        assert self.report.goals.avg_goals_ht == 1.2
        # Home scored (context=home): 2+0+3+1+2 = 8 → avg = 1.6
        assert self.report.goals.avg_goals_scored == 1.6
        # Home conceded: 1+0+2+0+2 = 5 → avg = 1.0
        assert self.report.goals.avg_goals_conceded == 1.0


# ==================================================================
# Results (W/D/L) tests
# ==================================================================


class TestResultPattern:
    def setup_method(self):
        self.analyzer = PatternAnalyzer()
        self.match_set = _home_match_set(SAMPLE_HOME_MATCHES)
        self.report = self.analyzer.analyze(self.match_set)

    def test_wdl(self):
        # Wins (home): match 1 (2-1), 3 (3-2), 4 (1-0) = 3
        # Draws: match 2 (0-0), 5 (2-2) = 2
        # Losses: 0
        assert self.report.results.wins.count == 3
        assert self.report.results.draws.count == 2
        assert self.report.results.losses.count == 0

    def test_ht_results(self):
        # HT: 1-0 (W), 0-0 (D), 2-1 (W), 0-0 (D), 1-1 (D)
        # Wins: 2, Draws: 3, Losses: 0
        assert self.report.results.ht_wins.count == 2
        assert self.report.results.ht_draws.count == 3
        assert self.report.results.ht_losses.count == 0

    def test_ht_ft_distribution(self):
        dist = self.report.results.ht_ft_distribution
        # HT W / FT W: match 1, 3 → 2
        assert "HT W / FT W" in dist
        assert dist["HT W / FT W"].count == 2


# ==================================================================
# Team Scoring tests
# ==================================================================


class TestTeamScoringPattern:
    def setup_method(self):
        self.analyzer = PatternAnalyzer()
        self.match_set = _home_match_set(SAMPLE_HOME_MATCHES)
        self.report = self.analyzer.analyze(self.match_set)

    def test_scored_and_failed(self):
        # Home scored: matches 1,3,4,5 = 4/5
        # Failed to score: match 2 = 1/5
        assert self.report.scoring.scored_in_match.count == 4
        assert self.report.scoring.failed_to_score.count == 1

    def test_clean_sheet(self):
        # Opposition scored 0: match 2 (0-0), match 4 (1-0) = 2
        assert self.report.scoring.clean_sheet.count == 2

    def test_scored_first(self):
        # Match 1: home scored first (min 23) ✓
        # Match 2: no goals (no data)
        # Match 3: home scored first (min 10) ✓
        # Match 4: home scored first (min 88) ✓
        # Match 5: home scored first (min 15) ✓
        # 4 matches with goals, home scored first in all 4
        assert self.report.scoring.scored_first.count == 4
        assert self.report.scoring.scored_first.total == 4

    def test_scored_in_halves(self):
        # Home scored in 1H: match 1 (23'), 3 (10',35'), 5 (15') = 3/5
        assert self.report.scoring.scored_in_1h.count == 3
        # Home scored in 2H: match 1 (55'), 3 (60'), 4 (88'), 5 (55') = 4/5
        assert self.report.scoring.scored_in_2h.count == 4

    def test_conceded_in_halves(self):
        # Away scored in 1H: match 3 (25'), 5 (30') = 2/5
        assert self.report.scoring.conceded_in_1h.count == 2
        # Away scored in 2H: match 1 (78'), 3 (80'), 5 (75') = 3/5
        assert self.report.scoring.conceded_in_2h.count == 3


# ==================================================================
# Corners tests
# ==================================================================


class TestCornersPattern:
    def setup_method(self):
        self.analyzer = PatternAnalyzer()
        self.match_set = _home_match_set(SAMPLE_HOME_MATCHES)
        self.report = self.analyzer.analyze(self.match_set)

    def test_averages(self):
        # Totals: 8, 10, 12, 5, 10 → avg = 9.0
        assert self.report.corners.avg_corners_total == 9.0
        # Team (home): 5, 4, 7, 3, 6 → avg = 5.0
        assert self.report.corners.avg_corners_team == 5.0
        # Opponent: 3, 6, 5, 2, 4 → avg = 4.0
        assert self.report.corners.avg_corners_opponent == 4.0

    def test_over_under_corners(self):
        # Totals: 8, 10, 12, 5, 10
        # O7.5: 4/5 (8,10,12,10), O8.5: 3/5 (10,12,10)
        # O9.5: 2/5 (12,10), O10.5: 1/5 (12)
        assert self.report.corners.over_7_5.count == 4
        assert self.report.corners.over_8_5.count == 3
        assert self.report.corners.over_9_5.count == 3
        assert self.report.corners.over_10_5.count == 1


# ==================================================================
# Cards tests
# ==================================================================


class TestCardsPattern:
    def setup_method(self):
        self.analyzer = PatternAnalyzer()
        self.match_set = _home_match_set(SAMPLE_HOME_MATCHES)
        self.report = self.analyzer.analyze(self.match_set)

    def test_averages(self):
        # Totals from stats: 3, 4, 5, 1, 4 → avg = 3.4
        assert self.report.cards.avg_yellow_total == 3.4
        # Team (home): 1, 3, 2, 0, 2 → avg = 1.6
        assert self.report.cards.avg_yellow_team == 1.6
        # Opponent: 2, 1, 3, 1, 2 → avg = 1.8
        assert self.report.cards.avg_yellow_opponent == 1.8

    def test_over_under_cards(self):
        # Totals: 3, 4, 5, 1, 4
        # O2.5: 4/5 (3,4,5,4)
        # O3.5: 3/5 (4,5,4)
        # O4.5: 1/5 (5)
        assert self.report.cards.over_2_5_cards.count == 4
        assert self.report.cards.over_3_5_cards.count == 3
        assert self.report.cards.over_4_5_cards.count == 1

    def test_cards_in_first_half(self):
        # Matches with 1H cards:
        # Match 1: 2 cards in 1H ✓
        # Match 2: 0 ✗
        # Match 3: 2 cards in 1H ✓
        # Match 4: 1 card in 1H ✓
        # Match 5: 2 cards in 1H ✓
        assert self.report.cards.cards_in_1h.count == 4


# ==================================================================
# First Half Events tests
# ==================================================================


class TestFirstHalfPattern:
    def setup_method(self):
        self.analyzer = PatternAnalyzer()
        self.match_set = _home_match_set(SAMPLE_HOME_MATCHES)
        self.report = self.analyzer.analyze(self.match_set)

    def test_goals_in_1h(self):
        # HT goals > 0: match 1(1), 3(3), 5(2) = 3/5
        assert self.report.first_half.goals_in_1h.count == 3

    def test_both_scored_1h(self):
        # Both scored in HT: match 3 (2-1), 5 (1-1) = 2/5
        assert self.report.first_half.both_scored_1h.count == 2

    def test_ht_results(self):
        # HT home win: match 1 (1-0), 3 (2-1) = 2/5
        # HT draw: match 2 (0-0), 4 (0-0), 5 (1-1) = 3/5
        # HT away win: 0/5
        assert self.report.first_half.ht_home_win.count == 2
        assert self.report.first_half.ht_draw.count == 3
        assert self.report.first_half.ht_away_win.count == 0


# ==================================================================
# Away context tests
# ==================================================================


class TestAwayContext:
    """Ensure patterns are correctly computed for the away team."""

    def setup_method(self):
        self.analyzer = PatternAnalyzer()
        # Reuse same matches but analyze from the away perspective
        self.match_set = _away_match_set(SAMPLE_HOME_MATCHES)
        self.report = self.analyzer.analyze(self.match_set)

    def test_away_wdl(self):
        # From away POV: wins=0, draws=2 (0-0, 2-2), losses=3 (2-1, 3-2, 1-0)
        assert self.report.results.wins.count == 0
        assert self.report.results.draws.count == 2
        assert self.report.results.losses.count == 3

    def test_away_scoring(self):
        # Away team scored: matches 1(1), 3(2), 5(2) = 3/5
        assert self.report.scoring.scored_in_match.count == 3
        # Failed to score: 2/5
        assert self.report.scoring.failed_to_score.count == 2

    def test_away_clean_sheet(self):
        # Home scored 0: match 2 only = 1/5
        assert self.report.scoring.clean_sheet.count == 1

    def test_away_goals_averages(self):
        # Away scored (context=away): 1+0+2+0+2 = 5 → avg = 1.0
        assert self.report.goals.avg_goals_scored == 1.0
        # Away conceded (home scored): 2+0+3+1+2 = 8 → avg = 1.6
        assert self.report.goals.avg_goals_conceded == 1.6


# ==================================================================
# Edge cases
# ==================================================================


class TestEdgeCases:
    def setup_method(self):
        self.analyzer = PatternAnalyzer()

    def test_empty_match_set(self):
        match_set = _home_match_set([])
        report = self.analyzer.analyze(match_set)
        assert report.total_matches == 0

    def test_no_statistics(self):
        """Matches without statistics should fall back to events."""
        m = MatchResult(
            match_id="99",
            match_date=date(2024, 10, 1),
            league_id="39",
            league_name="Premier League",
            season="2024",
            home_team_id="33",
            home_team_name="Team A",
            away_team_id="49",
            away_team_name="Team B",
            home_score_ft=1,
            away_score_ft=0,
            statistics=None,
            cards=[
                CardEvent(20, "P1", "yellow", is_home=True, half="1st Half"),
                CardEvent(70, "P2", "yellow", is_home=False, half="2nd Half"),
            ],
        )
        match_set = _home_match_set([m])
        report = self.analyzer.analyze(match_set)

        # Corners should be empty/zero
        assert report.corners.avg_corners_total == 0.0
        # Cards should still work from events
        assert report.cards.avg_yellow_total == 2.0

    def test_high_confidence_filter(self):
        """get_high_confidence_patterns correctly filters and sorts."""
        match_set = _home_match_set(SAMPLE_HOME_MATCHES)
        report = self.analyzer.analyze(match_set)

        high_80 = report.get_high_confidence_patterns(min_wilson=80.0)
        high_50 = report.get_high_confidence_patterns(min_wilson=50.0)

        # Everything in 80% should also be in 50%
        assert len(high_80) <= len(high_50)
        # All returned must meet threshold
        for p in high_80:
            assert p.percentage >= 80.0
        # Should be sorted descending
        for i in range(len(high_80) - 1):
            assert high_80[i].percentage >= high_80[i + 1].percentage

    def test_single_match(self):
        """Analysis should work correctly with just 1 match."""
        m = _match("1", 2, 1, 1, 0, corners_h=5, corners_a=3, yc_h=1, yc_a=2)
        match_set = _home_match_set([m])
        report = self.analyzer.analyze(match_set)

        assert report.total_matches == 1
        assert report.goals.btts_yes.percentage == 100.0
        assert report.goals.over_2_5_ft.percentage == 100.0
        assert report.results.wins.percentage == 100.0

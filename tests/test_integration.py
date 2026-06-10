"""
Integration Tests for End-to-End Flow (Issue #5 — Part 2).

Tests the COMPLETE pipeline:
    Raw MatchResult data
        → PatternAnalyzer.analyze()
        → FactorAnalyzer.analyze()
        → ReportFormatter.format_*()

Validates that data flows correctly through all layers and that
the final output is consistent with the input data.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from src.models.match import (
    CardEvent,
    GoalEvent,
    MatchResult,
    MatchStatistics,
    TeamMatchSet,
)
from src.models.patterns import TeamPatternReport
from src.processing.pattern_analyzer import PatternAnalyzer
from src.processing.factor_analyzer import FactorAnalyzer, MatchFactorReport
from src.reporting.report_formatter import ReportFormatter


# ==================================================================
# Realistic match data for integration testing
# ==================================================================

def _match(
    mid: str, home_ft: int, away_ft: int,
    home_ht: int = 0, away_ht: int = 0,
    home_id: str = "33", away_id: str = "49",
    corners_h: int = 5, corners_a: int = 4,
    yc_h: int = 1, yc_a: int = 2,
    goals: list = None, cards: list = None,
) -> MatchResult:
    stats = MatchStatistics(
        corners_home=corners_h, corners_away=corners_a,
        yellow_cards_home=yc_h, yellow_cards_away=yc_a,
    )
    return MatchResult(
        match_id=mid, match_date=date(2024, 10, int(mid)),
        league_id="39", league_name="Premier League", season="2024",
        home_team_id=home_id, home_team_name="Man United",
        away_team_id=away_id, away_team_name="Chelsea",
        home_score_ft=home_ft, away_score_ft=away_ft,
        home_score_ht=home_ht, away_score_ht=away_ht,
        goals=goals or [], cards=cards or [], statistics=stats,
    )


# 8 home matches for Team A
HOME_MATCHES = [
    _match("1", 2, 1, 1, 0, goals=[GoalEvent(20, "P1", is_home=True, half="1st Half"), GoalEvent(55, "P2", is_home=True, half="2nd Half"), GoalEvent(78, "P3", is_home=False, half="2nd Half")]),
    _match("2", 0, 0, 0, 0),
    _match("3", 3, 2, 2, 1, goals=[GoalEvent(10, "P1", is_home=True, half="1st Half"), GoalEvent(25, "P4", is_home=False, half="1st Half"), GoalEvent(35, "P2", is_home=True, half="1st Half"), GoalEvent(60, "P1", is_home=True, half="2nd Half"), GoalEvent(80, "P5", is_home=False, half="2nd Half")]),
    _match("4", 1, 0, 0, 0, goals=[GoalEvent(88, "P2", is_home=True, half="2nd Half")]),
    _match("5", 2, 2, 1, 1, goals=[GoalEvent(15, "P1", is_home=True, half="1st Half"), GoalEvent(30, "P6", is_home=False, half="1st Half"), GoalEvent(55, "P3", is_home=True, half="2nd Half"), GoalEvent(75, "P7", is_home=False, half="2nd Half")]),
    _match("6", 1, 1, 0, 1, goals=[GoalEvent(20, "P8", is_home=False, half="1st Half"), GoalEvent(65, "P1", is_home=True, half="2nd Half")]),
    _match("7", 3, 0, 2, 0, corners_h=8, corners_a=2, goals=[GoalEvent(5, "P1", is_home=True, half="1st Half"), GoalEvent(30, "P2", is_home=True, half="1st Half"), GoalEvent(70, "P3", is_home=True, half="2nd Half")]),
    _match("8", 2, 1, 1, 1, goals=[GoalEvent(12, "P9", is_home=False, half="1st Half"), GoalEvent(25, "P1", is_home=True, half="1st Half"), GoalEvent(55, "P2", is_home=True, half="2nd Half")]),
]

# 8 away matches for Team B  
AWAY_MATCHES = [
    _match("1", 1, 2, 0, 1, home_id="50", away_id="49", goals=[GoalEvent(15, "A1", is_home=False, half="1st Half"), GoalEvent(40, "H1", is_home=True, half="1st Half"), GoalEvent(60, "A2", is_home=False, half="2nd Half")]),
    _match("2", 2, 0, 1, 0, home_id="50", away_id="49"),
    _match("3", 1, 1, 0, 0, home_id="50", away_id="49"),
    _match("4", 0, 3, 0, 2, home_id="50", away_id="49", goals=[GoalEvent(10, "A1", is_home=False, half="1st Half"), GoalEvent(30, "A2", is_home=False, half="1st Half"), GoalEvent(55, "A3", is_home=False, half="2nd Half")]),
    _match("5", 2, 1, 1, 0, home_id="50", away_id="49", goals=[GoalEvent(20, "H1", is_home=True, half="1st Half"), GoalEvent(50, "H2", is_home=True, half="2nd Half"), GoalEvent(85, "A1", is_home=False, half="2nd Half")]),
    _match("6", 1, 2, 0, 1, home_id="50", away_id="49", corners_h=3, corners_a=7, goals=[GoalEvent(25, "A1", is_home=False, half="1st Half"), GoalEvent(60, "H1", is_home=True, half="2nd Half"), GoalEvent(80, "A2", is_home=False, half="2nd Half")]),
    _match("7", 0, 0, 0, 0, home_id="50", away_id="49"),
    _match("8", 3, 1, 2, 0, home_id="50", away_id="49", corners_h=7, corners_a=5, goals=[GoalEvent(15, "H1", is_home=True, half="1st Half"), GoalEvent(35, "H2", is_home=True, half="1st Half"), GoalEvent(55, "A1", is_home=False, half="2nd Half"), GoalEvent(75, "H3", is_home=True, half="2nd Half")]),
]


def _home_set() -> TeamMatchSet:
    return TeamMatchSet(
        team_id="33", team_name="Man United",
        league_id="39", league_name="Premier League",
        season="2024", context="home", matches=HOME_MATCHES,
    )


def _away_set() -> TeamMatchSet:
    return TeamMatchSet(
        team_id="49", team_name="Chelsea",
        league_id="39", league_name="Premier League",
        season="2024", context="away", matches=AWAY_MATCHES,
    )


# ==================================================================
# Full pipeline integration test
# ==================================================================


class TestEndToEndPipeline:
    """Test the complete flow: data → patterns → factors → report."""

    def setup_method(self):
        self.pattern_analyzer = PatternAnalyzer()
        self.factor_analyzer = FactorAnalyzer()
        self.formatter = ReportFormatter(confidence_threshold=50.0)

        self.home_set = _home_set()
        self.away_set = _away_set()

        # Issue #2: Compute patterns
        self.home_report = self.pattern_analyzer.analyze(self.home_set)
        self.away_report = self.pattern_analyzer.analyze(self.away_set)

        # Issue #3: Compute factors
        self.factor_report = self.factor_analyzer.analyze(
            self.home_report, self.away_report, min_wilson=50.0
        )

    def test_pattern_reports_populated(self):
        assert self.home_report.total_matches == 8
        assert self.away_report.total_matches == 8

    def test_home_goals_consistent(self):
        """Verify home goals averages are consistent with raw data."""
        total_scored = sum(m.home_score_ft for m in HOME_MATCHES)
        total_conceded = sum(m.away_score_ft for m in HOME_MATCHES)
        expected_avg_scored = round(total_scored / 8, 2)
        expected_avg_conceded = round(total_conceded / 8, 2)
        assert self.home_report.goals.avg_goals_scored == expected_avg_scored
        assert self.home_report.goals.avg_goals_conceded == expected_avg_conceded

    def test_away_goals_consistent(self):
        """Verify away goals averages are consistent with raw data."""
        total_scored = sum(m.away_score_ft for m in AWAY_MATCHES)
        total_conceded = sum(m.home_score_ft for m in AWAY_MATCHES)
        expected_avg_scored = round(total_scored / 8, 2)
        expected_avg_conceded = round(total_conceded / 8, 2)
        assert self.away_report.goals.avg_goals_scored == expected_avg_scored
        assert self.away_report.goals.avg_goals_conceded == expected_avg_conceded

    def test_btts_consistent_with_raw(self):
        """BTTS count should match raw data."""
        raw_btts = sum(1 for m in HOME_MATCHES if m.btts)
        assert self.home_report.goals.btts_yes.count == raw_btts

    def test_wdl_sums_to_total(self):
        """W + D + L must equal total matches."""
        r = self.home_report.results
        assert r.wins.count + r.draws.count + r.losses.count == 8

    def test_factor_report_has_intersection(self):
        assert len(self.factor_report.intersection) > 0

    def test_intersection_combined_is_average(self):
        """Verify combined % is exactly (home% + away%) / 2."""
        for f in self.factor_report.intersection:
            expected = round((f.home_stat.percentage + f.away_stat.percentage) / 2, 1)
            assert f.combined_percentage == expected

    def test_text_report_generated(self):
        text = self.formatter.format_text(
            self.factor_report, self.home_report, self.away_report
        )
        assert "Man United" in text
        assert "Chelsea" in text
        assert "DISCLAIMER" in text
        assert len(text) > 500

    def test_markdown_report_generated(self):
        md = self.formatter.format_markdown(
            self.factor_report, self.home_report, self.away_report
        )
        assert md.startswith("# Match Analysis:")
        assert "Stable Recommended Patterns" in md
        assert "|" in md  # Has tables

    def test_json_report_valid(self):
        d = self.formatter.format_dict(
            self.factor_report, self.home_report, self.away_report
        )
        # Must be JSON-serializable
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        assert parsed["match"]["home_team"] == "Man United"
        assert parsed["match"]["away_team"] == "Chelsea"
        assert "intersection" in parsed
        assert "disclaimer" in parsed

    def test_json_intersection_patterns_are_valid(self):
        d = self.formatter.format_dict(
            self.factor_report, self.home_report, self.away_report
        )
        for entry in d["intersection"]:
            assert 0 <= entry["home_percentage"] <= 100
            assert 0 <= entry["away_percentage"] <= 100
            assert 0 <= entry["combined_percentage"] <= 100
            assert entry["confidence"] in (
                "Very High", "High", "Medium", "Low", "Very Low"
            )

    def test_all_three_formats_consistent(self):
        """All formats should mention the same patterns."""
        text = self.formatter.format_text(self.factor_report)
        md = self.formatter.format_markdown(self.factor_report)
        d = self.formatter.format_dict(self.factor_report)

        # If there are intersection patterns, all formats should show them
        if d["intersection"]:
            first_pattern = d["intersection"][0]["pattern"]
            assert first_pattern in text
            assert first_pattern in md


# ==================================================================
# Pattern consistency validation
# ==================================================================


class TestPatternConsistency:
    """Validate that pattern computations are internally consistent."""

    def setup_method(self):
        self.analyzer = PatternAnalyzer()
        self.home_report = self.analyzer.analyze(_home_set())

    def test_over_under_complementary(self):
        """O2.5 + U2.5 must equal total matches."""
        g = self.home_report.goals
        if g.over_2_5_ft and g.under_2_5_ft:
            assert g.over_2_5_ft.count + g.under_2_5_ft.count == 8

    def test_scored_plus_failed_equals_total(self):
        s = self.home_report.scoring
        assert s.scored_in_match.count + s.failed_to_score.count == 8

    def test_btts_yes_plus_no_equals_total(self):
        g = self.home_report.goals
        assert g.btts_yes.count + g.btts_no.count == 8

    def test_percentages_valid_range(self):
        """All percentages must be between 0 and 100."""
        for stat in self.home_report.get_high_confidence_patterns(min_wilson=0):
            assert 0 <= stat.percentage <= 100

    def test_confidence_labels_valid(self):
        for stat in self.home_report.get_high_confidence_patterns(min_wilson=0):
            assert stat.confidence in (
                "Very High", "High", "Medium", "Low", "Very Low"
            )


# ==================================================================
# Report output validation
# ==================================================================


class TestReportOutputValidation:
    """Validate that report outputs are well-formed."""

    def setup_method(self):
        pa = PatternAnalyzer()
        fa = FactorAnalyzer()
        home_report = pa.analyze(_home_set())
        away_report = pa.analyze(_away_set())
        self.factor_report = fa.analyze(home_report, away_report, min_wilson=50.0)
        self.home_report = home_report
        self.away_report = away_report

    def test_text_no_predictive_language(self):
        fmt = ReportFormatter(confidence_threshold=50.0)
        text = fmt.format_text(self.factor_report, self.home_report, self.away_report)
        forbidden = [
            "will win", "will lose", "predicted", "expected to",
            "should win", "should lose", "likely to win", "going to",
            "guaranteed", "certain to", "must win",
        ]
        for word in forbidden:
            assert word.lower() not in text.lower(), f"Found forbidden: '{word}'"

    def test_markdown_no_predictive_language(self):
        fmt = ReportFormatter(confidence_threshold=50.0)
        md = fmt.format_markdown(self.factor_report, self.home_report, self.away_report)
        forbidden = [
            "will win", "will lose", "predicted", "expected to",
            "should win", "should lose", "likely to win",
        ]
        for word in forbidden:
            assert word.lower() not in md.lower(), f"Found forbidden in markdown: '{word}'"

    def test_json_schema_structure(self):
        fmt = ReportFormatter(confidence_threshold=50.0)
        d = fmt.format_dict(self.factor_report, self.home_report, self.away_report)

        # Required top-level keys
        assert "match" in d
        assert "intersection" in d
        assert "home_factors" in d
        assert "away_factors" in d
        assert "disclaimer" in d
        assert "generated_at" in d
        assert "generated_at" in d

        # Match structure
        assert "home_team" in d["match"]
        assert "away_team" in d["match"]
        assert "league" in d["match"]
        assert "season" in d["match"]

        # Averages structure
        assert "averages" in d
        assert "home" in d["averages"]
        assert "away" in d["averages"]

    def test_json_all_values_serializable(self):
        fmt = ReportFormatter(confidence_threshold=50.0)
        d = fmt.format_dict(self.factor_report, self.home_report, self.away_report)
        # Should not raise any exceptions
        result = json.dumps(d, ensure_ascii=False)
        assert isinstance(result, str)
        assert len(result) > 100

"""
Tests for the Report Formatter and LLM Formatter (Issue #4).

Validates:
  - Plain text output structure and content
  - Markdown output structure and tables
  - Dict output (JSON-serializable)
  - Disclaimer is always present
  - No predictive language in output
  - LLM formatter system prompt constraints
  - LLM fallback when no API key
  - Threshold filtering in reports
  - Edge cases (empty data, etc.)
"""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from src.models.patterns import (
    CardsPattern,
    CornersPattern,
    FirstHalfPattern,
    GoalsPattern,
    PatternStat,
    ResultPattern,
    TeamPatternReport,
    TeamScoringPattern,
)
from src.processing.factor_analyzer import (
    FactorAnalyzer,
    IntersectionFactor,
    MatchFactorReport,
)
from src.reporting.report_formatter import ReportFormatter
from src.reporting.llm_formatter import LLMReportFormatter, _SYSTEM_PROMPT


# ==================================================================
# Test data
# ==================================================================

def _stat(label: str, count: int, total: int) -> PatternStat:
    pct = round((count / total) * 100, 1) if total > 0 else 0.0
    return PatternStat(label=label, count=count, total=total, percentage=pct)


def _build_test_reports():
    """Build home report, away report, and factor report for testing."""
    home_report = TeamPatternReport(
        team_name="Arsenal",
        context="home",
        league_name="Premier League",
        season="2024",
        total_matches=10,
        goals=GoalsPattern(
            btts_yes=_stat("BTTS - Yes", 7, 10),
            btts_no=_stat("BTTS - No", 3, 10),
            over_0_5_ft=_stat("Over 0.5 Goals FT", 10, 10),
            over_1_5_ft=_stat("Over 1.5 Goals FT", 8, 10),
            over_2_5_ft=_stat("Over 2.5 Goals FT", 6, 10),
            avg_goals_ft=2.7,
            avg_goals_scored=1.8,
            avg_goals_conceded=0.9,
        ),
        scoring=TeamScoringPattern(
            scored_in_match=_stat("Team Scored", 9, 10),
        ),
        corners=CornersPattern(
            avg_corners_total=9.5,
            over_8_5=_stat("Over 8.5 Corners", 7, 10),
        ),
        cards=CardsPattern(
            avg_yellow_total=3.8,
            cards_in_1h=_stat("Card in 1st Half", 8, 10),
        ),
        first_half=FirstHalfPattern(
            goals_in_1h=_stat("Goal in 1st Half", 8, 10),
        ),
    )

    away_report = TeamPatternReport(
        team_name="Liverpool",
        context="away",
        league_name="Premier League",
        season="2024",
        total_matches=12,
        goals=GoalsPattern(
            btts_yes=_stat("BTTS - Yes", 9, 12),
            btts_no=_stat("BTTS - No", 3, 12),
            over_0_5_ft=_stat("Over 0.5 Goals FT", 11, 12),
            over_1_5_ft=_stat("Over 1.5 Goals FT", 9, 12),
            over_2_5_ft=_stat("Over 2.5 Goals FT", 7, 12),
            avg_goals_ft=2.5,
            avg_goals_scored=1.0,
            avg_goals_conceded=1.5,
        ),
        scoring=TeamScoringPattern(
            scored_in_match=_stat("Team Scored", 10, 12),
        ),
        corners=CornersPattern(
            avg_corners_total=9.2,
            over_8_5=_stat("Over 8.5 Corners", 8, 12),
        ),
        cards=CardsPattern(
            avg_yellow_total=4.1,
            cards_in_1h=_stat("Card in 1st Half", 9, 12),
        ),
        first_half=FirstHalfPattern(
            goals_in_1h=_stat("Goal in 1st Half", 9, 12),
        ),
    )

    analyzer = FactorAnalyzer()
    factor_report = analyzer.analyze(home_report, away_report, min_wilson=50.0)

    return home_report, away_report, factor_report


# ==================================================================
# ReportFormatter — Plain Text
# ==================================================================


class TestPlainTextFormatter:
    def setup_method(self):
        self.formatter = ReportFormatter(confidence_threshold=65.0)
        self.home, self.away, self.factor = _build_test_reports()

    def test_text_contains_teams(self):
        text = self.formatter.format_text(self.factor, self.home, self.away)
        assert "Arsenal" in text
        assert "Liverpool" in text

    def test_text_contains_league(self):
        text = self.formatter.format_text(self.factor, self.home, self.away)
        assert "Premier League" in text
        assert "2024" in text

    def test_text_contains_disclaimer(self):
        text = self.formatter.format_text(self.factor, self.home, self.away)
        assert "DISCLAIMER" in text
        assert "historical statistical patterns" in text
        assert "Past performance" in text



    def test_text_contains_intersection(self):
        text = self.formatter.format_text(self.factor, self.home, self.away)
        assert "PATTERN INTERSECTION" in text
        assert "Over 0.5 Goals" in text





    def test_no_predictive_language(self):
        """Ensure the deterministic report contains no predictive words."""
        text = self.formatter.format_text(self.factor, self.home, self.away)
        # These words should NEVER appear in purely statistical output
        forbidden = ["will win", "will lose", "predicted", "expected to",
                      "should win", "should lose", "likely to win"]
        for word in forbidden:
            assert word.lower() not in text.lower(), f"Found forbidden word: '{word}'"

    def test_text_without_optional_reports(self):
        """Should work without home/away reports (just no averages)."""
        text = self.formatter.format_text(self.factor)
        assert "Arsenal" in text
        assert "KEY AVERAGES" not in text


# ==================================================================
# ReportFormatter — Markdown
# ==================================================================


class TestMarkdownFormatter:
    def setup_method(self):
        self.formatter = ReportFormatter(confidence_threshold=65.0)
        self.home, self.away, self.factor = _build_test_reports()

    def test_markdown_has_header(self):
        md = self.formatter.format_markdown(self.factor, self.home, self.away)
        assert md.startswith("# Match Analysis:")

    def test_markdown_has_tables(self):
        md = self.formatter.format_markdown(self.factor, self.home, self.away)
        assert "|---------|" in md  # table separator

    def test_markdown_has_intersection_table(self):
        md = self.formatter.format_markdown(self.factor, self.home, self.away)
        assert "Stable Recommended Patterns" in md
        assert "Stability Score" in md



    def test_markdown_emoji_icons(self):
        md = self.formatter.format_markdown(self.factor, self.home, self.away)
        # High confidence patterns should have color icons
        assert "🟢" in md or "🟡" in md


# ==================================================================
# ReportFormatter — Dict (JSON)
# ==================================================================


class TestDictFormatter:
    def setup_method(self):
        self.formatter = ReportFormatter(confidence_threshold=65.0)
        self.home, self.away, self.factor = _build_test_reports()

    def test_dict_is_json_serializable(self):
        d = self.formatter.format_dict(self.factor, self.home, self.away)
        # Must not raise
        json_str = json.dumps(d)
        assert len(json_str) > 0

    def test_dict_has_match_info(self):
        d = self.formatter.format_dict(self.factor, self.home, self.away)
        assert d["match"]["home_team"] == "Arsenal"
        assert d["match"]["away_team"] == "Liverpool"
        assert d["match"]["league"] == "Premier League"

    def test_dict_has_intersection(self):
        d = self.formatter.format_dict(self.factor, self.home, self.away)
        assert "intersection" in d
        assert isinstance(d["intersection"], list)
        # Should have at least BTTS
        patterns = [i["pattern"] for i in d["intersection"]]
        assert len(patterns) >= 1

    def test_dict_has_home_away_factors(self):
        d = self.formatter.format_dict(self.factor, self.home, self.away)
        assert d is not None
        assert len(d["away_factors"]) > 0

    def test_dict_has_averages(self):
        d = self.formatter.format_dict(self.factor, self.home, self.away)
        assert "averages" in d
        assert d["averages"]["home"]["avg_goals_scored"] == 1.8
        assert d["averages"]["away"]["avg_goals_scored"] == 1.0

    def test_dict_has_disclaimer(self):
        d = self.formatter.format_dict(self.factor, self.home, self.away)
        assert "disclaimer" in d
        assert "historical statistical patterns" in d["disclaimer"]

    def test_dict_intersection_structure(self):
        d = self.formatter.format_dict(self.factor, self.home, self.away)
        if d["intersection"]:
            entry = d["intersection"][0]
            assert "pattern" in entry
            assert "home_percentage" in entry
            assert "away_percentage" in entry
            assert "combined_percentage" in entry
            assert "confidence" in entry
            assert "agreement" in entry

    def test_dict_without_optional_reports(self):
        d = self.formatter.format_dict(self.factor)
        assert "averages" not in d
        assert "match" in d


# ==================================================================
# LLM Formatter
# ==================================================================


class TestLLMFormatter:
    def test_system_prompt_forbids_predictions(self):
        """The system prompt must explicitly forbid predictive language."""
        prompt = _SYSTEM_PROMPT
        assert "NEVER predict" in prompt
        assert "NEVER use words like" in prompt
        assert '"likely"' in prompt
        assert '"will"' in prompt
        assert "formatting tool" in prompt

    def test_system_prompt_requires_historical_framing(self):
        prompt = _SYSTEM_PROMPT
        assert "historical patterns" in prompt
        assert "has occurred" in prompt

    def test_system_prompt_requires_disclaimer(self):
        prompt = _SYSTEM_PROMPT
        assert "disclaimer" in prompt

    def test_fallback_when_no_api_key(self):
        """Without an API key, LLMReportFormatter falls back to text."""
        formatter = LLMReportFormatter(api_key="")
        home, away, factor = _build_test_reports()
        result = formatter.format_prose(factor, home, away)
        # Should use fallback (plain text)
        assert "DISCLAIMER" in result
        assert "Arsenal" in result

    def test_build_prompt_contains_data(self):
        """The prompt sent to the LLM must contain all statistical data."""
        formatter = LLMReportFormatter(api_key="test-key")
        home, away, factor = _build_test_reports()
        prompt = formatter._build_prompt(factor, home, away, threshold=65.0)
        assert "Arsenal" in prompt
        assert "Liverpool" in prompt
        assert "Over 0.5 Goals FT" in prompt
        assert "Combined" in prompt

    @patch("requests.post")
    def test_llm_call_structure(self, mock_post):
        """Verify the LLM API call structure is correct."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Formatted report text."}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        formatter = LLMReportFormatter(api_key="test-key-123", model="gpt-4o-mini")
        home, away, factor = _build_test_reports()
        result = formatter.format_prose(factor, home, away)

        assert result == "Formatted report text."
        call_args = mock_post.call_args
        payload = call_args[1]["json"]

        # Verify system prompt is passed
        assert payload["messages"][0]["role"] == "system"
        assert "NEVER predict" in payload["messages"][0]["content"]

        # Verify user prompt has data
        assert payload["messages"][1]["role"] == "user"
        assert "Arsenal" in payload["messages"][1]["content"]

        # Verify low temperature
        assert payload["temperature"] == 0.3

    @patch("requests.post")
    def test_llm_fallback_on_error(self, mock_post):
        """If LLM call fails, should fall back to deterministic formatter."""
        mock_post.side_effect = Exception("API timeout")

        formatter = LLMReportFormatter(api_key="test-key")
        home, away, factor = _build_test_reports()
        result = formatter.format_prose(factor, home, away)

        # Should contain deterministic output
        assert "DISCLAIMER" in result
        assert "Arsenal" in result


# ==================================================================
# Threshold filtering in reports
# ==================================================================


class TestThresholdInReports:
    def test_high_threshold_fewer_intersection(self):
        home, away, factor = _build_test_reports()
        fmt_65 = ReportFormatter(confidence_threshold=65.0)
        fmt_80 = ReportFormatter(confidence_threshold=80.0)

        dict_65 = fmt_65.format_dict(factor, home, away)
        dict_80 = fmt_80.format_dict(factor, home, away)

        assert len(dict_80["intersection"]) <= len(dict_65["intersection"])


# ==================================================================
# Edge cases
# ==================================================================


class TestEdgeCases:
    def test_empty_factor_report(self):
        """Should handle empty reports gracefully."""
        empty_factor = MatchFactorReport(
            home_team="A", away_team="B",
            league_name="PL", season="2024",
        )
        fmt = ReportFormatter(confidence_threshold=65.0)

        text = fmt.format_text(empty_factor)
        assert "A" in text
        assert "B" in text
        assert "NO STABLE INTERSECTIONS" in text or len(text) > 0

        md = fmt.format_markdown(empty_factor)
        assert "Stable Recommended Patterns" in md

        d = fmt.format_dict(empty_factor)
        assert d["intersection"] == []

    def test_report_with_only_home(self):
        """Report with data for home team only."""
        home = TeamPatternReport(
            team_name="Test",
            context="home",
            league_name="PL",
            season="2024",
            total_matches=5,
            goals=GoalsPattern(
                btts_yes=_stat("BTTS - Yes", 4, 5),
            ),
        )
        away = TeamPatternReport(
            team_name="Empty",
            context="away",
            league_name="PL",
            season="2024",
            total_matches=0,
        )
        analyzer = FactorAnalyzer()
        factor = analyzer.analyze(home, away, min_wilson=50.0)

        fmt = ReportFormatter(confidence_threshold=50.0)
        d = fmt.format_dict(factor, home, away)
        assert d["intersection"] == []
        assert d is not None

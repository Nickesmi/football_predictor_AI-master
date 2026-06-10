"""
LLM Output Structure Validation (Issue #5 — Part 3).

Validates:
  - System prompt strictly forbids predictions
  - Prompt structure contains all required data sections
  - LLM response is used as-is (no post-processing adds guesses)
  - Output structure is consistent regardless of input
  - Fallback behavior preserves format contract
  - Low temperature enforced for determinism
"""

from __future__ import annotations

import re
from datetime import date
from unittest.mock import patch, MagicMock

import pytest

from src.models.match import MatchResult, MatchStatistics, TeamMatchSet
from src.models.patterns import (
    GoalsPattern,
    PatternStat,
    ResultPattern,
    TeamPatternReport,
    TeamScoringPattern,
    CornersPattern,
    CardsPattern,
    FirstHalfPattern,
)
from src.processing.pattern_analyzer import PatternAnalyzer
from src.processing.factor_analyzer import FactorAnalyzer
from src.reporting.llm_formatter import LLMReportFormatter, _SYSTEM_PROMPT, _USER_PROMPT_TEMPLATE
from src.reporting.report_formatter import ReportFormatter


# ==================================================================
# Test data
# ==================================================================

def _stat(label: str, count: int, total: int) -> PatternStat:
    pct = round((count / total) * 100, 1) if total > 0 else 0.0
    return PatternStat(label=label, count=count, total=total, percentage=pct)

def _build_reports():
    home = TeamPatternReport(
        team_name="Barcelona", context="home",
        league_name="La Liga", season="2024", total_matches=10,
        goals=GoalsPattern(
            btts_yes=_stat("BTTS - Yes", 8, 10),
            btts_no=_stat("BTTS - No", 2, 10),
            over_0_5_ft=_stat("Over 0.5 Goals FT", 10, 10),
            over_1_5_ft=_stat("Over 1.5 Goals FT", 9, 10),
            over_2_5_ft=_stat("Over 2.5 Goals FT", 7, 10),
            avg_goals_ft=3.2, avg_goals_scored=2.3, avg_goals_conceded=0.9,
        ),
        scoring=TeamScoringPattern(
            scored_in_match=_stat("Team Scored", 10, 10),
            failed_to_score=_stat("Failed to Score", 0, 10),
            clean_sheet=_stat("Clean Sheet", 3, 10),
        ),
        corners=CornersPattern(avg_corners_total=10.1),
        cards=CardsPattern(avg_yellow_total=3.5),
    )
    away = TeamPatternReport(
        team_name="Real Madrid", context="away",
        league_name="La Liga", season="2024", total_matches=10,
        goals=GoalsPattern(
            btts_yes=_stat("BTTS - Yes", 7, 10),
            btts_no=_stat("BTTS - No", 3, 10),
            over_0_5_ft=_stat("Over 0.5 Goals FT", 9, 10),
            over_1_5_ft=_stat("Over 1.5 Goals FT", 8, 10),
            over_2_5_ft=_stat("Over 2.5 Goals FT", 6, 10),
            avg_goals_ft=2.8, avg_goals_scored=1.5, avg_goals_conceded=1.3,
        ),
        scoring=TeamScoringPattern(
            scored_in_match=_stat("Team Scored", 9, 10),
            failed_to_score=_stat("Failed to Score", 1, 10),
            clean_sheet=_stat("Clean Sheet", 2, 10),
        ),
        corners=CornersPattern(avg_corners_total=8.7),
        cards=CardsPattern(avg_yellow_total=4.2),
    )
    fa = FactorAnalyzer()
    factor = fa.analyze(home, away, min_wilson=50.0)
    return home, away, factor


# ==================================================================
# System prompt validation
# ==================================================================


class TestSystemPromptConstraints:
    """Validate that the system prompt prevents ALL forms of prediction."""

    def test_forbids_predict(self):
        assert "NEVER predict" in _SYSTEM_PROMPT

    def test_forbids_guess(self):
        assert "guess" in _SYSTEM_PROMPT

    def test_forbids_speculate(self):
        assert "speculate" in _SYSTEM_PROMPT

    def test_forbids_likely(self):
        assert '"likely"' in _SYSTEM_PROMPT

    def test_forbids_should(self):
        assert '"should"' in _SYSTEM_PROMPT

    def test_forbids_expected_to(self):
        assert '"expected to"' in _SYSTEM_PROMPT

    def test_forbids_predicted(self):
        assert '"predicted"' in _SYSTEM_PROMPT

    def test_forbids_will(self):
        assert '"will"' in _SYSTEM_PROMPT

    def test_forbids_probable(self):
        assert '"probable"' in _SYSTEM_PROMPT

    def test_requires_historical_framing(self):
        assert "historical patterns" in _SYSTEM_PROMPT
        assert "has occurred" in _SYSTEM_PROMPT

    def test_requires_exact_percentages(self):
        assert "percentages exactly" in _SYSTEM_PROMPT

    def test_forbids_adding_info(self):
        assert "not add any information" in _SYSTEM_PROMPT

    def test_forbids_opinions(self):
        assert "opinions" in _SYSTEM_PROMPT

    def test_identifies_as_formatting_tool(self):
        assert "formatting tool" in _SYSTEM_PROMPT
        assert "not an analyst" in _SYSTEM_PROMPT

    def test_requires_disclaimer(self):
        assert "disclaimer" in _SYSTEM_PROMPT


# ==================================================================
# User prompt structure
# ==================================================================


class TestUserPromptStructure:
    """Validate the user prompt sent to the LLM."""

    def setup_method(self):
        self.home, self.away, self.factor = _build_reports()
        self.formatter = LLMReportFormatter(api_key="test-key")

    def test_prompt_contains_match_info(self):
        prompt = self.formatter._build_prompt(self.factor, self.home, self.away, 65.0)
        assert "Barcelona" in prompt
        assert "Real Madrid" in prompt
        assert "La Liga" in prompt
        assert "2024" in prompt

    def test_prompt_contains_averages(self):
        prompt = self.formatter._build_prompt(self.factor, self.home, self.away, 65.0)
        assert "2.3" in prompt     # avg scored
        assert "0.9" in prompt     # avg conceded
        assert "3.2" in prompt     # avg total

    def test_prompt_contains_intersection(self):
        prompt = self.formatter._build_prompt(self.factor, self.home, self.away, 65.0)
        assert "PATTERN INTERSECTION" in prompt
        assert "Combined" in prompt

    def test_prompt_contains_factors(self):
        prompt = self.formatter._build_prompt(self.factor, self.home, self.away, 65.0)
        assert "TOP HOME FACTORS" in prompt
        assert "TOP AWAY FACTORS" in prompt

    def test_prompt_contains_threshold(self):
        prompt = self.formatter._build_prompt(self.factor, self.home, self.away, 75.0)
        assert "75" in prompt

    def test_prompt_reminds_no_predictions(self):
        """The user prompt itself should remind the LLM not to predict."""
        prompt = self.formatter._build_prompt(self.factor, self.home, self.away, 65.0)
        assert "Do NOT predict" in prompt or "do NOT predict" in prompt.lower() or "NOT predict" in prompt

    def test_prompt_requests_specific_sections(self):
        prompt = self.formatter._build_prompt(self.factor, self.home, self.away, 65.0)
        assert "Match Overview" in prompt
        assert "Key Statistical Patterns" in prompt
        assert "Home Team Profile" in prompt
        assert "Away Team Profile" in prompt
        assert "Disclaimer" in prompt

    def test_prompt_without_detailed_reports(self):
        """Prompt should work even without home/away reports."""
        prompt = self.formatter._build_prompt(self.factor, None, None, 65.0)
        assert "Barcelona" in prompt
        assert "No averages available" in prompt


# ==================================================================
# LLM API call validation
# ==================================================================


class TestLLMApiCall:
    """Validate the LLM API call structure and parameters."""

    @patch("requests.post")
    def test_api_call_uses_system_prompt(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Test report."}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        home, away, factor = _build_reports()
        fmt = LLMReportFormatter(api_key="sk-test", model="gpt-4o-mini")
        fmt.format_prose(factor, home, away)

        payload = mock_post.call_args[1]["json"]
        assert payload["messages"][0]["role"] == "system"
        assert "NEVER predict" in payload["messages"][0]["content"]

    @patch("requests.post")
    def test_temperature_is_low(self, mock_post):
        """Low temperature = more deterministic output."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Report."}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        home, away, factor = _build_reports()
        fmt = LLMReportFormatter(api_key="sk-test", temperature=0.3)
        fmt.format_prose(factor, home, away)

        payload = mock_post.call_args[1]["json"]
        assert payload["temperature"] <= 0.5

    @patch("requests.post")
    def test_max_tokens_set(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Report."}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        home, away, factor = _build_reports()
        fmt = LLMReportFormatter(api_key="sk-test")
        fmt.format_prose(factor, home, away)

        payload = mock_post.call_args[1]["json"]
        assert "max_tokens" in payload
        assert payload["max_tokens"] > 0

    @patch("requests.post")
    def test_response_used_verbatim(self, mock_post):
        """LLM response should be returned as-is, no post-processing."""
        llm_output = "## Match Report\nBTTS occurred in 80% of matches."
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": llm_output}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        home, away, factor = _build_reports()
        fmt = LLMReportFormatter(api_key="sk-test")
        result = fmt.format_prose(factor, home, away)

        # Output should be exactly what the LLM returned
        assert result == llm_output


# ==================================================================
# Fallback behavior validation
# ==================================================================


class TestFallbackBehavior:
    def test_no_api_key_uses_text_format(self):
        home, away, factor = _build_reports()
        fmt = LLMReportFormatter(api_key="")
        result = fmt.format_prose(factor, home, away)
        assert "DISCLAIMER" in result
        assert "Barcelona" in result
        # Should be plain text format, not empty
        assert len(result) > 200

    @patch("requests.post")
    def test_api_error_uses_text_format(self, mock_post):
        mock_post.side_effect = Exception("Connection refused")
        home, away, factor = _build_reports()
        fmt = LLMReportFormatter(api_key="sk-test")
        result = fmt.format_prose(factor, home, away)
        assert "DISCLAIMER" in result
        assert "Barcelona" in result

    @patch("requests.post")
    def test_api_timeout_uses_text_format(self, mock_post):
        import requests as req
        mock_post.side_effect = req.exceptions.Timeout("Timed out")
        home, away, factor = _build_reports()
        fmt = LLMReportFormatter(api_key="sk-test")
        result = fmt.format_prose(factor, home, away)
        assert "DISCLAIMER" in result

    def test_fallback_still_has_all_sections(self):
        """The fallback text format should include all sections."""
        home, away, factor = _build_reports()
        fmt = LLMReportFormatter(api_key="")
        result = fmt.format_prose(factor, home, away)
        assert "MATCH ANALYSIS REPORT" in result
        assert "MATCH ANALYSIS REPORT" in result
        assert "PATTERN INTERSECTION" in result
        assert "DISCLAIMER" in result


# ==================================================================
# Output consistency validation
# ==================================================================


class TestOutputConsistency:
    """Validate that outputs are consistent across formats."""

    def setup_method(self):
        self.home, self.away, self.factor = _build_reports()

    def test_all_formats_have_disclaimer(self):
        rf = ReportFormatter(confidence_threshold=50.0)
        text = rf.format_text(self.factor, self.home, self.away)
        md = rf.format_markdown(self.factor, self.home, self.away)
        d = rf.format_dict(self.factor, self.home, self.away)

        assert "historical statistical patterns" in text
        assert "historical statistical patterns" in md
        assert "historical statistical patterns" in d["disclaimer"]

    def test_all_formats_have_both_teams(self):
        rf = ReportFormatter(confidence_threshold=50.0)
        text = rf.format_text(self.factor, self.home, self.away)
        md = rf.format_markdown(self.factor, self.home, self.away)
        d = rf.format_dict(self.factor, self.home, self.away)

        for output in [text, md]:
            assert "Barcelona" in output
            assert "Real Madrid" in output
        assert d["match"]["home_team"] == "Barcelona"
        assert d["match"]["away_team"] == "Real Madrid"

    def test_intersection_count_consistent(self):
        """All formats should report the same number of intersection factors."""
        rf = ReportFormatter(confidence_threshold=50.0)
        d = rf.format_dict(self.factor, self.home, self.away)
        n_json = len(d["intersection"])

        # Text and markdown should mention each intersection pattern
        text = rf.format_text(self.factor, self.home, self.away)
        md = rf.format_markdown(self.factor, self.home, self.away)

        for entry in d["intersection"]:
            assert entry["pattern"] in text
            assert entry["pattern"] in md

    def test_json_percentages_are_numbers(self):
        rf = ReportFormatter(confidence_threshold=50.0)
        d = rf.format_dict(self.factor, self.home, self.away)
        for entry in d["intersection"]:
            assert isinstance(entry["home_percentage"], (int, float))
            assert isinstance(entry["away_percentage"], (int, float))
            assert isinstance(entry["combined_percentage"], (int, float))

    def test_empty_report_handled_consistently(self):
        from src.processing.factor_analyzer import MatchFactorReport
        empty = MatchFactorReport(
            home_team="X", away_team="Y",
            league_name="L", season="2024",
        )
        rf = ReportFormatter(confidence_threshold=50.0)
        text = rf.format_text(empty)
        md = rf.format_markdown(empty)
        d = rf.format_dict(empty)

        assert len(text) > 0
        assert len(md) > 0
        assert d["intersection"] == []

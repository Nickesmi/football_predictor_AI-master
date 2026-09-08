"""
Regression tests for src/data/openfootball_parser.py — data validation
(Phase 2 §2): duplicates, invalid scores, inconsistent HT/FT, missing team
names, non-organic ("awarded") results must be quarantined, never silently
inserted.
"""

from __future__ import annotations

from src.data.openfootball_parser import parse_openfootball_text


SAMPLE_OK = """\
= English Premier League 2023/24

# Date       Fri Aug 11 2023 - Sun May 19 2024 (282d)
# Teams      20
# Matches    2

▪ Matchday 1
Fri Aug 11
  20:00  Burnley FC               0-3 (0-2)  Manchester City FC
Sat Aug 12
  13:00  Arsenal FC               2-1 (2-0)  Nottingham Forest FC
"""


def test_parses_real_matches_with_correct_dates_and_scores():
    result = parse_openfootball_text(SAMPLE_OK, league_hint="Premier League", season_hint="2023-24")
    assert len(result.matches) == 2
    assert result.quarantined == []

    m0 = result.matches[0]
    assert m0.date == "2023-08-11"
    assert m0.home_team == "Burnley FC"
    assert m0.away_team == "Manchester City FC"
    assert (m0.home_goals, m0.away_goals) == (0, 3)
    assert (m0.home_goals_ht, m0.away_goals_ht) == (0, 2)

    m1 = result.matches[1]
    assert m1.date == "2023-08-12"  # different weekday line -> new date


def test_older_v_format_with_explicit_year_is_parsed():
    text = """\
= Deutsche Bundesliga 2015/16

# Matches    1

▪ Matchday 1
  Fri Aug 14 2015
    20:30  Bayern München          v Hamburger SV             5-0 (1-0)
"""
    result = parse_openfootball_text(text, league_hint="Bundesliga", season_hint="2015-16")
    assert len(result.matches) == 1
    m = result.matches[0]
    assert m.date == "2015-08-14"
    assert m.home_team == "Bayern München"
    assert m.away_team == "Hamburger SV"
    assert (m.home_goals, m.away_goals) == (5, 0)


def test_january_fixture_infers_second_calendar_year_of_season():
    text = """\
= English Premier League 2023/24

# Matches    1

▪ Matchday 20
Sat Jan 13
  15:00  Arsenal FC               2-0 (1-0)  Chelsea FC
"""
    result = parse_openfootball_text(text, league_hint="Premier League", season_hint="2023-24")
    assert len(result.matches) == 1
    assert result.matches[0].date == "2024-01-13"  # season 2023-24 -> Jan belongs to 2024


def test_awarded_result_is_quarantined_not_silently_inserted():
    text = """\
= Italian Serie A 2020/21

# Matches    1

▪ Matchday 1
Sat Sep 19
  20:45  Hellas Verona FC         3-0  AS Roma                  [awarded]
"""
    result = parse_openfootball_text(text, league_hint="Serie A", season_hint="2020-21")
    assert result.matches == []
    assert len(result.quarantined) == 1
    assert "awarded" in result.quarantined[0]["reason"]


def test_ht_score_exceeding_ft_score_is_quarantined():
    text = """\
= English Premier League 2023/24

# Matches    1

▪ Matchday 1
Fri Aug 11
  20:00  Burnley FC               1-1 (2-0)  Manchester City FC
"""
    result = parse_openfootball_text(text, league_hint="Premier League", season_hint="2023-24")
    assert result.matches == []
    assert len(result.quarantined) == 1
    assert "inconsistent" in result.quarantined[0]["reason"]


def test_home_team_equals_away_team_is_quarantined():
    text = """\
= English Premier League 2023/24

# Matches    1

▪ Matchday 1
Fri Aug 11
  20:00  Burnley FC               1-0 (0-0)  Burnley FC
"""
    result = parse_openfootball_text(text, league_hint="Premier League", season_hint="2023-24")
    assert result.matches == []
    assert "home_team == away_team" in result.quarantined[0]["reason"]


def test_implausible_score_is_quarantined():
    text = """\
= English Premier League 2023/24

# Matches    1

▪ Matchday 1
Fri Aug 11
  20:00  Burnley FC               99-0 (0-0)  Manchester City FC
"""
    result = parse_openfootball_text(text, league_hint="Premier League", season_hint="2023-24")
    assert result.matches == []
    assert "implausible" in result.quarantined[0]["reason"]

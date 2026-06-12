"""
Statistical Pattern Analyzer (Issue #2).

Processes raw match data (TeamMatchSet) and computes high-confidence
patterns across all tracked markets:
  - BTTS (Both Teams To Score)
  - Over/Under Goals (Full Time & Half Time)
  - Corners
  - Cards
  - Win/Draw/Loss (FT & HT)
  - Team to Score (scored, failed, clean sheet, scored first)
  - First Half Events (goals, cards, corners in 1H)

Architecture:
    TeamMatchSet  (from Issue #1)
         ↓
    PatternAnalyzer.analyze(match_set)
         ↓
    TeamPatternReport  (consumed by Issue #3)
"""

from __future__ import annotations

from src.config import logger
from src.models.match import MatchResult, TeamMatchSet
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


class PatternAnalyzer:
    """
    Stateless engine that computes statistical patterns from a set of matches.

    Usage::

        analyzer = PatternAnalyzer()
        report = analyzer.analyze(home_team_match_set)
        high_conf = report.get_high_confidence_patterns(threshold=75.0)
    """

    # ==================================================================
    # PUBLIC
    # ==================================================================

    def analyze(self, match_set: TeamMatchSet) -> TeamPatternReport:
        """
        Analyze ALL matches in a TeamMatchSet and produce a full report.

        Args:
            match_set: A TeamMatchSet containing home or away matches.

        Returns:
            TeamPatternReport with all computed patterns.
        """
        matches = match_set.matches
        total = len(matches)

        if total == 0:
            logger.warning(
                "No matches to analyze for %s (%s)",
                match_set.team_name, match_set.context,
            )
            return TeamPatternReport(
                team_name=match_set.team_name,
                context=match_set.context,
                league_name=match_set.league_name,
                season=match_set.season,
                total_matches=0,
            )

        context = match_set.context  # "home" or "away"

        logger.info(
            "Analyzing %d %s matches for %s in %s %s",
            total, context, match_set.team_name,
            match_set.league_name, match_set.season,
        )

        report = TeamPatternReport(
            team_name=match_set.team_name,
            context=context,
            league_name=match_set.league_name,
            season=match_set.season,
            total_matches=total,
            goals=self._compute_goals(matches, total, context),
            results=self._compute_results(matches, total, context),
            scoring=self._compute_scoring(matches, total, context),
            corners=self._compute_corners(matches, total, context),
            cards=self._compute_cards(matches, total, context),
            first_half=self._compute_first_half(matches, total, context),
        )

        high = report.get_high_confidence_patterns(min_wilson=65.0)
        logger.info(
            "Analysis complete: %d high-confidence patterns (>=65%%) found.",
            len(high),
        )

        return report

    # ==================================================================
    # PRIVATE — Computation helpers
    # ==================================================================

    @staticmethod
    def _pct(count: int, total: int) -> float:
        """Safe percentage calculation."""
        return round((count / total) * 100, 1) if total > 0 else 0.0

    @classmethod
    def _make_stat(cls, label: str, count: int, total: int) -> PatternStat:
        return PatternStat(
            label=label,
            count=count,
            total=total,
            percentage=cls._pct(count, total),
        )

    # ---- Goals ----

    @classmethod
    def _compute_goals(
        cls, matches: list[MatchResult], total: int, context: str
    ) -> GoalsPattern:
        btts_y = sum(1 for m in matches if m.btts)
        btts_n = total - btts_y

        # Full-time O/U
        o05 = sum(1 for m in matches if m.total_goals_ft > 0)
        o15 = sum(1 for m in matches if m.total_goals_ft > 1)
        o25 = sum(1 for m in matches if m.total_goals_ft > 2)
        o35 = sum(1 for m in matches if m.total_goals_ft > 3)
        o45 = sum(1 for m in matches if m.total_goals_ft > 4)

        # Half-time O/U (only matches with HT data)
        ht_matches = [m for m in matches if m.home_score_ht is not None and m.away_score_ht is not None]
        ht_total = len(ht_matches)
        o05_ht = sum(1 for m in ht_matches if m.total_goals_ht > 0)
        o15_ht = sum(1 for m in ht_matches if m.total_goals_ht > 1)
        o25_ht = sum(1 for m in ht_matches if m.total_goals_ht > 2)

        # Averages
        avg_ft = round(sum(m.total_goals_ft for m in matches) / total, 2)
        avg_ht = round(sum(m.total_goals_ht for m in ht_matches) / ht_total, 2) if ht_total else 0.0

        # Context-aware scoring averages
        if context == "home":
            scored = sum(m.home_score_ft for m in matches)
            conceded = sum(m.away_score_ft for m in matches)
        else:
            scored = sum(m.away_score_ft for m in matches)
            conceded = sum(m.home_score_ft for m in matches)

        return GoalsPattern(
            btts_yes=cls._make_stat("BTTS - Yes", btts_y, total),
            btts_no=cls._make_stat("BTTS - No", btts_n, total),
            over_0_5_ft=cls._make_stat("Over 0.5 Goals FT", o05, total),
            over_1_5_ft=cls._make_stat("Over 1.5 Goals FT", o15, total),
            over_2_5_ft=cls._make_stat("Over 2.5 Goals FT", o25, total),
            over_3_5_ft=cls._make_stat("Over 3.5 Goals FT", o35, total),
            over_4_5_ft=cls._make_stat("Over 4.5 Goals FT", o45, total),
            under_0_5_ft=cls._make_stat("Under 0.5 Goals FT", total - o05, total),
            under_1_5_ft=cls._make_stat("Under 1.5 Goals FT", total - o15, total),
            under_2_5_ft=cls._make_stat("Under 2.5 Goals FT", total - o25, total),
            under_3_5_ft=cls._make_stat("Under 3.5 Goals FT", total - o35, total),
            under_4_5_ft=cls._make_stat("Under 4.5 Goals FT", total - o45, total),
            over_0_5_ht=cls._make_stat("Over 0.5 Goals HT", o05_ht, ht_total) if ht_total else None,
            over_1_5_ht=cls._make_stat("Over 1.5 Goals HT", o15_ht, ht_total) if ht_total else None,
            over_2_5_ht=cls._make_stat("Over 2.5 Goals HT", o25_ht, ht_total) if ht_total else None,
            under_0_5_ht=cls._make_stat("Under 0.5 Goals HT", ht_total - o05_ht, ht_total) if ht_total else None,
            under_1_5_ht=cls._make_stat("Under 1.5 Goals HT", ht_total - o15_ht, ht_total) if ht_total else None,
            under_2_5_ht=cls._make_stat("Under 2.5 Goals HT", ht_total - o25_ht, ht_total) if ht_total else None,
            avg_goals_ft=avg_ft,
            avg_goals_ht=avg_ht,
            avg_goals_scored=round(scored / total, 2),
            avg_goals_conceded=round(conceded / total, 2),
        )

    # ---- Results (W/D/L) ----

    @classmethod
    def _compute_results(
        cls, matches: list[MatchResult], total: int, context: str
    ) -> ResultPattern:
        if context == "home":
            wins = sum(1 for m in matches if m.home_win)
            losses = sum(1 for m in matches if m.away_win)
        else:
            wins = sum(1 for m in matches if m.away_win)
            losses = sum(1 for m in matches if m.home_win)

        draws = sum(1 for m in matches if m.draw)

        # Half-time results
        ht_matches = [m for m in matches if m.ht_result != "?"]
        ht_total = len(ht_matches)

        if context == "home":
            ht_wins = sum(1 for m in ht_matches if m.ht_result == "1")
            ht_losses = sum(1 for m in ht_matches if m.ht_result == "2")
        else:
            ht_wins = sum(1 for m in ht_matches if m.ht_result == "2")
            ht_losses = sum(1 for m in ht_matches if m.ht_result == "1")
        ht_draws = sum(1 for m in ht_matches if m.ht_result == "X")

        # HT/FT combo distribution
        ht_ft_dist: dict[str, int] = {}
        for m in ht_matches:
            ht_r = m.ht_result  # "1", "X", "2"
            ft_r = m.ft_result
            # Remap to context-relative: W/D/L
            if context == "home":
                ht_label = {"1": "W", "X": "D", "2": "L"}.get(ht_r, "?")
                ft_label = {"1": "W", "X": "D", "2": "L"}.get(ft_r, "?")
            else:
                ht_label = {"2": "W", "X": "D", "1": "L"}.get(ht_r, "?")
                ft_label = {"2": "W", "X": "D", "1": "L"}.get(ft_r, "?")
            combo = f"HT {ht_label} / FT {ft_label}"
            ht_ft_dist[combo] = ht_ft_dist.get(combo, 0) + 1

        ht_ft_patterns = {
            k: cls._make_stat(k, v, ht_total)
            for k, v in sorted(ht_ft_dist.items(), key=lambda x: -x[1])
        }

        return ResultPattern(
            wins=cls._make_stat(f"{context.title()} Win", wins, total),
            draws=cls._make_stat("Draw", draws, total),
            losses=cls._make_stat(f"{context.title()} Loss", losses, total),
            ht_wins=cls._make_stat(f"HT {context.title()} Win", ht_wins, ht_total) if ht_total else None,
            ht_draws=cls._make_stat("HT Draw", ht_draws, ht_total) if ht_total else None,
            ht_losses=cls._make_stat(f"HT {context.title()} Loss", ht_losses, ht_total) if ht_total else None,
            ht_ft_distribution=ht_ft_patterns,
        )

    # ---- Team Scoring ----

    @classmethod
    def _compute_scoring(
        cls, matches: list[MatchResult], total: int, context: str
    ) -> TeamScoringPattern:
        if context == "home":
            scored_count = sum(1 for m in matches if m.home_score_ft > 0)
            fts_count = sum(1 for m in matches if m.home_score_ft == 0)
            cs_count = sum(1 for m in matches if m.away_score_ft == 0)
        else:
            scored_count = sum(1 for m in matches if m.away_score_ft > 0)
            fts_count = sum(1 for m in matches if m.away_score_ft == 0)
            cs_count = sum(1 for m in matches if m.home_score_ft == 0)

        # Scored first / conceded first (from goal events)
        scored_first_count = 0
        conceded_first_count = 0
        for m in matches:
            if m.goals:
                first_goal = m.goals[0]
                team_scored_first = (
                    (context == "home" and first_goal.is_home) or
                    (context == "away" and not first_goal.is_home)
                )
                if team_scored_first:
                    scored_first_count += 1
                else:
                    conceded_first_count += 1

        matches_with_goals = sum(1 for m in matches if m.goals)

        # 1H / 2H scoring
        scored_1h = 0
        scored_2h = 0
        conceded_1h = 0
        conceded_2h = 0
        for m in matches:
            team_1h_goals = sum(
                1 for g in m.goals
                if g.half == "1st Half" and (
                    (context == "home" and g.is_home) or
                    (context == "away" and not g.is_home)
                )
            )
            team_2h_goals = sum(
                1 for g in m.goals
                if g.half == "2nd Half" and (
                    (context == "home" and g.is_home) or
                    (context == "away" and not g.is_home)
                )
            )
            opp_1h_goals = sum(
                1 for g in m.goals
                if g.half == "1st Half" and (
                    (context == "home" and not g.is_home) or
                    (context == "away" and g.is_home)
                )
            )
            opp_2h_goals = sum(
                1 for g in m.goals
                if g.half == "2nd Half" and (
                    (context == "home" and not g.is_home) or
                    (context == "away" and g.is_home)
                )
            )
            if team_1h_goals > 0:
                scored_1h += 1
            if team_2h_goals > 0:
                scored_2h += 1
            if opp_1h_goals > 0:
                conceded_1h += 1
            if opp_2h_goals > 0:
                conceded_2h += 1

        return TeamScoringPattern(
            scored_in_match=cls._make_stat("Team Scored", scored_count, total),
            failed_to_score=cls._make_stat("Failed to Score", fts_count, total),
            clean_sheet=cls._make_stat("Clean Sheet", cs_count, total),
            scored_first=cls._make_stat("Scored First", scored_first_count, matches_with_goals) if matches_with_goals else None,
            conceded_first=cls._make_stat("Conceded First", conceded_first_count, matches_with_goals) if matches_with_goals else None,
            scored_in_1h=cls._make_stat("Team Scored in 1H", scored_1h, total),
            scored_in_2h=cls._make_stat("Team Scored in 2H", scored_2h, total),
            conceded_in_1h=cls._make_stat("Conceded in 1H", conceded_1h, total),
            conceded_in_2h=cls._make_stat("Conceded in 2H", conceded_2h, total),
        )

    # ---- Corners ----

    @classmethod
    def _compute_corners(
        cls, matches: list[MatchResult], total: int, context: str
    ) -> CornersPattern:
        # Only matches with statistics
        stat_matches = [m for m in matches if m.statistics is not None]
        st = len(stat_matches)

        if st == 0:
            return CornersPattern()

        totals = []
        team_corners_list = []
        opp_corners_list = []

        for m in stat_matches:
            s = m.statistics
            c_total = s.corners_home + s.corners_away
            totals.append(c_total)
            if context == "home":
                team_corners_list.append(s.corners_home)
                opp_corners_list.append(s.corners_away)
            else:
                team_corners_list.append(s.corners_away)
                opp_corners_list.append(s.corners_home)

        avg_total = round(sum(totals) / st, 2)
        avg_team = round(sum(team_corners_list) / st, 2)
        avg_opp = round(sum(opp_corners_list) / st, 2)

        o75 = sum(1 for t in totals if t > 7)
        o85 = sum(1 for t in totals if t > 8)
        o95 = sum(1 for t in totals if t > 9)
        o105 = sum(1 for t in totals if t > 10)
        o115 = sum(1 for t in totals if t > 11)

        return CornersPattern(
            avg_corners_total=avg_total,
            avg_corners_team=avg_team,
            avg_corners_opponent=avg_opp,
            over_7_5=cls._make_stat("Over 7.5 Corners", o75, st),
            over_8_5=cls._make_stat("Over 8.5 Corners", o85, st),
            over_9_5=cls._make_stat("Over 9.5 Corners", o95, st),
            over_10_5=cls._make_stat("Over 10.5 Corners", o105, st),
            over_11_5=cls._make_stat("Over 11.5 Corners", o115, st),
            under_7_5=cls._make_stat("Under 7.5 Corners", st - o75, st),
            under_8_5=cls._make_stat("Under 8.5 Corners", st - o85, st),
            under_9_5=cls._make_stat("Under 9.5 Corners", st - o95, st),
            under_10_5=cls._make_stat("Under 10.5 Corners", st - o105, st),
            under_11_5=cls._make_stat("Under 11.5 Corners", st - o115, st),
        )

    # ---- Cards ----

    @classmethod
    def _compute_cards(
        cls, matches: list[MatchResult], total: int, context: str
    ) -> CardsPattern:
        stat_matches = [m for m in matches if m.statistics is not None]
        st = len(stat_matches)

        if st == 0:
            # Fall back to card events if statistics are unavailable
            return cls._compute_cards_from_events(matches, total, context)

        yellow_totals = []
        team_yellows = []
        opp_yellows = []
        red_totals = []

        for m in stat_matches:
            s = m.statistics
            yc_total = s.yellow_cards_home + s.yellow_cards_away
            rc_total = s.red_cards_home + s.red_cards_away
            yellow_totals.append(yc_total)
            red_totals.append(rc_total)
            if context == "home":
                team_yellows.append(s.yellow_cards_home)
                opp_yellows.append(s.yellow_cards_away)
            else:
                team_yellows.append(s.yellow_cards_away)
                opp_yellows.append(s.yellow_cards_home)

        avg_yc = round(sum(yellow_totals) / st, 2)
        avg_team_yc = round(sum(team_yellows) / st, 2)
        avg_opp_yc = round(sum(opp_yellows) / st, 2)
        avg_rc = round(sum(red_totals) / st, 2)

        o25 = sum(1 for t in yellow_totals if t > 2)
        o35 = sum(1 for t in yellow_totals if t > 3)
        o45 = sum(1 for t in yellow_totals if t > 4)
        o55 = sum(1 for t in yellow_totals if t > 5)

        # Cards in first half
        cards_1h_count = 0
        total_1h_cards = 0
        for m in matches:
            first_half_cards = [c for c in m.cards if c.half == "1st Half"]
            if first_half_cards:
                cards_1h_count += 1
            total_1h_cards += len(first_half_cards)

        return CardsPattern(
            avg_yellow_total=avg_yc,
            avg_yellow_team=avg_team_yc,
            avg_yellow_opponent=avg_opp_yc,
            avg_red_total=avg_rc,
            over_2_5_cards=cls._make_stat("Over 2.5 Yellow Cards", o25, st),
            over_3_5_cards=cls._make_stat("Over 3.5 Yellow Cards", o35, st),
            over_4_5_cards=cls._make_stat("Over 4.5 Yellow Cards", o45, st),
            over_5_5_cards=cls._make_stat("Over 5.5 Yellow Cards", o55, st),
            under_2_5_cards=cls._make_stat("Under 2.5 Yellow Cards", st - o25, st),
            under_3_5_cards=cls._make_stat("Under 3.5 Yellow Cards", st - o35, st),
            under_4_5_cards=cls._make_stat("Under 4.5 Yellow Cards", st - o45, st),
            under_5_5_cards=cls._make_stat("Under 5.5 Yellow Cards", st - o55, st),
            cards_in_1h=cls._make_stat("Card in 1st Half", cards_1h_count, total),
            avg_cards_1h=round(total_1h_cards / total, 2) if total else 0.0,
        )

    @classmethod
    def _compute_cards_from_events(
        cls, matches: list[MatchResult], total: int, context: str
    ) -> CardsPattern:
        """Fallback: compute card stats from event-level data when statistics are unavailable."""
        yellow_totals = []
        team_yellows = []
        opp_yellows = []
        red_totals = []
        cards_1h_count = 0
        total_1h_cards = 0

        for m in matches:
            yc_team = sum(
                1 for c in m.cards
                if c.card_type == "yellow" and (
                    (context == "home" and c.is_home) or
                    (context == "away" and not c.is_home)
                )
            )
            yc_opp = sum(
                1 for c in m.cards
                if c.card_type == "yellow" and (
                    (context == "home" and not c.is_home) or
                    (context == "away" and c.is_home)
                )
            )
            rc = sum(1 for c in m.cards if c.card_type == "red")
            yc_total = yc_team + yc_opp

            yellow_totals.append(yc_total)
            team_yellows.append(yc_team)
            opp_yellows.append(yc_opp)
            red_totals.append(rc)

            first_half_cards = [c for c in m.cards if c.half == "1st Half"]
            if first_half_cards:
                cards_1h_count += 1
            total_1h_cards += len(first_half_cards)

        if not yellow_totals:
            return CardsPattern()

        avg_yc = round(sum(yellow_totals) / total, 2)
        avg_team_yc = round(sum(team_yellows) / total, 2)
        avg_opp_yc = round(sum(opp_yellows) / total, 2)
        avg_rc = round(sum(red_totals) / total, 2)

        o25 = sum(1 for t in yellow_totals if t > 2)
        o35 = sum(1 for t in yellow_totals if t > 3)
        o45 = sum(1 for t in yellow_totals if t > 4)
        o55 = sum(1 for t in yellow_totals if t > 5)

        return CardsPattern(
            avg_yellow_total=avg_yc,
            avg_yellow_team=avg_team_yc,
            avg_yellow_opponent=avg_opp_yc,
            avg_red_total=avg_rc,
            over_2_5_cards=cls._make_stat("Over 2.5 Yellow Cards", o25, total),
            over_3_5_cards=cls._make_stat("Over 3.5 Yellow Cards", o35, total),
            over_4_5_cards=cls._make_stat("Over 4.5 Yellow Cards", o45, total),
            over_5_5_cards=cls._make_stat("Over 5.5 Yellow Cards", o55, total),
            under_2_5_cards=cls._make_stat("Under 2.5 Yellow Cards", total - o25, total),
            under_3_5_cards=cls._make_stat("Under 3.5 Yellow Cards", total - o35, total),
            under_4_5_cards=cls._make_stat("Under 4.5 Yellow Cards", total - o45, total),
            under_5_5_cards=cls._make_stat("Under 5.5 Yellow Cards", total - o55, total),
            cards_in_1h=cls._make_stat("Card in 1st Half", cards_1h_count, total),
            avg_cards_1h=round(total_1h_cards / total, 2) if total else 0.0,
        )

    # ---- First Half Events ----

    @classmethod
    def _compute_first_half(
        cls, matches: list[MatchResult], total: int, context: str
    ) -> FirstHalfPattern:
        ht_matches = [m for m in matches if m.home_score_ht is not None and m.away_score_ht is not None]
        ht_total = len(ht_matches)

        if ht_total == 0:
            return FirstHalfPattern()

        goals_1h_count = sum(1 for m in ht_matches if m.total_goals_ht > 0)
        o05_goals = goals_1h_count
        o15_goals = sum(1 for m in ht_matches if m.total_goals_ht > 1)

        # Both scored in 1H
        both_1h = sum(
            1 for m in ht_matches
            if m.home_score_ht > 0 and m.away_score_ht > 0
        )

        # Cards in 1H
        cards_1h_count = 0
        total_1h_cards = 0
        o05_cards_1h = 0
        o15_cards_1h = 0
        for m in matches:
            first_half_cards = [c for c in m.cards if c.half == "1st Half"]
            n = len(first_half_cards)
            total_1h_cards += n
            if n > 0:
                cards_1h_count += 1
                o05_cards_1h += 1
            if n > 1:
                o15_cards_1h += 1

        # Corners in 1H (from statistics if available)
        corners_1h_list = []
        for m in matches:
            if m.statistics and (m.statistics.corners_home_1h or m.statistics.corners_away_1h):
                corners_1h_list.append(m.statistics.corners_home_1h + m.statistics.corners_away_1h)
        avg_corners_1h = round(sum(corners_1h_list) / len(corners_1h_list), 2) if corners_1h_list else 0.0

        # HT result distribution
        if context == "home":
            ht_wins = sum(1 for m in ht_matches if m.ht_result == "1")
            ht_losses = sum(1 for m in ht_matches if m.ht_result == "2")
        else:
            ht_wins = sum(1 for m in ht_matches if m.ht_result == "2")
            ht_losses = sum(1 for m in ht_matches if m.ht_result == "1")
        ht_draws = sum(1 for m in ht_matches if m.ht_result == "X")

        return FirstHalfPattern(
            goals_in_1h=cls._make_stat("Goal in 1st Half", goals_1h_count, ht_total),
            both_scored_1h=cls._make_stat("Both Scored in 1H", both_1h, ht_total),
            over_0_5_goals_1h=cls._make_stat("Over 0.5 Goals 1H", o05_goals, ht_total),
            over_1_5_goals_1h=cls._make_stat("Over 1.5 Goals 1H", o15_goals, ht_total),
            cards_in_1h=cls._make_stat("Card in 1st Half", cards_1h_count, total),
            over_0_5_cards_1h=cls._make_stat("Over 0.5 Cards 1H", o05_cards_1h, total),
            over_1_5_cards_1h=cls._make_stat("Over 1.5 Cards 1H", o15_cards_1h, total),
            avg_corners_1h=avg_corners_1h,
            ht_home_win=cls._make_stat("HT Home Win", ht_wins if context == "home" else ht_losses, ht_total),
            ht_draw=cls._make_stat("HT Draw", ht_draws, ht_total),
            ht_away_win=cls._make_stat("HT Away Win", ht_losses if context == "home" else ht_wins, ht_total),
        )

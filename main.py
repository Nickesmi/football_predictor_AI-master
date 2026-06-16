"""
Football Predictor AI  –  Main Entry Point

Usage:
    python main.py --home "Man United" --away "Chelsea" --league "Premier League" --season 2024
    python main.py --home-id 33 --away-id 49 --league-id 39 --season 2024 --format markdown
    python main.py --home-id 33 --away-id 49 --league-id 39 --season 2024 --format json --output report.json
    python main.py --home-id 33 --away-id 49 --league-id 39 --season 2024 --llm

Full pipeline:
  1. Search for team & league IDs          (Issue #1)
  2. Fetch all HOME/AWAY matches           (Issue #1)
  3. Compute statistical patterns          (Issue #2)
  4. Compute factor intersection           (Issue #3)
  5. Format report (text/markdown/JSON)    (Issue #4)
"""

from __future__ import annotations

import argparse
import json
import sys
from textwrap import dedent


def main():
    if len(sys.argv) == 1:
        try:
            from streamlit_app import render
        except ImportError:
            print(
                "This file is a CLI tool and requires arguments.\n\n"
                "For Streamlit Cloud, set the app entrypoint to streamlit_app.py.\n"
                "For CLI usage, run: python main.py --help\n"
            )
            return
        render()
        return

    parser = argparse.ArgumentParser(
        description="Football Predictor AI – Statistical Pattern Analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""\
            Output Formats:
              text     Plain text (default, terminal-friendly)
              markdown Markdown with tables and emoji
              json     JSON-serializable dict

            Examples:
              python main.py --home "Manchester United" --away "Chelsea" --league "Premier League" --season 2024
              python main.py --home-id 33 --away-id 49 --league-id 39 --season 2024 --format markdown
              python main.py --home-id 33 --away-id 49 --league-id 39 --season 2024 --format json --output report.json
              python main.py --home-id 33 --away-id 49 --league-id 39 --season 2024 --llm
        """),
    )

    # Team & league identification
    parser.add_argument("--home", type=str, help="Home team name (searches API)")
    parser.add_argument("--away", type=str, help="Away team name (searches API)")
    parser.add_argument("--home-id", type=int, help="Home team ID (skip search)")
    parser.add_argument("--away-id", type=int, help="Away team ID (skip search)")
    parser.add_argument("--league", type=str, help="League name (searches API)")
    parser.add_argument("--league-id", type=int, help="League ID (skip search)")
    parser.add_argument("--country", type=str, help="Country filter for league search")
    parser.add_argument("--season", type=int, required=True, help="Season year (e.g. 2024)")

    # Analysis options
    parser.add_argument(
        "--threshold", type=float, default=65.0,
        help="Confidence threshold %% (default: 65.0)"
    )

    # Output format options (Issue #4)
    parser.add_argument(
        "--format", type=str, default="text",
        choices=["text", "markdown", "json"],
        help="Output format: text (default), markdown, or json"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Write output to file instead of stdout"
    )
    parser.add_argument(
        "--llm", action="store_true",
        help="Use LLM for natural language formatting (requires OPENAI_API_KEY)"
    )
    parser.add_argument(
        "--llm-model", type=str, default="gpt-4o-mini",
        help="LLM model to use (default: gpt-4o-mini)"
    )

    args = parser.parse_args()

    from src.config import APIFOOTBALL_API_KEY
    from src.data.api_football_fetcher import APIFootballFetcher
    from src.processing.pattern_analyzer import PatternAnalyzer
    from src.processing.factor_analyzer import FactorAnalyzer
    from src.reporting.report_formatter import ReportFormatter
    from src.reporting.llm_formatter import LLMReportFormatter

    # ---- Validate API key ----
    if not APIFOOTBALL_API_KEY:
        print(
            "❌ ERROR: APIFOOTBALL_API_KEY is not set.\n"
            "Please set it in your .env file or as an environment variable.\n"
            "Get a free key at: https://www.api-football.com/\n"
        )
        sys.exit(1)

    fetcher = APIFootballFetcher()
    pattern_analyzer = PatternAnalyzer()
    factor_analyzer = FactorAnalyzer()

    # ---- Resolve IDs ----
    home_team_id = args.home_id
    away_team_id = args.away_id

    if not home_team_id:
        if not args.home:
            print("❌ ERROR: Provide --home (team name) or --home-id (team ID)")
            sys.exit(1)
        teams = fetcher.search_team(args.home)
        if not teams:
            print(f"❌ No teams found matching '{args.home}'")
            sys.exit(1)
        home_team_id = teams[0]["id"]
        print(f"✓ Home team: {teams[0]['name']} (ID: {home_team_id})")

    if not away_team_id:
        if not args.away:
            print("❌ ERROR: Provide --away (team name) or --away-id (team ID)")
            sys.exit(1)
        teams = fetcher.search_team(args.away)
        if not teams:
            print(f"❌ No teams found matching '{args.away}'")
            sys.exit(1)
        away_team_id = teams[0]["id"]
        print(f"✓ Away team: {teams[0]['name']} (ID: {away_team_id})")

    league_id = args.league_id
    if not league_id:
        if not args.league:
            print("❌ ERROR: Provide --league (name) or --league-id (ID)")
            sys.exit(1)
        leagues = fetcher.search_league(args.league, country=args.country)
        if not leagues:
            print(f"❌ No leagues found matching '{args.league}'")
            sys.exit(1)
        league_id = leagues[0]["id"]
        print(f"✓ League: {leagues[0]['name']} (ID: {league_id})")

    # ---- Issue #1: Fetch ----
    print(f"\n🔄 Fetching match data for season {args.season}...")
    home_matches, away_matches = fetcher.fetch_match_context(
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        league_id=league_id,
        season=args.season,
    )
    print(f"   Found {len(home_matches.matches)} home & {len(away_matches.matches)} away matches.")

    # ---- Issue #2: Analyze ----
    print(f"🔬 Computing statistical patterns...")
    home_report = pattern_analyzer.analyze(home_matches)
    away_report = pattern_analyzer.analyze(away_matches)

    # ---- Issue #3: Intersect ----
    print(f"�� Computing factor intersection...")
    factor_report = factor_analyzer.analyze(
        home_report, away_report, threshold=args.threshold
    )

    # ---- Issue #4: Format & Output ----
    print(f"📝 Generating {args.format} report...\n")

    if args.llm:
        # LLM natural language mode
        llm_formatter = LLMReportFormatter(model=args.llm_model)
        output = llm_formatter.format_prose(
            factor_report, home_report, away_report,
            threshold=args.threshold,
        )
    else:
        # Deterministic formatting
        formatter = ReportFormatter(confidence_threshold=args.threshold)

        if args.format == "markdown":
            output = formatter.format_markdown(
                factor_report, home_report, away_report
            )
        elif args.format == "json":
            d = formatter.format_dict(
                factor_report, home_report, away_report
            )
            output = json.dumps(d, indent=2, ensure_ascii=False)
        else:
            output = formatter.format_text(
                factor_report, home_report, away_report
            )

    # Write to file or stdout
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"✅ Report written to: {args.output}")
    else:
        print(output)

    print("\n✅ Analysis complete.")


if __name__ == "__main__":
    main()

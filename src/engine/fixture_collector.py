"""
Agent 1: Fixture Collector

Fetches matches from SofaScore and stores them in the database.
Extracts odds from SofaScore event data when available.
"""

import logging
import sqlite3
from src.db.match_repo import upsert_match, upsert_matches_batch
from src.db.odds_repo import insert_odds, insert_odds_batch

logger = logging.getLogger("football_predictor")

# Leagues we track (SofaScore uniqueTournament IDs → reliability tier)
TRACKED_LEAGUES: dict[int, str] = {
    # Tier 1 — Top 5
    17:  "Premier League",
    8:   "LaLiga",
    23:  "Serie A",
    35:  "Bundesliga",
    34:  "Ligue 1",
    # Tier 2 — Strong data
    18:  "Championship",
    37:  "Eredivisie",
    238: "Primeira Liga",
    38:  "Belgian Pro League",
    52:  "Süper Lig",
    36:  "Scottish Premiership",
    # Tier 3 — UEFA
    7:   "Champions League",
    679: "Europa League",
    17015: "Conference League",
}

TRACKED_IDS = set(TRACKED_LEAGUES.keys())


def collect_fixtures(
    events: list[dict],
    date_str: str,
    conn: sqlite3.Connection,
) -> list[dict]:
    """
    Filter SofaScore events to tracked leagues, store in DB, extract odds.
    Returns list of fixture dicts for downstream pipeline.
    """
    fixtures = []
    skipped = 0
    odds_snapshots = []

    for ev in events:
        # Filter by league
        ut = ev.get("tournament", {}).get("uniqueTournament", {})
        ut_id = ut.get("id", 0)
        if ut_id not in TRACKED_IDS:
            skipped += 1
            continue

        # Build match record
        home = ev.get("homeTeam", {})
        away = ev.get("awayTeam", {})
        status_info = ev.get("status", {})
        home_score = ev.get("homeScore", {})
        away_score = ev.get("awayScore", {})

        match = {
            "id": str(ev.get("id", "")),
            "date": date_str,
            "kickoff": str(ev.get("startTimestamp", "")),
            "home_team": home.get("name", "Unknown"),
            "away_team": away.get("name", "Unknown"),
            "league_name": ut.get("name", TRACKED_LEAGUES.get(ut_id, "Unknown")),
            "league_id": ut_id,
            "status": _map_status(status_info),
            "home_goals": home_score.get("current"),
            "away_goals": away_score.get("current"),
        }

        fixtures.append(match)

        # Extract odds if present in SofaScore data
        _extract_odds(ev, match["id"], odds_snapshots)

    if fixtures:
        upsert_matches_batch(conn, fixtures)
    if odds_snapshots:
        insert_odds_batch(conn, odds_snapshots)

    logger.info(
        f"Fixture collector: {len(fixtures)} tracked, {skipped} skipped for {date_str}"
    )
    return fixtures


def _map_status(status_info: dict) -> str:
    """Map SofaScore status to our status codes."""
    stype = status_info.get("type", "")
    if stype == "finished":
        return "FT"
    elif stype == "inprogress":
        return "LIVE"
    return "NS"


def _extract_odds(ev: dict, match_id: str, snapshots: list[dict]) -> None:
    """Extract odds from SofaScore vote/odds data if available."""
    # SofaScore sometimes includes vote percentages as a proxy
    vote = ev.get("vote", {})
    if not vote:
        return

    vote1 = vote.get("vote1", 0)
    votex = vote.get("votex", 0)
    vote2 = vote.get("vote2", 0)
    total = vote1 + votex + vote2

    if total <= 0:
        return

    # Convert crowd vote percentages → pseudo-odds (not real bookmaker odds)
    # These are weak proxies — real odds API will replace this
    for selection, vote_count in [("home", vote1), ("draw", votex), ("away", vote2)]:
        pct = vote_count / total
        if pct > 0.01:
            pseudo_odds = round(1.0 / pct, 3)
            snapshots.append({
                "match_id": match_id,
                "market": "1X2",
                "selection": selection,
                "odds": pseudo_odds,
                "bookmaker": "sofascore_vote",
                "is_opening": True,
            })


"""
Agent 1: Fixture Collector

Fetches matches from SofaScore and stores them in the database.
Extracts odds from SofaScore event data when available.
"""

import logging
import sqlite3
from src.db.match_repo import upsert_match, upsert_matches_batch
from datetime import datetime, timezone

from src.odds.store import store_crowd_votes

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
    crowd_votes = []

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

        # SofaScore crowd votes are opinions, not prices: kept apart from odds
        _extract_crowd_votes(ev, match["id"], crowd_votes)

    if fixtures:
        upsert_matches_batch(conn, fixtures)
    if crowd_votes:
        observed_at = datetime.now(timezone.utc).isoformat()
        with conn:
            for fixture_id, votes in crowd_votes:
                store_crowd_votes(conn, fixture_id, "sofascore_vote", votes, observed_at)

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


def _extract_crowd_votes(ev: dict, match_id: str, crowd_votes: list[tuple[str, dict]]) -> None:
    """Collect SofaScore 1X2 crowd votes. They are never converted into odds."""
    vote = ev.get("vote") or {}
    votes = {"home": vote.get("vote1", 0), "draw": vote.get("votex", 0), "away": vote.get("vote2", 0)}
    if sum(int(v or 0) for v in votes.values()) > 0:
        crowd_votes.append((match_id, votes))
"""Identity-checked team inputs for match analysis (2026-11-01; K43 option (b), demo only).

The legacy analysis lookup (src/ml/team_stats_db.get_team_stats) reads team history by the Poisson
profile key and the raw provider name, so it misses split labels ("La Liga" vs "LaLiga": Real Madrid's
stale 1-match "LaLiga" row instead of its current "La Liga" row), misses spellings ("Real Madrid CF"),
and substitutes other teams by substring ("Brisbane Roar FC W" -> the men's "Brisbane Roar").

This module resolves each side with team-identity-v1 (src/identity/teams.py: explicit mapping, then exact
normalised name inside the competition's own history labels, history provenance checked against the
competition's league ids, ambiguity never merged) and returns the resolved record's statistics with the
same venue transform as the probability inputs (src/identity/inputs._venue_stats). The competition is:
  * API-Football: the tracked competition for the league id, otherwise that league id with its own label;
  * football-data.org: the tracked competition for its competition code (no other leagues);
  * anything else: None, and the caller keeps its legacy behaviour.
An unresolved side means no forecast: the reason is reported and no statistics are substituted.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from src.identity.competitions import COMPETITIONS, Competition, competition_for_fixture
from src.identity.inputs import _venue_stats
from src.identity.teams import TEAM_IDENTITY_VERSION, resolve_team

FOOTBALL_DATA_CODES = {"PL": "england-premier-league", "ELC": "england-championship", "PD": "spain-la-liga",
                       "SA": "italy-serie-a", "BL1": "germany-bundesliga", "FL1": "france-ligue-1",
                       "DED": "netherlands-eredivisie", "PPL": "portugal-primeira-liga",
                       "CL": "uefa-champions-league"}
MIN_MATCHES = 5
STALE_DAYS = 21


def competition_for(provider: str | None, league_id, league_name: str = "", country: str = "",
                    league_code: str | None = None) -> Competition | None:
    if provider == "api_football":
        tracked = competition_for_fixture("api_football", league_id)
        if tracked is not None:
            return tracked
        try:
            lid = int(league_id)
        except (TypeError, ValueError):
            return None
        # Any other league: its own provider id and label; provenance is still checked against that id.
        return Competition(f"api-football-{lid}", country or "", lid, -1, (league_name,)) if league_name else None
    if provider == "football_data":
        key = FOOTBALL_DATA_CODES.get(league_code or "")
        return next((c for c in COMPETITIONS if c.key == key), None)
    return None


def _last_match(conn, res) -> str | None:
    row = conn.execute("SELECT last_match_date FROM team_state WHERE team_name_lower = ? AND league = ? "
                       "AND venue = 'overall'", (res.team_name_lower, res.history_league)).fetchone()
    return row[0] if row else None


def resolve_for_analysis(conn: sqlite3.Connection, *, provider: str | None, league_id, league_name: str,
                         country: str = "", league_code: str | None = None, home: str, away: str,
                         home_id=None, away_id=None, profile_key: str, match_date: str | None = None,
                         registry=None) -> dict | None:
    """None when the fixture's competition cannot be identified (legacy path). Otherwise
    {available, identity, home_stats, away_stats, blocking, warnings}."""
    competition = competition_for(provider, league_id, league_name, country, league_code)
    if competition is None:
        return None
    sides = {"home": (home, home_id), "away": (away, away_id)}
    res = {side: resolve_team(conn, competition, name, provider, tid, False, registry)
           for side, (name, tid) in sides.items()}
    blocking, warnings, stats = [], [], {}
    for side, r in res.items():
        label = f"{side}_team '{sides[side][0]}'"
        if not r.resolved:
            blocking.append(f"{label} has NO real data — identity {r.status}: {r.reason}")
            continue
        stats[side] = _venue_stats(conn, r, side, profile_key)
        if r.matches_played < MIN_MATCHES:
            warnings.append(f"{label} has only {r.matches_played} matches in its '{r.history_league}' history")
        last = _last_match(conn, r)
        if last and match_date:
            try:
                gap = (date.fromisoformat(match_date[:10]) - date.fromisoformat(last[:10])).days
            except ValueError:
                gap = None
            if gap is not None and gap >= STALE_DAYS:
                warnings.append(f"{label} history ends {last[:10]} ({gap} days before this match); "
                                "newer results are not in the database")
    return {"available": not blocking, "blocking": blocking, "warnings": warnings,
            "home_stats": stats.get("home"), "away_stats": stats.get("away"),
            "identity": {"version": TEAM_IDENTITY_VERSION, "competition": competition.key,
                         "home": res["home"].to_dict(), "away": res["away"].to_dict()}}

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

Cross-competition (2026-11-03): in a continental club competition a club with fewer than MIN_MATCHES verified
matches in that competition is read from its verified domestic-league record, linked by provider team id
(src/identity/club_membership.py). The Poisson profile, venue transform and models are unchanged.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from src.identity.club_membership import domestic_league, is_continental
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


def _side(conn, competition: Competition, continental: bool, name: str, provider, tid, match_date, registry):
    """(selected resolution or None, own-competition resolution, domestic details or None).

    Own competition first (unchanged team-identity-v1). In a continental club competition, when the club has
    fewer than MIN_MATCHES verified matches there, its verified domestic-league record is used instead
    (found by provider team id, src/identity/club_membership.py), as the legacy UCL feature definition does
    (src/ml/team_stats_db.py: domestic statistics as the UCL base). The two records are never averaged."""
    own = resolve_team(conn, competition, name, provider, tid, False, registry)
    if not continental or (own.resolved and (own.matches_played or 0) >= MIN_MATCHES):
        return (own if own.resolved else None), own, None
    dl = domestic_league(conn, provider, tid, match_date)
    if not dl.found:
        return (own if own.resolved else None), own, {"status": "NOT_FOUND", "reason": dl.reason,
                                                      "evidence": dl.evidence}
    dom = resolve_team(conn, dl.competition, dl.team_name, provider, tid, False, registry)
    details = {"status": dom.status, "reason": dom.reason, "competition": dl.competition.key,
               "league_id": dl.competition.api_football_id, "provider_name": dl.team_name,
               "resolution": dom.to_dict(), "membership": dl.evidence}
    if dom.resolved and (not own.resolved or (dom.matches_played or 0) > (own.matches_played or 0)):
        return dom, own, details
    return (own if own.resolved else None), own, details


def resolve_for_analysis(conn: sqlite3.Connection, *, provider: str | None, league_id, league_name: str,
                         country: str = "", league_code: str | None = None, home: str, away: str,
                         home_id=None, away_id=None, profile_key: str, match_date: str | None = None,
                         registry=None) -> dict | None:
    """None when the fixture's competition cannot be identified (legacy path). Otherwise
    {available, identity, home_stats, away_stats, blocking, warnings}.

    `match_date` is the kick-off date (UTC). A selected record whose last match is on or after that date
    would put the match itself (or later ones) into its own inputs: the side is blocked, never used."""
    competition = competition_for(provider, league_id, league_name, country, league_code)
    if competition is None:
        return None
    continental = is_continental(competition, country)
    sides = {"home": (home, home_id), "away": (away, away_id)}
    blocking, warnings, stats, ident = [], [], {}, {}
    for side, (name, tid) in sides.items():
        label = f"{side}_team '{name}'"
        chosen, own, domestic = _side(conn, competition, continental, name, provider, tid, match_date, registry)
        source = "domestic_league" if chosen is not None and chosen is not own else "competition"
        ident[side] = own.to_dict()
        ident[f"{side}_history"] = {"source": source, "record": None if chosen is None else {
            "team_name_lower": chosen.team_name_lower, "history_league": chosen.history_league,
            "matches_played": chosen.matches_played, "competition": chosen.competition_key,
            "canonical_team_id": chosen.canonical_team_id}, "domestic": domestic}
        if chosen is None:
            why = f"identity {own.status}: {own.reason}"
            if continental:
                why += ("; domestic league: " + (domestic["reason"] or domestic["status"]) if domestic
                        else "")
            blocking.append(f"{label} has NO real data — {why}")
            continue
        last = _last_match(conn, chosen)
        if last and match_date and last[:10] >= match_date[:10]:
            blocking.append(f"{label} history record '{chosen.history_league}' already includes results dated "
                            f"{last[:10]}, on or after this match's kick-off date {match_date[:10]}; "
                            "no point-in-time record is available, so it is not used")
            continue
        stats[side] = _venue_stats(conn, chosen, side, profile_key)
        if source == "domestic_league":
            warnings.append(f"{label} uses its domestic '{chosen.history_league}' history "
                            f"({chosen.matches_played} matches); it has {own.matches_played or 0} verified "
                            f"'{competition.key}' matches")
        if chosen.matches_played < MIN_MATCHES:
            warnings.append(f"{label} has only {chosen.matches_played} matches in its '{chosen.history_league}' history")
        if last and match_date:
            try:
                gap = (date.fromisoformat(match_date[:10]) - date.fromisoformat(last[:10])).days
            except ValueError:
                gap = None
            if gap is not None and gap >= STALE_DAYS:
                warnings.append(f"{label} history ends {last[:10]} ({gap} days before this match); "
                                "newer results are not in the database")
    recs = [ident[f"{s}_history"]["record"] for s in ("home", "away")]
    if not blocking and all(recs) and recs[0]["competition"] != recs[1]["competition"]:
        warnings.append(f"home and away statistics come from different competitions ('{recs[0]['history_league']}', "
                        f"'{recs[1]['history_league']}'); the model applies no league-strength adjustment")
    return {"available": not blocking, "blocking": blocking, "warnings": warnings,
            "home_stats": stats.get("home"), "away_stats": stats.get("away"),
            "identity": {"version": TEAM_IDENTITY_VERSION, "competition": competition.key,
                         "cross_competition": continental, **ident}}

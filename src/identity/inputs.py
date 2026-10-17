"""Identity-checked probability inputs (K43). Same structure and same mathematics as
probability_engine.resolve_probability_inputs, but team history comes only from the
resolved team_state record, and an unresolved team raises instead of falling back.

Differences from the legacy resolver, all on the input side:
  * the history record is chosen by src/identity/teams.py (exact normalised name,
    competition-verified), not by the Poisson profile key. The legacy resolver
    looked Championship teams up under "Premier League" and La Liga teams under
    "LaLiga", missed their real rows and fell back to hard-coded tables or
    hash-generated statistics;
  * no hard-coded season tables (whose matches_played=20 claims history that was
    never observed), no substring matches, no hash statistics;
  * the conn passed in is used (the legacy path uses the global get_db(), K39).

Unchanged: the Poisson profile key (LEAGUE_KEY_MAP), the venue transform of
team_stats_db.get_team_stats priority 1 (overall row x1.12/0.88, neutral venues
decided from the same profile key the legacy path used), and the
estimate_probabilities_from_inputs mathematics. Corners are not seeded from the
static tables (they do not feed 1X2, O/U 2.5 or BTTS).
"""
from __future__ import annotations

import sqlite3
from dataclasses import asdict

from src.identity.competitions import Competition, competition_for_fixture
from src.identity.teams import TEAM_IDENTITY_VERSION, TeamResolution, resolve_team


class UnresolvedTeamIdentity(RuntimeError):
    def __init__(self, resolutions: dict[str, TeamResolution]):
        self.resolutions = resolutions
        bad = {side: f"{r.provider_name}: {r.status}" for side, r in resolutions.items() if not r.resolved}
        super().__init__(f"team identity unresolved: {bad}")


def _exact_state(conn, team_name_lower: str, league: str):
    """The resolved record by exact key. Never src/db/team_state.get_team_state: that function falls
    back to an unordered SQL LIKE substring match (another team's row) when the exact row is missing."""
    return conn.execute(
        """SELECT rolling_scored, rolling_conceded, rolling_corners, rolling_cards, matches_played, form_last5
           FROM team_state WHERE team_name_lower = ? AND league = ? AND venue = 'overall'""",
        (team_name_lower, league),
    ).fetchone()


def _venue_stats(conn, res: TeamResolution, venue: str, profile_key: str):
    from src.ml.team_stats_db import TeamVenueStats, is_neutral_tournament

    state = _exact_state(conn, res.team_name_lower, res.history_league)
    if state is None or state[4] != res.matches_played or state[4] < 1:
        raise RuntimeError(f"history record changed during resolution: {res.team_name_lower}/{res.history_league}")
    scored_raw, conceded_raw, corners, cards, played, form = state
    if is_neutral_tournament(profile_key):
        scored, conceded = round(scored_raw, 2), round(conceded_raw, 2)
    elif venue == "home":
        scored, conceded = round(scored_raw * 1.12, 2), round(conceded_raw * 0.88, 2)
    else:
        scored, conceded = round(scored_raw * 0.88, 2), round(conceded_raw * 1.12, 2)
    return TeamVenueStats(scored=scored, conceded=conceded, corners=round(corners, 1),
                          cards=round(cards, 1), matches_played=played, form_last5=form)


def resolve_identity(conn: sqlite3.Connection, match: dict, competition: Competition | None = None,
                     remember: bool = False, registry=None) -> dict[str, TeamResolution]:
    competition = competition or competition_for_fixture(match.get("provider"), match.get("league_id"))
    provider = match.get("provider")
    return {
        "home": resolve_team(conn, competition, match["home_team"], provider, match.get("home_team_id"), remember,
                             registry),
        "away": resolve_team(conn, competition, match["away_team"], provider, match.get("away_team_id"), remember,
                             registry),
    }


def resolve_probability_inputs_v2(conn: sqlite3.Connection, match: dict, competition: Competition | None = None,
                                  remember: bool = False, registry=None) -> dict:
    from src.engine.probability_engine import LEAGUE_KEY_MAP

    resolutions = resolve_identity(conn, match, competition, remember, registry)
    if not all(r.resolved for r in resolutions.values()):
        raise UnresolvedTeamIdentity(resolutions)
    league_name = match.get("league_name", "")
    profile_key = LEAGUE_KEY_MAP.get(league_name, "default")
    return {
        "home_team": match["home_team"],
        "away_team": match["away_team"],
        "league_name": league_name,
        "country": match.get("country", ""),
        "league_key": profile_key,
        "home_stats": asdict(_venue_stats(conn, resolutions["home"], "home", profile_key)),
        "away_stats": asdict(_venue_stats(conn, resolutions["away"], "away", profile_key)),
        "identity": {"version": TEAM_IDENTITY_VERSION,
                     "home": resolutions["home"].to_dict(), "away": resolutions["away"].to_dict()},
    }


LEGACY_INPUTS = "legacy"
IDENTITY_INPUTS = "identity-v1"


def inputs_version() -> str:
    """PROBABILITY_INPUTS: 'legacy' (default; production today) or 'identity-v1'. Anything else is refused."""
    import os

    value = (os.getenv("PROBABILITY_INPUTS") or LEGACY_INPUTS).strip().lower()
    if value not in (LEGACY_INPUTS, IDENTITY_INPUTS):
        raise ValueError(f"unknown PROBABILITY_INPUTS {value!r}")
    return value


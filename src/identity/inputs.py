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
    if value not in (LEGACY_INPUTS, IDENTITY_INPUTS, "identity-v1-rh1"):
        raise ValueError(f"unknown PROBABILITY_INPUTS {value!r}")
    return value



IDENTITY_RH1_INPUTS = "identity-v1-rh1"


def _rh1_resolution(match: dict, side: str, competition: Competition | None, state) -> TeamResolution:
    from src.identity.teams import NO_HISTORY, RESOLVED, UNKNOWN_COMPETITION

    name = match[f"{side}_team"]
    base = dict(provider=match.get("provider"),
                provider_team_id=None if match.get(f"{side}_team_id") is None else str(match.get(f"{side}_team_id")),
                provider_name=name, competition_key=competition.key if competition else None)
    if competition is None:
        return TeamResolution(UNKNOWN_COMPETITION, **base, reason="fixture is not in a tracked competition")
    ok = state.status == "RESOLVED" and state.matches_played >= 1
    res = TeamResolution(RESOLVED if ok else (state.status if state.status != "RESOLVED" else NO_HISTORY), **base,
                         team_name_lower=state.team_key if ok else None,
                         history_league=f"rh1:{competition.key}" if ok else None,
                         matches_played=state.matches_played, source="rh1_competition_fixtures",
                         reason="" if ok else f"{state.status}: no observed fixture in {competition.key} before the decision",
                         evidence={"as_of_fixture_id": state.as_of_fixture_id, "available_from": state.available_from,
                                   "raw_names": list(state.raw_names)})
    res.competition_ids = {"api_football": competition.api_football_id, "sofascore": competition.sofascore_id}
    res.canonical_team_id = state.canonical_team_id if ok else None
    return res


def resolve_probability_inputs_rh1(conn: sqlite3.Connection, match: dict, decided_at,
                                   competition: Competition | None = None) -> dict:
    """identity-v1 rules on the reconstructed point-in-time history (src/history/reconstruct.py).

    Same mathematics as the legacy priority-1 path (overall rolling averages x1.12/0.88, neutral venue from
    the profile key); history only from the competition's own fixtures available at `decided_at`."""
    from src.engine.probability_engine import LEAGUE_KEY_MAP
    from src.history.reconstruct import HISTORY_VERSION, state_at
    from src.ml.team_stats_db import TeamVenueStats, is_neutral_tournament

    competition = competition or competition_for_fixture(match.get("provider"), match.get("league_id"))
    states = {s: (state_at(conn, competition, match[f"{s}_team"], decided_at) if competition else None)
              for s in ("home", "away")}
    resolutions = {s: _rh1_resolution(match, s, competition, states[s]) for s in ("home", "away")}
    if not all(r.resolved for r in resolutions.values()):
        raise UnresolvedTeamIdentity(resolutions)
    profile_key = LEAGUE_KEY_MAP.get(match.get("league_name", ""), "default")
    neutral = is_neutral_tournament(profile_key)

    def venue_stats(state, venue):
        if neutral:
            sc, co = round(state.rolling_scored, 2), round(state.rolling_conceded, 2)
        elif venue == "home":
            sc, co = round(state.rolling_scored * 1.12, 2), round(state.rolling_conceded * 0.88, 2)
        else:
            sc, co = round(state.rolling_scored * 0.88, 2), round(state.rolling_conceded * 1.12, 2)
        return TeamVenueStats(scored=sc, conceded=co, corners=5.0, cards=2.1, matches_played=state.matches_played,
                              form_last5=state.form_last5 if state.form_last5 is not None else 0.5)

    from datetime import datetime as _dt
    from src.odds.model import iso_utc

    return {
        "home_team": match["home_team"], "away_team": match["away_team"],
        "league_name": match.get("league_name", ""), "country": match.get("country", ""), "league_key": profile_key,
        "home_stats": asdict(venue_stats(states["home"], "home")),
        "away_stats": asdict(venue_stats(states["away"], "away")),
        "identity": {"version": TEAM_IDENTITY_VERSION, "history_version": HISTORY_VERSION,
                     "decided_at": iso_utc(decided_at if isinstance(decided_at, _dt) else decided_at),
                     "home": resolutions["home"].to_dict(), "away": resolutions["away"].to_dict()},
    }

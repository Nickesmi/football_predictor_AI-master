"""Tracked competitions with their provider ids and the team_state labels that hold their history.

team_state is keyed by a competition *label* without a country ("Premier League"
holds rows from about 25 different competitions). A label therefore never
identifies a competition on its own; the provider league ids do. A team's
history row is only usable when every stored fixture behind that label and
team name belongs to one of the competition's ids (see teams.py).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Competition:
    key: str
    country: str
    api_football_id: int
    sofascore_id: int
    history_labels: tuple[str, ...]

    @property
    def league_ids(self) -> frozenset[int]:
        return frozenset({self.api_football_id, self.sofascore_id})


COMPETITIONS: tuple[Competition, ...] = (
    Competition("england-premier-league", "England", 39, 17, ("Premier League",)),
    Competition("england-championship", "England", 40, 18, ("Championship",)),
    Competition("spain-la-liga", "Spain", 140, 8, ("La Liga", "LaLiga")),
    Competition("italy-serie-a", "Italy", 135, 23, ("Serie A",)),
    Competition("germany-bundesliga", "Germany", 78, 35, ("Bundesliga",)),
    Competition("france-ligue-1", "France", 61, 34, ("Ligue 1",)),
    Competition("netherlands-eredivisie", "Netherlands", 88, 37, ("Eredivisie", "VriendenLoterij Eredivisie")),
    Competition("portugal-primeira-liga", "Portugal", 94, 238,
                ("Primeira Liga", "Liga Portugal Betclic", "Liga Portugal")),
    Competition("belgium-pro-league", "Belgium", 144, 38, ("Jupiler Pro League", "Pro League", "Belgian Pro League")),
    Competition("turkey-super-lig", "Turkey", 203, 52, ("Süper Lig", "Trendyol Süper Lig")),
    Competition("scotland-premiership", "Scotland", 179, 36, ("Premiership", "Scottish Premiership")),
    Competition("uefa-champions-league", "Europe", 2, 7, ("UEFA Champions League", "Champions League")),
    Competition("uefa-europa-league", "Europe", 3, 679, ("UEFA Europa League", "Europa League")),
    Competition("uefa-conference-league", "Europe", 848, 17015,
                ("UEFA Europa Conference League", "UEFA Conference League", "Conference League")),
)

# matches.provider -> which id space its league_id uses
_ID_SPACE = {"api_football": "api_football", "matches_db": "api_football", "sofascore": "sofascore"}


def competition_for_fixture(provider: str | None, league_id) -> Competition | None:
    """The tracked competition of a stored fixture, by provider id space. Names alone are never used."""
    space = _ID_SPACE.get(provider or "")
    try:
        lid = int(league_id)
    except (TypeError, ValueError):
        return None
    for c in COMPETITIONS:
        if (space == "api_football" and c.api_football_id == lid) or (space == "sofascore" and c.sofascore_id == lid):
            return c
    return None

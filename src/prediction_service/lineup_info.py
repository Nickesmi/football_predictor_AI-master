"""
Lineup / injury availability handling (Phase 3 §6).

Hard rule: UNKNOWN must never be silently converted into an assumption
("probably fine", "assume full strength"). This module only ever produces
a confidence PENALTY from uncertainty — it never fabricates a player's
availability.

This environment has no live lineup/injury data source wired up (same
network restriction documented in AUDIT_REPORT.md — the provider APIs the
legacy system uses are blocked here). So by default, with no LineupInfo
supplied, every match is treated as fully UNKNOWN and receives the
DEFAULT_UNKNOWN_PENALTY below — not zero, because "we have no idea" is a
real uncertainty, not an assumption of normalcy. A future session wiring a
real lineup provider in would pass a populated LineupInfo instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Availability(str, Enum):
    CONFIRMED = "CONFIRMED"
    PROBABLE = "PROBABLE"
    UNKNOWN = "UNKNOWN"


@dataclass
class PlayerStatus:
    player_name: str
    availability: Availability
    is_key_player: bool = False


@dataclass
class LineupInfo:
    home_team: str
    away_team: str
    source: str                      # where this came from — never "" if real
    retrieved_at: str                # ISO timestamp
    home_players: list[PlayerStatus] = field(default_factory=list)
    away_players: list[PlayerStatus] = field(default_factory=list)


# Points, out of the confidence engine's 100, subtracted per situation.
DEFAULT_UNKNOWN_PENALTY = 8.0     # no lineup data source at all for this match
_PER_KEY_PLAYER_UNKNOWN = 4.0
_PER_KEY_PLAYER_PROBABLE = 1.5
_PER_NON_KEY_UNKNOWN = 0.5
_MAX_PENALTY = 15.0


def compute_lineup_uncertainty_penalty(lineup_info: Optional[LineupInfo]) -> tuple[float, list[str]]:
    """Returns (penalty_0_to_15, human_readable_reasons)."""
    if lineup_info is None:
        return DEFAULT_UNKNOWN_PENALTY, [
            "no lineup/injury data source available for this match — treating as fully UNKNOWN, "
            "not assuming full-strength squads"
        ]

    penalty = 0.0
    reasons = []
    for side, players in (("home", lineup_info.home_players), ("away", lineup_info.away_players)):
        for p in players:
            if p.availability == Availability.CONFIRMED:
                continue
            if p.availability == Availability.PROBABLE:
                add = _PER_KEY_PLAYER_PROBABLE if p.is_key_player else _PER_KEY_PLAYER_PROBABLE / 2
                penalty += add
                reasons.append(f"{side}: {p.player_name} is PROBABLE ({'key player' if p.is_key_player else 'squad player'})")
            elif p.availability == Availability.UNKNOWN:
                add = _PER_KEY_PLAYER_UNKNOWN if p.is_key_player else _PER_NON_KEY_UNKNOWN
                penalty += add
                reasons.append(f"{side}: {p.player_name} availability UNKNOWN ({'key player' if p.is_key_player else 'squad player'})")

    penalty = min(penalty, _MAX_PENALTY)
    if not reasons:
        reasons.append("lineup data present and all tracked players CONFIRMED")
    return round(penalty, 2), reasons

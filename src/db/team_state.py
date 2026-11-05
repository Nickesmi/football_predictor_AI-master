"""
Team State Repository — Phase 4 Live Adaptive Pipeline

CRUD operations for the team_state table.
This is the persistent, evolving representation of each team's
current strength, form, and fitness.

Every query for team stats now checks here FIRST, falling back
to the hardcoded defaults only for cold-start teams.
"""

import sqlite3
import logging
from datetime import date, datetime
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger("football_predictor")


@dataclass
class TeamState:
    """Full live state for one team at one venue."""
    team_name: str
    league: str
    venue: str

    # Ratings
    elo: float = 1500.0
    attack_rating: float = 1.0
    defense_rating: float = 1.0

    # Rolling averages
    rolling_scored: float = 1.2
    rolling_conceded: float = 1.2
    rolling_xg: float = 1.2
    rolling_xga: float = 1.2
    rolling_corners: float = 5.0
    rolling_cards: float = 2.0

    # Form
    form_last5: float = 0.5
    form_last10: float = 0.5
    win_streak: int = 0
    unbeaten_streak: int = 0

    # Fatigue
    matches_last_14d: int = 0
    rest_days: int = 7

    # Meta
    matches_played: int = 0
    last_match_date: Optional[str] = None
    last_match_id: Optional[str] = None


def get_team_state(
    conn: sqlite3.Connection,
    team_name: str,
    league: str,
    venue: str,
) -> Optional[TeamState]:
    """Retrieve a team's current state from the database.

    Args:
        conn: database connection
        team_name: team name (case-insensitive lookup)
        league: league name
        venue: 'home' | 'away' | 'overall'

    Returns:
        TeamState if found, None if the team has no history yet.
    """
    name_lower = team_name.lower().strip()

    row = conn.execute(
        """SELECT team_name, league, venue, elo,
                  attack_rating, defense_rating,
                  rolling_scored, rolling_conceded,
                  rolling_xg, rolling_xga,
                  rolling_corners, rolling_cards,
                  form_last5, form_last10,
                  win_streak, unbeaten_streak,
                  matches_last_14d, rest_days,
                  matches_played, last_match_date, last_match_id
           FROM team_state
           WHERE team_name_lower = ? AND league = ? AND venue = ?""",
        (name_lower, league, venue),
    ).fetchone()

    if row is None:
        # Try fuzzy: see if name_lower is contained in any stored team
        row = conn.execute(
            """SELECT team_name, league, venue, elo,
                      attack_rating, defense_rating,
                      rolling_scored, rolling_conceded,
                      rolling_xg, rolling_xga,
                      rolling_corners, rolling_cards,
                      form_last5, form_last10,
                      win_streak, unbeaten_streak,
                      matches_last_14d, rest_days,
                      matches_played, last_match_date, last_match_id
               FROM team_state
               WHERE (team_name_lower LIKE ? OR ? LIKE '%' || team_name_lower || '%')
                     AND league = ? AND venue = ?
               LIMIT 1""",
            (f"%{name_lower}%", name_lower, league, venue),
        ).fetchone()

    if row is None:
        return None

    return TeamState(
        team_name=row[0],
        league=row[1],
        venue=row[2],
        elo=row[3],
        attack_rating=row[4],
        defense_rating=row[5],
        rolling_scored=row[6],
        rolling_conceded=row[7],
        rolling_xg=row[8],
        rolling_xga=row[9],
        rolling_corners=row[10],
        rolling_cards=row[11],
        form_last5=row[12],
        form_last10=row[13],
        win_streak=row[14],
        unbeaten_streak=row[15],
        matches_last_14d=row[16],
        rest_days=row[17],
        matches_played=row[18],
        last_match_date=row[19],
        last_match_id=row[20],
    )


def get_team_state_exact(conn: sqlite3.Connection, team_name: str, league: str, venue: str) -> Optional[TeamState]:
    """The record with exactly this (lower-cased) name, label and venue, or None. Never a substring match:
    get_team_state's LIKE fallback can return another club's record ("Inter" -> "Internacional")."""
    row = conn.execute(
        """SELECT team_name, league, venue, elo, attack_rating, defense_rating, rolling_scored, rolling_conceded,
                  rolling_xg, rolling_xga, rolling_corners, rolling_cards, form_last5, form_last10, win_streak,
                  unbeaten_streak, matches_last_14d, rest_days, matches_played, last_match_date, last_match_id
           FROM team_state WHERE team_name_lower = ? AND league = ? AND venue = ?""",
        (team_name.lower().strip(), league, venue),
    ).fetchone()
    if row is None:
        return None
    names = ("team_name", "league", "venue", "elo", "attack_rating", "defense_rating", "rolling_scored",
             "rolling_conceded", "rolling_xg", "rolling_xga", "rolling_corners", "rolling_cards", "form_last5",
             "form_last10", "win_streak", "unbeaten_streak", "matches_last_14d", "rest_days", "matches_played",
             "last_match_date", "last_match_id")
    return TeamState(**dict(zip(names, tuple(row))))


def upsert_team_state(conn: sqlite3.Connection, state: TeamState) -> None:
    """Insert or update a team's state in the database.

    Uses SQLite UPSERT (INSERT ... ON CONFLICT ... DO UPDATE).
    """
    name_lower = state.team_name.lower().strip()

    conn.execute(
        """INSERT INTO team_state
               (team_name, team_name_lower, league, venue,
                elo, attack_rating, defense_rating,
                rolling_scored, rolling_conceded,
                rolling_xg, rolling_xga,
                rolling_corners, rolling_cards,
                form_last5, form_last10,
                win_streak, unbeaten_streak,
                matches_last_14d, rest_days,
                matches_played, last_match_date, last_match_id,
                updated_at)
           VALUES (?, ?, ?, ?,
                   ?, ?, ?,
                   ?, ?,
                   ?, ?,
                   ?, ?,
                   ?, ?,
                   ?, ?,
                   ?, ?,
                   ?, ?, ?,
                   CURRENT_TIMESTAMP)
           ON CONFLICT(team_name_lower, league, venue)
           DO UPDATE SET
               elo = excluded.elo,
               attack_rating = excluded.attack_rating,
               defense_rating = excluded.defense_rating,
               rolling_scored = excluded.rolling_scored,
               rolling_conceded = excluded.rolling_conceded,
               rolling_xg = excluded.rolling_xg,
               rolling_xga = excluded.rolling_xga,
               rolling_corners = excluded.rolling_corners,
               rolling_cards = excluded.rolling_cards,
               form_last5 = excluded.form_last5,
               form_last10 = excluded.form_last10,
               win_streak = excluded.win_streak,
               unbeaten_streak = excluded.unbeaten_streak,
               matches_last_14d = excluded.matches_last_14d,
               rest_days = excluded.rest_days,
               matches_played = excluded.matches_played,
               last_match_date = excluded.last_match_date,
               last_match_id = excluded.last_match_id,
               updated_at = CURRENT_TIMESTAMP""",
        (state.team_name, name_lower, state.league, state.venue,
         state.elo, state.attack_rating, state.defense_rating,
         state.rolling_scored, state.rolling_conceded,
         state.rolling_xg, state.rolling_xga,
         state.rolling_corners, state.rolling_cards,
         state.form_last5, state.form_last10,
         state.win_streak, state.unbeaten_streak,
         state.matches_last_14d, state.rest_days,
         state.matches_played, state.last_match_date, state.last_match_id),
    )
    conn.commit()


def get_all_team_states(conn: sqlite3.Connection, league: str = None) -> list[TeamState]:
    """Get all team states, optionally filtered by league."""
    if league:
        rows = conn.execute(
            "SELECT * FROM team_state WHERE league = ? ORDER BY elo DESC",
            (league,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM team_state ORDER BY league, elo DESC"
        ).fetchall()

    return [TeamState(
        team_name=r["team_name"],
        league=r["league"],
        venue=r["venue"],
        elo=r["elo"],
        attack_rating=r["attack_rating"],
        defense_rating=r["defense_rating"],
        rolling_scored=r["rolling_scored"],
        rolling_conceded=r["rolling_conceded"],
        rolling_xg=r["rolling_xg"],
        rolling_xga=r["rolling_xga"],
        rolling_corners=r["rolling_corners"],
        rolling_cards=r["rolling_cards"],
        form_last5=r["form_last5"],
        form_last10=r["form_last10"],
        win_streak=r["win_streak"],
        unbeaten_streak=r["unbeaten_streak"],
        matches_last_14d=r["matches_last_14d"],
        rest_days=r["rest_days"],
        matches_played=r["matches_played"],
        last_match_date=r["last_match_date"],
        last_match_id=r["last_match_id"],
    ) for r in rows]


def get_team_count(conn: sqlite3.Connection) -> int:
    """Get the number of unique teams being tracked."""
    return conn.execute(
        "SELECT COUNT(DISTINCT team_name_lower) FROM team_state"
    ).fetchone()[0]


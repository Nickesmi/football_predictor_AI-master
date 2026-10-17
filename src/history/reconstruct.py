"""Deterministic reconstruction of team history per competition id (M3.3, history version rh1).

Why: team_state is keyed by a country-less label and updated through
get_team_state, whose LIKE fallback can write one club's result into another
club's row. Its histories mix competitions and clubs (K49, K54, K55) and it is
a "now" snapshot, so it cannot reproduce an earlier prediction without leakage.

What: for each tracked competition, every finished stored fixture whose
provider league id belongs to that competition (and nothing else) is replayed in
kickoff order with exactly the production update rule
(src/engine/live_updater._update_rolling: new = round(0.85*old + 0.15*goals, 3),
start 1.2/1.2; form_last5 = W3/D1/L0 points of the last 5 results / 15). This is a
data-only reconstruction: same mathematics, different (verified) inputs.

Identity: a team is (competition, normalised name) — src/identity/teams.normalise_team,
which keeps youth/reserve/women markers. The same real match stored by several
feeds counts once (key: competition, date, home key, away key); every source
fixture id is kept. A key that appears in two different fixtures on one date is a
collision (two clubs normalised together) and is excluded.

Point in time: every state row records the fixture it came from and
available_from = naive kickoff (or end of the matchday) + 3 h, read as UTC. Stored
fixture times are local (+03:30), so this is never earlier than the true end of the
match. A lookup for decision time T uses only rows with available_from <= T.
Unknown teams get no row; matches_played counts observed fixtures only.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.identity.competitions import COMPETITIONS, Competition, competition_for_fixture
from src.identity.teams import normalise_team
from src.odds.model import InvalidQuote, iso_utc

HISTORY_VERSION = "rh1"
DECAY = 0.85                 # src/engine/live_updater.ROLLING_DECAY
START = (1.2, 1.2)           # src/engine/live_updater.OVERALL_DEFAULTS
AVAILABILITY_MARGIN = timedelta(hours=3)
PROVIDER_PREFERENCE = {"api_football": 0, "matches_db": 1, "sofascore": 2}

SCHEMA = """
CREATE TABLE IF NOT EXISTS team_history_rh1 (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    competition_key     TEXT NOT NULL,
    team_key            TEXT NOT NULL,
    canonical_team_id   TEXT NOT NULL,
    sequence            INTEGER NOT NULL,
    fixture_id          TEXT NOT NULL,
    source_fixture_ids  TEXT NOT NULL,
    fixture_date        TEXT NOT NULL,
    kickoff_naive       TEXT NOT NULL,
    available_from      TEXT NOT NULL,
    venue               TEXT NOT NULL,
    goals_for           INTEGER NOT NULL,
    goals_against       INTEGER NOT NULL,
    rolling_scored      REAL NOT NULL,
    rolling_conceded    REAL NOT NULL,
    matches_played      INTEGER NOT NULL,
    form_last5          REAL,
    history_version     TEXT NOT NULL,
    UNIQUE (competition_key, team_key, sequence)
);
CREATE INDEX IF NOT EXISTS idx_team_history_rh1 ON team_history_rh1 (competition_key, team_key, available_from);
CREATE TABLE IF NOT EXISTS team_history_rh1_keys (
    competition_key     TEXT NOT NULL,
    team_key            TEXT NOT NULL,
    canonical_team_id   TEXT NOT NULL,
    status              TEXT NOT NULL,
    raw_names           TEXT NOT NULL,
    fixtures            INTEGER NOT NULL,
    reason              TEXT NOT NULL,
    history_version     TEXT NOT NULL,
    PRIMARY KEY (competition_key, team_key)
);
"""

RESOLVED = "RESOLVED"
COLLISION = "AMBIGUOUS_COLLISION"


def canonical_id(competition_key: str, team_key: str) -> str:
    return "cth-" + hashlib.sha256(f"{competition_key}\x1f{team_key}".encode()).hexdigest()[:16]


def _kickoff(date_text: str, kickoff_text) -> datetime:
    """Naive kickoff. 'HH:MM' is local (+03:30) and kept naive; epoch/ISO with zone become UTC (naive);
    unknown time = end of the matchday. Read as UTC, every variant is no earlier than the true time."""
    day = str(date_text)[:10]
    k = str(kickoff_text or "").strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", k):
        hh, mm = k.split(":")
        return datetime.fromisoformat(f"{day} {int(hh):02d}:{mm}")
    if k:
        try:
            return datetime.fromisoformat(iso_utc(k)).replace(tzinfo=None)
        except InvalidQuote:
            pass
    return datetime.fromisoformat(f"{day} 23:59")


def _points(gf: int, ga: int) -> float:
    return 3.0 if gf > ga else 1.0 if gf == ga else 0.0


@dataclass
class _Fixture:
    competition: Competition
    date: str
    kickoff: datetime
    home_key: str
    away_key: str
    home_name: str
    away_name: str
    home_goals: int
    away_goals: int
    ids: list


def _finished_fixtures(conn: sqlite3.Connection) -> dict[tuple, _Fixture]:
    rows = conn.execute(
        """SELECT id, date, kickoff, home_team, away_team, league_id, provider, home_goals, away_goals
           FROM matches WHERE home_goals IS NOT NULL AND away_goals IS NOT NULL
             AND COALESCE(status, 'FT') IN ('FT', 'AET', 'PEN')"""
    ).fetchall()
    merged: dict[tuple, _Fixture] = {}
    for r in sorted(rows, key=lambda r: (PROVIDER_PREFERENCE.get(r[6], 9), str(r[0]))):
        comp = competition_for_fixture(r[6], r[5])
        if comp is None:
            continue
        hk, ak = normalise_team(r[3] or ""), normalise_team(r[4] or "")
        if not hk or not ak:
            continue
        key = (comp.key, str(r[1])[:10], hk, ak)
        if key in merged:
            merged[key].ids.append(f"{r[6]}:{r[0]}")
            continue
        merged[key] = _Fixture(comp, str(r[1])[:10], _kickoff(r[1], r[2]), hk, ak, r[3], r[4],
                               int(r[7]), int(r[8]), [f"{r[6]}:{r[0]}"])
    return merged


def reconstruct(conn: sqlite3.Connection) -> dict:
    """Rebuild team_history_rh1 from scratch in `conn` (an isolated DB). Returns a summary."""
    conn.executescript(SCHEMA)
    with conn:
        conn.execute("DELETE FROM team_history_rh1")
        conn.execute("DELETE FROM team_history_rh1_keys")
    fixtures = _finished_fixtures(conn)

    # Collision detection: one key in two different fixtures on the same date
    appearances: dict[tuple, set] = defaultdict(set)
    raw_names: dict[tuple, set] = defaultdict(set)
    for f in fixtures.values():
        for key, name in ((f.home_key, f.home_name), (f.away_key, f.away_name)):
            appearances[(f.competition.key, key, f.date)].add((f.home_key, f.away_key))
            raw_names[(f.competition.key, key)].add(name)
    collided = {(c, k) for (c, k, _d), fx in appearances.items() if len(fx) > 1}

    state: dict[tuple, dict] = {}
    rows = []
    for f in sorted(fixtures.values(), key=lambda f: (f.kickoff, f.competition.key, f.home_key, f.away_key)):
        for key, venue, gf, ga in ((f.home_key, "home", f.home_goals, f.away_goals),
                                   (f.away_key, "away", f.away_goals, f.home_goals)):
            tk = (f.competition.key, key)
            if tk in collided:
                continue
            s = state.setdefault(tk, {"scored": START[0], "conceded": START[1], "played": 0, "results": []})
            s["scored"] = round(DECAY * s["scored"] + (1 - DECAY) * gf, 3)
            s["conceded"] = round(DECAY * s["conceded"] + (1 - DECAY) * ga, 3)
            s["played"] += 1
            s["results"].insert(0, _points(gf, ga))
            form = round(sum(s["results"][:5]) / 15.0, 3) if len(s["results"]) >= 5 else None
            rows.append((f.competition.key, key, canonical_id(*tk), s["played"], f.ids[0], json.dumps(sorted(f.ids)),
                         f.date, f.kickoff.isoformat(), iso_utc(f.kickoff + AVAILABILITY_MARGIN),
                         venue, gf, ga, s["scored"], s["conceded"], s["played"], form, HISTORY_VERSION))
    keys = []
    for tk, names in raw_names.items():
        status = COLLISION if tk in collided else RESOLVED
        reason = "two fixtures on one date normalise to this key" if tk in collided else ""
        n = state.get(tk, {}).get("played", 0)
        keys.append((tk[0], tk[1], canonical_id(*tk), status, json.dumps(sorted(names), ensure_ascii=False), n,
                     reason, HISTORY_VERSION))
    with conn:
        conn.executemany(f"INSERT INTO team_history_rh1 (competition_key, team_key, canonical_team_id, sequence, "
                         f"fixture_id, source_fixture_ids, fixture_date, kickoff_naive, available_from, venue, "
                         f"goals_for, goals_against, rolling_scored, rolling_conceded, matches_played, form_last5, "
                         f"history_version) VALUES ({','.join('?' * 17)})", rows)
        conn.executemany("INSERT INTO team_history_rh1_keys VALUES (?, ?, ?, ?, ?, ?, ?, ?)", keys)
    return {"history_version": HISTORY_VERSION, "fixtures_used": len(fixtures),
            "source_rows_merged": sum(len(f.ids) for f in fixtures.values()),
            "teams": len(keys), "collisions": len(collided), "state_rows": len(rows),
            "by_competition": {c.key: sum(1 for f in fixtures.values() if f.competition.key == c.key)
                               for c in COMPETITIONS}}


@dataclass(frozen=True)
class HistoryState:
    competition_key: str
    team_key: str
    canonical_team_id: str
    status: str
    rolling_scored: float | None
    rolling_conceded: float | None
    matches_played: int
    form_last5: float | None
    as_of_fixture_id: str | None
    available_from: str | None
    raw_names: tuple


def state_at(conn: sqlite3.Connection, competition: Competition, provider_name: str, at: datetime) -> HistoryState:
    """Point-in-time state of a team: only fixtures available at `at` (UTC). Never another team's row."""
    key = normalise_team(provider_name)
    try:
        meta = conn.execute("SELECT status, raw_names FROM team_history_rh1_keys WHERE competition_key = ? "
                            "AND team_key = ?", (competition.key, key)).fetchone()
    except sqlite3.OperationalError:  # history never rebuilt in this DB: fail closed
        return HistoryState(competition.key, key, canonical_id(competition.key, key), "HISTORY_NOT_BUILT",
                            None, None, 0, None, None, None, ())
    status = meta[0] if meta else "UNRESOLVED_NO_HISTORY"
    names = tuple(json.loads(meta[1])) if meta else ()
    row = None
    if status == RESOLVED:
        row = conn.execute(
            """SELECT rolling_scored, rolling_conceded, matches_played, form_last5, fixture_id, available_from
               FROM team_history_rh1 WHERE competition_key = ? AND team_key = ? AND available_from <= ?
               ORDER BY sequence DESC LIMIT 1""",
            (competition.key, key, iso_utc(at)),
        ).fetchone()
        if row is None:
            status = "NO_HISTORY_BEFORE_DECISION"
    return HistoryState(competition.key, key, canonical_id(competition.key, key), status,
                        row[0] if row else None, row[1] if row else None, row[2] if row else 0,
                        row[3] if row else None, row[4] if row else None, row[5] if row else None, names)

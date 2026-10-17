"""Deterministic, versioned team identity resolution (K43). Fails closed.

A provider team (provider, provider team id, display name) inside a tracked
competition resolves to exactly one historical record (team_state row: team
name + competition label), or to nothing:

1. An explicit mapping in team_identity_map (provider + provider team id) is
   used first. It is re-validated against team_state on every use.
2. Otherwise the provider name is normalised (accents, punctuation, legal
   suffixes such as FC/SC, a short verified alias list; src/odds/identity.py)
   and compared for equality with the normalised team_state names in the
   competition's own history labels. No substring or fuzzy matching. Youth,
   reserve and women's markers (U21, II, B, Women, ...) survive normalisation,
   so "Chelsea U21" can never become "Chelsea".
3. Two or more different team_state names that normalise to the same key are
   AMBIGUOUS. One team present under two labels of the same competition (e.g.
   "La Liga" from one feed and "LaLiga" from another) is one team with split
   history; the row with more matches is used and the split is recorded.
4. The history must provably belong to this competition: every stored fixture
   behind that team name and label must carry one of the competition's league
   ids. Fixtures from another competition with the same label (another
   country's "Premier League") mean CONTAMINATED_HISTORY. No fixtures at all
   mean UNVERIFIED_HISTORY.

There is no fallback to hard-coded season tables, substring matches or
hash-generated statistics. Unresolved teams get no forecast.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from src.identity.competitions import Competition
from src.odds.identity import normalise_team as _odds_normalise

TEAM_IDENTITY_VERSION = "team-identity-v1"


def normalise_team(name: str) -> str:
    """Odds-layer normalisation plus one club-name rule: a leading "1." ("1. FC Union Berlin")
    is dropped, so feeds that omit it match. Youth/reserve/women markers are kept."""
    key = _odds_normalise(name)
    tokens = key.split()
    if len(tokens) > 1 and tokens[0] == "1":
        tokens = tokens[1:]
    return " ".join(tokens)

RESOLVED = "RESOLVED"
UNKNOWN_COMPETITION = "UNKNOWN_COMPETITION"
NO_HISTORY = "UNRESOLVED_NO_HISTORY"
AMBIGUOUS = "AMBIGUOUS"
CONTAMINATED = "CONTAMINATED_HISTORY"
UNVERIFIED = "UNVERIFIED_HISTORY"
STALE_MAPPING = "STALE_EXPLICIT_MAPPING"

SCHEMA = """
CREATE TABLE IF NOT EXISTS team_identity_map (
    provider            TEXT NOT NULL,
    provider_team_id    TEXT NOT NULL,
    competition_key     TEXT NOT NULL,
    provider_team_name  TEXT NOT NULL,
    team_name_lower     TEXT NOT NULL,
    history_league      TEXT NOT NULL,
    source              TEXT NOT NULL,
    mapping_version     TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    PRIMARY KEY (provider, provider_team_id, competition_key)
);
"""


@dataclass
class TeamResolution:
    status: str
    provider: str | None
    provider_team_id: str | None
    provider_name: str
    competition_key: str | None
    team_name_lower: str | None = None
    history_league: str | None = None
    matches_played: int | None = None
    source: str | None = None
    reason: str = ""
    evidence: dict = field(default_factory=dict)
    version: str = TEAM_IDENTITY_VERSION
    competition_ids: dict = field(default_factory=dict)
    canonical_team_id: str | None = None

    @property
    def resolved(self) -> bool:
        return self.status == RESOLVED

    def to_dict(self) -> dict:
        return asdict(self)


def canonical_team_id(team_name_lower: str, history_league: str) -> str:
    """Stable id of one historical record (team_state name + label)."""
    digest = hashlib.sha256(f"{history_league}\x1f{team_name_lower}".encode()).hexdigest()[:16]
    return f"tsr-{digest}"


def _finish(res: TeamResolution, competition: Competition | None) -> TeamResolution:
    if competition is not None:
        res.competition_ids = {"api_football": competition.api_football_id, "sofascore": competition.sofascore_id}
    if res.resolved and res.team_name_lower and res.history_league:
        res.canonical_team_id = canonical_team_id(res.team_name_lower, res.history_league)
    return res


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _history_rows(conn, competition: Competition) -> list[sqlite3.Row]:
    placeholders = ",".join("?" * len(competition.history_labels))
    return conn.execute(
        f"""SELECT team_name, team_name_lower, league, matches_played FROM team_state
            WHERE venue = 'overall' AND league IN ({placeholders})""",
        competition.history_labels,
    ).fetchall()


def _fixture_league_ids(conn, team_name: str, label: str) -> dict[int, int]:
    """League ids of stored fixtures under `label` whose home or away name normalises like `team_name`.

    team_state rows and fixtures can come from feeds that spell the club
    differently ("SV Werder Bremen" vs "Werder Bremen"), so names are compared
    after normalisation, never as substrings."""
    target = normalise_team(team_name)
    counts: dict[int, int] = {}
    for league_id, home, away in conn.execute(
        "SELECT league_id, home_team, away_team FROM matches WHERE league_name = ?", (label,)
    ):
        if normalise_team(home or "") == target or normalise_team(away or "") == target:
            lid = int(league_id) if league_id is not None else -1
            counts[lid] = counts.get(lid, 0) + 1
    return counts


def _verify_history(conn, competition: Competition, row) -> tuple[str, str, dict]:
    ids = _fixture_league_ids(conn, row["team_name"], row["league"])
    evidence = {"fixture_league_ids": ids, "allowed_league_ids": sorted(competition.league_ids)}
    if not ids:
        return UNVERIFIED, f"no stored fixture proves the source of {row['league']!r} history", evidence
    foreign = {lid: n for lid, n in ids.items() if lid not in competition.league_ids}
    if foreign:
        return CONTAMINATED, (f"{row['league']!r} history for {row['team_name']!r} includes fixtures from other "
                              f"competitions {sorted(foreign)}"), evidence
    return RESOLVED, "", evidence


def _from_explicit(conn, competition, provider, provider_team_id, name) -> TeamResolution | None:
    if not provider or not provider_team_id:
        return None
    try:
        m = conn.execute(
            "SELECT team_name_lower, history_league, source, mapping_version FROM team_identity_map "
            "WHERE provider = ? AND provider_team_id = ? AND competition_key = ?",
            (provider, str(provider_team_id), competition.key),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if m is None:
        return None
    row = conn.execute(
        "SELECT team_name, team_name_lower, league, matches_played FROM team_state "
        "WHERE venue = 'overall' AND team_name_lower = ? AND league = ?",
        (m[0], m[1]),
    ).fetchone()
    base = dict(provider=provider, provider_team_id=str(provider_team_id), provider_name=name,
                competition_key=competition.key)
    if row is None or not row["matches_played"]:
        return TeamResolution(STALE_MAPPING, **base, reason=f"mapped record {m[0]!r}/{m[1]!r} has no history")
    status, reason, evidence = _verify_history(conn, competition, row)
    return TeamResolution(status, **base, team_name_lower=row["team_name_lower"], history_league=row["league"],
                          matches_played=row["matches_played"], source=f"explicit:{m[2]}", reason=reason,
                          evidence={**evidence, "mapping_version": m[3]})


def resolve_team(conn: sqlite3.Connection, competition: Competition | None, name: str,
                 provider: str | None = None, provider_team_id=None, remember: bool = False,
                 registry=None) -> TeamResolution:
    """Resolve one team. `registry` (src/identity/registry.py) settles reviewed ambiguous cases;
    only entries the owner approved are used unless the registry was loaded with allow_proposed."""
    from src.identity import registry as registry_mod

    reg = registry if registry is not None else registry_mod.load()
    res = _resolve(conn, competition, name, provider, provider_team_id, remember)
    if competition is not None and not res.resolved and res.status != STALE_MAPPING:
        res = registry_mod.apply(conn, reg, competition, res, remember) or res
    return _finish(res, competition)


def _resolve(conn: sqlite3.Connection, competition: Competition | None, name: str,
             provider: str | None, provider_team_id, remember: bool) -> TeamResolution:
    conn.row_factory = sqlite3.Row
    base = dict(provider=provider, provider_team_id=None if provider_team_id is None else str(provider_team_id),
                provider_name=name, competition_key=competition.key if competition else None)
    if competition is None:
        return TeamResolution(UNKNOWN_COMPETITION, **base, reason="fixture is not in a tracked competition")

    explicit = _from_explicit(conn, competition, provider, provider_team_id, name)
    if explicit is not None:
        return explicit

    target = normalise_team(name)
    rows = [r for r in _history_rows(conn, competition) if normalise_team(r["team_name"]) == target]
    names = sorted({r["team_name_lower"] for r in rows})
    if not rows:
        return TeamResolution(NO_HISTORY, **base, reason=f"no {competition.key} history for normalised {target!r}")
    if len(names) > 1:
        # Never merged automatically, even when every candidate verifies for this
        # competition (typically one club spelled differently by two feeds). Those
        # cases are listed for review and can be settled by an explicit mapping.
        checks = {r["team_name_lower"] + " @ " + r["league"]: _verify_history(conn, competition, r)[0] for r in rows}
        return TeamResolution(AMBIGUOUS, **base, reason=f"{len(names)} records normalise to {target!r}",
                              evidence={"candidates": names, "candidate_verification": checks,
                                        "review": "same-club spelling split" if set(checks.values()) == {RESOLVED}
                                        else "distinct or unverified records"})
    usable = [r for r in rows if (r["matches_played"] or 0) >= 1]
    if not usable:
        return TeamResolution(NO_HISTORY, **base, reason="history record has no matches")
    row = sorted(usable, key=lambda r: (-(r["matches_played"] or 0), r["league"]))[0]
    status, reason, evidence = _verify_history(conn, competition, row)
    if len(usable) > 1:
        evidence["split_history"] = {r["league"]: r["matches_played"] for r in usable}
    res = TeamResolution(status, **base, team_name_lower=row["team_name_lower"], history_league=row["league"],
                         matches_played=row["matches_played"], source="normalised_name_in_competition",
                         reason=reason, evidence=evidence)
    if res.resolved and remember and provider and provider_team_id is not None:
        ensure_tables(conn)
        conn.execute(
            "INSERT OR IGNORE INTO team_identity_map VALUES (?, ?, ?, ?, ?, ?, 'auto-verified', ?, ?)",
            (provider, str(provider_team_id), competition.key, name, row["team_name_lower"], row["league"],
             TEAM_IDENTITY_VERSION, datetime.now(timezone.utc).replace(microsecond=0).isoformat()),
        )
        conn.commit()
    return res

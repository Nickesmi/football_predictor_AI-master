"""Map a provider's event to exactly one canonical fixture (matches.id), or to none.

Mapping is deliberately strict. A wrong mapping would attach another match's
prices to a fixture, which is worse than having no prices: an unmapped event
only means NO ACTIONABLE BET for that fixture. There is no substring or fuzzy
matching ("Manchester" must never match both Manchester clubs).

Rules:
  1. A stored mapping (fixture_identity) for (provider, event id) is reused.
  2. Otherwise both normalised team names must match one fixture on the
     provider's UTC date or one day either side (stored dates may be local).
  3. When the fixture's kickoff is an absolute time, it must lie within
     KICKOFF_TOLERANCE of the provider's commence time. Stored kickoffs that
     are only "HH:MM" cannot be compared and fall back to names + date.
  4. Zero or several candidates: unmapped, with the reason.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.odds.model import InvalidQuote, iso_utc

KICKOFF_TOLERANCE = timedelta(hours=2)

_DROP_TOKENS = frozenset({"fc", "afc", "cf", "sc", "ac", "cd", "fk", "sk", "ssc", "sv", "bk", "if", "the"})

# Exact spellings that differ between providers for the tracked leagues.
# Keys and values are already normalised. Extend only with verified pairs.
ALIASES = {
    "man utd": "manchester united",
    "man united": "manchester united",
    "man city": "manchester city",
    "spurs": "tottenham hotspur",
    "tottenham": "tottenham hotspur",
    "wolves": "wolverhampton wanderers",
    "wolverhampton": "wolverhampton wanderers",
    "brighton": "brighton and hove albion",
    "brighton hove albion": "brighton and hove albion",
    "nottm forest": "nottingham forest",
    "newcastle": "newcastle united",
    "west ham": "west ham united",
    "leicester": "leicester city",
    "ipswich": "ipswich town",
    "psg": "paris saint germain",
    "inter milan": "inter",
    "internazionale": "inter",
    "bayern munchen": "bayern munich",
    "atletico de madrid": "atletico madrid",
    "dortmund": "borussia dortmund",
}


def normalise_team(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    tokens = [t for t in text.split() if t not in _DROP_TOKENS]
    joined = " ".join(tokens)
    return ALIASES.get(joined, joined)


@dataclass(frozen=True)
class ProviderEvent:
    provider: str
    event_id: str
    home_team: str
    away_team: str
    commence_time: str  # ISO UTC


@dataclass(frozen=True)
class Mapping:
    fixture_id: str | None
    method: str
    reason: str = ""


def _absolute_kickoff(kickoff) -> datetime | None:
    text = str(kickoff or "").strip()
    if not text or re.fullmatch(r"\d{1,2}:\d{2}", text):
        return None
    try:
        return datetime.fromisoformat(iso_utc(text))
    except InvalidQuote:
        return None


def resolve_fixture(conn: sqlite3.Connection, event: ProviderEvent) -> Mapping:
    stored = conn.execute(
        "SELECT fixture_id FROM fixture_identity WHERE provider = ? AND provider_event_id = ?",
        (event.provider, event.event_id),
    ).fetchone()
    if stored:
        return Mapping(stored[0], "stored")

    commence = datetime.fromisoformat(iso_utc(event.commence_time))
    dates = [(commence + timedelta(days=d)).strftime("%Y-%m-%d") for d in (-1, 0, 1)]
    home, away = normalise_team(event.home_team), normalise_team(event.away_team)
    rows = conn.execute(
        f"SELECT id, home_team, away_team, kickoff FROM matches WHERE date IN ({','.join('?' * len(dates))})",
        dates,
    ).fetchall()

    candidates = []
    for row in rows:
        if normalise_team(row["home_team"]) != home or normalise_team(row["away_team"]) != away:
            continue
        kickoff = _absolute_kickoff(row["kickoff"])
        if kickoff is None:
            candidates.append((row["id"], "names+date"))
        elif abs(kickoff - commence) <= KICKOFF_TOLERANCE:
            candidates.append((row["id"], "names+kickoff"))

    if not candidates:
        return Mapping(None, "unmapped", "no fixture with these teams near this kickoff")
    if len(candidates) > 1:
        return Mapping(None, "unmapped", f"ambiguous: {len(candidates)} fixtures match")
    fixture_id, method = candidates[0]
    return Mapping(fixture_id, method)


def remember(conn: sqlite3.Connection, event: ProviderEvent, mapping: Mapping, mapped_at: str) -> None:
    if mapping.fixture_id is None or mapping.method == "stored":
        return
    conn.execute(
        """INSERT OR IGNORE INTO fixture_identity
               (provider, provider_event_id, fixture_id, provider_home, provider_away,
                commence_time, method, mapped_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (event.provider, event.event_id, mapping.fixture_id, event.home_team, event.away_team,
         iso_utc(event.commence_time), mapping.method, mapped_at),
    )

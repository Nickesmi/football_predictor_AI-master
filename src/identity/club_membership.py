"""Cross-competition club identity by provider team id (2026-11-03, demo branch).

A club keeps one provider team id across every competition it plays in: API-Football gives Feyenoord the id
209 in the Eredivisie (league 88) and in the Champions League (league 2). This module records, from the
fixture lists the demo already holds (no request is ever sent), in which leagues each provider team id was
listed and under which name. It is used for one thing only: to find the *domestic league* of a club that
plays a continental club competition, so that the club's verified domestic history can be read.

Rules (no fuzzy or partial matching anywhere):
  * the link is the provider team id, never the name. Youth, reserve and women's sides have their own ids
    ("Feyenoord U19" is 7981, "Feyenoord" is 209), so they can never be linked to the senior club;
  * a league counts as a domestic league only when it has a country (not "World") and the club was listed
    there in a league round ("Regular Season - N", "Apertura - N", "Clausura - N"), or it is one of the
    tracked domestic competitions (src/identity/competitions.py). Cups, friendlies and continental
    competitions never qualify;
  * only fixtures dated before the match being predicted are used as evidence;
  * if the club was listed in two domestic leagues with the same latest date and the same number of
    fixtures, the result is ambiguous and nothing is returned;
  * the domestic record itself is then resolved by team-identity-v1 inside that league (exact normalised
    name, provenance checked against the league id). This module never returns statistics.
"""
from __future__ import annotations

import glob
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.identity.competitions import Competition, competition_for_fixture

MEMBERSHIP_VERSION = "club-membership-v1"
_LEAGUE_ROUND = re.compile(r"^(regular season|apertura|clausura)(\s*-\s*\d+)?$", re.IGNORECASE)
_CONTINENTAL_KEYS = {"uefa-champions-league", "uefa-europa-league", "uefa-conference-league"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_team_competitions (
    provider          TEXT NOT NULL,
    provider_team_id  TEXT NOT NULL,
    league_id         INTEGER NOT NULL,
    league_name       TEXT NOT NULL,
    country           TEXT NOT NULL,
    team_name         TEXT NOT NULL,
    fixtures          INTEGER NOT NULL,
    league_rounds     INTEGER NOT NULL,
    first_date        TEXT NOT NULL,
    last_date         TEXT NOT NULL,
    source            TEXT NOT NULL,
    version           TEXT NOT NULL,
    PRIMARY KEY (provider, provider_team_id, league_id, team_name)
);
"""


def is_league_round(round_label) -> bool:
    return bool(_LEAGUE_ROUND.match(str(round_label or "").strip()))


def is_continental(competition: Competition | None, country: str = "") -> bool:
    """A continental club competition: the three tracked UEFA competitions, or an untracked league whose
    provider country is "World" (Libertadores, AFC Champions League, ...). National-team competitions also
    have the country "World"; their teams are never listed in a domestic league, so they find nothing."""
    if competition is None:
        return False
    return competition.key in _CONTINENTAL_KEYS or (competition.key.startswith("api-football-")
                                                     and (country or competition.country) == "World")


def collect(cache_dir: Path, provider: str = "api_football") -> list[dict]:
    """One row per (team id, league id, spelling) from the cached day files fixtures-*.json."""
    acc: dict[tuple, dict] = {}
    for p in sorted(glob.glob(str(Path(cache_dir) / "fixtures-*.json"))):
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, list):
            continue
        for f in data:
            if not isinstance(f, dict) or (f.get("source") or f.get("provider")) != provider:
                continue
            lg = f.get("league") or {}
            try:
                lid = int(f.get("league_id_apifootball") or lg.get("id"))
            except (TypeError, ValueError):
                continue
            day = str(f.get("kickoff_utc") or f.get("date") or "")[:10]
            if len(day) != 10:
                continue
            for side in ("home_team", "away_team"):
                t = f.get(side) or {}
                tid, name = str(t.get("id") or ""), (t.get("name") or "").strip()
                if not tid.isdigit() or not name:
                    continue
                k = (provider, tid, lid, name)
                row = acc.setdefault(k, {"provider": provider, "provider_team_id": tid, "league_id": lid,
                                         "league_name": lg.get("name") or "", "country": lg.get("country") or "",
                                         "team_name": name, "fixture_ids": set(), "league_round_ids": set(),
                                         "first_date": day, "last_date": day})
                fid = str(f.get("id"))
                row["fixture_ids"].add(fid)
                if is_league_round(f.get("round")):
                    row["league_round_ids"].add(fid)
                row["first_date"] = min(row["first_date"], day)
                row["last_date"] = max(row["last_date"], day)
    out = []
    for row in acc.values():
        out.append({**{k: v for k, v in row.items() if k not in ("fixture_ids", "league_round_ids")},
                    "fixtures": len(row["fixture_ids"]), "league_rounds": len(row["league_round_ids"])})
    return out


def write(conn: sqlite3.Connection, rows: list[dict], source: str) -> int:
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT OR REPLACE INTO provider_team_competitions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(r["provider"], r["provider_team_id"], r["league_id"], r["league_name"], r["country"], r["team_name"],
          r["fixtures"], r["league_rounds"], r["first_date"], r["last_date"], source, MEMBERSHIP_VERSION)
         for r in rows])
    conn.commit()
    return len(rows)


@dataclass
class DomesticLeague:
    competition: Competition | None
    team_name: str | None
    reason: str
    evidence: dict

    @property
    def found(self) -> bool:
        return self.competition is not None


def domestic_league(conn: sqlite3.Connection, provider: str | None, provider_team_id,
                    before_date: str | None) -> DomesticLeague:
    """The club's domestic league from fixtures listed before `before_date` (YYYY-MM-DD), or a reason."""
    if not provider or provider_team_id in (None, ""):
        return DomesticLeague(None, None, "the fixture has no provider team id", {})
    try:
        rows = conn.execute(
            "SELECT league_id, league_name, country, team_name, fixtures, league_rounds, last_date "
            "FROM provider_team_competitions WHERE provider = ? AND provider_team_id = ? AND first_date < ?",
            (provider, str(provider_team_id), before_date or "9999-12-31")).fetchall()
    except sqlite3.OperationalError:
        return DomesticLeague(None, None, "club membership table is not in this database", {})
    by_league: dict[int, dict] = {}
    for lid, lname, country, tname, n, rounds, last in rows:
        tracked = competition_for_fixture(provider, lid)
        domestic_tracked = tracked is not None and tracked.key not in _CONTINENTAL_KEYS
        if not country or country == "World":
            continue
        if not (domestic_tracked or rounds > 0):
            continue
        e = by_league.setdefault(int(lid), {"league_id": int(lid), "league_name": lname, "country": country,
                                            "names": {}, "fixtures": 0, "last_date": "", "tracked": tracked})
        e["names"][tname] = e["names"].get(tname, 0) + n
        e["fixtures"] += n
        e["last_date"] = max(e["last_date"], last)
    evidence = {"leagues": {lid: {k: v for k, v in e.items() if k != "tracked"} for lid, e in by_league.items()},
                "version": MEMBERSHIP_VERSION}
    if not by_league:
        return DomesticLeague(None, None, f"provider team {provider_team_id} is listed in no domestic league "
                                          "before this match", evidence)
    ranked = sorted(by_league.values(), key=lambda e: (e["last_date"], e["fixtures"]), reverse=True)
    if len(ranked) > 1 and (ranked[0]["last_date"], ranked[0]["fixtures"]) == (ranked[1]["last_date"],
                                                                               ranked[1]["fixtures"]):
        return DomesticLeague(None, None, f"provider team {provider_team_id} is listed in two domestic leagues "
                                          f"({ranked[0]['league_id']}, {ranked[1]['league_id']}) equally", evidence)
    best = ranked[0]
    if len(best["names"]) > 1:
        return DomesticLeague(None, None, f"provider team {provider_team_id} has several names in league "
                                          f"{best['league_id']}: {sorted(best['names'])}", evidence)
    name = next(iter(best["names"]))
    comp = best["tracked"] or Competition(f"api-football-{best['league_id']}", best["country"], best["league_id"],
                                          -1, (best["league_name"],))
    evidence["chosen_league_id"] = best["league_id"]
    return DomesticLeague(comp, name, "", evidence)

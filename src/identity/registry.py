"""Reviewed team-identity registry (M3.2). Settles the cases the resolver must not decide alone.

Entries live in team_registry.v1.json, keyed by competition and normalised
provider team name. Each entry is either
  * "map": the provider team is the given team_state record. The record's
    history is verified again on every use (src/identity/teams._verify_history);
  * "exclude": no usable record (unverifiable alias, contaminated or missing
    history). The team stays fail-closed, with the registry reason attached.

Every entry carries its evidence and review state. Only entries with
review.status == "approved" are used in normal operation. Isolated comparisons
may load the registry with allow_proposed=True to show the effect of the
proposed entries; that mode is never used by the pipeline or the shadow runner.
A provider team id, once bound, must match on every later use.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_FILE = Path(__file__).with_name("team_registry.v1.json")


@dataclass
class Registry:
    version: str
    entries: dict[tuple[str, str], dict]
    allow_proposed: bool = False

    def lookup(self, competition_key: str, normalised_name: str) -> dict | None:
        return self.entries.get((competition_key, normalised_name))

    def usable(self, entry: dict) -> bool:
        status = (entry.get("review") or {}).get("status")
        return status == "approved" or (self.allow_proposed and status == "proposed")


def load(path: Path = REGISTRY_FILE, allow_proposed: bool = False) -> Registry:
    from src.identity.teams import normalise_team

    if not path.exists():
        return Registry("none", {}, allow_proposed)
    data = json.loads(path.read_text())
    entries = {}
    for e in data["entries"]:
        key = (e["competition_key"], normalise_team(e["provider_team_name"]))
        if key in entries:
            raise ValueError(f"duplicate registry entry {key}")
        entries[key] = e
    return Registry(data["version"], entries, allow_proposed)


def apply(conn, reg: Registry, competition, res, remember: bool):
    """A new TeamResolution from the registry, or None when no entry applies."""
    from src.identity import teams

    entry = reg.lookup(competition.key, teams.normalise_team(res.provider_name))
    if entry is None:
        return None
    note = {"registry_version": reg.version, "entry_id": entry["id"], "decision": entry["decision"],
            "review_status": (entry.get("review") or {}).get("status"), "reason": entry.get("reason")}
    bound = entry.get("provider_team_id")
    if bound and res.provider_team_id and str(bound) != str(res.provider_team_id):
        return teams.TeamResolution(teams.AMBIGUOUS, res.provider, res.provider_team_id, res.provider_name,
                                    competition.key, reason="provider team id differs from the registry binding",
                                    evidence={"registry": note, "bound_id": bound})
    if not reg.usable(entry) or entry["decision"] == "exclude":
        res.evidence = {**res.evidence, "registry": note}
        return res
    target = entry["team_state"]
    row = conn.execute(
        "SELECT team_name, team_name_lower, league, matches_played FROM team_state "
        "WHERE venue = 'overall' AND team_name_lower = ? AND league = ?",
        (target["team_name_lower"], target["history_league"]),
    ).fetchone()
    if row is None or not row["matches_played"]:
        return teams.TeamResolution(teams.STALE_MAPPING, res.provider, res.provider_team_id, res.provider_name,
                                    competition.key, reason="registry record has no history",
                                    evidence={"registry": note})
    status, reason, evidence = teams._verify_history(conn, competition, row)
    out = teams.TeamResolution(status, res.provider, res.provider_team_id, res.provider_name, competition.key,
                               team_name_lower=row["team_name_lower"], history_league=row["league"],
                               matches_played=row["matches_played"], source=f"registry:{entry['id']}",
                               reason=reason, evidence={**evidence, "registry": note})
    if out.resolved and remember and res.provider and res.provider_team_id is not None:
        teams.ensure_tables(conn)
        conn.execute(
            "INSERT OR IGNORE INTO team_identity_map VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (res.provider, str(res.provider_team_id), competition.key, res.provider_name, row["team_name_lower"],
             row["league"], f"registry:{entry['id']}", reg.version,
             datetime.now(timezone.utc).replace(microsecond=0).isoformat()),
        )
        conn.commit()
    return out

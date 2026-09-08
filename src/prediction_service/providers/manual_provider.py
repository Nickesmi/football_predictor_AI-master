"""
Manual data mode (Phase 4 §17).

A controlled path for an operator to supply a real fixture/result by hand
when no live provider is reachable. Manual records go through the SAME
validation discipline as the openfootball historical parser (Phase 2) —
schema completeness, score plausibility, team-identity sanity, duplicate
detection, and chronological validation — before they can reach the
feature engine. There is no bypass.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.prediction_service.data_contract import (
    DataMode, FixtureRecord, OddsRecord, LineupRecord, ProviderHealth, ProviderHealthStatus, now_iso,
    to_naive_timestamp as _naive,
)
from src.prediction_service.providers.base import FootballDataProvider


class ManualDataRejected(Exception):
    """Raised when a manually-supplied record fails validation. Never
    silently dropped or silently accepted — the caller sees exactly why."""


class ManualProvider(FootballDataProvider):
    name = "manual_provider"
    data_mode = DataMode.MANUAL

    def __init__(self):
        self._fixtures: dict[str, FixtureRecord] = {}

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(self.name, ProviderHealthStatus.AVAILABLE, now_iso(),
                               detail=f"{len(self._fixtures)} manually-entered fixtures")

    def add_fixture(self, raw: dict) -> FixtureRecord:
        """raw must contain: fixture_id, competition, season, home_team,
        away_team, scheduled_kickoff, and optionally home_goals/away_goals
        (present together, or not at all)."""
        required = ["fixture_id", "competition", "season", "home_team", "away_team", "scheduled_kickoff"]
        missing = [f for f in required if not raw.get(f)]
        if missing:
            raise ManualDataRejected(f"missing required field(s): {missing}")

        fixture_id = str(raw["fixture_id"])
        home_team, away_team = raw["home_team"], raw["away_team"]

        if home_team == away_team:
            raise ManualDataRejected(f"home_team == away_team ({home_team!r})")

        try:
            kickoff = pd.Timestamp(raw["scheduled_kickoff"])
        except Exception as e:
            raise ManualDataRejected(f"scheduled_kickoff is not a valid timestamp: {e}") from e

        home_goals, away_goals = raw.get("home_goals"), raw.get("away_goals")
        if (home_goals is None) != (away_goals is None):
            raise ManualDataRejected("home_goals and away_goals must both be present or both be absent")
        if home_goals is not None:
            if not isinstance(home_goals, int) or not isinstance(away_goals, int):
                raise ManualDataRejected("home_goals/away_goals must be integers")
            if home_goals < 0 or away_goals < 0 or home_goals > 20 or away_goals > 20:
                raise ManualDataRejected(f"implausible score {home_goals}-{away_goals}")

        if fixture_id in self._fixtures:
            raise ManualDataRejected(f"duplicate fixture_id {fixture_id!r} — already recorded")

        for existing in self._fixtures.values():
            if (existing.home_team == home_team and existing.away_team == away_team
                    and _naive(existing.scheduled_kickoff) == _naive(kickoff)):
                raise ManualDataRejected(
                    f"duplicate fixture content: {home_team} vs {away_team} on {raw['scheduled_kickoff']} "
                    f"already recorded as fixture_id={existing.fixture_id!r}"
                )

        record = FixtureRecord(
            fixture_id=fixture_id, competition=raw["competition"], season=raw["season"],
            home_team=home_team, away_team=away_team, scheduled_kickoff=kickoff.isoformat(),
            status="finished" if home_goals is not None else "scheduled",
            data_mode=DataMode.MANUAL, source="manual", source_timestamp=kickoff.isoformat(),
            ingestion_timestamp=now_iso(), home_goals=home_goals, away_goals=away_goals,
        )
        self._fixtures[fixture_id] = record
        return record

    def fetch_fixtures(self, as_of: str, competition: Optional[str] = None) -> list[FixtureRecord]:
        as_of_date = pd.Timestamp(as_of).strftime("%Y-%m-%d")
        return [
            f for f in self._fixtures.values()
            if pd.Timestamp(f.scheduled_kickoff).strftime("%Y-%m-%d") == as_of_date
            and (competition is None or f.competition == competition)
        ]

    def fetch_result(self, fixture_id: str, as_of: str) -> Optional[FixtureRecord]:
        record = self._fixtures.get(fixture_id)
        if record is None or record.home_goals is None:
            return None
        if _naive(as_of) < _naive(record.scheduled_kickoff):
            return None  # even manually-entered results must respect the simulated/real clock
        return record

    def fetch_odds(self, fixture_id: str, as_of: str) -> OddsRecord:
        return OddsRecord.unavailable(fixture_id)

    def fetch_lineup(self, fixture_id: str, as_of: str) -> LineupRecord:
        return LineupRecord.unknown(fixture_id)

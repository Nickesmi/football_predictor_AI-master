"""
Replay provider (Phase 4 §15).

Wraps real historical match data (data/real_historical/matches.csv) and
exposes it exactly like a live provider would have, AS OF a simulated
timestamp — never revealing more. This is what makes the replay engine a
genuine simulation of "what would production have known" rather than a
disguised historical-accuracy check: fetch_fixtures() only returns
fixtures scheduled for that exact simulated date (with no score), and
fetch_result() returns None until the simulated clock has actually
reached-or-passed that match's real date.

No odds or lineup data exists in this dataset (see REAL_DATA_BACKTEST_REPORT.md
§1) — fetch_odds/fetch_lineup always report UNAVAILABLE/UNKNOWN, honestly,
rather than inventing plausible-looking values.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.prediction_service.data_contract import (
    DataMode, FixtureRecord, OddsRecord, LineupRecord, ProviderHealth, ProviderHealthStatus, now_iso,
    to_naive_timestamp as _naive,
)
from src.prediction_service.providers.base import FootballDataProvider


class ReplayProvider(FootballDataProvider):
    name = "replay_provider"
    data_mode = DataMode.REPLAY

    def __init__(self, matches: pd.DataFrame):
        self.matches = matches.sort_values("date", kind="stable").reset_index(drop=True)

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            self.name, ProviderHealthStatus.AVAILABLE, now_iso(),
            detail=f"{len(self.matches)} real historical matches loaded (replay only — not live)",
        )

    def fetch_fixtures(self, as_of: str, competition: Optional[str] = None) -> list[FixtureRecord]:
        as_of_date = pd.Timestamp(as_of).strftime("%Y-%m-%d")
        day = self.matches[self.matches["date"] == as_of_date]
        if competition:
            day = day[day["league"] == competition]

        ingestion_ts = now_iso()
        out = []
        for _, row in day.iterrows():
            out.append(FixtureRecord(
                fixture_id=row["match_id"], competition=row["league"], season=row["season"],
                home_team=row["home_team"], away_team=row["away_team"],
                scheduled_kickoff=row["date"], status="scheduled",
                data_mode=DataMode.REPLAY, source="openfootball_replay",
                source_timestamp=row["date"], ingestion_timestamp=ingestion_ts,
                # Deliberately NOT populating home_goals/away_goals here —
                # a "fixture" record must never carry the result, even
                # though this replay provider technically has it in
                # memory. See fetch_result() for the only place a result
                # may be revealed, and only once as_of has caught up.
            ))
        return out

    def fetch_result(self, fixture_id: str, as_of: str) -> Optional[FixtureRecord]:
        rows = self.matches[self.matches["match_id"] == fixture_id]
        if len(rows) == 0:
            return None
        row = rows.iloc[0]
        if _naive(as_of) < _naive(row["date"]):
            return None  # the match has not happened yet in simulated time — this IS the blindness guarantee

        return FixtureRecord(
            fixture_id=fixture_id, competition=row["league"], season=row["season"],
            home_team=row["home_team"], away_team=row["away_team"],
            scheduled_kickoff=row["date"], status="finished",
            data_mode=DataMode.REPLAY, source="openfootball_replay",
            source_timestamp=row["date"], ingestion_timestamp=now_iso(),
            home_goals=int(row["home_goals"]), away_goals=int(row["away_goals"]),
        )

    def fetch_odds(self, fixture_id: str, as_of: str) -> OddsRecord:
        return OddsRecord.unavailable(fixture_id)  # honestly: no real odds exist in this dataset

    def fetch_lineup(self, fixture_id: str, as_of: str) -> LineupRecord:
        return LineupRecord.unknown(fixture_id)  # honestly: no real lineup data exists in this dataset

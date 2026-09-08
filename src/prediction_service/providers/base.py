"""
Provider abstraction (Phase 4 §3/§9). The prediction engine only ever
consumes the canonical types in data_contract.py — it never knows or
cares whether a FixtureRecord came from a live HTTP call, a replay of
real historical results, or a manually-entered fixture. That separation
is the whole point: swapping in a real live provider later must not
require touching the feature engine, the pipeline, or any model code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from src.prediction_service.data_contract import (
    DataMode, FixtureRecord, OddsRecord, LineupRecord, ProviderHealth,
)


class ProviderError(Exception):
    """Raised by fetch_* methods on failure. Always carries a specific
    ProviderHealthStatus — callers must translate this into the matching
    pipeline refusal reason, never into a silent empty result."""

    def __init__(self, status, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f"[{status}] {detail}")


class FootballDataProvider(ABC):
    name: str
    data_mode: DataMode

    @abstractmethod
    def health_check(self) -> ProviderHealth:
        ...

    @abstractmethod
    def fetch_fixtures(self, as_of: str, competition: Optional[str] = None) -> list[FixtureRecord]:
        """Fixtures knowable as of `as_of` — never anything that would
        only be knowable later (e.g. results of matches after `as_of`)."""

    @abstractmethod
    def fetch_odds(self, fixture_id: str, as_of: str) -> OddsRecord:
        """Must return OddsRecord(status=UNAVAILABLE) rather than raise
        when no real odds exist — odds absence is a normal, expected
        state, not a provider failure."""

    @abstractmethod
    def fetch_lineup(self, fixture_id: str, as_of: str) -> LineupRecord:
        """Must return LineupRecord(status=UNKNOWN) rather than raise
        when lineup information doesn't exist yet — same reasoning as odds."""

    @abstractmethod
    def fetch_result(self, fixture_id: str, as_of: str) -> Optional[FixtureRecord]:
        """The critical §15/§19 method: must return None if the fixture
        has not actually finished as of `as_of` in this provider's notion
        of time — for ReplayProvider that means the match's real date is
        still in the simulated future. This is what makes shadow/replay
        mode blind: the result literally does not exist yet from this
        method's point of view until the caller advances past it."""

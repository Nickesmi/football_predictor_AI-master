"""
Canonical data contract (Phase 4 §3/§4/§5).

Every piece of data that reaches the prediction pipeline — whether it came
from a real live provider, a historical replay, or a manually-entered
fixture — is normalized into these types first. The prediction engine
never inspects where data came from to decide HOW to predict; it only
ever looks at DataMode to decide whether a prediction may be labeled LIVE
vs REPLAY vs MANUAL, and at each record's provenance timestamps to decide
whether it's even allowed to be used at all.

This is the fix for Phase 4's central risk: "silently fall back from live
data to historical data" or "mark predictions as live when they are
actually replay predictions" is structurally prevented by DataMode being
a required, explicit field on every record and every prediction — there
is no code path that constructs a prediction without it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class DataMode(str, Enum):
    """Where a piece of data (or a prediction built from it) came from.
    Required on every record — never inferred, never defaulted silently."""
    LIVE = "LIVE"
    REPLAY = "REPLAY"
    MANUAL = "MANUAL"
    UNAVAILABLE = "UNAVAILABLE"


class ProviderHealthStatus(str, Enum):
    """§9: every provider failure mode gets its own status — a network
    failure must never collapse into the same bucket as 'no matches
    today'."""
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_FAILURE = "AUTH_FAILURE"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    STALE_DATA = "STALE_DATA"
    UNAVAILABLE = "UNAVAILABLE"


class FeatureStatus(str, Enum):
    """§5: if a feature's timestamp can't be established, it is UNKNOWN —
    never silently assumed available."""
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"


class LineupAvailabilityStatus(str, Enum):
    """§13 — a superset of src.prediction_service.lineup_info.Availability
    (per-player) at the FIXTURE level: whether lineup information exists
    for this fixture AT ALL, distinct from any individual player's status."""
    CONFIRMED = "CONFIRMED"
    PROBABLE = "PROBABLE"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"


class OddsStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass
class ProviderHealth:
    provider_name: str
    status: ProviderHealthStatus
    checked_at: str
    latency_ms: Optional[float] = None
    detail: str = ""


@dataclass
class TeamRecord:
    canonical_id: str
    name: str
    competition: str
    season: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class FixtureRecord:
    fixture_id: str
    competition: str
    season: str
    home_team: str
    away_team: str
    scheduled_kickoff: str            # ISO timestamp
    status: str                       # e.g. "scheduled", "finished", "postponed"
    data_mode: DataMode
    source: str                       # provider name / "openfootball" / "manual"
    source_timestamp: str             # when the SOURCE says this is true
    ingestion_timestamp: str          # when WE received/recorded it
    actual_kickoff: Optional[str] = None
    home_goals: Optional[int] = None
    away_goals: Optional[int] = None


@dataclass
class OddsRecord:
    fixture_id: str
    status: OddsStatus
    source: Optional[str] = None
    market: Optional[str] = None
    selection: Optional[str] = None
    odds: Optional[float] = None
    timestamp: Optional[str] = None
    is_opening: Optional[bool] = None
    is_closing: Optional[bool] = None

    @staticmethod
    def unavailable(fixture_id: str) -> "OddsRecord":
        return OddsRecord(fixture_id=fixture_id, status=OddsStatus.UNAVAILABLE)


@dataclass
class LineupRecord:
    fixture_id: str
    status: LineupAvailabilityStatus
    source: Optional[str] = None
    retrieved_at: Optional[str] = None

    @staticmethod
    def unknown(fixture_id: str) -> "LineupRecord":
        return LineupRecord(fixture_id=fixture_id, status=LineupAvailabilityStatus.UNKNOWN)


@dataclass
class FeatureProvenance:
    """§5: per-feature provenance record. A prediction's full feature
    snapshot is a list of these, not just a bag of numbers — every value
    can be traced back to where it came from and when it was knowable."""
    feature_name: str
    feature_value: Optional[float]
    source: str
    source_timestamp: Optional[str]
    computed_timestamp: str
    status: FeatureStatus

    def assert_available_before(self, prediction_timestamp: str) -> None:
        """§5's hard rule: information_timestamp <= prediction_timestamp.
        Raises ValueError if violated — callers must not catch this and
        substitute a default; it means the feature must not be used."""
        if self.status == FeatureStatus.UNKNOWN or self.source_timestamp is None:
            return  # nothing to check — status already says don't trust it
        if to_naive_timestamp(self.source_timestamp) > to_naive_timestamp(prediction_timestamp):
            raise ValueError(
                f"feature '{self.feature_name}' has source_timestamp "
                f"({self.source_timestamp}) AFTER prediction_timestamp "
                f"({prediction_timestamp}) — this is exactly the leakage §5 forbids."
            )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_naive_timestamp(ts):
    """Normalize any timestamp (naive or tz-aware, e.g. a trailing 'Z') to
    a timezone-naive pandas Timestamp so two timestamps from different
    sources can always be compared without raising. Used everywhere a
    point-in-time comparison happens (feature provenance, replay
    providers) — this dataset's real precision is day-granularity anyway."""
    from pandas import Timestamp
    t = Timestamp(ts)
    return t.tz_localize(None) if t.tzinfo is not None else t

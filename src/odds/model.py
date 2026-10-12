"""Canonical odds vocabulary (M2.5).

Four kinds of price-like information exist in the system and must never be
mixed up:

* BOOKMAKER       a real price quoted by a named bookmaker, delivered by an
                  odds provider. The only kind that can support a bet decision.
* CROWD_VOTE      SofaScore user votes. Opinion shares, not prices. Stored in
                  crowd_vote_signals as shares; never converted to odds.
* MODEL_FAIR      1 / model probability. Derived on demand for display or
                  comparison; never stored as a quote.
* SYNTHETIC_TEST  explicitly fake prices used by tests only. Storable only in
                  the test environment, never while the baseline is frozen, and
                  always labelled.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class OddsType(str, Enum):
    BOOKMAKER = "bookmaker"
    CROWD_VOTE = "crowd_vote"
    MODEL_FAIR = "model_fair"
    SYNTHETIC_TEST = "synthetic_test"


# Kinds that may be stored in odds_quotes. Crowd votes and model fair odds never are.
QUOTE_TYPES = (OddsType.BOOKMAKER, OddsType.SYNTHETIC_TEST)

# Canonical markets and their selections. A market with a line (totals, Asian
# handicap) is identified by (market, line); the line is part of the key.
MARKETS: dict[str, tuple[str, ...]] = {
    "1X2": ("home", "draw", "away"),
    "TOTALS": ("over", "under"),
    "BTTS": ("yes", "no"),
    "AH": ("home", "away"),
    "DC": ("home_draw", "home_away", "draw_away"),
    "DNB": ("home", "away"),
}
LINE_MARKETS = frozenset({"TOTALS", "AH"})

# Decimal odds outside this range are treated as provider errors, not prices.
MIN_DECIMAL_ODDS = 1.01
MAX_DECIMAL_ODDS = 1000.0


class InvalidQuote(ValueError):
    """A provider value that cannot be a genuine decimal price or canonical selection."""


def decimal_odds(value) -> float:
    """Validate a decimal price. Never repairs or guesses a value."""
    try:
        price = float(value)
    except (TypeError, ValueError):
        raise InvalidQuote(f"not a number: {value!r}") from None
    if not math.isfinite(price) or not (MIN_DECIMAL_ODDS <= price <= MAX_DECIMAL_ODDS):
        raise InvalidQuote(f"decimal odds out of range: {value!r}")
    return round(price, 3)


def format_line(value) -> str:
    """Canonical text for a market line ('2.5', '-0.25', '0'); '' for markets without one."""
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise InvalidQuote(f"invalid line: {value!r}") from None
    if not math.isfinite(number) or abs(number * 4 - round(number * 4)) > 1e-9:
        raise InvalidQuote(f"invalid line: {value!r}")
    return f"{number:g}"


def iso_utc(value) -> str:
    """Normalise a timestamp to 'YYYY-MM-DDTHH:MM:SS+00:00' so stored values sort lexically."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        moment = datetime.fromtimestamp(int(value), tz=timezone.utc)
    elif isinstance(value, str) and value.strip():
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            raise InvalidQuote(f"not a timestamp: {value!r}") from None
    else:
        raise InvalidQuote(f"missing timestamp: {value!r}")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Quote:
    """One price for one selection from one bookmaker at one moment."""
    provider: str
    provider_event_id: str
    bookmaker: str
    market: str
    selection: str
    line: str
    decimal_odds: float
    observed_at: str
    commence_time: str
    odds_type: OddsType = OddsType.BOOKMAKER
    raw_market: str = ""
    raw_selection: str = ""

    def __post_init__(self):
        if self.odds_type not in QUOTE_TYPES:
            raise InvalidQuote(f"{self.odds_type.value} is not a price and cannot be a quote")
        if self.market not in MARKETS or self.selection not in MARKETS[self.market]:
            raise InvalidQuote(f"not a canonical selection: {self.market}/{self.selection}")
        if (self.market in LINE_MARKETS) != bool(self.line):
            raise InvalidQuote(f"line {self.line!r} is invalid for market {self.market}")
        if not self.bookmaker:
            raise InvalidQuote("bookmaker identity is required")

    @property
    def implied_probability(self) -> float:
        return round(1.0 / self.decimal_odds, 6)


def model_fair_odds(probability: float) -> float | None:
    """Fair price implied by a model probability (0-1). Display only; never a quote."""
    if not 0.0 < probability < 1.0:
        return None
    return round(1.0 / probability, 3)

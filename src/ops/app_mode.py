"""Demo launcher mode: APP_MODE=demo (default) or APP_MODE=live (2026-11-05).

Read once at start-up by scripts/demo_server.py, from the environment or the launching worktree's .env:

    APP_MODE=demo|live                 demo: cached snapshot, API-Football only through an owner grant and an
                                       explicit 'Refresh fixtures' (unchanged).
                                       live: fixtures fetched automatically within a daily request budget.
    API_FOOTBALL_DAILY_BUDGET=30       live only: requests per UTC day this process may send (1..LIVE_BUDGET_MAX).
                                       The provider's own remaining count must also stay above the provider floor,
                                       and a key shared with the shadow experiment still needs the shared ledger.
    LIVE_TODAY_REFRESH_MINUTES=120     live only: re-fetch today at most this often.
    LIVE_TOMORROW_REFRESH_MINUTES=360  live only: re-fetch tomorrow at most this often.
    LIVE_MANUAL_MIN_MINUTES=10         live only: a manual refresh of a date is refused within this time of its
                                       last update.
    LIVE_FIXTURE_DAYS_AHEAD=1          live only: fetch fixtures up to this many days ahead (1..7). Keep 1 on the
                                       free plan (it refuses later dates); 7 on a paid plan for the 7-day
                                       team-history prefetch. Days after tomorrow refresh like tomorrow.
    TEAM_HISTORY_FETCH=off             live only: on = fetch a team's last results by provider team id
                                       (src/ops/team_history_fetch.py). Needs a paid plan.

Live mode fetches only yesterday, today and tomorrow (display time zone): the API-Football free plan refuses
other dates. Yesterday is fetched again until all of its fixtures are finished; that is the daily results
collection (finished results are stored and replayed by the existing fixture service). Nothing in this module
sends a request or reads a key.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta

DEMO = "demo"
LIVE = "live"
DEFAULT_DAILY_BUDGET = 30
LIVE_BUDGET_MAX = 7500          # never more than the largest plan's daily limit; the provider floor still applies
FINISHED = {"FT", "AET", "PEN", "AWD", "WO", "CANC", "ABD", "PST"}   # no later change expected


@dataclass(frozen=True)
class ModeSettings:
    mode: str
    daily_budget: int | None
    today_minutes: int
    tomorrow_minutes: int
    manual_min_minutes: int
    days_ahead: int = 1

    @property
    def live(self) -> bool:
        return self.mode == LIVE


def _int(env: dict, name: str, default: int, low: int, high: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}, got {value}")
    return value


def settings(env: dict | None = None) -> ModeSettings:
    """The validated mode. An unknown APP_MODE or an out-of-range number stops the launcher."""
    env = os.environ if env is None else env
    mode = (env.get("APP_MODE") or DEMO).strip().lower()
    if mode not in (DEMO, LIVE):
        raise ValueError(f"APP_MODE must be 'demo' or 'live', got {mode!r}")
    budget = _int(env, "API_FOOTBALL_DAILY_BUDGET", DEFAULT_DAILY_BUDGET, 1, LIVE_BUDGET_MAX) if mode == LIVE else None
    return ModeSettings(mode, budget,
                        _int(env, "LIVE_TODAY_REFRESH_MINUTES", 120, 15, 24 * 60),
                        _int(env, "LIVE_TOMORROW_REFRESH_MINUTES", 360, 30, 24 * 60),
                        _int(env, "LIVE_MANUAL_MIN_MINUTES", 10, 1, 24 * 60),
                        _int(env, "LIVE_FIXTURE_DAYS_AHEAD", 1, 1, 7))


def _age_minutes(updated_at: str | None, now: datetime) -> float | None:
    if not updated_at:
        return None
    t = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=now.tzinfo)
    return (now - t).total_seconds() / 60


def refresh_due(s: ModeSettings, date_str: str, today: str, updated_at: str | None, fixtures: list | None,
                now: datetime) -> tuple[bool, str]:
    """Whether live mode should fetch `date_str` now, and why (for the log and the interface)."""
    if not s.live:
        return False, "demo mode"
    d, t = date.fromisoformat(date_str), date.fromisoformat(today)
    if not t - timedelta(days=1) <= d <= t + timedelta(days=s.days_ahead):
        return False, (f"outside yesterday..today+{s.days_ahead} (LIVE_FIXTURE_DAYS_AHEAD; the free plan refuses "
                       "dates after tomorrow)")
    age = _age_minutes(updated_at, now)
    if age is None:
        return True, "not fetched yet"
    if d < t:
        open_ = [f for f in fixtures or [] if (f.get("status") or "NS") not in FINISHED]
        if not open_:
            return False, "yesterday is complete"
        return (age >= s.today_minutes, f"{len(open_)} fixtures of yesterday not finished")
    limit = s.today_minutes if d == t else s.tomorrow_minutes
    return (age >= limit, f"last update {int(age)} min ago (every {limit} min)")

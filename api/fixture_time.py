"""Explicit fixture time handling for the demo: UTC inside, one named time zone for display.

The fixture cache is stored by provider "storage date" with local `date`/`time` strings and no UTC
time. Their basis changed silently: Europe/Istanbul (UTC+3) until ~2026-09-20, then Asia/Tehran
(UTC+3:30, since a76ab45). New API-Football fixtures carry `kickoff_utc` (exact). For older ones,
a file's basis is inferred only when its API-Football kick-off minutes are decisive:
most UTC kick-offs are on the hour, so a +3:30 basis shows mostly ':30' and a +3:00 basis mostly
':00'. With fewer than MIN_SAMPLES fixtures or no 2:1 majority the basis is "unknown", and those
fixtures are shown unconverted and flagged (never moved to another date).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_DISPLAY_TZ = "Europe/Istanbul"
TEHRAN = "Asia/Tehran"
ISTANBUL = "Europe/Istanbul"
MIN_SAMPLES = 20
CONVERTIBLE_PROVIDERS = {"api_football"}


def zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def today(tz: str = DEFAULT_DISPLAY_TZ, now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(zone(tz)).date().isoformat()


def _valid_time(t) -> bool:
    return isinstance(t, str) and len(t) == 5 and t[2] == ":" and t[:2].isdigit() and t[3:].isdigit()


def infer_basis(fixtures: list, storage_date: str) -> str | None:
    minutes = [f["time"][3:] for f in fixtures
               if isinstance(f, dict) and f.get("provider") in CONVERTIBLE_PROVIDERS and not f.get("kickoff_utc")
               and f.get("date") == storage_date and _valid_time(f.get("time"))]
    if len(minutes) < MIN_SAMPLES:
        return None
    half, whole = minutes.count("30"), minutes.count("00")
    if half >= 2 * max(whole, 1):
        return TEHRAN
    if whole >= 2 * max(half, 1):
        return ISTANBUL
    return None


def to_utc(fixture: dict, basis: str | None) -> tuple[datetime | None, str]:
    """(UTC kick-off, how it is known): 'utc' (stored), 'inferred:<zone>', or (None, 'unknown')."""
    raw = fixture.get("kickoff_utc")
    if raw:
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc), "utc"
        except ValueError:
            pass
    if basis and fixture.get("provider") in CONVERTIBLE_PROVIDERS and _valid_time(fixture.get("time")):
        try:
            local = datetime.fromisoformat(f"{fixture['date']}T{fixture['time']}").replace(tzinfo=zone(basis))
            return local.astimezone(timezone.utc), f"inferred:{basis}"
        except (KeyError, ValueError):
            pass
    return None, "unknown"


def day_window(display_date: str, tz: str) -> tuple[datetime, datetime]:
    d = date.fromisoformat(display_date)
    start = datetime(d.year, d.month, d.day, tzinfo=zone(tz))
    nxt = d + timedelta(days=1)
    return start.astimezone(timezone.utc), datetime(nxt.year, nxt.month, nxt.day, tzinfo=zone(tz)).astimezone(timezone.utc)


def day_view(display_date: str, tz: str, sources: dict[str, list]) -> list[dict]:
    """Fixtures whose kick-off falls on `display_date` in `tz`, from storage-date lists `sources`
    (normally the day before, the day and the day after). Each carries `kickoff_utc` (ISO, UTC) when
    known, `date`/`time` in `tz`, `display_timezone` and `time_basis`."""
    start, end = day_window(display_date, tz)
    picked: dict[str, tuple[int, dict]] = {}
    for storage_date, fixtures in sources.items():
        basis = infer_basis(fixtures, storage_date)
        for f in fixtures:
            if not isinstance(f, dict):
                continue
            utc, how = to_utc(f, basis)
            if utc is None:
                if storage_date != display_date or f.get("date") != display_date:
                    continue
                item, rank = {**f, "kickoff_utc": None, "display_timezone": None, "time_basis": "unknown"}, 0
            elif start <= utc < end:
                local = utc.astimezone(zone(tz))
                item = {**f, "date": local.date().isoformat(), "time": local.strftime("%H:%M"),
                        "kickoff_utc": utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "display_timezone": tz, "time_basis": how}
                rank = 2 if how == "utc" else 1
            else:
                continue
            key = str(f.get("id"))
            if key not in picked or rank > picked[key][0]:
                picked[key] = (rank, item)
    return sorted((item for _, item in picked.values()), key=lambda x: (x.get("kickoff_utc") or "~", x.get("time") or ""))

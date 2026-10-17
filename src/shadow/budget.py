"""Request-budget estimates for the 14-day shadow profiles (M3.3 Package E).

Fixture volumes come from the competitions' formats (league matches per
season spread over the season's weeks; UEFA league-phase matchdays), because the
stored fixtures cover only 25 of 61 days. They are assumptions and are reported
as such.
"""
from __future__ import annotations

# Average tracked fixtures per week in the autumn (domestic: season matches / ~38 weeks; UEFA: league
# phase 144 matches each for UCL/UEL, 108 for UECL, over ~10 weeks with matchdays in about 2 of 3 weeks).
WEEKLY_FIXTURES = {
    "england-premier-league": 10, "england-championship": 13, "spain-la-liga": 10, "italy-serie-a": 10,
    "germany-bundesliga": 9, "france-ligue-1": 9, "netherlands-eredivisie": 9, "portugal-primeira-liga": 9,
    "belgium-pro-league": 8, "turkey-super-lig": 9, "scotland-premiership": 6,
    "uefa-champions-league": 12, "uefa-europa-league": 12, "uefa-conference-league": 9,
}
SATURDAY_SHARE = 0.42      # share of a week's domestic fixtures on the busiest day (observed peak 53 of ~125)
DISCOVERY_PER_DAY = 3      # today + tomorrow once per day (rediscover_after_hours=20) + one results date


def estimate(competitions=None, closing_capture: bool = False, days: int = 14, daily_budget: int = 70) -> dict:
    keys = list(competitions) if competitions else list(WEEKLY_FIXTURES)
    weekly = sum(WEEKLY_FIXTURES[k] for k in keys)
    fixtures = round(weekly * days / 7)
    domestic_weekly = sum(v for k, v in WEEKLY_FIXTURES.items() if k in keys and not k.startswith("uefa"))
    peak_fixtures = round(domestic_weekly * SATURDAY_SHARE)
    per_fixture = 2 if closing_capture else 1
    total = fixtures * per_fixture + DISCOVERY_PER_DAY * days
    peak = peak_fixtures * per_fixture + DISCOVERY_PER_DAY
    closing_lost = max(0, peak - daily_budget) if closing_capture else 0
    return {"competitions": len(keys), "fixtures_14d": fixtures, "requests_14d": total,
            "requests_per_day_avg": round(total / days, 1), "peak_day_requests": peak,
            "peak_day_fixtures": peak_fixtures, "within_daily_budget_on_peak": peak <= daily_budget,
            "closing_fetches_lost_on_peak": closing_lost}

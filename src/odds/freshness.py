"""Quote-age measurement for the freshness policy (M3 Phase C).

The age of a quote at decision time is ``decision_time - observed_at`` (the
provider's own update time), counting only quotes this system had fetched by
the decision time. Ages are reported in fixed buckets so the owner can choose
an empirically supported ``ODDS_MAX_AGE_MINUTES``. A quote inside the window is
a price the provider published then; it is not proof that a bet could have
been placed at that price.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime

from src.odds.model import iso_utc

BUCKETS = ("0-60", "61-120", "121-240", ">240")
MODEL_MARKET_FILTER = "((market = '1X2' AND line = '') OR (market = 'TOTALS' AND line = '2.5') OR (market = 'BTTS' AND line = ''))"


def age_minutes(decision_time, observed_at) -> float:
    return (datetime.fromisoformat(iso_utc(decision_time))
            - datetime.fromisoformat(iso_utc(observed_at))).total_seconds() / 60.0


def bucket(minutes: float) -> str:
    if minutes <= 60:
        return "0-60"
    if minutes <= 120:
        return "61-120"
    if minutes <= 240:
        return "121-240"
    return ">240"


def fixture_quote_status(conn: sqlite3.Connection, fixture_id: str, decision_time: datetime) -> dict:
    """Freshest usable model-market quote for one fixture as of decision_time, or why there is none."""
    as_of = iso_utc(decision_time)
    row = conn.execute(
        f"""SELECT MAX(observed_at), MIN(commence_time) FROM odds_quotes
            WHERE fixture_id = ? AND odds_type = 'bookmaker' AND {MODEL_MARKET_FILTER}
              AND fetched_at <= ? AND observed_at <= ? AND observed_at < commence_time""",
        (fixture_id, as_of, as_of),
    ).fetchone()
    latest, commence = row
    if latest is None:
        mapped = conn.execute(
            "SELECT 1 FROM fixture_identity WHERE fixture_id = ? UNION ALL "
            "SELECT 1 FROM matches WHERE id = ? AND provider = 'api_football' LIMIT 1",
            (fixture_id, fixture_id),
        ).fetchone()
        return {"fixture_id": fixture_id, "status": "NO_QUOTES" if mapped else "UNMAPPED",
                "age_minutes": None, "bucket": None}
    if commence <= as_of:
        return {"fixture_id": fixture_id, "status": "STARTED", "age_minutes": None, "bucket": None}
    minutes = round(age_minutes(as_of, latest), 1)
    return {"fixture_id": fixture_id, "status": "QUOTED", "age_minutes": minutes, "bucket": bucket(minutes)}


def coverage_report(conn: sqlite3.Connection, fixture_ids: list[str], decision_time: datetime,
                    max_age_minutes: int) -> dict:
    """Coverage and rejection counts per age bucket for fixtures decided at decision_time."""
    statuses = [fixture_quote_status(conn, fid, decision_time) for fid in fixture_ids]
    total = len(statuses)
    by_status = Counter(s["status"] for s in statuses)
    by_bucket = Counter(s["bucket"] for s in statuses if s["bucket"])
    within = sum(1 for s in statuses if s["age_minutes"] is not None and s["age_minutes"] <= max_age_minutes)

    def pct(n: int) -> float:
        return round(100.0 * n / total, 1) if total else 0.0

    return {
        "decision_time": iso_utc(decision_time),
        "fixtures": total,
        "max_age_minutes": max_age_minutes,
        "by_status": dict(by_status),
        "by_bucket": {b: {"fixtures": by_bucket.get(b, 0), "pct": pct(by_bucket.get(b, 0)),
                          "accepted": by_bucket.get(b, 0) if _bucket_upper(b) <= max_age_minutes else 0}
                      for b in BUCKETS},
        "covered_within_window": within,
        "coverage_pct": pct(within),
        "rejected_pct": pct(total - within),
        "fixtures_detail": statuses,
    }


def _bucket_upper(name: str) -> float:
    return {"0-60": 60, "61-120": 120, "121-240": 240}.get(name, float("inf"))

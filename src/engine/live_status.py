"""
Real-time match status: provider refresh, kickoff-based recompute, and UI enrichment.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from src.config import logger

LIVE_STATUS_MAX_AGE_SECONDS = 60
STALE_UI_SECONDS = 90
MATCH_SOFT_END_MINUTES = 120
MATCH_HARD_END_MINUTES = 130
LIVE_WINDOW_MINUTES = 120

FINISHED_STATUSES = frozenset({"FT", "AET", "PEN"})
PROVIDER_LIVE_STATUSES = frozenset({"LIVE", "1H", "2H", "HT", "ET", "BT", "P"})
PENDING_STATUSES = frozenset({
    "LIVE_STATUS_PENDING",
    "LIVE_PENDING_PROVIDER",
    "LIKELY_LIVE_OR_HT",
    "RESULT_PENDING",
})
UPCOMING_STATUSES = frozenset({"NS", "TBD"})

_STATUS_LABELS = {
    "NS": "Not Started",
    "TBD": "Not Started",
    "LIVE": "Live",
    "1H": "Live",
    "2H": "Live",
    "HT": "Half Time",
    "ET": "Live",
    "FT": "Full Time",
    "AET": "Full Time",
    "PEN": "Full Time",
    "LIVE_STATUS_PENDING": "Live status pending provider",
    "LIVE_PENDING_PROVIDER": "Live status pending provider",
    "LIKELY_LIVE_OR_HT": "Live status needs refresh",
    "RESULT_PENDING": "Result pending from provider",
    "PST": "Postponed",
    "CANC": "Cancelled",
}

_LOCK = threading.Lock()
_LAST_STATUS_REFRESH: Optional[datetime] = None
_NEXT_STATUS_REFRESH: Optional[datetime] = None
_PROVIDER_ERRORS: list[str] = []


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_matches_live_columns(conn) -> None:
    cur = conn.execute("PRAGMA table_info(matches)")
    cols = {row[1] for row in cur.fetchall()}
    cur.close()
    for name, typ in (
        ("elapsed", "INTEGER"),
        ("last_live_update", "TEXT"),
        ("source", "TEXT"),
        ("status_detail", "TEXT"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE matches ADD COLUMN {name} {typ}")
    conn.commit()


def _parse_kickoff_utc(fixture: dict) -> Optional[datetime]:
    raw = fixture.get("kickoff_iso") or fixture.get("kickoff") or fixture.get("kickoff_time")
    if raw is None or raw == "":
        return None
    try:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(int(raw), tz=timezone.utc)
        s = str(raw)
        if s.isdigit():
            return datetime.fromtimestamp(int(s), tz=timezone.utc)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def recompute_match_status(fixture: dict, now: Optional[datetime] = None) -> dict:
    """
    Authoritative status from server time + kickoff + provider hint.
    Never keep LIVE forever — old DB/provider LIVE is overridden after 120 minutes,
    and matches older than 130 minutes are considered finished or result pending.
    """
    now = now or _now()
    out = dict(fixture)
    raw = (fixture.get("status") or "NS").upper()
    kick = _parse_kickoff_utc(fixture)
    has_score = fixture.get("home_goals") is not None and fixture.get("away_goals") is not None
    fresh = _freshness_seconds(fixture, now)
    provider_fresh = fresh is not None and fresh <= STALE_UI_SECONDS

    if raw in FINISHED_STATUSES:
        out["status"] = raw
        return out

    if not kick:
        if raw in UPCOMING_STATUSES:
            out["status"] = raw
        elif raw in PROVIDER_LIVE_STATUSES and provider_fresh:
            out["status"] = raw
        else:
            out["status"] = "RESULT_PENDING" if not has_score and raw not in UPCOMING_STATUSES else raw
        return out

    mins_since = (now - kick).total_seconds() / 60.0

    if mins_since < 0:
        out["status"] = "NS"
        return out

    if mins_since > MATCH_HARD_END_MINUTES:
        out["status"] = "FT" if has_score else "RESULT_PENDING"
        return out

    if mins_since > MATCH_SOFT_END_MINUTES:
        if has_score:
            out["status"] = "FT"
            return out
        out["status"] = "LIVE_STATUS_PENDING"
        return out

    if raw in PROVIDER_LIVE_STATUSES or raw == "LIVE":
        if provider_fresh and mins_since <= MATCH_HARD_END_MINUTES:
            out["status"] = raw if raw in ("HT", "1H", "2H", "ET") else "LIVE"
            return out
        out["status"] = "LIVE_STATUS_PENDING"
        return out

    if raw in UPCOMING_STATUSES and 0 <= mins_since <= LIVE_WINDOW_MINUTES:
        out["status"] = "LIVE_STATUS_PENDING"
        return out

    if mins_since <= LIVE_WINDOW_MINUTES:
        out["status"] = "LIVE_STATUS_PENDING"
        return out

    out["status"] = "RESULT_PENDING" if not has_score else "FT"
    return out


def apply_time_based_fallback(fixture: dict, now: Optional[datetime] = None) -> dict:
    """Alias — always use full recompute."""
    return recompute_match_status(fixture, now)


def _status_label(status: str) -> str:
    return _STATUS_LABELS.get((status or "NS").upper(), status or "NS")


def _freshness_seconds(fixture: dict, now: datetime) -> Optional[int]:
    raw = fixture.get("last_live_update")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0, int((now - ts.astimezone(timezone.utc)).total_seconds()))
    except Exception:
        return None


def enrich_fixture(fixture: dict, server_now: Optional[datetime] = None) -> dict:
    now = server_now or _now()
    fx = recompute_match_status(dict(fixture), now)
    status = (fx.get("status") or "NS").upper()
    fresh = _freshness_seconds(fx, now)

    is_finished = status in FINISHED_STATUSES
    is_result_pending = status == "RESULT_PENDING" or status in PENDING_STATUSES
    is_upcoming = status in UPCOMING_STATUSES and not is_finished and not is_result_pending
    is_live = (
        status in PROVIDER_LIVE_STATUSES
        and status not in PENDING_STATUSES
        and not is_finished
    )

    is_stale = False
    if is_live and fresh is not None and fresh > STALE_UI_SECONDS:
        is_stale = True
    if status in PENDING_STATUSES and fresh is not None and fresh > STALE_UI_SECONDS:
        is_stale = True
    if status in PENDING_STATUSES and fresh is None and not is_finished:
        is_stale = True

    fx["status"] = status
    fx["status_label"] = _status_label(status)
    fx["kickoff_time"] = fx.get("kickoff_iso") or fx.get("kickoff")
    fx["server_time"] = now.isoformat()
    fx["is_live"] = is_live
    fx["is_finished"] = is_finished
    fx["is_upcoming"] = is_upcoming
    fx["is_result_pending"] = is_result_pending and not is_finished
    fx["is_stale"] = is_stale
    fx["freshness_seconds"] = fresh
    return fx


def enrich_fixtures_list(fixtures: list[dict], server_now: Optional[datetime] = None) -> list[dict]:
    now = server_now or _now()
    return [enrich_fixture(f, now) for f in fixtures]


def _fetch_provider_fixtures(date_str: str) -> tuple[dict[str, dict], list[str]]:
    from src.data.fixtures_fetcher import (
        _fetch_apifootball,
        _fetch_openligadb,
        _dedupe_fixtures,
    )

    errors: list[str] = []
    batches: list[dict] = []

    try:
        af_batch, _, err = _fetch_apifootball(date_str)
        if af_batch:
            batches.extend(af_batch)
        if err:
            errors.append(f"apifootball: {err}")
    except Exception as exc:
        errors.append(f"apifootball: {exc}")

    try:
        ol = _fetch_openligadb(date_str)
        if ol:
            batches.extend(ol)
    except Exception as exc:
        errors.append(f"openligadb: {exc}")

    by_id: dict[str, dict] = {}
    for fx in _dedupe_fixtures(batches):
        mid = str(fx.get("id", ""))
        if mid:
            by_id[mid] = fx
    return by_id, errors


def _row_to_fixture_dict(row: dict, date_str: str) -> dict:
    from src.data.fixtures_fetcher import _kickoff_meta

    kickoff = row.get("kickoff") or ""
    ko = _kickoff_meta(kickoff if str(kickoff).isdigit() else None)
    return {
        "id": str(row.get("id", "")),
        "date": date_str,
        "time": ko.get("time", "TBD"),
        "kickoff_iso": ko.get("kickoff_iso"),
        "status": row.get("status") or "NS",
        "status_detail": row.get("status_detail"),
        "elapsed": row.get("elapsed"),
        "home_goals": row.get("home_goals"),
        "away_goals": row.get("away_goals"),
        "last_live_update": row.get("last_live_update"),
        "source": row.get("source") or "local",
        "league": {
            "id": str(row.get("league_id") or 0),
            "name": row.get("league_name") or "Unknown",
            "country": "",
            "logo": "",
        },
        "home_team": {"id": "", "name": row.get("home_team") or "", "logo": ""},
        "away_team": {"id": "", "name": row.get("away_team") or "", "logo": ""},
    }


def _persist_match_row(conn, row: dict) -> None:
    from src.db.match_repo import upsert_match

    upsert_match(
        conn,
        {
            "id": row["id"],
            "date": row["date"],
            "kickoff": row.get("kickoff") or "",
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "league_name": row["league_name"],
            "league_id": row.get("league_id") or 0,
            "status": row.get("status") or "NS",
            "home_goals": row.get("home_goals"),
            "away_goals": row.get("away_goals"),
            "elapsed": row.get("elapsed"),
            "last_live_update": row.get("last_live_update"),
            "source": row.get("source"),
            "status_detail": row.get("status_detail"),
        },
    )


def _handle_newly_finished(conn, row: dict, prev_status: str) -> bool:
    status = (row.get("status") or "").upper()
    if status not in FINISHED_STATUSES:
        return False
    if (prev_status or "").upper() in FINISHED_STATUSES:
        return False
    if row.get("home_goals") is None or row.get("away_goals") is None:
        return False

    try:
        from src.engine.live_updater import on_match_finished

        on_match_finished(
            conn=conn,
            match_id=str(row["id"]),
            match_date=str(row["date"]),
            league=str(row.get("league_name") or ""),
            home_team=str(row["home_team"]),
            away_team=str(row["away_team"]),
            home_goals=int(row["home_goals"]),
            away_goals=int(row["away_goals"]),
        )
        return True
    except Exception as exc:
        logger.warning("on_match_finished failed for %s: %s", row.get("id"), exc)
        return False


def update_live_match_statuses(
    date_str: Optional[str] = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    global _LAST_STATUS_REFRESH, _NEXT_STATUS_REFRESH, _PROVIDER_ERRORS

    date_str = date_str or date.today().isoformat()
    now = _now()
    now_iso = now.isoformat()

    with _LOCK:
        if not force and _LAST_STATUS_REFRESH:
            age = (now - _LAST_STATUS_REFRESH).total_seconds()
            if age < LIVE_STATUS_MAX_AGE_SECONDS:
                return {
                    "status": "skipped",
                    "reason": "fresh",
                    "age_seconds": age,
                    "last_status_refresh": _LAST_STATUS_REFRESH.isoformat(),
                }

    from src.db.database import get_db
    from src.db.match_repo import get_matches_by_date

    conn = get_db()
    from src.db.match_repo import ensure_matches_live_columns as ensure_cols

    ensure_cols(conn)

    provider_map, errors = _fetch_provider_fixtures(date_str)
    rows = get_matches_by_date(conn, date_str)

    changed: list[dict] = []
    finished_new = 0

    for row in rows:
        prev_status = row.get("status") or "NS"
        mid = str(row.get("id", ""))
        base = _row_to_fixture_dict(row, date_str)

        if mid in provider_map:
            prov = provider_map[mid]
            merged = {
                **base,
                "status": prov.get("status") or prev_status,
                "status_detail": prov.get("status_detail"),
                "elapsed": prov.get("elapsed"),
                "home_goals": prov.get("home_goals")
                if prov.get("home_goals") is not None
                else row.get("home_goals"),
                "away_goals": prov.get("away_goals")
                if prov.get("away_goals") is not None
                else row.get("away_goals"),
                "source": prov.get("source") or "provider",
                "last_live_update": now_iso,
            }
        else:
            merged = {**base, "last_live_update": row.get("last_live_update") or now_iso}

        merged = recompute_match_status(merged, now)
        new_status = (merged.get("status") or "NS").upper()

        score_changed = (
            merged.get("home_goals") != row.get("home_goals")
            or merged.get("away_goals") != row.get("away_goals")
        )
        status_changed = new_status != (prev_status or "NS").upper()

        if status_changed or score_changed or force:
            persist = {
                "id": mid,
                "date": date_str,
                "kickoff": row.get("kickoff") or "",
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "league_name": row.get("league_name"),
                "league_id": row.get("league_id") or 0,
                "status": new_status,
                "home_goals": merged.get("home_goals"),
                "away_goals": merged.get("away_goals"),
                "elapsed": merged.get("elapsed"),
                "last_live_update": merged.get("last_live_update", now_iso),
                "source": merged.get("source") or row.get("source"),
                "status_detail": merged.get("status_detail"),
            }
            _persist_match_row(conn, persist)
            if status_changed or score_changed:
                changed.append(enrich_fixture({**base, **persist}, now))
            if _handle_newly_finished(conn, persist, prev_status):
                finished_new += 1

    existing_ids = {str(r["id"]) for r in rows}
    for mid, prov in provider_map.items():
        if mid not in existing_ids:
            from src.data.fixtures_fetcher import _sync_fixtures_to_matches_db

            _sync_fixtures_to_matches_db([prov], date_str)

    try:
        from src.engine.automation import run_result_ingest

        run_result_ingest()
    except Exception as exc:
        logger.debug("Result ingest after status refresh: %s", exc)

    try:
        from src.data.fixtures_fetcher import (
            _fetch_local_components,
            _dedupe_fixtures,
            _persist_fixtures_snapshot,
        )

        snap, live, hist = _fetch_local_components(date_str)
        merged = enrich_fixtures_list(_dedupe_fixtures(snap + live + hist), now)
        _persist_fixtures_snapshot(date_str, merged, "live_status")
    except Exception as exc:
        logger.debug("Could not persist snapshot after status refresh: %s", exc)

    with _LOCK:
        _LAST_STATUS_REFRESH = now
        _NEXT_STATUS_REFRESH = now + timedelta(seconds=LIVE_STATUS_MAX_AGE_SECONDS)
        _PROVIDER_ERRORS = errors

    return {
        "status": "ok",
        "date": date_str,
        "matches_checked": len(rows),
        "changed_count": len(changed),
        "finished_ingested": finished_new,
        "changed_matches": changed[:50],
        "provider_errors": errors,
        "last_status_refresh": now.isoformat(),
        "next_status_refresh": (now + timedelta(seconds=LIVE_STATUS_MAX_AGE_SECONDS)).isoformat(),
    }


def update_live_match_statuses_if_stale(
    date_str: str,
    *,
    max_age_seconds: int = LIVE_STATUS_MAX_AGE_SECONDS,
    force: bool = False,
) -> Optional[dict]:
    if date_str != date.today().isoformat():
        return None
    with _LOCK:
        last = _LAST_STATUS_REFRESH
    if not force and last:
        age = (_now() - last).total_seconds()
        if age < max_age_seconds:
            return None
    return update_live_match_statuses(date_str, force=force)


def get_live_dashboard_status() -> dict[str, Any]:
    from src.db.database import get_db
    from src.db.match_repo import get_matches_by_date

    now = _now()
    today = date.today().isoformat()
    conn = get_db()
    ensure_matches_live_columns(conn)
    rows = get_matches_by_date(conn, today)

    live_count = finished_count = stale_count = 0
    for row in rows:
        fx = enrich_fixture(_row_to_fixture_dict(row, today), now)
        if fx.get("is_live"):
            live_count += 1
        if fx.get("is_finished"):
            finished_count += 1
        if fx.get("is_stale"):
            stale_count += 1

    with _LOCK:
        last = _LAST_STATUS_REFRESH.isoformat() if _LAST_STATUS_REFRESH else None
        nxt = _NEXT_STATUS_REFRESH.isoformat() if _NEXT_STATUS_REFRESH else None
        errors = list(_PROVIDER_ERRORS)

    return {
        "server_time": now.isoformat(),
        "live_matches_count": live_count,
        "finished_today_count": finished_count,
        "stale_matches_count": stale_count,
        "last_status_refresh": last,
        "next_status_refresh": nxt,
        "provider_errors": errors,
    }

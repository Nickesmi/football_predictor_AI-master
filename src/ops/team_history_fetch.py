"""On-demand team history from API-Football (2026-11-10). OFF by default.

Enabled only when both APP_MODE=live and TEAM_HISTORY_FETCH=on. The owner switches it on after the paid-plan
upgrade: the free plan refuses current-season team queries (`fixtures?team=ID&last=N`).

What it does, for one API-Football team id:
  * one request `fixtures?team=<id>&last=<TEAM_HISTORY_LAST>` (default 20, all competitions);
  * every finished fixture (FT/AET/PEN with a score) is stored in `matches` and `match_history`, filed under
    the record its provider team id is bound to in that league (src/engine/live_updater.resolve_record:
    provider id, then exact or normalised name, never a substring), and the binding is written;
  * fixtures already stored (same provider fixture id) are skipped; nothing is overwritten;
  * one provenance row per fetch in `team_history_fetches`.
It does not replay EWMA/ELO team state (results arrive out of date order); the fallback chain
(src/identity/fallback_chain.py) reads the stored rows directly, by date, before each kick-off.

Requests go through the normal HTTP stack, so the demo launcher's daily budget, provider floor and shared
ledger (src/ops/demo_quota.py) apply to every one of them. A refused or failed request stops the pass.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

FINISHED = ("FT", "AET", "PEN")
REFETCH_HOURS = 24


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return ((env.get("APP_MODE") or "").strip().lower() == "live"
            and (env.get("TEAM_HISTORY_FETCH") or "").strip().lower() in ("on", "1", "true", "yes"))


def last_n(env=None) -> int:
    env = os.environ if env is None else env
    try:
        return max(5, min(50, int(env.get("TEAM_HISTORY_LAST") or 20)))
    except ValueError:
        return 20


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS team_history_fetches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider TEXT NOT NULL, provider_team_id TEXT NOT NULL, team_name TEXT,
        fetched_at TEXT NOT NULL, request TEXT NOT NULL, outcome TEXT NOT NULL,
        fixtures INTEGER NOT NULL DEFAULT 0, finished INTEGER NOT NULL DEFAULT 0,
        inserted INTEGER NOT NULL DEFAULT 0, already_stored INTEGER NOT NULL DEFAULT 0)""")


def recently_fetched(conn: sqlite3.Connection, team_id, now: datetime | None = None) -> bool:
    _ensure(conn)
    now = now or datetime.now(timezone.utc)
    row = conn.execute("SELECT MAX(fetched_at) FROM team_history_fetches WHERE provider = 'api_football' "
                       "AND provider_team_id = ? AND outcome IN ('ok', 'provider_refused')",
                       (str(team_id),)).fetchone()
    if not row or not row[0]:
        return False
    return datetime.fromisoformat(row[0]) > now - timedelta(hours=REFETCH_HOURS)


def provider_refused_recently(conn: sqlite3.Connection, now: datetime | None = None) -> bool:
    """The provider refused a team query in the last REFETCH_HOURS (e.g. the free plan): no pass until then."""
    _ensure(conn)
    now = now or datetime.now(timezone.utc)
    row = conn.execute("SELECT MAX(fetched_at) FROM team_history_fetches WHERE outcome = 'provider_refused'").fetchone()
    return bool(row and row[0] and datetime.fromisoformat(row[0]) > now - timedelta(hours=REFETCH_HOURS))


def stored_count(conn: sqlite3.Connection, team_id, before: str) -> int:
    """Stored results before `before` linked to this provider team id (any competition)."""
    from src.identity import fallback_chain as fc
    idx = fc.history_index(conn)
    return fc.team_recent(idx, fc.team_record_keys(conn, "api_football", team_id, None), before, 10 ** 6).n


def _default_get(endpoint: str, params: dict) -> dict:
    from src.data.api_client import APIFootballClient
    return APIFootballClient().get(endpoint, **params)


def store_fixtures(conn: sqlite3.Connection, raw_fixtures: list[dict]) -> dict:
    """Store finished API-Football fixtures (raw v3 shape). Returns counts."""
    from api.services.fixtures_service import _api_football_to_fixture
    from src.engine.live_updater import resolve_record

    counts = {"fixtures": len(raw_fixtures), "finished": 0, "inserted": 0, "already_stored": 0}
    for raw in raw_fixtures:
        f = _api_football_to_fixture(raw)
        if f.get("status") not in FINISHED or f.get("home_goals") is None or f.get("away_goals") is None:
            continue
        counts["finished"] += 1
        mid = str(f["id"])
        if conn.execute("SELECT 1 FROM match_history WHERE match_id = ?", (mid,)).fetchone():
            counts["already_stored"] += 1
            continue
        league = f.get("league") or {}
        label = league.get("name") or "Unknown"
        home, away = f.get("home_team") or {}, f.get("away_team") or {}
        h_rec, _ = resolve_record(conn, home.get("name", ""), label, "api_football", home.get("id"))
        a_rec, _ = resolve_record(conn, away.get("name", ""), label, "api_football", away.get("id"))
        if h_rec == a_rec:
            continue
        conn.execute("""INSERT INTO matches (id, date, kickoff, home_team, away_team, league_name, league_id,
                        status, home_goals, away_goals, provider, is_stale)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'api_football', 0)
                        ON CONFLICT(id) DO NOTHING""",
                     (mid, f.get("date"), f.get("time"), home.get("name"), away.get("name"), label,
                      league.get("id"), f.get("status"), int(f["home_goals"]), int(f["away_goals"])))
        conn.execute("""INSERT OR IGNORE INTO match_history (match_id, match_date, league, home_team, away_team,
                        home_goals, away_goals) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                     (mid, f.get("date"), label, h_rec, a_rec, int(f["home_goals"]), int(f["away_goals"])))
        counts["inserted"] += 1
    conn.commit()
    return counts


def fetch_team(conn: sqlite3.Connection, team_id, team_name: str = "", *, http_get=None, env=None,
               cache_dir: Path | None = None) -> dict:
    """One request for one team; stores what it returns. Never raises for provider errors."""
    _ensure(conn)
    n = last_n(env)
    request = f"fixtures?team={team_id}&last={n}"
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    try:
        data = (http_get or _default_get)("fixtures", {"team": str(team_id), "last": n})
    except Exception as e:      # refused by the budget, network, HTTP error
        conn.execute("INSERT INTO team_history_fetches (provider, provider_team_id, team_name, fetched_at, request, "
                     "outcome) VALUES ('api_football', ?, ?, ?, ?, ?)",
                     (str(team_id), team_name, now, request, f"error: {type(e).__name__}"))
        conn.commit()
        return {"team_id": str(team_id), "outcome": "error", "error": type(e).__name__}
    errors = (data or {}).get("errors")
    if errors:
        conn.execute("INSERT INTO team_history_fetches (provider, provider_team_id, team_name, fetched_at, request, "
                     "outcome) VALUES ('api_football', ?, ?, ?, ?, ?)",
                     (str(team_id), team_name, now, request, "provider_refused"))
        conn.commit()
        return {"team_id": str(team_id), "outcome": "provider_refused",
                "errors": list(errors) if isinstance(errors, dict) else errors}
    raw = (data or {}).get("response") or []
    if cache_dir is not None:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        (Path(cache_dir) / f"team-history-{team_id}.json").write_text(json.dumps(raw), encoding="utf-8")
    counts = store_fixtures(conn, raw)
    conn.execute("INSERT INTO team_history_fetches (provider, provider_team_id, team_name, fetched_at, request, "
                 "outcome, fixtures, finished, inserted, already_stored) VALUES ('api_football', ?, ?, ?, ?, 'ok', "
                 "?, ?, ?, ?)", (str(team_id), team_name, now, request, counts["fixtures"], counts["finished"],
                                 counts["inserted"], counts["already_stored"]))
    conn.commit()
    return {"team_id": str(team_id), "outcome": "ok", **counts}


def upcoming_teams(cache_dir: Path, today: str, days: int = 7) -> list[tuple[str, str, str]]:
    """(team id, name, kick-off date) for every API-Football fixture cached from today to today+days."""
    out, seen = [], set()
    d0 = date.fromisoformat(today)
    for i in range(days + 1):
        day = (d0 + timedelta(days=i)).isoformat()
        p = Path(cache_dir) / f"fixtures-{day}.json"
        if not p.exists():
            continue
        try:
            fixtures = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for f in fixtures if isinstance(fixtures, list) else []:
            if (f.get("source") or f.get("provider")) != "api_football":
                continue
            for side in ("home_team", "away_team"):
                t = f.get(side) or {}
                tid = str(t.get("id") or "")
                if tid and tid not in seen:
                    seen.add(tid)
                    out.append((tid, t.get("name", ""), (f.get("kickoff_utc") or day)[:10]))
    return out


def prefetch_upcoming(conn: sqlite3.Connection, cache_dir: Path, today: str, *, days: int = 7, max_requests: int = 10,
                      threshold: int | None = None, http_get=None, env=None) -> dict:
    """Fetch history for teams in the next `days` days of cached fixtures that have fewer than `threshold`
    stored results. At most `max_requests` requests; stops at the first refused or failed one."""
    from src.identity.fallback_chain import FULL_HISTORY
    threshold = FULL_HISTORY if threshold is None else threshold
    report = {"candidates": 0, "requests": 0, "ok": 0, "stopped": None, "inserted": 0}
    if provider_refused_recently(conn):
        report["stopped"] = "provider_refused_within_24h"
        return report
    for tid, name, kick in upcoming_teams(cache_dir, today, days):
        if stored_count(conn, tid, kick) >= threshold or recently_fetched(conn, tid):
            continue
        report["candidates"] += 1
        if report["requests"] >= max_requests:
            continue
        r = fetch_team(conn, tid, name, http_get=http_get, env=env, cache_dir=Path(cache_dir) / "team-history")
        report["requests"] += 1
        if r["outcome"] != "ok":
            report["stopped"] = r["outcome"]
            break
        report["ok"] += 1
        report["inserted"] += r["inserted"]
    return report

"""One shadow-validation cycle: discover fixtures, collect real odds, observe decisions, settle.

Nothing here writes picks or touches the baseline. Each observation records the
forecast, its point-in-time inputs, the real quotes available at decision time
(fetched_at <= decision time), the legacy and v1 selections, and BILQE's
layer results. Observations are taken once per fixture, when it first falls
inside the decision window (0 < minutes to kickoff <= decision_lead_minutes).
Settlement uses API-Football's regulation-time score (score.fulltime), which is
what 1X2, O/U 2.5 and BTTS settle on. Missing data is recorded as missing.

API-Football requests (all through the budget guard, provider_quota_log and
the per-minute throttle):
  * GET /fixtures?date=   every discovery_interval_hours (12) for today and tomorrow
                          (UTC), plus the dates of observed fixtures still
                          awaiting a result;
  * odds, odds_mode "per_fixture" (default; the free plan serves current-season
    odds only per fixture or per date): GET /odds?fixture= once when a
    fixture enters the pre-decision window (mandatory), and once in the
    closing window (only while budget remains, for CLV);
    odds_mode "league_date" (paid plans): GET /odds?league=&season=&date=
    pages for league-date groups with a fixture due.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from src.odds import collector, freshness, identity, quota
from src.odds.model import InvalidQuote, iso_utc
from src.odds.providers import api_football
from src.odds.providers.the_odds_api import API_FOOTBALL_SPORT_KEYS
from src.shadow.legacy import LEGACY_POLICY_VERSION, legacy_select
from src.shadow.store import ensure_shadow_tables

TRACKED_LEAGUE_IDS = frozenset(API_FOOTBALL_SPORT_KEYS)
FINAL_STATUSES = frozenset({"FT", "AET", "PEN"})
NOT_STARTED = frozenset({"NS", "TBD"})
MODEL_TO_CANONICAL = {"1X2": ("1X2", ""), "O/U 2.5": ("TOTALS", "2.5"), "BTTS": ("BTTS", "")}
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ShadowConfig:
    decision_lead_minutes: int = 120
    lookahead_hours: int = 36
    discovery_interval_hours: int = 12
    refresh_minutes: int = 180
    pre_decision_refresh_minutes: int = 30
    closing_window_minutes: int = 45
    pre_decision_margin_minutes: int = 15
    odds_mode: str = "per_fixture"
    max_requests_per_cycle: int = 12
    max_attempts_per_window: int = 2   # odds attempts per fixture and window (failures are retried at most once)
    history: str = "rh1"   # "rh1": rebuilt point-in-time history each cycle; "team_state": identity-v1 on team_state
    competitions: tuple | None = None   # competition keys to observe; None = all 14 tracked
    closing_capture: bool = True        # extra fetch in the closing window (CLV)
    rediscover_after_hours: int = 20    # a date already discovered this recently is not fetched again
    cycle_interval_minutes: int = 30
    lease_minutes: int = 10
    # Observation waits for the decision fetch (e.g. request cap reached) until this close to kickoff.
    defer_observation_until_minutes: int = 60


DEFAULT = ShadowConfig()

LOW_COST_COMPETITIONS = ("england-premier-league", "spain-la-liga", "italy-serie-a", "germany-bundesliga",
                         "france-ligue-1", "uefa-champions-league")
PROFILES = {
    "low": ShadowConfig(competitions=LOW_COST_COMPETITIONS, closing_capture=False, max_requests_per_cycle=20),
    "medium": ShadowConfig(closing_capture=False, max_requests_per_cycle=20),
    "full": ShadowConfig(closing_capture=True, max_requests_per_cycle=20),
}

_ADDED_COLUMNS = {
    "shadow_cycles": (("status", "TEXT"), ("gap_minutes", "REAL"), ("missed_slots", "INTEGER")),
    "shadow_fixtures": (("home_team_id", "TEXT"), ("away_team_id", "TEXT")),
    "shadow_observations": (("identity_json", "TEXT"), ("v1_1_policy_version", "TEXT"), ("v1_1_json", "TEXT"),
                            ("v1_1_assuming_frozen_json", "TEXT"),
                            # M3.5 forward protocol: explicit ids and input availability per observation
                            ("manifest_id", "TEXT"), ("code_fingerprint", "TEXT"), ("competition_key", "TEXT"),
                            ("provider_league_id", "INTEGER"), ("provider_home_team_id", "TEXT"),
                            ("provider_away_team_id", "TEXT"), ("home_canonical_team_id", "TEXT"),
                            ("away_canonical_team_id", "TEXT"), ("inputs_available_json", "TEXT")),
}


def _ensure(conn: sqlite3.Connection) -> None:
    ensure_shadow_tables(conn)
    for table, columns in _ADDED_COLUMNS.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")


def _code_revision() -> str:
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT, capture_output=True,
                             text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=PROJECT_ROOT, capture_output=True,
                               text=True, timeout=5).stdout.strip()
        return f"{rev}+dirty" if dirty else rev
    except Exception:
        return "unknown"


def _minutes(a: datetime, b_iso: str) -> float:
    return (datetime.fromisoformat(iso_utc(b_iso)) - a).total_seconds() / 60.0


# ── Discovery and results ─────────────────────────────────────────────────────

def _ingest_fixture_list(conn, payload: dict, now: datetime, report: dict) -> None:
    for item in (payload or {}).get("response") or []:
        try:
            fx, league, teams = item["fixture"], item["league"], item["teams"]
            if league.get("id") not in TRACKED_LEAGUE_IDS:
                continue
            allowed = report.get("_competitions")
            if allowed is not None:
                from src.identity.competitions import competition_for_fixture as _cf

                comp = _cf("api_football", league.get("id"))
                if comp is None or comp.key not in allowed:
                    continue
            fid = str(int(fx["id"]))
            kickoff = iso_utc(fx["date"])
            status = (fx.get("status") or {}).get("short") or ""
            home, away = teams["home"]["name"], teams["away"]["name"]
            home_id, away_id = teams["home"].get("id"), teams["away"].get("id")
        except (KeyError, TypeError, ValueError, InvalidQuote):
            report["discovery_skipped"] += 1
            continue
        existing = conn.execute("SELECT 1 FROM matches WHERE id = ?", (fid,)).fetchone()
        inserted = 0
        if existing is None:
            conn.execute(
                """INSERT INTO matches (id, date, kickoff, home_team, away_team, league_name, league_id, status,
                                        provider)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'api_football')""",
                (fid, kickoff[:10], kickoff, home, away, league.get("name", ""), league["id"], status or "NS"),
            )
            inserted = 1
            report["fixtures_inserted"] += 1
        conn.execute(
            """INSERT INTO shadow_fixtures (fixture_id, provider, league_id, season, first_seen_at, last_seen_at,
                                            kickoff_utc, inserted_by_shadow)
               VALUES (?, 'api_football', ?, ?, ?, ?, ?, ?)
               ON CONFLICT(fixture_id) DO UPDATE SET last_seen_at = excluded.last_seen_at,
                   kickoff_utc = excluded.kickoff_utc, season = excluded.season""",
            (fid, league["id"], league.get("season"), iso_utc(now), iso_utc(now), kickoff, inserted),
        )
        conn.execute("UPDATE shadow_fixtures SET home_team_id = ?, away_team_id = ? WHERE fixture_id = ?",
                     (None if home_id is None else str(home_id), None if away_id is None else str(away_id), fid))
        own = conn.execute("SELECT inserted_by_shadow FROM shadow_fixtures WHERE fixture_id = ?", (fid,)).fetchone()[0]
        full = ((item.get("score") or {}).get("fulltime") or {})
        if own:
            conn.execute("UPDATE matches SET status = ? WHERE id = ?", (status or "NS", fid))
        if status in FINAL_STATUSES and full.get("home") is not None and full.get("away") is not None:
            if own:
                conn.execute("UPDATE matches SET home_goals = ?, away_goals = ? WHERE id = ?",
                             (full["home"], full["away"], fid))
            new = conn.execute(
                "INSERT OR IGNORE INTO shadow_outcomes VALUES (?, ?, ?, ?, 'api_football:score.fulltime', ?)",
                (fid, status, full["home"], full["away"], iso_utc(now)),
            ).rowcount
            report["outcomes_recorded"] += new
        # Map the API-Football event onto rows other feeds stored for the same match.
        event = identity.ProviderEvent(api_football.PROVIDER, fid, home, away, kickoff)
        mapping = identity.resolve_fixture(conn, event)
        if mapping.mapped and mapping.method != "stored":
            identity.remember(conn, event, mapping, iso_utc(now))
    conn.commit()


def discover(conn, dates: list[str], api_key: str, host: str, transport, now: datetime,
             sleep: Callable[[float], None], report: dict) -> None:
    for index, date in enumerate(dates):
        if report["requests_sent"] >= report["request_cap"]:
            report["errors"].append(f"fixtures {date}: per-cycle request cap {report['request_cap']} reached")
            return
        if index:
            sleep(collector.api_football_interval())
        report["requests_sent"] += 1
        try:
            payload = api_football.fetch_fixtures_by_date(conn, date, api_key, host, transport, now)
        except quota.BudgetExceeded as exc:
            report["errors"].append(f"fixtures {date}: refused, {exc}")
            return
        except quota.ProviderUnavailable as exc:
            report["errors"].append(f"fixtures {date}: {exc}")
            continue
        report["discovery_requests"] += 1
        _ingest_fixture_list(conn, payload, now, report)


def _recently_discovered(conn, now: datetime, hours: int) -> set:
    """Dates fetched by a discovery request within `hours` (results dates are fetched regardless)."""
    rows = conn.execute(
        "SELECT endpoint FROM provider_quota_log WHERE provider = 'api_football' AND status = 'ok' "
        "AND endpoint LIKE '/fixtures?date=%' AND at >= ?", (iso_utc(now - timedelta(hours=hours)),)).fetchall()
    return {r[0].split("=", 1)[1] for r in rows}


def _discovery_due(conn, cfg: ShadowConfig, now: datetime) -> bool:
    row = conn.execute("SELECT MAX(started_at) FROM shadow_cycles WHERE discovery_ran = 1").fetchone()
    return row[0] is None or (now - datetime.fromisoformat(row[0])) >= timedelta(hours=cfg.discovery_interval_hours)


def _awaiting_result_dates(conn, now: datetime) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT substr(o.kickoff, 1, 10) FROM shadow_observations o
           LEFT JOIN shadow_outcomes r ON r.fixture_id = o.fixture_id
           WHERE r.fixture_id IS NULL AND o.kickoff <= ? ORDER BY 1 DESC LIMIT 3""",
        (iso_utc(now - timedelta(hours=2, minutes=30)),),
    ).fetchall()
    return [r[0] for r in rows]


# ── Odds collection ───────────────────────────────────────────────────────────

def _allowed(f: dict, cfg: ShadowConfig) -> bool:
    if not cfg.competitions:
        return True
    from src.identity.competitions import competition_for_fixture as _cf

    comp = _cf("api_football", f.get("af_league_id") or f.get("league_id"))
    return comp is not None and comp.key in cfg.competitions


def _upcoming(conn, now: datetime, horizon: timedelta) -> list[dict]:
    rows = conn.execute(
        """SELECT m.*, s.kickoff_utc, s.season, s.last_odds_fetch_at, s.league_id AS af_league_id
           FROM shadow_fixtures s JOIN matches m ON m.id = s.fixture_id
           WHERE s.kickoff_utc > ? AND s.kickoff_utc <= ? AND m.status IN ('NS', 'TBD')""",
        (iso_utc(now), iso_utc(now + horizon)),
    ).fetchall()
    return [dict(r) for r in rows]


def _odds_due(f: dict, cfg: ShadowConfig, now: datetime) -> bool:
    to_kickoff = _minutes(now, f["kickoff_utc"])
    last = f.get("last_odds_fetch_at")
    since = None if not last else (now - datetime.fromisoformat(last)).total_seconds() / 60.0
    if since is None or since >= cfg.refresh_minutes:
        return True
    if to_kickoff <= cfg.decision_lead_minutes + 15 and since >= cfg.pre_decision_refresh_minutes:
        return True
    closing_start = datetime.fromisoformat(iso_utc(f["kickoff_utc"])) - timedelta(minutes=cfg.closing_window_minutes)
    return to_kickoff <= cfg.closing_window_minutes and datetime.fromisoformat(last) < closing_start


def _fetched_since(f: dict, moment: datetime) -> bool:
    last = f.get("last_odds_fetch_at")
    return bool(last) and datetime.fromisoformat(last) >= moment


def _collect_per_fixture(conn, cfg: ShadowConfig, now: datetime, api_key: str, host: str, transport,
                         sleep: Callable[[float], None], report: dict,
                         stop_requested: Callable[[], bool] | None = None) -> None:
    upcoming = [f for f in _upcoming(conn, now, timedelta(minutes=cfg.decision_lead_minutes
                                                         + cfg.pre_decision_margin_minutes)) if _allowed(f, cfg)]
    decision, closing = [], []
    for f in upcoming:
        kickoff = datetime.fromisoformat(iso_utc(f["kickoff_utc"]))
        window_start = kickoff - timedelta(minutes=cfg.decision_lead_minutes + cfg.pre_decision_margin_minutes)
        attempts = conn.execute(
            "SELECT COUNT(*) FROM provider_quota_log WHERE provider = 'api_football' AND endpoint = ? "
            "AND status IN ('ok', 'error') AND at >= ?", (f"/odds?fixture={int(f['id'])}", iso_utc(window_start)),
        ).fetchone()[0]
        if attempts >= cfg.max_attempts_per_window and not _fetched_since(f, window_start):
            report.setdefault("retry_limit_reached", []).append(str(f["id"]))
            continue
        if not _fetched_since(f, window_start):
            decision.append(f)
        elif cfg.closing_capture and _minutes(now, f["kickoff_utc"]) <= cfg.closing_window_minutes and \
                not _fetched_since(f, kickoff - timedelta(minutes=cfg.closing_window_minutes)):
            closing.append(f)
    report["odds_due"] = {"decision": len(decision), "closing": len(closing)}
    totals = {"requests_ok": 0, "requests_failed": 0, "requests_refused": 0, "quotes_new": 0,
              "quotes_after_kickoff": 0, "rejected_events": []}
    for index, f in enumerate(sorted(decision, key=lambda x: x["kickoff_utc"]) +
                              sorted(closing, key=lambda x: x["kickoff_utc"])):
        if report["requests_sent"] >= report["request_cap"]:
            report["errors"].append(f"odds: per-cycle request cap {report['request_cap']} reached")
            break
        if _stop(stop_requested, report, "odds"):
            break
        if index:
            sleep(collector.api_football_interval())
        report["requests_sent"] += 1
        fixture = {**f, "provider": "api_football", "league_id": f["af_league_id"], "kickoff": f["kickoff_utc"]}
        result = collector.collect_api_football(conn, [fixture], api_key, host, transport, now, sleep=lambda s: None)
        for key in ("requests_ok", "requests_failed", "requests_refused", "quotes_new", "quotes_after_kickoff"):
            totals[key] += result[key]
        totals["rejected_events"] += result["rejected_events"]
        if result["requests_ok"]:
            conn.execute("UPDATE shadow_fixtures SET last_odds_fetch_at = ? WHERE fixture_id = ?",
                         (iso_utc(now), str(f["id"])))
            conn.commit()
        else:
            report["errors"].extend(result["notes"])
        if result["requests_refused"]:
            break
        if any("HTTP 429" in n for n in result["notes"]):
            report["throttled"] = True   # provider rate limit: stop this cycle, retry next cycle (attempt cap applies)
            break
    report["odds"] = totals


def collect(conn, cfg: ShadowConfig, now: datetime, api_key: str, host: str, transport,
            sleep: Callable[[float], None], report: dict, stop_requested: Callable[[], bool] | None = None) -> None:
    if cfg.odds_mode == "per_fixture":
        _collect_per_fixture(conn, cfg, now, api_key, host, transport, sleep, report, stop_requested)
        return
    if report["requests_sent"] >= report["request_cap"]:
        report["errors"].append("odds: per-cycle request cap reached")
        return
    upcoming = _upcoming(conn, now, timedelta(hours=cfg.lookahead_hours))
    due_groups = {(f["af_league_id"], f["kickoff_utc"][:10]) for f in upcoming if _odds_due(f, cfg, now)}
    batch = [{**f, "provider": "api_football", "league_id": f["af_league_id"], "kickoff": f["kickoff_utc"]}
             for f in upcoming if (f["af_league_id"], f["kickoff_utc"][:10]) in due_groups]
    report["odds_groups_due"] = len(due_groups)
    if not batch:
        return
    seasons = {str(f["id"]): f["season"] for f in batch if f.get("season")}
    result = collector.collect_api_football_by_league(conn, batch, seasons, api_key, host, transport, now, sleep)
    report["odds"] = {k: v for k, v in result.items() if k != "unmapped"}
    if not result["requests_failed"] and not result["requests_refused"] and not result["values_skipped"]:
        conn.executemany("UPDATE shadow_fixtures SET last_odds_fetch_at = ? WHERE fixture_id = ?",
                         [(iso_utc(now), str(f["id"])) for f in batch])
        conn.commit()
    else:
        report["errors"].extend(result["notes"])


# ── Decision snapshot ─────────────────────────────────────────────────────────

def _bilqe(match: dict) -> dict:
    from api.services.match_analysis_service import _compute_match_analysis

    analysis = _compute_match_analysis(match["home_team"], match["away_team"], match["league_name"],
                                       shuffle_tiers=False, fixture_id=str(match["id"]))
    q = analysis.get("qualification") or {}
    failed: dict[str, int] = {}
    for r in q.get("rejected_picks") or []:
        for layer in r.get("failed_layers") or []:
            failed[layer] = failed.get(layer, 0) + 1
    return {
        "data_quality": analysis.get("data_quality_score"),
        "layer_gate_results": q.get("layer_gate_results"),
        "tier_counts": q.get("tier_counts"),
        "qualified": [{k: p.get(k) for k in ("market", "tier", "probability", "pick_quality_score")}
                      for p in q.get("qualified_picks") or []],
        "rejected_count": q.get("rejected_count"),
        "rejections_by_failed_layer": failed,
        "summary": q.get("summary"),
    }


def _availability(sides) -> str:
    """Exactly which history each side's inputs could use: the last fixture and when it became available."""
    out = {}
    for side in ("home", "away"):
        r = sides.get(side) if isinstance(sides, dict) else None
        r = r.to_dict() if hasattr(r, "to_dict") else (r or {})
        ev = r.get("evidence") or {}
        out[side] = {"status": r.get("status"), "matches_played": r.get("matches_played"),
                     "as_of_fixture_id": ev.get("as_of_fixture_id"), "available_from": ev.get("available_from")}
    return json.dumps(out, sort_keys=True, default=str)


class _Recorded(Exception):
    """The observation row already describes why there is no forecast."""


def observe(conn, match: dict, kickoff_utc: str, now: datetime, cycle_id: str, history: str = "rh1") -> dict:
    from src.baseline import state as baseline
    from src.engine.market_value import find_value
    from src.engine.pipeline import _pick_calibrator, calibrate_probabilities
    from src.engine.probability_engine import estimate_probabilities_from_inputs
    from src.identity.competitions import competition_for_fixture
    from src.identity.inputs import (UnresolvedTeamIdentity, resolve_probability_inputs_rh1,
                                     resolve_probability_inputs_v2)
    from src.identity.teams import TEAM_IDENTITY_VERSION
    from src.policy import baseline_v1 as v1
    from src.policy import baseline_v1_1 as v11
    from src.policy.league_evidence import load_league_evidence

    decided_at = iso_utc(now)
    prov = baseline.provenance(None, decided_at)
    provenance = {"model_version": prov.get("model_version"), "calibration_version": prov.get("calibration_version"),
                  "calibration_frozen": baseline.is_frozen()}
    row = {"cycle_id": cycle_id, "fixture_id": str(match["id"]), "decided_at": decided_at, "kickoff": kickoff_utc,
           "minutes_to_kickoff": round(_minutes(now, kickoff_utc), 1), "league_name": match.get("league_name"),
           "code_revision": _code_revision(), "model_version": provenance["model_version"],
           "calibration_version": provenance["calibration_version"],
           "legacy_policy_version": LEGACY_POLICY_VERSION, "v1_policy_version": v1.version(),
           "candidates_json": "[]", "legacy_json": "{}", "v1_json": "{}", "v1_assuming_frozen_json": "{}",
           "v1_1_policy_version": v11.version(), "manifest_id": os.getenv("SHADOW_MANIFEST_ID"),
           "code_fingerprint": os.getenv("SHADOW_CODE_FINGERPRINT"),
           "provider_league_id": match.get("league_id"),
           "provider_home_team_id": None if match.get("home_team_id") is None else str(match["home_team_id"]),
           "provider_away_team_id": None if match.get("away_team_id") is None else str(match["away_team_id"])}
    competition = competition_for_fixture(match.get("provider"), match.get("league_id"))
    row["competition_key"] = competition.key if competition else None
    status = freshness.fixture_quote_status(conn, str(match["id"]), now)
    row.update({"quote_status": status["status"], "quote_age_minutes": status["age_minutes"],
                "quote_bucket": status["bucket"]})
    try:
        try:
            if history == "rh1":
                inputs = resolve_probability_inputs_rh1(conn, match, now, competition)
            else:
                inputs = resolve_probability_inputs_v2(conn, match, competition, remember=True)
        except UnresolvedTeamIdentity as exc:
            # Fail closed: no forecast, no candidates, the identity failure is the record.
            row["identity_json"] = json.dumps({"version": TEAM_IDENTITY_VERSION, "resolved": False,
                                               **{side: r.to_dict() for side, r in exc.resolutions.items()}},
                                              sort_keys=True, default=str)
            row["error"] = "NO_FORECAST: TEAM_IDENTITY_UNRESOLVED"
            row["inputs_available_json"] = _availability(exc.resolutions)
            raise _Recorded() from None
        row["identity_json"] = json.dumps({**inputs["identity"], "resolved": True}, sort_keys=True, default=str)
        row["home_canonical_team_id"] = inputs["identity"]["home"].get("canonical_team_id")
        row["away_canonical_team_id"] = inputs["identity"]["away"].get("canonical_team_id")
        row["inputs_available_json"] = _availability(inputs["identity"])
        raw = estimate_probabilities_from_inputs(inputs)
        probs = calibrate_probabilities(raw, _pick_calibrator(conn))
        candidates = find_value(match, probs, conn, now=now)
        for c in candidates:
            c.update({"competition_key": competition.key if competition else None, "identity_resolved": True,
                      "identity_version": TEAM_IDENTITY_VERSION})
        records = v1.load_league_records(conn)
        evidence = load_league_evidence(conn)
        legacy_picks, legacy_reasons = legacy_select(conn, candidates)
        actual = v1.evaluate(candidates, now, provenance, records)
        frozen = v1.evaluate(candidates, now, {**provenance, "calibration_frozen": True}, records)
        actual11 = v11.evaluate(candidates, now, provenance, evidence)
        frozen11 = v11.evaluate(candidates, now, {**provenance, "calibration_frozen": True}, evidence)

        def picked11(decisions):
            return [v11.decision_dict(d) for d in decisions if d.selected]

        def picked(decisions):
            return [d.to_dict() for d in decisions if d.selected]

        row.update({
            "probability_inputs_json": json.dumps(inputs, sort_keys=True, default=str),
            "raw_probabilities_json": json.dumps(raw, sort_keys=True, default=str),
            "forecast_json": json.dumps({k: probs.get(k) for k in ("1X2", "O/U 2.5", "BTTS", "source",
                                                                   "data_quality")}, sort_keys=True),
            "data_quality": probs.get("data_quality"),
            "model_source": probs.get("source"),
            "v1_1_json": json.dumps({"selected": picked11(actual11), "funnel": v1.funnel(actual11)}, sort_keys=True),
            "v1_1_assuming_frozen_json": json.dumps({"selected": picked11(frozen11), "funnel": v1.funnel(frozen11)},
                                                    sort_keys=True),
            "candidates_json": json.dumps(candidates, sort_keys=True, default=str),
            "legacy_json": json.dumps({"picks": [{k: p.get(k) for k in (
                "market", "selection", "model_prob", "odds", "edge", "bookmaker", "odds_observed_at",
                "odds_age_minutes", "grade", "stake_units")} for p in legacy_picks],
                "rejections_by_reason": dict(legacy_reasons)}, sort_keys=True),
            "v1_json": json.dumps({"selected": picked(actual), "funnel": v1.funnel(actual)}, sort_keys=True),
            "v1_assuming_frozen_json": json.dumps({"selected": picked(frozen), "funnel": v1.funnel(frozen)},
                                                  sort_keys=True),
        })
    except _Recorded:
        pass
    except Exception as exc:  # recorded, never filled in
        row["error"] = f"{type(exc).__name__}: {exc}"[:500]
    try:
        row["bilqe_json"] = json.dumps(_bilqe(match), sort_keys=True, default=str)
    except Exception as exc:
        row["bilqe_json"] = json.dumps({"error": f"{type(exc).__name__}: {exc}"[:300]})
    cols = list(row)
    conn.execute(f"INSERT OR IGNORE INTO shadow_observations ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 [row[c] for c in cols])
    conn.commit()
    return row


def decide(conn, cfg: ShadowConfig, now: datetime, cycle_id: str, report: dict) -> None:
    rows = conn.execute(
        """SELECT m.*, s.kickoff_utc, s.home_team_id, s.away_team_id, s.last_odds_fetch_at
           FROM shadow_fixtures s JOIN matches m ON m.id = s.fixture_id
           LEFT JOIN shadow_observations o ON o.fixture_id = s.fixture_id
           WHERE o.fixture_id IS NULL AND s.kickoff_utc > ? AND s.kickoff_utc <= ?
             AND m.status IN ('NS', 'TBD')""",
        (iso_utc(now), iso_utc(now + timedelta(minutes=cfg.decision_lead_minutes))),
    ).fetchall()
    if rows and cfg.history == "rh1":
        from src.history.reconstruct import reconstruct

        report["history"] = reconstruct(conn)   # deterministic rebuild from stored finished fixtures
    rows = [r for r in rows if _allowed({"league_id": r["league_id"]}, cfg)]
    for r in rows:
        kickoff = datetime.fromisoformat(iso_utc(r["kickoff_utc"]))
        window_start = kickoff - timedelta(minutes=cfg.decision_lead_minutes + cfg.pre_decision_margin_minutes)
        fetched = r["last_odds_fetch_at"] and datetime.fromisoformat(r["last_odds_fetch_at"]) >= window_start
        if not fetched and _minutes(now, r["kickoff_utc"]) > cfg.defer_observation_until_minutes:
            report["observations_deferred"] = report.get("observations_deferred", 0) + 1
            continue
        row = observe(conn, dict(r), r["kickoff_utc"], now, cycle_id, cfg.history)
        report["observations"] += 1
        if row.get("error", "").startswith("NO_FORECAST"):
            report["identity_unresolved"] = report.get("identity_unresolved", 0) + 1
        elif row.get("error"):
            report["errors"].append(f"observe {r['id']}: {row['error']}")


# ── Settlement and CLV ────────────────────────────────────────────────────────

def _won(market: str, selection: str, home: int, away: int) -> bool:
    if market == "1X2":
        return selection == ("home" if home > away else "away" if away > home else "draw")
    if market == "O/U 2.5":
        return (home + away > 2.5) == (selection == "over")
    if market == "BTTS":
        return (home > 0 and away > 0) == (selection == "yes")
    raise ValueError(market)


def _closing(conn, fixture_id: str, pick: dict) -> tuple[float | None, str | None]:
    market, line = MODEL_TO_CANONICAL[pick["market"]]
    row = conn.execute(
        """SELECT decimal_odds, observed_at, fetched_at FROM odds_quotes
           WHERE fixture_id = ? AND odds_type = 'bookmaker' AND bookmaker = ? AND market = ? AND selection = ?
             AND line = ? AND observed_at < commence_time AND fetched_at <= commence_time
           ORDER BY observed_at DESC, fetched_at DESC, revision DESC LIMIT 1""",
        (fixture_id, pick.get("bookmaker"), market, pick["selection"], line),
    ).fetchone()
    taken = (iso_utc(pick["odds_observed_at"]), iso_utc(pick.get("odds_fetched_at") or pick["odds_observed_at"])) \
        if pick.get("odds_observed_at") else None
    if row is None or taken is None or (row[1], row[2]) <= taken:
        return None, None  # no later pre-kickoff price from the same bookmaker: CLV undefined
    return float(row[0]), row[1]


def settle(conn, now: datetime, report: dict) -> None:
    rows = conn.execute(
        """SELECT o.fixture_id, o.legacy_json, o.v1_json, o.v1_assuming_frozen_json, o.v1_1_json,
                  o.v1_1_assuming_frozen_json, o.candidates_json,
                  r.home_goals_ft, r.away_goals_ft
           FROM shadow_observations o JOIN shadow_outcomes r ON r.fixture_id = o.fixture_id
           WHERE NOT EXISTS (SELECT 1 FROM shadow_selection_results s WHERE s.fixture_id = o.fixture_id)""",
    ).fetchall()
    for r in rows:
        candidates = {(c["market"], c["selection"]): c for c in json.loads(r["candidates_json"] or "[]")}
        selections = [("legacy", p) for p in json.loads(r["legacy_json"] or "{}").get("picks", [])]
        for policy, column in (("v1", "v1_json"), ("v1_assuming_frozen", "v1_assuming_frozen_json"),
                               ("v1_1", "v1_1_json"), ("v1_1_assuming_frozen", "v1_1_assuming_frozen_json")):
            for d in json.loads((r[column] if column in r.keys() else None) or "{}").get("selected", []):
                c = candidates.get((d["market"], d["selection"]), {})
                selections.append((policy, {**c, "market": d["market"], "selection": d["selection"]}))
        for policy, pick in selections:
            won = _won(pick["market"], pick["selection"], r["home_goals_ft"], r["away_goals_ft"])
            closing, closing_at = _closing(conn, r["fixture_id"], pick)
            conn.execute(
                """INSERT OR IGNORE INTO shadow_selection_results
                       (fixture_id, policy, market, selection, odds_taken, bookmaker, result, pnl_flat_units,
                        closing_odds, clv_pct, closing_observed_at, settled_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (r["fixture_id"], policy, pick["market"], pick["selection"], pick["odds"], pick.get("bookmaker"),
                 "won" if won else "lost", round(pick["odds"] - 1.0, 4) if won else -1.0, closing,
                 round(pick["odds"] / closing - 1.0, 4) if closing else None, closing_at, iso_utc(now)),
            )
            report["selections_settled"] += 1
    conn.commit()


# ── Cycle ─────────────────────────────────────────────────────────────────────

def _stop(stop_requested: Callable[[], bool] | None, report: dict, stage: str) -> bool:
    """Emergency stop during a cycle (SHADOW_STOP or SIGTERM): no further request and no new observation."""
    if report.get("stopped"):
        return True
    if stop_requested is not None and stop_requested():
        report["stopped"] = f"emergency stop before {stage}"
        return True
    return False


def stop_signal(stop_file) -> Callable[[], bool]:
    """Stop check for run_cycle: true once `stop_file` exists or the process has received SIGTERM
    (launchctl kill / bootout, systemctl stop). Installs the SIGTERM handler; call from the main thread."""
    import signal

    terminated = []
    signal.signal(signal.SIGTERM, lambda signum, frame: terminated.append(signum))
    return lambda: bool(terminated) or Path(stop_file).exists()


def run_cycle(conn: sqlite3.Connection, api_key: str, host: str, cfg: ShadowConfig = DEFAULT,
              now: datetime | None = None, transport=None, sleep: Callable[[float], None] = time.sleep,
              force_discovery: bool = False, stop_requested: Callable[[], bool] | None = None) -> dict:
    conn.row_factory = sqlite3.Row
    _ensure(conn)
    now = now or datetime.now(timezone.utc)
    cycle_id = uuid.uuid4().hex
    report = {"cycle_id": cycle_id, "started_at": iso_utc(now), "discovery_requests": 0, "discovery_skipped": 0,
              "fixtures_inserted": 0, "outcomes_recorded": 0, "odds_groups_due": 0, "observations": 0,
              "selections_settled": 0, "errors": [], "requests_sent": 0,
              "request_cap": cfg.max_requests_per_cycle}
    # One cycle at a time: after sleep/wake or with two schedulers, a second start inside the lease
    # of an unfinished cycle is recorded as skipped and does nothing else.
    running = conn.execute(
        "SELECT cycle_id FROM shadow_cycles WHERE finished_at IS NULL AND started_at > ? "
        "AND COALESCE(status, 'running') = 'running'",
        (iso_utc(now - timedelta(minutes=cfg.lease_minutes)),),
    ).fetchone()
    last = conn.execute("SELECT MAX(started_at) FROM shadow_cycles WHERE COALESCE(status, 'running') != 'skipped'"
                        ).fetchone()[0]
    gap = None if last is None else round((now - datetime.fromisoformat(last)).total_seconds() / 60.0, 1)
    missed = 0 if gap is None else max(0, int(gap // cfg.cycle_interval_minutes) - 1)
    report.update({"gap_minutes": gap, "missed_slots": missed})
    if running:
        report["skipped"] = f"cycle {running[0]} still running"
        conn.execute("INSERT INTO shadow_cycles (cycle_id, started_at, finished_at, discovery_ran, status, gap_minutes, "
                     "missed_slots, report_json) VALUES (?, ?, ?, 0, 'skipped', ?, 0, ?)",
                     (cycle_id, iso_utc(now), iso_utc(now), gap, json.dumps(report, default=str)))
        conn.commit()
        return report
    run_discovery = force_discovery or _discovery_due(conn, cfg, now)
    conn.execute("INSERT INTO shadow_cycles (cycle_id, started_at, discovery_ran, status, gap_minutes, missed_slots) "
                 "VALUES (?, ?, ?, 'running', ?, ?)", (cycle_id, iso_utc(now), int(run_discovery), gap, missed))
    conn.commit()
    report["_competitions"] = set(cfg.competitions) if cfg.competitions else None
    dates = []
    if run_discovery:
        recent = _recently_discovered(conn, now, cfg.rediscover_after_hours)
        dates = [d for d in (now.strftime("%Y-%m-%d"), (now + timedelta(days=1)).strftime("%Y-%m-%d"))
                 if d not in recent]
    dates += [d for d in _awaiting_result_dates(conn, now) if d not in dates]
    # A stop stops the rest of the cycle: requests not yet sent are not sent, and no decision is taken from
    # what was collected, so a fixture whose window then passes is a missed observation (never back-filled).
    if dates and not _stop(stop_requested, report, "discovery"):
        discover(conn, dates, api_key, host, transport, now, sleep, report)
    if not _stop(stop_requested, report, "odds"):
        collect(conn, cfg, now, api_key, host, transport, sleep, report, stop_requested)
    if not _stop(stop_requested, report, "decisions"):
        decide(conn, cfg, now, cycle_id, report)
        settle(conn, now, report)
    report.pop("_competitions", None)
    report["finished_at"] = iso_utc(datetime.now(timezone.utc) if transport is None else now)
    conn.execute("UPDATE shadow_cycles SET finished_at = ?, report_json = ?, status = ? WHERE cycle_id = ?",
                 (report["finished_at"], json.dumps(report, default=str),
                  "stopped" if report.get("stopped") else "finished", cycle_id))
    conn.commit()
    return report

#!/usr/bin/env python3
"""
Shadow monitor (M3.5, Mac host). Read-only; sends no request.

    python scripts/shadow_monitor.py --run <root>/run [--manifest <root>/run/shadow_manifest.v3.json] [--notify]

Reports cycle staleness, missed slots, network failures, HTTP 429s, quota (shared ledger and the
provider's own remaining count), database integrity, the forward-evidence rule, the emergency stop
file, the experiment's caffeinate and the power source. Exit 0 = no alert, 1 = at least one alert.
--notify also shows a macOS notification when there is an alert (osascript; nothing else).
"""
import argparse
import json
import platform
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _q(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def build(run: Path, manifest: dict | None, now: datetime, stale_minutes: int, budget: int,
          min_remaining: int, network_alert: int) -> dict:
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.shadow import manifest as sm

    db = run / "engine_shadow.db"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    day = now.strftime("%Y-%m-%d")
    since = (now - timedelta(hours=24)).isoformat()
    alerts = []
    last = conn.execute("SELECT cycle_id, started_at, finished_at, status FROM shadow_cycles "
                        "WHERE COALESCE(status, 'running') != 'skipped' ORDER BY started_at DESC LIMIT 1").fetchone()
    age = None if last is None else round((now - datetime.fromisoformat(last[1])).total_seconds() / 60, 1)
    if age is None or age > stale_minutes:
        alerts.append(f"stale: last cycle {None if last is None else last[1]} ({age} min ago)")
    cycles = {
        "last": None if last is None else {"started_at": last[1], "finished_at": last[2], "status": last[3],
                                           "age_minutes": age},
        "last_24h": _q(conn, "SELECT COUNT(*) FROM shadow_cycles WHERE started_at >= ? AND "
                             "COALESCE(status, 'running') != 'skipped'", since),
        "expected_24h": 48,
        "skipped_24h": _q(conn, "SELECT COUNT(*) FROM shadow_cycles WHERE started_at >= ? AND status = 'skipped'",
                          since),
        "missed_slots_24h": _q(conn, "SELECT COALESCE(SUM(missed_slots), 0) FROM shadow_cycles WHERE started_at >= ?",
                               since),
        "unfinished": _q(conn, "SELECT COUNT(*) FROM shadow_cycles WHERE finished_at IS NULL AND "
                               "COALESCE(status, 'running') = 'running' AND started_at < ?",
                         (now - timedelta(minutes=30)).isoformat()),
    }
    if cycles["unfinished"]:
        alerts.append(f"{cycles['unfinished']} cycle(s) never finished (abrupt termination?)")
    net = dict(conn.execute(
        "SELECT CASE WHEN http_status IS NULL THEN 'connection' WHEN http_status = 429 THEN 'http_429' "
        "ELSE 'http_' || http_status END, COUNT(*) FROM provider_quota_log "
        "WHERE provider = 'api_football' AND status = 'error' AND at >= ? GROUP BY 1", (since,)).fetchall())
    refused = _q(conn, "SELECT COUNT(*) FROM provider_quota_log WHERE provider = 'api_football' AND "
                       "status = 'refused' AND at >= ?", since)
    if sum(net.values()) >= network_alert:
        alerts.append(f"network/provider failures in 24 h: {net}")
    if net.get("http_429"):
        alerts.append(f"HTTP 429 in 24 h: {net['http_429']}")
    remaining = conn.execute(
        "SELECT requests_remaining, at FROM provider_quota_log WHERE provider = 'api_football' AND status = 'ok' "
        "AND requests_remaining IS NOT NULL AND substr(at, 1, 10) = ? ORDER BY at DESC, id DESC LIMIT 1",
        (day,)).fetchone()
    ledger_path = run / "api_football_ledger.db"
    by_consumer = {}
    if ledger_path.exists():
        lc = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
        by_consumer = dict(lc.execute("SELECT consumer, SUM(cost) FROM ledger WHERE provider = 'api_football' "
                                      "AND day = ? GROUP BY 1", (day,)).fetchall())
        lc.close()
    used = sum(by_consumer.values())
    quota = {"ledger_used_today": used, "daily_budget": budget, "ledger_left_today": budget - used,
             "by_consumer": by_consumer, "refused_24h": refused,
             "provider_remaining_today": None if remaining is None else int(float(remaining[0])),
             "provider_remaining_at": None if remaining is None else remaining[1]}
    if used > budget:
        alerts.append(f"ledger day total {used} above {budget}")
    unknown = sorted(set(by_consumer) - {"shadow_cycle", "shadow_settle", "shadow_preflight"})
    if unknown:
        alerts.append(f"unexpected consumers in the shadow ledger: {unknown}")
    if quota["provider_remaining_today"] is not None and quota["provider_remaining_today"] <= min_remaining:
        alerts.append(f"provider remaining {quota['provider_remaining_today']} <= {min_remaining}")
    quick = conn.execute("PRAGMA quick_check").fetchone()[0]
    fwd = sm.forward_evidence_check(conn, None if manifest is None else manifest["manifest_id"])
    if quick != "ok":
        alerts.append(f"database quick_check: {quick}")
    if not fwd["ok"]:
        alerts.append(f"forward-evidence rule violated: {fwd}")
    conn.close()
    stop = (run / "SHADOW_STOP").exists()
    host = {"platform": platform.system(), "stop_file_present": stop}
    if platform.system() == "Darwin":
        pidfile = run / "caffeinate.pid"
        pid = pidfile.read_text().strip() if pidfile.exists() else None
        alive = bool(pid) and subprocess.run(["ps", "-p", pid, "-o", "comm="], capture_output=True,
                                             text=True).stdout.strip().endswith("caffeinate")
        batt = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
        host.update({"caffeinate_pid": pid, "caffeinate_running": alive,
                     "power_source": "AC" if "AC Power" in batt else ("battery" if "Battery Power" in batt else None)})
        if not alive and not stop:
            alerts.append("experiment caffeinate is not running: the Mac may sleep")
        if host["power_source"] == "battery" and not stop:
            alerts.append("on battery: caffeinate -s is inactive and the battery will run down")
    return {"at_utc": now.isoformat(), "alerts": alerts, "cycles": cycles, "network_24h": net, "quota": quota,
            "integrity": {"quick_check": quick, "forward_evidence": fwd}, "host": host}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", required=True, type=Path)
    p.add_argument("--manifest", type=Path)
    p.add_argument("--stale-minutes", type=int, default=75)
    p.add_argument("--budget", type=int, default=70)
    p.add_argument("--min-remaining", type=int, default=20)
    p.add_argument("--network-alert", type=int, default=6)
    p.add_argument("--notify", action="store_true")
    a = p.parse_args()
    manifest = json.loads(a.manifest.read_text()) if a.manifest else None
    doc = build(a.run.resolve(), manifest, datetime.now(timezone.utc), a.stale_minutes, a.budget, a.min_remaining,
                a.network_alert)
    print(json.dumps(doc, indent=1, default=str))
    if doc["alerts"] and a.notify and platform.system() == "Darwin":
        text = "; ".join(doc["alerts"])[:200].replace('"', "'")
        subprocess.run(["osascript", "-e", f'display notification "{text}" with title "Football shadow ALERT"'])
    return 1 if doc["alerts"] else 0


if __name__ == "__main__":
    sys.exit(main())

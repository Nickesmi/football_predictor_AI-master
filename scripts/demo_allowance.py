#!/usr/bin/env python3
"""
Owner control of the local demo's API-Football allowance (src/ops/demo_quota.py). Sends nothing.

    python scripts/demo_allowance.py --usage-db <demo dir>/demo_api_usage.db status
    python scripts/demo_allowance.py --usage-db <demo dir>/demo_api_usage.db grant \
        --total 20 --daily 3 --days 14 --note "why" --i-authorize-api-football-requests
    python scripts/demo_allowance.py --usage-db <demo dir>/demo_api_usage.db revoke --id 1

A grant authorizes NEW requests only; the legacy record (demo_api_usage) is never changed or reset.
Grants are append-only (revoke sets revoked_at). Limits per grant: total 1..200, daily 1..20.
The running demo picks up a grant on its next refresh; no restart is needed.
"""
import argparse
import getpass
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--usage-db", required=True, type=Path)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    g = sub.add_parser("grant")
    g.add_argument("--total", required=True, type=int)
    g.add_argument("--daily", required=True, type=int)
    g.add_argument("--days", type=int, help="the grant expires after this many days")
    g.add_argument("--note", required=True)
    g.add_argument("--i-authorize-api-football-requests", dest="confirm", action="store_true")
    r = sub.add_parser("revoke")
    r.add_argument("--id", required=True, type=int)
    a = p.parse_args()

    from src.ops import demo_quota

    if not a.usage_db.exists():
        print(f"refusing: {a.usage_db} does not exist (point at the demo's usage DB)", file=sys.stderr)
        return 2
    if a.cmd == "grant":
        if not a.confirm:
            print("refusing: add --i-authorize-api-football-requests to confirm this grant", file=sys.stderr)
            return 2
        expires = None if not a.days else (datetime.now(timezone.utc) + timedelta(days=a.days)).isoformat()
        try:
            gid = demo_quota.grant(a.usage_db, a.total, a.daily, a.note, getpass.getuser(), expires)
        except ValueError as exc:
            print(f"refusing: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"granted": gid, "total": a.total, "daily": a.daily, "expires_at": expires}))
        return 0
    if a.cmd == "revoke":
        ok = demo_quota.revoke(a.usage_db, a.id)
        print(json.dumps({"revoked": a.id if ok else None}))
        return 0 if ok else 1
    demo_quota._state.setdefault("cap", 0)
    demo_quota._state["path"] = str(a.usage_db)
    print(json.dumps(demo_quota.status(), indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

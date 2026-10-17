"""Minimal odds-timestamp experiment (M3.2, Decision 4) — plan and analysis are offline.

Question: is API-Football's per-fixture `update` the time of the last PRICE
CHANGE, the time of the last CHECK, or neither (a stale record)? We only see
one stamp per fixture, so the semantics come from repeated fetches of the same
fixture and a comparison of stamp and prices between consecutive fetches:

  stamp moved, prices changed   -> CHANGE      (consistent with last-change or last-check)
  stamp moved, prices identical -> CHECK       (stamp is a check time, not a change time)
  stamp same,  prices identical -> NO_REFRESH  (nothing new since the previous fetch)
  stamp same,  prices changed   -> INCONSISTENT (stamp does not describe the prices; caching)

Verdict over all pairs:
  any CHECK and no INCONSISTENT  -> stamp = last check     (age measures check staleness)
  CHANGE only (plus NO_REFRESH)  -> stamp = last change    (an old stamp can still be a current price)
  any INCONSISTENT               -> stamp unreliable       (freshness cannot be measured with it)
  only NO_REFRESH                -> undetermined (the provider did not refresh in the window)

Raw responses are kept per fetch: the odds_quotes unique key does not include
the price, so a changed price under an unchanged stamp would be dropped by the
normal store (see K53).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.odds.model import iso_utc

DEFAULT_OFFSETS_MIN = (360, 150, 30)   # fetch at kickoff - 6 h, - 2 h 30, - 30 min


@dataclass(frozen=True)
class PlannedFetch:
    fixture_id: str
    league_id: int
    kickoff: str
    at: str


def plan(fixtures: list[dict], n_fixtures: int = 4, offsets_min=DEFAULT_OFFSETS_MIN,
         needs_discovery: bool = True) -> dict:
    """Pick 2 leagues x 2 fixtures (league batches are visible), 3 fetches each."""
    by_league: dict[int, list[dict]] = {}
    for f in sorted(fixtures, key=lambda f: f["kickoff"]):
        by_league.setdefault(int(f["league_id"]), []).append(f)
    leagues = [lid for lid, fs in sorted(by_league.items(), key=lambda kv: (-len(kv[1]), kv[0])) if len(fs) >= 2]
    chosen = []
    for lid in leagues:
        chosen += by_league[lid][:2]
        if len(chosen) >= n_fixtures:
            break
    chosen = chosen[:n_fixtures]
    fetches = [PlannedFetch(str(f["id"]), int(f["league_id"]), iso_utc(f["kickoff"]),
                            iso_utc(datetime.fromisoformat(iso_utc(f["kickoff"])) - timedelta(minutes=m)))
               for f in chosen for m in offsets_min]
    fetches.sort(key=lambda p: p.at)
    requests = len(fetches) + (1 if needs_discovery else 0)
    return {"fixtures": [f["id"] for f in chosen], "leagues": sorted({f["league_id"] for f in chosen}),
            "fetches": [p.__dict__ for p in fetches], "requests": requests,
            "free_quota_share_pct": round(100 * requests / 100, 1)}


def price_signature(item: dict) -> str:
    """Hash of every (bookmaker, bet, value, odd) in one /odds response item."""
    rows = sorted(
        (str(b.get("name")), str(bet.get("name")), str(v.get("value")), str(v.get("odd")))
        for b in item.get("bookmakers") or [] for bet in b.get("bets") or [] for v in bet.get("values") or []
    )
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


def classify_pair(prev: dict, cur: dict) -> str:
    moved = prev["update"] != cur["update"]
    changed = prev["prices"] != cur["prices"]
    if moved and changed:
        return "CHANGE"
    if moved:
        return "CHECK"
    if changed:
        return "INCONSISTENT"
    return "NO_REFRESH"


def analyse(fetches: list[dict]) -> dict:
    """fetches: [{fixture_id, fetched_at, kickoff, payload}] (raw /odds?fixture= responses)."""
    series: dict[str, list[dict]] = {}
    for f in sorted(fetches, key=lambda f: f["fetched_at"]):
        items = (f["payload"] or {}).get("response") or []
        if not items:
            series.setdefault(f["fixture_id"], []).append({"fetched_at": f["fetched_at"], "missing": True})
            continue
        item = items[0]
        series.setdefault(f["fixture_id"], []).append({
            "fetched_at": iso_utc(f["fetched_at"]), "update": iso_utc(item["update"]), "prices": price_signature(item),
            "stamp_age_min": round((datetime.fromisoformat(iso_utc(f["fetched_at"]))
                                    - datetime.fromisoformat(iso_utc(item["update"]))).total_seconds() / 60, 1),
            "to_kickoff_min": round((datetime.fromisoformat(iso_utc(f["kickoff"]))
                                     - datetime.fromisoformat(iso_utc(f["fetched_at"]))).total_seconds() / 60, 1),
        })
    pairs = []
    for fid, s in series.items():
        good = [x for x in s if not x.get("missing")]
        for a, b in zip(good, good[1:]):
            pairs.append({"fixture_id": fid, "from": a["fetched_at"], "to": b["fetched_at"], "kind": classify_pair(a, b)})
    kinds = {p["kind"] for p in pairs}
    if "INCONSISTENT" in kinds:
        verdict = "STAMP_UNRELIABLE"
    elif "CHECK" in kinds:
        verdict = "STAMP_IS_LAST_CHECK"
    elif "CHANGE" in kinds:
        verdict = "STAMP_IS_LAST_CHANGE_OR_CHECK"
    else:
        verdict = "UNDETERMINED"
    ages = [x["stamp_age_min"] for s in series.values() for x in s if not x.get("missing")]
    return {"series": series, "pairs": pairs, "pair_kinds": {k: sum(p["kind"] == k for p in pairs) for k in sorted(kinds)},
            "verdict": verdict,
            "stamp_age_within": {w: (sum(a <= w for a in ages), len(ages)) for w in (60, 120, 240)}}

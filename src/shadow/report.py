"""Aggregate shadow observations into the shadow-validation report (counts only what was observed)."""
from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from datetime import datetime

from src.odds.freshness import BUCKETS

POLICIES = ("legacy", "v1", "v1_assuming_frozen", "v1_1", "v1_1_assuming_frozen")


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def _period(conn) -> dict:
    rows = [r[0] for r in conn.execute("SELECT started_at FROM shadow_cycles ORDER BY started_at")]
    if not rows:
        return {"cycles": 0, "first_cycle": None, "last_cycle": None, "elapsed_days": 0.0, "max_gap_hours": None}
    times = [datetime.fromisoformat(t) for t in rows]
    gaps = [(b - a).total_seconds() / 3600 for a, b in zip(times, times[1:])]
    return {"cycles": len(rows), "first_cycle": rows[0], "last_cycle": rows[-1],
            "elapsed_days": round((times[-1] - times[0]).total_seconds() / 86400, 2),
            "max_gap_hours": round(max(gaps), 2) if gaps else None}


def _coverage(obs: list[sqlite3.Row], max_age: int) -> dict:
    status = Counter(o["quote_status"] or "ERROR" for o in obs)
    buckets = Counter(o["quote_bucket"] for o in obs if o["quote_bucket"])
    within = sum(1 for o in obs if o["quote_age_minutes"] is not None and o["quote_age_minutes"] <= max_age)
    n = len(obs)
    return {"observed_fixtures": n, "by_status": dict(status),
            "by_bucket": {b: {"fixtures": buckets.get(b, 0),
                              "pct": round(100 * buckets.get(b, 0) / n, 1) if n else 0.0} for b in BUCKETS},
            "within_window": within, "coverage_pct": round(100 * within / n, 1) if n else 0.0,
            "rejected_pct": round(100 * (n - within) / n, 1) if n else 0.0, "window_minutes": max_age}


def _funnels(obs) -> dict:
    legacy, v1, frozen, v11, v11_frozen = Counter(), Counter(), Counter(), Counter(), Counter()
    selections = {p: Counter() for p in POLICIES}
    by_league = {p: Counter() for p in POLICIES}
    candidates = positive = 0
    for o in obs:
        cands = _loads(o["candidates_json"], [])
        candidates += len(cands)
        positive += sum(1 for c in cands if (c.get("edge") or 0) > 0)
        lj = _loads(o["legacy_json"], {})
        legacy.update(lj.get("rejections_by_reason", {}))
        for p in lj.get("picks", []):
            selections["legacy"][p["market"]] += 1
            by_league["legacy"][o["league_name"]] += 1
        for name, column, counter in (("v1", "v1_json", v1), ("v1_assuming_frozen", "v1_assuming_frozen_json", frozen),
                                      ("v1_1", "v1_1_json", v11),
                                      ("v1_1_assuming_frozen", "v1_1_assuming_frozen_json", v11_frozen)):
            data = _loads(o[column] if column in o.keys() else None, {})
            counter.update((data.get("funnel") or {}).get("rejections_by_reason", {}))
            for d in data.get("selected", []):
                selections[name][d["market"]] += 1
                by_league[name][o["league_name"]] += 1
    return {"candidates": candidates, "positive_edge_candidates": positive,
            "legacy_rejections": dict(legacy.most_common()), "v1_rejections": dict(v1.most_common()),
            "v1_assuming_frozen_rejections": dict(frozen.most_common()),
            "v1_1_rejections": dict(v11.most_common()),
            "v1_1_assuming_frozen_rejections": dict(v11_frozen.most_common()),
            "selections_by_market": {p: dict(c) for p, c in selections.items()},
            "selections_by_league": {p: dict(c.most_common()) for p, c in by_league.items()}}


def _identity(obs) -> dict:
    statuses, versions, unresolved = Counter(), Counter(), []
    for o in obs:
        data = _loads(o["identity_json"] if "identity_json" in o.keys() else None, None)
        if data is None:
            statuses["NOT_RECORDED (legacy inputs)"] += 1
            continue
        versions[data.get("version")] += 1
        statuses["RESOLVED" if data.get("resolved") else "UNRESOLVED"] += 1
        if not data.get("resolved"):
            unresolved.append({side: {"name": data[side]["provider_name"], "status": data[side]["status"]}
                               for side in ("home", "away") if data[side]["status"] != "RESOLVED"})
    return {"observations": dict(statuses), "versions": dict(versions), "unresolved": unresolved}


def _bilqe(obs) -> dict:
    layers_failed, tiers, errors = Counter(), Counter(), 0
    gate_fail = Counter()
    for o in obs:
        b = _loads(o["bilqe_json"], {})
        if "error" in b:
            errors += 1
            continue
        layers_failed.update(b.get("rejections_by_failed_layer") or {})
        tiers.update(b.get("tier_counts") or {})
        for layer, res in (b.get("layer_gate_results") or {}).items():
            if isinstance(res, dict) and res.get("passed") is False:
                gate_fail[layer] += 1
    return {"observations_with_bilqe": len(obs) - errors, "bilqe_errors": errors,
            "match_level_gate_failures": dict(gate_fail), "pick_rejections_by_failed_layer": dict(layers_failed.most_common()),
            "qualified_by_tier": dict(tiers)}


def _results(conn) -> dict:
    out = {}
    for policy in POLICIES:
        rows = conn.execute("SELECT result, pnl_flat_units, clv_pct FROM shadow_selection_results WHERE policy = ?",
                            (policy,)).fetchall()
        n = len(rows)
        clv = [r[2] for r in rows if r[2] is not None]
        out[policy] = {"settled": n, "won": sum(r[0] == "won" for r in rows),
                       "pnl_flat_units": round(sum(r[1] for r in rows), 3),
                       "roi_flat": round(sum(r[1] for r in rows) / n, 4) if n else None,
                       "clv_valid": len(clv), "clv_mean_pct": round(100 * sum(clv) / len(clv), 2) if clv else None}
    return out


def _calibration(conn) -> dict:
    rows = conn.execute(
        """SELECT o.forecast_json, r.home_goals_ft, r.away_goals_ft FROM shadow_observations o
           JOIN shadow_outcomes r ON r.fixture_id = o.fixture_id WHERE o.forecast_json IS NOT NULL"""
    ).fetchall()
    briers = {"1X2": [], "O/U 2.5": [], "BTTS": []}
    logloss = {"1X2": [], "O/U 2.5": [], "BTTS": []}
    for f_json, h, a in rows:
        f = _loads(f_json, {})
        res = "home" if h > a else "away" if a > h else "draw"
        if f.get("1X2"):
            p = f["1X2"]
            briers["1X2"].append(sum((p.get(k, 0) - (k == res)) ** 2 for k in ("home", "draw", "away")))
            logloss["1X2"].append(-math.log(max(1e-9, p.get(res, 0))))
        for market, key, hit in (("O/U 2.5", "over", h + a > 2.5), ("BTTS", "yes", h > 0 and a > 0)):
            if f.get(market):
                p = f[market].get(key, 0)
                briers[market].append((p - hit) ** 2)
                logloss[market].append(-math.log(max(1e-9, p if hit else 1 - p)))
    return {m: {"n": len(b), "brier": round(sum(b) / len(b), 4) if b else None,
                "log_loss": round(sum(logloss[m]) / len(logloss[m]), 4) if logloss[m] else None}
            for m, b in briers.items()}


def _reproducibility(obs) -> dict:
    from src.engine.probability_engine import estimate_probabilities_from_inputs

    checked = mismatched = 0
    for o in obs:
        if not o["probability_inputs_json"] or not o["raw_probabilities_json"]:
            continue
        checked += 1
        again = json.loads(json.dumps(estimate_probabilities_from_inputs(json.loads(o["probability_inputs_json"])),
                                      sort_keys=True, default=str))
        if again != json.loads(o["raw_probabilities_json"]):
            mismatched += 1
    return {"checked": checked, "mismatched": mismatched}


def _quota(conn) -> dict:
    rows = conn.execute(
        """SELECT substr(at, 1, 10) AS day, provider, status, COUNT(*) AS requests, SUM(cost) AS cost,
                  MIN(CAST(requests_remaining AS INTEGER)) AS min_remaining
           FROM provider_quota_log GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"""
    ).fetchall()
    errors = conn.execute(
        "SELECT http_status, COUNT(*) FROM provider_quota_log WHERE status = 'error' GROUP BY 1"
    ).fetchall()
    return {"by_day": [dict(r) for r in rows], "errors_by_http_status": {str(k): v for k, v in errors}}


def build_report(conn: sqlite3.Connection, max_age_minutes: int = 240) -> dict:
    conn.row_factory = sqlite3.Row
    obs = conn.execute("SELECT * FROM shadow_observations ORDER BY decided_at").fetchall()
    outcomes = conn.execute("SELECT COUNT(*) FROM shadow_outcomes").fetchone()[0]
    settled_obs = conn.execute(
        "SELECT COUNT(*) FROM shadow_observations o JOIN shadow_outcomes r USING (fixture_id)").fetchone()[0]
    inconsistent = conn.execute(
        """SELECT COUNT(*) FROM shadow_outcomes WHERE status NOT IN ('FT', 'AET', 'PEN')
               OR home_goals_ft IS NULL OR away_goals_ft IS NULL OR home_goals_ft < 0 OR away_goals_ft < 0"""
    ).fetchone()[0]
    versions = conn.execute(
        """SELECT code_revision, model_version, calibration_version, legacy_policy_version, v1_policy_version,
                  COUNT(*) AS n FROM shadow_observations GROUP BY 1, 2, 3, 4, 5"""
    ).fetchall()
    return {
        "period": _period(conn),
        "fixtures_tracked": conn.execute("SELECT COUNT(*) FROM shadow_fixtures").fetchone()[0],
        "observations": len(obs),
        "observation_errors": sum(1 for o in obs if o["error"]),
        "minutes_to_kickoff_at_decision": sorted(o["minutes_to_kickoff"] for o in obs)[:: max(1, len(obs) // 10)],
        "versions": [dict(v) for v in versions],
        "data_quality": dict(Counter("<60" if (o["data_quality"] or 0) < 60 else ">=60" for o in obs)),
        "model_sources": dict(Counter(o["model_source"] for o in obs)),
        "identity": _identity(obs),
        "odds_coverage": _coverage(obs, max_age_minutes),
        "funnels": _funnels(obs),
        "bilqe": _bilqe(obs),
        "settlement": {"outcomes_recorded": outcomes, "observed_and_settled": settled_obs,
                       "observed_awaiting_result": len(obs) - settled_obs, "inconsistent_outcomes": inconsistent},
        "results": _results(conn),
        "calibration": _calibration(conn),
        "reproducibility": _reproducibility(obs),
        "provider_quota": _quota(conn),
        "counts_toward_baseline": conn.execute(
            "SELECT COUNT(*) FROM shadow_observations WHERE counts_toward_baseline != 0").fetchone()[0],
    }

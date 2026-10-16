#!/usr/bin/env python3
"""
Compare the legacy official selection with baseline policy v1 (proposal) — M3 Phase D.

    python scripts/compare_selection_policies.py --out report.json [--probe-db <isolated db>] [--seed N]

Two samples, neither counted toward any baseline:

* simulated: seeded synthetic matches with a known "true" probability per
  selection, a noisy model estimate and a bookmaker price with margin. Both
  policies see the same candidates; the report gives counts, picks per match,
  markets, reasons and the expected ROI under the true probabilities.
* forward real-odds sample (optional): fixtures and real API-Football quotes
  in an isolated probe DB (scripts/validate_odds_provider.py). Probabilities
  come from the production probability engine against that DB.

Legacy = pipeline steps 4-5 + grading unchanged (positive edge, score_confidence
with league_profiles, apply_risk_filter, assign_grade != Pass). v1 is evaluated
with frozen provenance assumed, so the remaining rules are visible; with the
current unfrozen provenance v1 selects nothing by design.
"""

import argparse
import json
import os
import random
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = (PROJECT_ROOT / "data" / "engine.db").resolve()
ASSUMED_FROZEN = {"model_version": "assumed", "calibration_version": "assumed", "calibration_frozen": True}
ILLUSTRATIVE_APPROVED = ("Premier League", "LaLiga", "Serie A", "Bundesliga", "Ligue 1", "Champions League")
SEED_LEAGUES = [(17, "Premier League"), (8, "LaLiga"), (23, "Serie A"), (35, "Bundesliga"), (34, "Ligue 1"),
                (18, "Championship"), (37, "Eredivisie"), (52, "Süper Lig"), (7, "Champions League"),
                (999001, "Unknown League A"), (999002, "Unknown League B")]


def legacy_select(conn, candidates):
    from src.engine.risk_control import apply_risk_filter, get_league_profile, score_confidence
    from src.models.pick import assign_grade

    positive = [dict(c) for c in candidates if c["edge"] > 0.0]
    for c in positive:
        score_confidence(c, get_league_profile(conn, c.get("league_id", 0)), c.get("model_source", "fallback"))
    picks, reasons = [], Counter()
    if len(positive) < len(candidates):
        reasons["NO_POSITIVE_EDGE"] += len(candidates) - len(positive)
    passed = apply_risk_filter(positive)
    for c in positive:
        for r in c.get("risk_rejections", []):
            reasons[r["code"]] += 1
    for c in passed:
        grade, stake = assign_grade(c["edge"], c["confidence"], c["league_reliability"], c["model_prob"], c["odds"])
        if grade == "Pass":
            reasons["GRADE_PASS"] += 1
            continue
        picks.append({**c, "grade": grade, "stake_units": min(stake, c.get("max_stake_units", 2.0))})
    return picks, reasons


def summarize(picks, total_candidates, matches, true_prob=None):
    per_match = Counter(Counter(p["match_id"] for p in picks).values())
    out = {
        "candidates": total_candidates,
        "selections": len(picks),
        "matches": matches,
        "matches_with_selection": len({p["match_id"] for p in picks}),
        "selections_per_match_histogram": dict(sorted(per_match.items())),
        "by_market": dict(Counter(p["market"] for p in picks)),
        "by_league": dict(Counter(p["league_name"] for p in picks).most_common()),
    }
    if true_prob is not None and picks:
        flat = [true_prob[(p["match_id"], p["market"], p["selection"])] * p["odds"] - 1 for p in picks]
        staked = [(p.get("stake_units") or 1.0) for p in picks]
        weighted = sum(e * s for e, s in zip(flat, staked)) / sum(staked)
        out["expected_roi_flat_under_truth"] = round(sum(flat) / len(flat), 4)
        out["expected_roi_staked_under_truth"] = round(weighted, 4)
        out["share_negative_true_ev"] = round(sum(e < 0 for e in flat) / len(flat), 3)
    return out


def simulated_candidates(rng, n_matches, now, model_sd=0.05, market_sd=0.0):
    cands, truth = [], {}
    for m in range(n_matches):
        league_id, league = rng.choice(SEED_LEAGUES)
        mid = f"sim-{m}"
        dq = round(rng.uniform(30, 95), 1)
        source = "poisson" if rng.random() < 0.9 else "fallback"
        h, d, a = (rng.gammavariate(x, 1) for x in (4.5, 2.6, 3.2))
        s = h + d + a
        over = min(max(rng.gauss(0.52, 0.09), 0.2), 0.8)
        btts = min(max(rng.gauss(0.50, 0.08), 0.2), 0.8)
        markets = {"1X2": {"home": h / s, "draw": d / s, "away": a / s},
                   "O/U 2.5": {"over": over, "under": 1 - over}, "BTTS": {"yes": btts, "no": 1 - btts}}
        age = rng.choice([rng.uniform(0, 60), rng.uniform(61, 240), rng.uniform(190, 290), rng.uniform(241, 600)])
        fetched = rng.uniform(0, 120)
        for market, sels in markets.items():
            for sel, p_true in sels.items():
                p_model = min(max(p_true + rng.gauss(0, model_sd), 0.02), 0.97)
                p_market = min(max(p_true + rng.gauss(0, market_sd), 0.02), 0.97)
                margin = 1.05
                odds = round(max(1.02, 1.0 / (p_market * margin) * rng.uniform(0.98, 1.02)), 2)
                truth[(mid, market, sel)] = p_true
                cands.append({
                    "match_id": mid, "home_team": f"H{m}", "away_team": f"A{m}", "league_name": league,
                    "league_id": league_id, "market": market, "selection": sel, "model_prob": round(p_model, 4),
                    "implied_prob": round(1 / odds, 4), "edge": round(p_model - 1 / odds, 4), "odds": odds,
                    "bookmaker": "sim", "odds_provider": "simulation", "odds_type": "bookmaker",
                    "odds_observed_at": (now - timedelta(minutes=age)).isoformat(),
                    "odds_fetched_at": (now - timedelta(minutes=fetched)).isoformat(),
                    "data_quality": dq, "model_source": source,
                })
    return cands, truth


def run_v1(cands, now, config, league_records=None, provenance=ASSUMED_FROZEN):
    from src.policy import baseline_v1 as v1

    decisions = v1.evaluate(cands, now, provenance, league_records or {}, config)
    by_key = {(c["match_id"], c["market"], c["selection"]): c for c in cands}
    picks = [{**by_key[(d.match_id, d.market, d.selection)], "stake_units": d.stake_units}
             for d in decisions if d.selected]
    return picks, v1.funnel(decisions)


def forward_sample(probe_db: Path, now):
    from src.db.database import get_db
    from src.engine.market_value import find_value
    from src.engine.pipeline import _pick_calibrator, calibrate_probabilities
    from src.engine.probability_engine import estimate_probabilities_from_inputs, resolve_probability_inputs

    conn = get_db()
    fixtures = [dict(r) for r in conn.execute(
        "SELECT * FROM matches WHERE provider = 'api_football' AND status = 'NS' AND id IN "
        "(SELECT DISTINCT fixture_id FROM odds_quotes)").fetchall()]
    calibrator = _pick_calibrator(conn)
    cands, forecasts = [], {}
    for match in fixtures:
        inputs = resolve_probability_inputs(match)
        probs = calibrate_probabilities(estimate_probabilities_from_inputs(inputs), calibrator)
        forecasts[match["id"]] = {"source": probs.get("source"), "data_quality": probs.get("data_quality"),
                                  "1X2": probs.get("1X2"), "O/U 2.5": probs.get("O/U 2.5"), "BTTS": probs.get("BTTS")}
        cands.extend(find_value(match, probs, conn, now=now))
    return conn, fixtures, cands, forecasts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--probe-db", type=Path)
    parser.add_argument("--seed", type=int, default=20261016)
    parser.add_argument("--matches", type=int, default=2000)
    args = parser.parse_args()

    if args.probe_db and args.probe_db.resolve() == LIVE_DB:
        print("refusing: --probe-db must be an isolated copy", file=sys.stderr)
        return 2
    import tempfile
    scratch = Path(tempfile.mkdtemp(prefix="policy-compare-"))
    os.environ["FOOTBALL_PREDICTOR_DB_PATH"] = str(args.probe_db.resolve() if args.probe_db else scratch / "sim.db")
    os.environ.setdefault("ODDS_MAX_AGE_MINUTES", "240")
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.db.database import get_db
    from src.policy import baseline_v1 as v1

    now = datetime.now(timezone.utc)
    report = {"generated_at": now.isoformat(), "policy_version_default": v1.version(),
              "policy_config_default": v1.DEFAULT_CONFIG.__dict__}

    # Simulated sample (league_profiles of a fresh schema: the 14 seeded tracked leagues)
    sim_conn = get_db() if not args.probe_db else __import__("sqlite3").connect(":memory:")
    if args.probe_db:
        from src.db.database import init_db
        sim_conn.row_factory = __import__("sqlite3").Row
        init_db(sim_conn)
    approved = v1.PolicyConfig(approved_leagues=ILLUSTRATIVE_APPROVED)
    scenarios = {
        "efficient_market": {"model_sd": 0.05, "market_sd": 0.0,
                             "meaning": "bookmaker prices = truth + 5% margin; model = truth + N(0, 0.05)"},
        "model_has_edge": {"model_sd": 0.02, "market_sd": 0.04,
                           "meaning": "model = truth + N(0, 0.02); market = truth + N(0, 0.04) + 5% margin"},
    }
    report["simulated"] = {}
    for name, sc in scenarios.items():
        rng = random.Random(args.seed)
        cands, truth = simulated_candidates(rng, args.matches, now, sc["model_sd"], sc["market_sd"])
        legacy_picks, legacy_reasons = legacy_select(sim_conn, cands)
        v1_default, f_default = run_v1(cands, now, v1.DEFAULT_CONFIG)
        v1_approved, f_approved = run_v1(cands, now, approved)
        _, f_unfrozen = run_v1(cands, now, approved, provenance={})
        report["simulated"][name] = {
            "seed": args.seed, "matches": args.matches, **sc,
            "legacy": {**summarize(legacy_picks, len(cands), args.matches, truth),
                       "rejections_by_reason": dict(legacy_reasons.most_common())},
            "v1_default_no_approved_leagues": {**summarize(v1_default, len(cands), args.matches, truth),
                                               "funnel": f_default},
            "v1_illustrative_approved_leagues": {**summarize(v1_approved, len(cands), args.matches, truth),
                                                 "approved_leagues": ILLUSTRATIVE_APPROVED,
                                                 "policy_version": v1.version(approved), "funnel": f_approved},
            "v1_with_current_unfrozen_provenance": f_unfrozen,
        }

    if args.probe_db:
        conn, fixtures, fcands, forecasts = forward_sample(args.probe_db, now)
        lp, lr = legacy_select(conn, fcands)
        vp_d, vf_d = run_v1(fcands, now, v1.DEFAULT_CONFIG)
        vp_a, vf_a = run_v1(fcands, now, approved)
        report["forward_real_odds_sample"] = {
            "note": "real API-Football quotes, upcoming fixtures, probabilities from the production engine; "
                    "not counted and not a forward prediction record",
            "fixtures": len(fixtures), "candidates": len(fcands),
            "forecast_sources": dict(Counter(f["source"] for f in forecasts.values())),
            "legacy": {**summarize(lp, len(fcands), len(fixtures)), "rejections_by_reason": dict(lr.most_common()),
                       "picks": [{k: p[k] for k in ("match_id", "league_name", "market", "selection", "model_prob",
                                                    "odds", "edge", "grade", "stake_units")} for p in lp]},
            "v1_default_no_approved_leagues": {**summarize(vp_d, len(fcands), len(fixtures)), "funnel": vf_d},
            "v1_illustrative_approved_leagues": {**summarize(vp_a, len(fcands), len(fixtures)), "funnel": vf_a,
                                                 "picks": [{k: p[k] for k in ("match_id", "league_name", "market",
                                                                              "selection", "model_prob", "odds", "edge")}
                                                           for p in vp_a]},
            "candidate_ages_minutes": sorted({c["odds_age_minutes"] for c in fcands}),
        }
    report["historical_real_odds"] = ("not possible: no historical bookmaker quotes exist in the database "
                                      "(odds_snapshots and odds_quotes are empty before M3)")
    args.out.write_text(json.dumps(report, indent=1, default=str))
    keys = ("selections", "matches_with_selection", "selections_per_match_histogram",
            "expected_roi_flat_under_truth", "expected_roi_staked_under_truth", "share_negative_true_ev")
    print(json.dumps({name: {k: {kk: vv for kk, vv in v.items() if kk in keys}
                             for k, v in sim.items() if isinstance(v, dict) and "selections" in v}
                      for name, sim in report["simulated"].items()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

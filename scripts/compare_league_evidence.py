#!/usr/bin/env python3
"""
League-evidence handling: current behaviour vs conservative alternatives (M3.1) — isolated copies only.

    python scripts/compare_league_evidence.py --original <pre-M3 backup> --candidate <remediated candidate> \
        --out report.json [--seed N --matches N]

Per tracked competition:
  * evidence class from the verified evidence (src/policy/league_evidence.py);
  * BILQE Layer 3 league reliability as the code computes it today
    (get_league_adjustment by label: active adjustment -> its reliability, else 65),
    on the original artifacts and on the remediated candidate, and pass/fail at 60;
  * alternatives: strict (VERIFIED_SUFFICIENT and reliability >= 60) and
    approved-provisional (strict, or on the owner's provisional list).
Then legacy / v1 / v1.1 on the seeded simulation of compare_selection_policies.py.
"""

import argparse
import json
import os
import random
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = (PROJECT_ROOT / "data" / "engine.db").resolve()
ILLUSTRATIVE_PROVISIONAL = ("england-premier-league", "spain-la-liga", "italy-serie-a", "germany-bundesliga",
                            "france-ligue-1", "uefa-champions-league")
SIM_COMPETITION = {"Premier League": "england-premier-league", "LaLiga": "spain-la-liga", "Serie A": "italy-serie-a",
                   "Bundesliga": "germany-bundesliga", "Ligue 1": "france-ligue-1",
                   "Championship": "england-championship", "Eredivisie": "netherlands-eredivisie",
                   "Süper Lig": "turkey-super-lig", "Champions League": "uefa-champions-league"}


def bilqe_reliability(conn, label: str) -> tuple[float, bool, int]:
    from src.db.error_intelligence import get_league_adjustment

    adj = get_league_adjustment(conn, label)
    if adj.get("active"):
        return float(adj.get("reliability") or 0.0), True, int(adj.get("sample_count") or 0)
    return 65.0, False, int(adj.get("sample_count") or 0)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--original", required=True, type=Path)
    p.add_argument("--candidate", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--seed", type=int, default=20261016)
    p.add_argument("--matches", type=int, default=2000)
    a = p.parse_args()
    if LIVE_DB in (a.original.resolve(), a.candidate.resolve()):
        print("refusing: use isolated copies", file=sys.stderr)
        return 2
    os.environ["FOOTBALL_PREDICTOR_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "scratch.db")
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.identity.competitions import COMPETITIONS
    from src.policy import baseline_v1 as v1
    from src.policy import baseline_v1_1 as v11
    from src.policy.league_evidence import load_league_evidence

    orig = sqlite3.connect(f"file:{a.original.resolve()}?mode=ro", uri=True)
    cand = sqlite3.connect(f"file:{a.candidate.resolve()}?mode=ro", uri=True)
    for c in (orig, cand):
        c.row_factory = sqlite3.Row
    evidence = load_league_evidence(cand)

    rows = []
    for comp in COMPETITIONS:
        ev = evidence[comp.key]
        label = comp.history_labels[0]
        o_rel, o_active, o_n = bilqe_reliability(orig, label)
        c_rel, c_active, c_n = bilqe_reliability(cand, label)
        strict = ev.demonstrated and (ev.reliability or 0) >= 60
        rows.append({
            "competition": comp.key, "label_used_by_bilqe": label,
            "evidence_class": ev.evidence_class, "verified_settled": ev.verified_settled,
            "quarantined_rows": ev.quarantined, "verified_reliability": ev.reliability,
            "bilqe_original": {"reliability": o_rel, "from_active_adjustment": o_active, "label_samples": o_n,
                               "passes_layer3": o_rel >= 60},
            "bilqe_remediated": {"reliability": c_rel, "from_active_adjustment": c_active, "label_samples": c_n,
                                 "passes_layer3": c_rel >= 60},
            "strict_verified_passes": strict,
            "approved_provisional_passes": strict or comp.key in ILLUSTRATIVE_PROVISIONAL,
        })

    # Simulation (same generator as compare_selection_policies.py)
    import importlib.util
    spec = importlib.util.spec_from_file_location("csp", PROJECT_ROOT / "scripts" / "compare_selection_policies.py")
    csp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(csp)
    from src.db.database import get_db

    sim_conn = get_db()
    now = datetime.now(timezone.utc)
    frozen = csp.ASSUMED_FROZEN
    sim = {}
    for name, (msd, ksd) in {"efficient_market": (0.05, 0.0), "model_has_edge": (0.02, 0.04)}.items():
        cands, truth = csp.simulated_candidates(random.Random(a.seed), a.matches, now, msd, ksd)
        for c in cands:
            c["competition_key"] = SIM_COMPETITION.get(c["league_name"])
            c["identity_resolved"] = c["model_source"] != "fallback"
        legacy, _ = csp.legacy_select(sim_conn, cands)
        v1_picks, _ = csp.run_v1(cands, now, v1.PolicyConfig(approved_leagues=csp.ILLUSTRATIVE_APPROVED))
        out = {"legacy": csp.summarize(legacy, len(cands), a.matches, truth),
               "v1_illustrative_list": csp.summarize(v1_picks, len(cands), a.matches, truth)}
        for label, cfg in (("v1_1_default", v11.DEFAULT_CONFIG),
                           ("v1_1_illustrative_provisional",
                            v11.PolicyV11Config(provisional_competitions=ILLUSTRATIVE_PROVISIONAL))):
            ds = v11.evaluate(cands, now, frozen, evidence, cfg)
            by_key = {(c["match_id"], c["market"], c["selection"]): c for c in cands}
            picks = [{**by_key[(d.match_id, d.market, d.selection)], "stake_units": d.stake_units}
                     for d in ds if d.selected]
            reasons = Counter(r["code"] for d in ds for r in d.reasons)
            out[label] = {**csp.summarize(picks, len(cands), a.matches, truth), "policy_version": v11.version(cfg),
                          "evidence_status": dict(Counter(getattr(d, "evidence_status", None) for d in ds
                                                          if d.selected)),
                          "rejections_by_reason": dict(reasons.most_common())}
        sim[name] = out

    report = {
        "evidence_by_competition": rows,
        "summary": {
            "evidence_classes": dict(Counter(r["evidence_class"] for r in rows)),
            "bilqe_layer3_pass_original": sum(r["bilqe_original"]["passes_layer3"] for r in rows),
            "bilqe_layer3_pass_remediated": sum(r["bilqe_remediated"]["passes_layer3"] for r in rows),
            "strict_verified_pass": sum(r["strict_verified_passes"] for r in rows),
            "approved_provisional_pass": sum(r["approved_provisional_passes"] for r in rows),
            "competitions": len(rows),
        },
        "illustrative_provisional_list": ILLUSTRATIVE_PROVISIONAL,
        "simulation": sim,
    }
    a.out.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(report["summary"], indent=1))
    for name, s in sim.items():
        print(name, {k: (v["selections"], v.get("expected_roi_flat_under_truth")) for k, v in s.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())

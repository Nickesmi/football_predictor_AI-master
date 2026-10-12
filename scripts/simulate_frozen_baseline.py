"""Simulate N days of frozen-baseline operation on an ISOLATED database copy.

Usage:
    python scripts/simulate_frozen_baseline.py --db COPY.db --manifest-dir DIR --out REPORT.md [--days 30]

Never point --db at the production database: the script freezes a baseline in
it, inserts fixtures, picks and results, and runs the nightly refit.

Each simulated day runs the real code paths: run_pipeline (official picks with
provenance), match results -> live_updater.on_match_finished (Elo/form),
auto_settle_picks, error-intelligence outcome logging (store/settle_prediction,
which updates league profiles), prediction_log growth, and the nightly refit.
After every day it checks that frozen parameters, calibration, league
adjustments, gate statistics and selection inputs are unchanged, that official
picks carry complete provenance and reproduce exactly, and that Elo/form moved.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _parse():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True)
    p.add_argument("--manifest-dir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--version", default="football-sim-v1")
    p.add_argument("--seed", type=int, default=20261012)
    return p.parse_args()


ARGS = _parse()
if Path(ARGS.db).resolve() == (ROOT / "data" / "engine.db").resolve():
    sys.exit("Refusing to simulate on the production database.")
os.environ.update(
    HOME=tempfile.mkdtemp(prefix="fp-sim-home-"), FOOTBALL_PREDICTOR_DB_PATH=str(Path(ARGS.db).resolve()),
    APIFOOTBALL_API_KEY="", API_FOOTBALL_KEY="", RAPIDAPI_KEY="", ODDS_API_KEY="",
)
os.environ.pop("BASELINE_FREEZE", None)
sys.path.insert(0, str(ROOT))

from api.services.portfolio_service import auto_settle_picks  # noqa: E402
from api.services.system_service import run_nightly_refit  # noqa: E402
from src import config  # noqa: E402
from src.baseline import state as baseline  # noqa: E402
from src.baseline.freeze import create_baseline, table_content_hash  # noqa: E402
from src.db.database import get_db  # noqa: E402
from src.db.error_intelligence import get_league_adjustment, settle_prediction, store_prediction_record  # noqa: E402
from src.db.picks_repo import get_experiment_progress  # noqa: E402
from src.db.team_state import get_team_state  # noqa: E402
from src.engine import isotonic_calibrator  # noqa: E402
from src.engine.calibration import ProbabilityCalibrator  # noqa: E402
from src.engine.live_updater import on_match_finished  # noqa: E402
from src.engine.performance_gate import load_market_performance  # noqa: E402
from src.engine.pipeline import _pick_calibrator, calibrate_probabilities, reproduce_probabilities, run_pipeline  # noqa: E402
from src.engine.probability_engine import estimate_probabilities_from_inputs  # noqa: E402
from src.engine.qualification_engine import _pick_history_for_qualification  # noqa: E402

PROBE_INPUTS = {  # fixed inputs: anything that changes their output is a parameter change
    "home_team": "Probe Home", "away_team": "Probe Away", "league_name": "Premier League",
    "country": "England", "league_key": "Premier League",
    "home_stats": {"scored": 1.8, "conceded": 0.9, "corners": 5.5, "cards": 1.8, "matches_played": 20, "form_last5": 0.7},
    "away_stats": {"scored": 1.1, "conceded": 1.5, "corners": 4.5, "cards": 2.2, "matches_played": 20, "form_last5": 0.4},
}
PROBE_LEAGUES = ("Premier League", "Friendlies Clubs", "USL League Two", "Serie A", "Bundesliga")


def frozen_fingerprint(conn) -> dict:
    isotonic_calibrator._calibrator_instance = None
    iso = isotonic_calibrator.get_isotonic_calibrator(conn)
    return {
        "probe_probabilities": calibrate_probabilities(estimate_probabilities_from_inputs(PROBE_INPUTS), _pick_calibrator(conn)),
        "pick_calibrator": _pick_calibrator(conn).to_state(),
        "gate_stats": load_market_performance(conn),
        "league_adjustments": {lg: get_league_adjustment(conn, lg) for lg in PROBE_LEAGUES},
        "bilqe_history": _pick_history_for_qualification(conn),
        "isotonic_probe": {mt: iso.calibrate(72.5, mt) for mt in sorted(iso._models)},
        "prod_calibration_models": table_content_hash(conn, "calibration_models", "id"),
        "prod_adjustments": table_content_hash(conn, "model_confidence_adjustments", "league_name"),
    }


def pick_teams(conn, n=20) -> list[str]:
    rows = conn.execute(
        """SELECT team_name FROM team_state WHERE league = 'Premier League' AND venue = 'overall'
           ORDER BY matches_played DESC, team_name LIMIT ?""", (n,),
    ).fetchall()
    return [r[0] for r in rows]


def elo(conn, team):
    state = get_team_state(conn, team, "Premier League", "overall")
    return (round(state.elo, 3), state.matches_played) if state else (None, 0)


def main() -> int:
    rng = random.Random(ARGS.seed)
    conn = get_db()
    manifest = create_baseline(conn, ARGS.version, Path(ARGS.manifest_dir), approved_by="M2 simulation")
    config.BASELINE_MANIFEST_DIR = Path(ARGS.manifest_dir)
    config.BASELINE_FREEZE = ARGS.version

    day0 = frozen_fingerprint(conn)
    teams = pick_teams(conn)
    start = date(2030, 1, 1)
    rows, failures = [], []
    shadow_cal0 = conn.execute("SELECT COUNT(*) FROM calibration_models_shadow").fetchone()[0]

    for day in range(ARGS.days):
        d = (start + timedelta(days=day)).isoformat()
        rng.shuffle(teams)
        events = []
        for k in range(5):
            home, away = teams[2 * k], teams[2 * k + 1]
            v1, vx = rng.randint(5, 60), rng.randint(15, 35)
            events.append({
                "id": f"sim-{day:02d}-{k}", "startTimestamp": 1893456000 + day * 86400,
                "tournament": {"uniqueTournament": {"id": 17, "name": "Premier League"}},
                "homeTeam": {"name": home}, "awayTeam": {"name": away},
                "status": {"type": "notstarted"}, "homeScore": {}, "awayScore": {},
                "vote": {"vote1": v1, "votex": vx, "vote2": max(5, 100 - v1 - vx)},
            })
        elo_before = {t: elo(conn, t) for t in teams[:10]}

        result = run_pipeline(d, events, conn)

        for ev in events:  # matches finish: results, Elo/form, outcome logging (P8/P18), legacy log growth
            hg, ag = rng.choice([0, 0, 1, 1, 1, 2, 2, 3]), rng.choice([0, 0, 1, 1, 2, 2, 3])
            home, away = ev["homeTeam"]["name"], ev["awayTeam"]["name"]
            conn.execute("UPDATE matches SET status = 'FT', home_goals = ?, away_goals = ? WHERE id = ?", (hg, ag, ev["id"]))
            conn.commit()
            on_match_finished(conn, ev["id"], d, "Premier League", home, away, hg, ag)
            store_prediction_record(conn, ev["id"], d, "Premier League", "England", home, away, 48.0, 27.0, 25.0)
            settle_prediction(conn, ev["id"], hg, ag)
            for m in range(10):
                p = rng.uniform(55, 95)
                conn.execute(
                    """INSERT INTO prediction_log (match_id, match_date, home_team, away_team, league_name,
                                                   market, market_type, predicted_prob, actual_outcome)
                       VALUES (?, ?, ?, ?, 'Premier League', 'Over 1.5 Goals', 'goals', ?, ?)""",
                    (f"{ev['id']}-{m}", d, home, away, round(p, 1), int(rng.random() * 100 < p)),
                )
        conn.commit()
        settled = auto_settle_picks(conn)
        refit_mode = run_nightly_refit(conn)

        report = baseline.integrity_report(conn)
        fingerprint = frozen_fingerprint(conn)
        changed = sorted(k for k in day0 if fingerprint[k] != day0[k])
        elo_moved = sum(1 for t in teams[:10] if elo(conn, t) != elo_before[t])

        frozen_cal = ProbabilityCalibrator.from_state(baseline.load_artifact(conn, "pick_calibrator"))
        day_picks = [dict(r) for r in conn.execute(
            "SELECT * FROM picks WHERE match_id LIKE ?", (f"sim-{day:02d}-%",)
        ).fetchall()]
        provenance_ok = all(
            p["baseline_version"] == manifest["baseline_version"]
            and p["model_version"] == manifest["model_version"]
            and p["calibration_version"] == manifest["calibration_version"]
            and p["selection_policy_version"] == manifest["selection_policy_version"]
            and p["code_revision"] and p["runtime"] and p["input_data_timestamp"] and p["inputs_json"]
            for p in day_picks
        )
        reproduced = all(
            abs(reproduce_probabilities(p["inputs_json"], frozen_cal)["calibrated"][p["market"]][p["selection"]] - p["model_prob"]) <= 1e-4
            for p in day_picks
        )
        row = {
            "day": day + 1, "date": d, "official_picks": result["final_picks"],
            "settled_today": settled.get("settled", settled.get("settled_count")), "integrity_ok": report["ok"],
            "frozen_values_changed": changed, "elo_moved_teams": elo_moved, "refit_mode": refit_mode,
            "provenance_complete": provenance_ok, "reproduced_exactly": reproduced,
        }
        rows.append(row)
        if not report["ok"] or changed or not provenance_ok or not reproduced or refit_mode != "shadow" or elo_moved == 0:
            failures.append(row)

    progress = get_experiment_progress(conn)
    totals = {
        "official_picks": conn.execute("SELECT COUNT(*) FROM picks WHERE match_id LIKE 'sim-%'").fetchone()[0],
        "prediction_errors_added": conn.execute("SELECT COUNT(*) FROM prediction_errors WHERE fixture_id LIKE 'sim-%'").fetchone()[0],
        "prediction_log_added": conn.execute("SELECT COUNT(*) FROM prediction_log WHERE match_id LIKE 'sim-%'").fetchone()[0],
        "shadow_calibration_models_added": conn.execute("SELECT COUNT(*) FROM calibration_models_shadow").fetchone()[0] - shadow_cal0,
        "shadow_league_profiles": conn.execute("SELECT COUNT(*) FROM model_confidence_adjustments_shadow").fetchone()[0],
        "premier_league_samples_frozen_vs_shadow": tuple(
            conn.execute(f"SELECT sample_count FROM {t} WHERE league_name = 'Premier League'").fetchone()[0]
            for t in ("model_confidence_adjustments_frozen", "model_confidence_adjustments_shadow")
        ),
    }
    write_report(manifest, rows, failures, progress, totals)
    print(json.dumps({"days": len(rows), "failures": len(failures), "progress": progress, **totals}, indent=2))
    return 1 if failures else 0


def write_report(manifest, rows, failures, progress, totals):
    lines = [
        "# M2 — Frozen Baseline: 30-Day Simulation",
        "",
        f"Generated by `scripts/simulate_frozen_baseline.py` on an isolated copy of the production database "
        f"(B4 backup), seed {ARGS.seed}. No production data, provider or running process was touched.",
        "",
        f"Baseline `{manifest['baseline_version']}` · model `{manifest['model_version']}` · calibration "
        f"`{manifest['calibration_version']}` · selection policy `{manifest['selection_policy_version']}` · runtime "
        f"`{manifest['runtime']['python']}`",
        "",
        "## Result",
        "",
        f"- Days simulated: **{len(rows)}**; days violating any invariant: **{len(failures)}**",
        f"- Official picks generated: {totals['official_picks']}; settled and counted toward the experiment: "
        f"{progress['settled_eligible']} (excluded for provenance: {progress['excluded_missing_or_mismatched_provenance']})",
        f"- Outcome records added (feed league learning): {totals['prediction_errors_added']}; legacy prediction_log rows added: {totals['prediction_log_added']}",
        f"- Shadow learning kept running: {totals['shadow_calibration_models_added']} shadow isotonic fits, "
        f"{totals['shadow_league_profiles']} shadow league profiles",
        f"- Counterfactual: Premier League league-profile sample count frozen = "
        f"{totals['premier_league_samples_frozen_vs_shadow'][0]}, shadow (what production would have used without the freeze) = "
        f"{totals['premier_league_samples_frozen_vs_shadow'][1]}",
        "",
        "Invariants checked every day: integrity report OK; fixed-input probe probabilities, pick calibrator, gate statistics, "
        "league adjustments, BILQE history inputs, isotonic calibration and production calibration/adjustment tables "
        "identical to day 0; nightly refit ran in shadow mode; every official pick has complete provenance equal to the "
        "manifest and its probability reproduces exactly from the stored inputs; team Elo/form moved after matches.",
        "",
        "## Daily log",
        "",
        "| Day | Date | Official picks | Integrity | Frozen values changed | Teams with Elo/form moved (of 10 sampled) | Refit | Provenance complete | Reproduced |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['day']} | {r['date']} | {r['official_picks']} | {'OK' if r['integrity_ok'] else 'FAIL'} | "
            f"{', '.join(r['frozen_values_changed']) or 'none'} | {r['elo_moved_teams']} | {r['refit_mode']} | "
            f"{'yes' if r['provenance_complete'] else 'NO'} | {'yes' if r['reproduced_exactly'] else 'NO'} |"
        )
    Path(ARGS.out).write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())

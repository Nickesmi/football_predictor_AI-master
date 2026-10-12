"""
Pipeline orchestrator: runs all agents in sequence for a given date.

    1. Fixture Collector  → fetch & filter matches, store in DB
    2. Odds Fetcher        → fetch real bookmaker odds from The Odds API
    3. Probability Engine  → estimate market probabilities
    3b. Calibration        → adjust raw probs using historical performance
    4. Market Value         → find edge vs bookmaker odds
    5. Risk Control         → score confidence, filter by league thresholds
    6. Grading & Sizing     → assign grade + stake
    7. Store picks in DB
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

from src.baseline import state as baseline
from src.db.database import get_db
from src.db.picks_repo import insert_pick, get_picks_by_date
from src.engine.fixture_collector import collect_fixtures
from src.engine.probability_engine import estimate_probabilities_from_inputs, resolve_probability_inputs
from src.engine.market_value import find_value, filter_positive_edge
from src.engine.risk_control import get_league_profile, score_confidence, apply_risk_filter
from src.engine.calibration import ProbabilityCalibrator
from src.engine import decisions

from src.models.pick import Pick, assign_grade

logger = logging.getLogger("football_predictor")

# Singleton calibrator
_calibrator = ProbabilityCalibrator(n_bins=10)


def _pick_calibrator(conn: sqlite3.Connection) -> ProbabilityCalibrator:
    """Frozen calibrator while the baseline is frozen; otherwise refit from settled picks (legacy behaviour)."""
    if baseline.is_frozen():
        return ProbabilityCalibrator.from_state(baseline.load_artifact(conn, "pick_calibrator"))
    _calibrator.fit_from_db(conn)
    return _calibrator


def calibrate_probabilities(raw: dict, calibrator: ProbabilityCalibrator) -> dict:
    """Apply pick calibration while preserving market coherence."""
    probs = {k: (dict(v) if isinstance(v, dict) else v) for k, v in raw.items()}
    if "1X2" in probs:
        calibrated = {
            sel: calibrator.calibrate(value, "1X2", sel)
            for sel, value in probs["1X2"].items()
        }
        total = sum(calibrated.values())
        if total > 0:
            probs["1X2"] = {
                sel: round(value / total, 4)
                for sel, value in calibrated.items()
            }

    if "O/U 2.5" in probs:
        over = calibrator.calibrate(
            probs["O/U 2.5"].get("over", 0.5),
            "O/U 2.5",
            "over",
        )
        over = max(0.01, min(0.99, over))
        probs["O/U 2.5"] = {
            "over": round(over, 4),
            "under": round(1.0 - over, 4),
        }

    if "BTTS" in probs:
        yes = calibrator.calibrate(
            probs["BTTS"].get("yes", 0.5),
            "BTTS",
            "yes",
        )
        yes = max(0.01, min(0.99, yes))
        probs["BTTS"] = {
            "yes": round(yes, 4),
            "no": round(1.0 - yes, 4),
        }
    return probs


def reproduce_probabilities(inputs_json: str, calibrator: ProbabilityCalibrator) -> dict:
    """Recompute a stored pick's probabilities from its recorded point-in-time inputs."""
    record = json.loads(inputs_json)
    raw = estimate_probabilities_from_inputs(record["probability_inputs"])
    return {"raw": raw, "calibrated": calibrate_probabilities(raw, calibrator)}


def run_pipeline(
    date_str: str,
    raw_events: list[dict],
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """
    Run the full investment pipeline for a date.

    Args:
        date_str: "YYYY-MM-DD"
        raw_events: raw SofaScore event list (from _fetch_sofascore_events)
        conn: optional DB connection (defaults to singleton)

    Returns:
        {
            "date": str,
            "total_matches": int,
            "tracked_matches": int,
            "candidates_found": int,
            "picks": [Pick.to_display_dict(), ...],
            "summary": { grade counts, total stake }
        }
    """
    if conn is None:
        conn = get_db()

    # ── Agent 1: Fixture Collector ───────────────────────────────
    all_fixtures = collect_fixtures(raw_events, date_str, conn)
    if not all_fixtures:
        logger.warning(f"No tracked fixtures for {date_str}")
        return _empty_result(date_str, len(raw_events))

    # Only generate picks for matches that haven't kicked off yet. Without this,
    # re-running the pipeline for a date with some matches already LIVE/FT would
    # silently generate a "pre-match" pick using team state that may already
    # reflect that very match's result — the live-pipeline equivalent of feature
    # leakage, and exactly what must not happen to the frozen 1,000-pick sample.
    fixtures = [m for m in all_fixtures if m.get("status") == "NS"]
    skipped_started = len(all_fixtures) - len(fixtures)
    if skipped_started:
        logger.warning(
            f"Skipped {skipped_started} already-started/finished fixture(s) for {date_str} "
            f"(not NS) — no picks generated for them."
        )
    if not fixtures:
        logger.warning(f"No not-yet-started fixtures for {date_str}")
        return _empty_result(date_str, len(raw_events))

    # ── Agent 2: Odds ────────────────────────────────────────────
    # Prices come only from odds_quotes: real bookmaker quotes collected by
    # scripts/odds_worker.py within the approved request budget (and, in an
    # isolated test database only, explicitly synthetic test prices). SofaScore
    # crowd votes are stored separately as opinions and are never used as odds.
    # Without a fresh real price a fixture gets a forecast but NO ACTIONABLE BET.
    odds_status = "real_bookmaker_quotes"

    # ── Baseline integrity (frozen mode only) ────────────────────
    # Official picks are refused outright if the running code, runtime or
    # frozen artifacts differ from the baseline manifest.
    try:
        manifest = baseline.require_official_integrity(conn)
    except baseline.BaselineIntegrityError as exc:
        logger.critical(f"Official pick generation refused for {date_str}: {exc}")
        refused = _empty_result(date_str, len(raw_events))
        refused["baseline_integrity_error"] = str(exc)
        return refused

    # ── Agent 3: Probability Engine ──────────────────────────────
    calibrator = _pick_calibrator(conn)

    match_probs = {}
    match_records = {}
    run_id = uuid.uuid4().hex
    decided_at = datetime.now(timezone.utc).isoformat()
    decision_rows: list[dict] = []
    for match in fixtures:
        try:
            inputs = resolve_probability_inputs(match)
            input_data_timestamp = datetime.now(timezone.utc).isoformat()
            raw = estimate_probabilities_from_inputs(inputs)
            probs = calibrate_probabilities(raw, calibrator)
            match_probs[match["id"]] = probs
            match_records[match["id"]] = {
                "input_data_timestamp": input_data_timestamp,
                "inputs_json": json.dumps({
                    "probability_inputs": inputs,
                    "raw_probabilities": raw,
                    "calibrated_probabilities": probs,
                }, sort_keys=True),
            }
        except Exception as e:
            logger.error(f"Probability engine failed for {match['id']}: {e}")
            decision_rows.append({"match_id": match["id"], "decision": "NO_FORECAST", "reasons_json": [
                {"code": decisions.PROBABILITY_ENGINE_FAILED, "detail": type(e).__name__}]})

    # ── Agent 4: Market Value ────────────────────────────────────
    all_candidates = []
    for match in fixtures:
        if match["id"] not in match_probs:
            continue
        probs = match_probs[match["id"]]
        candidates = find_value(match, probs, conn)
        positive = filter_positive_edge(candidates)
        all_candidates.extend(positive)
        if not candidates:
            decision_rows.append({"match_id": match["id"], "decision": "NO_ACTIONABLE_BET",
                                  "reasons_json": [decisions.NO_REAL_ODDS], "forecast_json": probs})
        for c in candidates:
            if c not in positive:
                decision_rows.append({"match_id": c["match_id"], "market": c["market"], "selection": c["selection"],
                                      "decision": "REJECTED", **decisions.candidate_fields(c), "reasons_json": [
                                          {"code": decisions.NO_POSITIVE_EDGE, "value": c["edge"], "threshold": 0.0}]})

    # ── Agent 5: Risk Control ────────────────────────────────────
    for candidate in all_candidates:
        league_id = candidate.get("league_id", 0)
        profile = get_league_profile(conn, league_id)
        model_source = match_probs.get(candidate["match_id"], {}).get("source", "fallback")
        score_confidence(candidate, profile, model_source)

    # Apply risk filter (removes candidates below league-specific edge threshold)
    filtered = apply_risk_filter(all_candidates)
    for c in all_candidates:
        if c.get("risk_rejections"):
            decision_rows.append({"match_id": c["match_id"], "market": c["market"], "selection": c["selection"],
                                  "decision": "REJECTED", **decisions.candidate_fields(c),
                                  "reasons_json": c["risk_rejections"]})

    # ── Grading & Sizing ─────────────────────────────────────────
    picks: list[Pick] = []
    odds_used: dict[tuple, dict] = {}
    for c in filtered:
        grade, stake = assign_grade(
            edge=c["edge"],
            confidence=c["confidence"],
            league_reliability=c["league_reliability"],
            model_prob=c["model_prob"],
            odds=c["odds"]
        )
        if grade == "Pass":
            decision_rows.append({"match_id": c["match_id"], "market": c["market"], "selection": c["selection"],
                                  "decision": "REJECTED", **decisions.candidate_fields(c), "reasons_json": [
                                      {"code": decisions.GRADE_PASS, "confidence": c["confidence"],
                                       "league_reliability": c["league_reliability"]}]})
            continue

        pick = Pick(
            match_id=c["match_id"],
            home_team=c["home_team"],
            away_team=c["away_team"],
            league_name=c["league_name"],
            market=c["market"],
            selection=c["selection"],
            model_prob=c["model_prob"],
            implied_prob=c["implied_prob"],
            edge=c["edge"],
            odds_at_pick=c["odds"],
            confidence=c["confidence"],
            league_reliability=c["league_reliability"],
            grade=grade,
            stake_units=min(stake, c.get("max_stake_units", 2.0)),
            model_source=c.get("model_source", "unknown"),
            bookmaker=c["bookmaker"],
            odds_type=c["odds_type"],
        )
        picks.append(pick)
        decision_rows.append({"match_id": c["match_id"], "market": c["market"], "selection": c["selection"],
                              "decision": "PICK", **decisions.candidate_fields(c), "reasons_json": [],
                              "grade": pick.grade, "stake_units": pick.stake_units})
        odds_used[(pick.match_id, pick.market, pick.selection)] = {
            "odds_type": c["odds_type"], "provider": c["odds_provider"], "bookmaker": c["bookmaker"],
            "decimal_odds": c["odds"], "observed_at": c["odds_observed_at"],
        }

    # ── Store picks in DB (with provenance) ──────────────────────
    for pick in picks:
        try:
            record = match_records[pick.match_id]
            inputs = json.loads(record["inputs_json"])
            inputs["odds"] = odds_used[(pick.match_id, pick.market, pick.selection)]
            insert_pick(conn, {
                **pick.to_db_dict(),
                **baseline.provenance(manifest, record["input_data_timestamp"]),
                "inputs_json": json.dumps(inputs, sort_keys=True),
            })
        except Exception as e:
            logger.error(f"Failed to store pick: {e}")

    # ── Record every decision (informational; never counted as picks) ──
    picked_matches = {p.match_id for p in picks}
    priced_matches = {row["match_id"] for row in decision_rows if row.get("market")}
    for match_id in sorted(priced_matches):
        decision_rows.append({"match_id": match_id, "forecast_json": match_probs.get(match_id), **(
            {"decision": "ACTIONABLE", "reasons_json": []} if match_id in picked_matches
            else {"decision": "NO_ACTIONABLE_BET", "reasons_json": [decisions.ALL_SELECTIONS_REJECTED]})})
    provenance = baseline.provenance(manifest, decided_at)
    try:
        decisions.record(conn, [{**row, "run_id": run_id, "decided_at": decided_at,
                                 "code_revision": provenance["code_revision"],
                                 "baseline_version": provenance["baseline_version"]} for row in decision_rows])
    except Exception as e:
        logger.error(f"Failed to record selection decisions: {e}")
    no_actionable = sum(1 for row in decision_rows if row["decision"] == "NO_ACTIONABLE_BET")

    # ── Build result ─────────────────────────────────────────────
    grade_counts = {}
    total_stake = 0.0
    for p in picks:
        grade_counts[p.grade] = grade_counts.get(p.grade, 0) + 1
        total_stake += p.stake_units

    result = {
        "date": date_str,
        "total_events": len(raw_events),
        "tracked_matches": len(fixtures),
        "odds_status": odds_status,
        "candidates_found": len(all_candidates),
        "picks_after_filter": len(filtered),
        "final_picks": len(picks),
        "no_actionable_bet": no_actionable,
        "decision_run_id": run_id,
        "picks": [p.to_display_dict() for p in picks],
        "summary": {
            "grades": grade_counts,
            "total_stake_units": round(total_stake, 2),
            "avg_edge": round(
                sum(p.edge for p in picks) / max(len(picks), 1) * 100, 1
            ),
            "avg_confidence": round(
                sum(p.confidence for p in picks) / max(len(picks), 1), 3
            ),
        },
    }

    logger.info(
        f"Pipeline complete for {date_str}: "
        f"{result['tracked_matches']} matches → "
        f"{result['candidates_found']} candidates → "
        f"{result['final_picks']} picks "
        f"({result['summary']['total_stake_units']}u total) "
        f"[odds: {odds_status}]"
    )

    return result


def _empty_result(date_str: str, total_events: int) -> dict:
    return {
        "date": date_str,
        "total_events": total_events,
        "tracked_matches": 0,
        "odds_status": "no_fixtures",
        "candidates_found": 0,
        "picks_after_filter": 0,
        "final_picks": 0,
        "picks": [],
        "summary": {
            "grades": {},
            "total_stake_units": 0,
            "avg_edge": 0,
            "avg_confidence": 0,
        },
    }

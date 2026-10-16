"""Offline replay of recorded shadow observations with the current code (M3.1).

For each stored observation the forecast is recomputed at its original decision
time with identity-checked inputs, value candidates use only quotes fetched by
that time, and legacy / v1 / v1.1 are evaluated again. No provider request is
made. A replay is RETROSPECTIVE: it is never a forward observation and never
written to shadow_observations.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from src.odds.model import iso_utc


def replay(conn: sqlite3.Connection) -> list[dict]:
    from src.engine.market_value import find_value
    from src.engine.pipeline import _pick_calibrator, calibrate_probabilities
    from src.engine.probability_engine import estimate_probabilities_from_inputs
    from src.identity.competitions import competition_for_fixture
    from src.identity.inputs import UnresolvedTeamIdentity, resolve_probability_inputs_v2
    from src.policy import baseline_v1 as v1
    from src.policy import baseline_v1_1 as v11
    from src.policy.league_evidence import load_league_evidence
    from src.shadow.legacy import legacy_select

    conn.row_factory = sqlite3.Row
    evidence = load_league_evidence(conn)
    out = []
    rows = conn.execute(
        """SELECT o.fixture_id, o.decided_at, o.data_quality AS recorded_dq, o.legacy_json, m.*
           FROM shadow_observations o JOIN matches m ON m.id = o.fixture_id ORDER BY o.decided_at"""
    ).fetchall()
    for r in rows:
        match = dict(r)
        now = datetime.fromisoformat(iso_utc(r["decided_at"]))
        entry = {"fixture_id": r["fixture_id"], "decided_at": r["decided_at"], "kind": "RETROSPECTIVE_REPLAY",
                 "match": f"{r['home_team']} v {r['away_team']}", "league": r["league_name"],
                 "recorded_dq": r["recorded_dq"],
                 "recorded_legacy_picks": len(json.loads(r["legacy_json"] or "{}").get("picks", []))}
        comp = competition_for_fixture(match.get("provider"), match.get("league_id"))
        try:
            inputs = resolve_probability_inputs_v2(conn, match, comp)
        except UnresolvedTeamIdentity as exc:
            entry.update({"identity": "UNRESOLVED", "unresolved": {
                s: {"name": x.provider_name, "status": x.status} for s, x in exc.resolutions.items() if not x.resolved}})
            out.append(entry)
            continue
        raw = estimate_probabilities_from_inputs(inputs)
        probs = calibrate_probabilities(raw, _pick_calibrator(conn))
        cands = find_value(match, probs, conn, now=now)
        for c in cands:
            c.update({"competition_key": comp.key if comp else None, "identity_resolved": True})
            assert c["odds_fetched_at"] <= iso_utc(now)  # never a price we did not have
        frozen = {"model_version": "assumed", "calibration_version": "assumed", "calibration_frozen": True}
        legacy, _ = legacy_select(conn, cands)
        entry.update({
            "identity": "RESOLVED", "replay_dq": raw.get("data_quality"), "candidates": len(cands),
            "legacy_picks": [(p["market"], p["selection"], p["grade"]) for p in legacy],
            "v1_assuming_frozen": v1.funnel(v1.evaluate(cands, now, frozen)),
            "v1_1_assuming_frozen": v1.funnel(v11.evaluate(cands, now, frozen, evidence)),
        })
        out.append(entry)
    return out

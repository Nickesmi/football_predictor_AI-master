#!/usr/bin/env python3
"""
Rebuild the demo's team history on a COPY of its database (owner-approved Step 1, 2026-11-01). Sends nothing.

    python scripts/rebuild_team_history.py --source <demo engine.db> --cache <demo/.cache> --out <dir>
        [--import-before 2026-10-31] [--as-of 2026-10-31]

1. <out>/snapshot.db: an online SQLite backup of --source (read only; the source is never written),
   checked with PRAGMA integrity_check.
2. Candidates: finished API-Football fixtures of the 14 tracked competitions in the cached day files
   (fixtures-*.json). Accepted only with a numeric fixture id, status FT, integer final score, a tracked
   league id, a UTC kick-off before --import-before, one consistent score across cache files, both teams
   resolved by team-identity-v1 (exact normalised name inside the competition, provenance verified; no
   fuzzy matching) to records under the same history label, and no existing record of the same match
   (same id, or same date +-1 day with the same resolved teams, in matches or match_history). Nothing is
   estimated: corners, cards and xG of imported matches stay unknown (None).
3. <out>/rebuilt.db: the snapshot plus the accepted matches (in `matches`, provider api_football), with
   match_history and team_state rebuilt by replaying every match through the unchanged
   src/engine/live_updater.on_match_finished, oldest first. State is loaded by exact (name, label, venue):
   the module's substring fallback (src/db/team_state.get_team_state) can load and overwrite another team's
   state. team_state rows without any match_history are carried over unchanged.
4. --as-of D also writes <out>/rebuilt_asof.db, replaying only matches dated before D (for leakage-free
   evaluation of fixtures on D), and <out>/noimport_asof.db, the same without imported matches (attribution).
5. <out>/reproduction.json: the snapshot's own history replayed in its original insertion order with the
   original lookup, compared with the snapshot's team_state (checks that the replay is faithful).
6. <out>/import_report.json and an integrity audit of each written database.
Models, weights, calibration and configuration are not touched.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

STATE_COLS = ("elo", "rolling_scored", "rolling_conceded", "rolling_xg", "rolling_xga", "rolling_corners",
              "rolling_cards", "form_last5", "form_last10", "win_streak", "unbeaten_streak", "matches_last_14d",
              "rest_days", "matches_played", "last_match_date")


def backup(src: Path, dst: Path, readonly: bool = True) -> None:
    if dst.exists():
        dst.unlink()
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True) if readonly else sqlite3.connect(src)
    d = sqlite3.connect(dst)
    s.backup(d)
    d.execute("PRAGMA journal_mode=DELETE")
    s.close()
    d.close()


def integrity(path: Path) -> dict:
    c = sqlite3.connect(path)
    out = {"integrity_check": c.execute("PRAGMA integrity_check").fetchone()[0],
           "foreign_key_violations": len(c.execute("PRAGMA foreign_key_check").fetchall()),
           "matches": c.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
           "match_history": c.execute("SELECT COUNT(*) FROM match_history").fetchone()[0],
           "duplicate_history_ids": c.execute("SELECT COUNT(*) - COUNT(DISTINCT match_id) FROM match_history").fetchone()[0],
           "history_without_score": c.execute("SELECT COUNT(*) FROM match_history WHERE home_goals IS NULL "
                                              "OR away_goals IS NULL").fetchone()[0],
           "team_state_overall": c.execute("SELECT COUNT(*) FROM team_state WHERE venue='overall'").fetchone()[0]}
    # team_state must agree with its own history (matches played, last match date)
    row = c.execute("""WITH h AS (SELECT home_team t, league l, match_date d FROM match_history
                                  UNION ALL SELECT away_team, league, match_date FROM match_history),
                            g AS (SELECT t, l, COUNT(*) n, MAX(d) m FROM h GROUP BY t, l)
                       SELECT COUNT(*), SUM(s.matches_played = g.n), SUM(s.last_match_date = g.m)
                       FROM team_state s JOIN g ON g.t = s.team_name AND g.l = s.league
                       WHERE s.venue = 'overall'""").fetchone()
    out.update(state_rows_with_history=row[0], matches_played_consistent=row[1], last_date_consistent=row[2])
    c.close()
    return out


# ── Candidates ───────────────────────────────────────────────────────────────

def candidates(cache: Path, import_before: str):
    from api import fixture_time
    from src.identity.competitions import COMPETITIONS

    tracked = {c.api_football_id: c for c in COMPETITIONS}
    seen: dict[str, dict] = {}
    scores: dict[str, set] = defaultdict(set)
    rejected = Counter()
    for p in sorted(glob.glob(str(cache / "fixtures-2026-*.json"))) + sorted(glob.glob(str(cache / "fixtures-2025-*.json"))):
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, list):
            continue
        # Older entries carry local date/time strings only; their basis (Tehran/Istanbul) is inferred per
        # file from decisive kick-off minutes (api/fixture_time.py), or the entry is rejected.
        basis = fixture_time.infer_basis(data, Path(p).name[9:19])
        for f in data:
            if not isinstance(f, dict) or f.get("source") != "api_football":
                continue
            try:
                lid = int(f.get("league_id_apifootball") or (f.get("league") or {}).get("id"))
            except (TypeError, ValueError):
                continue
            if lid not in tracked:
                continue
            if f.get("status") != "FT":
                continue
            fid = str(f.get("id") or "")
            hg, ag = f.get("home_goals"), f.get("away_goals")
            key = fid
            if not fid.isdigit():
                rejected["invalid_match_id"] += 1
                continue
            if not isinstance(hg, int) or not isinstance(ag, int) or hg < 0 or ag < 0:
                rejected["no_final_score"] += 1
                continue
            utc, how = fixture_time.to_utc(f, basis)
            if utc is None:
                rejected["kickoff_time_basis_unknown"] += 1
                continue
            ko = utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            scores[key].add((hg, ag))
            seen.setdefault(key, {"id": fid, "league_id": lid, "competition": tracked[lid], "kickoff_utc": ko,
                                  "date": ko[:10], "league_name": f["league"].get("name"),
                                  "home": f["home_team"]["name"], "away": f["away_team"]["name"],
                                  "home_id": f["home_team"].get("id"), "away_id": f["away_team"].get("id"),
                                  "home_goals": hg, "away_goals": ag})
    out = []
    for key, c in seen.items():
        if len(scores[key]) > 1:
            rejected["conflicting_scores_in_cache"] += 1
            continue
        if c["date"] >= import_before:
            rejected["kickoff_not_before_cutoff"] += 1
            continue
        out.append(c)
    return out, rejected


def select(conn: sqlite3.Connection, cands: list[dict]):
    """(accepted, rejected Counter, rejected details, duplicate Counter) using the snapshot (unchanged)."""
    from src.identity import registry
    from src.identity.teams import normalise_team, resolve_team

    conn.row_factory = sqlite3.Row
    reg = registry.load()
    ids = {r[0] for r in conn.execute("SELECT id FROM matches")} | {r[0] for r in conn.execute("SELECT match_id FROM match_history")}
    # existing results by (date, normalised home, normalised away) in either table
    existing = defaultdict(set)
    for r in conn.execute("SELECT date, home_team, away_team, home_goals, away_goals FROM matches WHERE status='FT' "
                          "UNION ALL SELECT match_date, home_team, away_team, home_goals, away_goals FROM match_history"):
        existing[(normalise_team(r[1]), normalise_team(r[2]))].add((str(r[0])[:10], r[3], r[4]))
    accepted, rejected, details, dup = [], Counter(), [], Counter()
    keys_taken = set()
    for c in sorted(cands, key=lambda x: (x["kickoff_utc"], x["id"])):
        comp = c["competition"]
        if c["id"] in ids:
            dup["same_match_id_already_stored"] += 1
            continue
        res = {s: resolve_team(conn, comp, c[s], "api_football", c[f"{s}_id"], False, reg) for s in ("home", "away")}
        bad = [f"{s}:{r.status}" for s, r in res.items() if not r.resolved]
        if bad:
            rejected["team_identity_unresolved"] += 1
            details.append({**_public(c), "reason": "team_identity_unresolved", "detail": bad})
            continue
        if res["home"].history_league != res["away"].history_league:
            rejected["teams_under_different_history_labels"] += 1
            details.append({**_public(c), "reason": "teams_under_different_history_labels",
                            "detail": [res["home"].history_league, res["away"].history_league]})
            continue
        names = {}
        for s, r in res.items():
            names[s] = conn.execute("SELECT team_name FROM team_state WHERE team_name_lower = ? AND league = ? "
                                    "AND venue = 'overall'", (r.team_name_lower, r.history_league)).fetchone()[0]
        nk = (normalise_team(names["home"]), normalise_team(names["away"]))
        d0 = date.fromisoformat(c["date"])
        near = {(d0 + timedelta(days=k)).isoformat() for k in (-1, 0, 1)}
        same = [e for e in existing.get(nk, set()) | existing.get((normalise_team(c["home"]), normalise_team(c["away"])), set())
                if e[0] in near]
        if same:
            if any((e[1], e[2]) != (c["home_goals"], c["away_goals"]) for e in same):
                rejected["conflicts_with_stored_score"] += 1
                details.append({**_public(c), "reason": "conflicts_with_stored_score", "detail": same})
            else:
                dup["same_match_stored_under_another_id"] += 1
            continue
        if (c["date"], nk) in keys_taken:
            dup["same_match_twice_in_cache"] += 1
            continue
        keys_taken.add((c["date"], nk))
        accepted.append({**c, "label": res["home"].history_league, "home_record": names["home"],
                         "away_record": names["away"],
                         "identity": {s: {"record": r.team_name_lower, "label": r.history_league, "source": r.source}
                                      for s, r in res.items()}})
    return accepted, rejected, details, dup


def _public(c: dict) -> dict:
    return {k: v for k, v in c.items() if k != "competition"} | {"competition": c["competition"].key}


# ── Replay ───────────────────────────────────────────────────────────────────

def _exact_get_team_state(conn, team_name, league, venue):
    from src.db.team_state import TeamState

    row = conn.execute(
        "SELECT team_name, league, venue, elo, attack_rating, defense_rating, rolling_scored, rolling_conceded, "
        "rolling_xg, rolling_xga, rolling_corners, rolling_cards, form_last5, form_last10, win_streak, "
        "unbeaten_streak, matches_last_14d, rest_days, matches_played, last_match_date, last_match_id "
        "FROM team_state WHERE team_name_lower = ? AND league = ? AND venue = ?",
        (team_name.lower().strip(), league, venue)).fetchone()
    if row is None:
        return None
    names = ("team_name", "league", "venue", "elo", "attack_rating", "defense_rating", "rolling_scored",
             "rolling_conceded", "rolling_xg", "rolling_xga", "rolling_corners", "rolling_cards", "form_last5",
             "form_last10", "win_streak", "unbeaten_streak", "matches_last_14d", "rest_days", "matches_played",
             "last_match_date", "last_match_id")
    return TeamState(**dict(zip(names, row)))


def replay(db: Path, rows: list[dict], exact: bool) -> None:
    from src.engine import live_updater

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=OFF")
    orphan_states = conn.execute(
        """SELECT * FROM team_state s WHERE NOT EXISTS (SELECT 1 FROM match_history h WHERE
           (h.home_team = s.team_name OR h.away_team = s.team_name) AND h.league = s.league)""").fetchall()
    conn.execute("DELETE FROM match_history")
    conn.execute("DELETE FROM team_state")
    conn.commit()
    # Since 2026-11-05 the live pipeline itself files results by exact record (live_updater.resolve_record,
    # get_team_state_exact). exact=False reproduces the pipeline as it was: raw names and the substring lookup.
    from src.db.team_state import get_team_state as legacy_lookup

    original = (live_updater.get_team_state_exact, live_updater.resolve_record)
    if exact:
        live_updater.get_team_state_exact = _exact_get_team_state
    else:
        live_updater.get_team_state_exact = legacy_lookup
        live_updater.resolve_record = lambda conn, name, league, *a, **k: (name, "legacy")
    try:
        for r in rows:
            live_updater.on_match_finished(conn, r["match_id"], r["match_date"], r["league"], r["home_team"],
                                           r["away_team"], r["home_goals"], r["away_goals"], r.get("home_xg"),
                                           r.get("away_xg"), r.get("home_corners"), r.get("away_corners"),
                                           r.get("home_cards"), r.get("away_cards"))
    finally:
        live_updater.get_team_state_exact, live_updater.resolve_record = original
    if orphan_states:
        cols = orphan_states[0].keys()
        conn.executemany(f"INSERT OR IGNORE INTO team_state ({', '.join(c for c in cols if c != 'id')}) VALUES "
                         f"({', '.join('?' for c in cols if c != 'id')})",
                         [tuple(r[c] for c in cols if c != "id") for r in orphan_states])
    conn.commit()
    conn.close()


def history_rows(conn) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute("SELECT * FROM match_history ORDER BY id")]


def compare_states(a: Path, b: Path) -> dict:
    ca, cb = sqlite3.connect(a), sqlite3.connect(b)
    q = f"SELECT team_name, league, venue, {', '.join(STATE_COLS)} FROM team_state"
    sa = {r[:3]: r[3:] for r in ca.execute(q)}
    sb = {r[:3]: r[3:] for r in cb.execute(q)}
    same = sum(1 for k, v in sa.items() if k in sb and all(
        (x == y) or (isinstance(x, float) and isinstance(y, float) and abs(x - y) < 1e-9) for x, y in zip(v, sb[k])))
    return {"rows_a": len(sa), "rows_b": len(sb), "identical": same, "only_a": len(sa.keys() - sb.keys()),
            "only_b": len(sb.keys() - sa.keys())}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--cache", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--import-before", default="2026-10-31")
    p.add_argument("--as-of")
    p.add_argument("--skip-reproduction", action="store_true")
    a = p.parse_args()
    logging.getLogger("football_predictor").setLevel(logging.WARNING)
    if a.out.resolve() == a.source.resolve().parent or (PROJECT_ROOT / "data").resolve() in a.out.resolve().parents:
        raise SystemExit("refusing: --out must be a separate directory")
    a.out.mkdir(parents=True, exist_ok=True)
    snap, rebuilt = a.out / "snapshot.db", a.out / "rebuilt.db"
    backup(a.source.resolve(), snap)
    report = {"source": str(a.source), "snapshot_integrity": integrity(snap), "import_before": a.import_before}
    if report["snapshot_integrity"]["integrity_check"] != "ok":
        raise SystemExit("snapshot failed integrity_check")

    cands, pre_rejected = candidates(a.cache, a.import_before)
    sconn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
    accepted, rejected, details, dup = select(sconn, cands)
    base_rows = history_rows(sconn)
    sconn.close()
    rejected.update(pre_rejected)
    report.update(candidates=len(cands) + sum(pre_rejected.values()), accepted=len(accepted), rejected=dict(rejected),
                  deduplicated=dict(dup), rejected_details=details,
                  accepted_by_competition=dict(Counter(c["competition"].key for c in accepted)),
                  accepted_dates=sorted({c["date"] for c in accepted}))

    # Reproduction: original rows, original order, original lookup.
    if not a.skip_reproduction:
        repro = a.out / "reproduction.db"
        backup(snap, repro, readonly=False)
        replay(repro, base_rows, exact=False)
        report["reproduction"] = compare_states(snap, repro)
        repro.unlink()

    imported = [{"match_id": c["id"], "match_date": c["date"], "league": c["label"], "home_team": c["home_record"],
                 "away_team": c["away_record"], "home_goals": c["home_goals"], "away_goals": c["away_goals"],
                 "_order": 1} for c in accepted]
    ordered = sorted([{**r, "_order": 0} for r in base_rows] + imported,
                     key=lambda r: (r["match_date"], r["_order"], r.get("id") or 0, r["match_id"]))

    def build(path: Path, rows: list[dict]) -> dict:
        backup(snap, path, readonly=False)
        conn = sqlite3.connect(path)
        conn.executemany("INSERT INTO matches (id, date, home_team, away_team, league_name, league_id, status, "
                         "home_goals, away_goals, provider) VALUES (?, ?, ?, ?, ?, ?, 'FT', ?, ?, 'api_football')",
                         [(c["id"], c["date"], c["home_record"], c["away_record"], c["label"], c["league_id"],
                           c["home_goals"], c["away_goals"]) for c in accepted])
        conn.commit()
        conn.close()
        replay(path, rows, exact=True)
        return integrity(path)

    report["rebuilt_integrity"] = build(rebuilt, ordered)
    if a.as_of:
        report["as_of"] = a.as_of
        report["rebuilt_asof_integrity"] = build(a.out / "rebuilt_asof.db", [r for r in ordered if r["match_date"] < a.as_of])
        # Attribution: the same chronological, exact-lookup replay without the imported matches.
        keep = accepted[:]
        accepted.clear()
        report["noimport_asof_integrity"] = build(a.out / "noimport_asof.db",
                                                  [r for r in ordered if r["match_date"] < a.as_of and r["_order"] == 0])
        accepted.extend(keep)
    (a.out / "accepted.json").write_text(json.dumps([_public(c) for c in accepted], indent=1, ensure_ascii=False))
    (a.out / "import_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    print(json.dumps({k: v for k, v in report.items() if k not in ("rejected_details", "accepted_dates")}, indent=1,
                     default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

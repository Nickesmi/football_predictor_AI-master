#!/usr/bin/env python3
"""
Recover finished results from the demo's cached fixture lists into a COPY of its database (2026-11-03).
Sends nothing. Never writes the source database or the cache.

    python scripts/recover_cached_results.py --source <demo engine.db> --cache <demo/.cache> --out <dir>
        [--import-before 2026-11-03]

Supersedes the tracked-competition-only importer (scripts/rebuild_team_history.py, 2026-11-01, never deployed):
  * every API-Football league, not only the 14 tracked competitions;
  * a team with no record yet can be imported (that importer required an existing record, so a club whose
    results had never been stored, e.g. Inter in Serie A, could never get one). A new record is created only
    when its provider team id is unambiguous: one spelling for that id in that league, no other id with the
    same normalised name in that league, and no existing record that normalises to the same name;
  * provenance: every imported result is listed in recovered_results with its cache files and their SHA-256;
  * club membership (src/identity/club_membership.py) is written to provider_team_competitions.

Acceptance of a cached fixture: numeric id, status FT, integer final score, one score across all cache files,
a known UTC kick-off (api/fixture_time.py; otherwise rejected), kick-off date before --import-before, both
teams resolved to the same history label, no stored match with the same id, and no stored match of the same
two records within +-1 day (a different score there is a conflict and is rejected). Corners, cards and xG are
not in the cache and stay unknown (NULL); nothing is estimated.

Then the whole history (stored + recovered) is replayed oldest first through the unchanged
src/engine/live_updater.on_match_finished with the exact (name, label, venue) state lookup
(scripts/rebuild_team_history.replay). Models, weights, calibration and configuration are not touched.
Output: <out>/snapshot.db (online backup of --source), <out>/recovered.db, <out>/recovery_report.json.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import logging
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from rebuild_team_history import backup, history_rows, integrity  # noqa: E402

IMPORTER_VERSION = "cache-recovery-v2"

PROVENANCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS recovered_results (
    match_id            TEXT PRIMARY KEY,
    kickoff_utc         TEXT NOT NULL,
    match_date          TEXT NOT NULL,
    league_id           INTEGER NOT NULL,
    league_name         TEXT NOT NULL,
    country             TEXT NOT NULL,
    history_label       TEXT NOT NULL,
    home_provider_id    TEXT NOT NULL,
    away_provider_id    TEXT NOT NULL,
    home_provider_name  TEXT NOT NULL,
    away_provider_name  TEXT NOT NULL,
    home_record         TEXT NOT NULL,
    away_record         TEXT NOT NULL,
    home_identity       TEXT NOT NULL,
    away_identity       TEXT NOT NULL,
    home_goals          INTEGER NOT NULL,
    away_goals          INTEGER NOT NULL,
    status              TEXT NOT NULL,
    source_files        TEXT NOT NULL,
    importer_version    TEXT NOT NULL,
    imported_at         TEXT NOT NULL
);
"""


MERGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS team_record_merges (
    history_label       TEXT NOT NULL,
    original_name       TEXT NOT NULL,
    canonical_name      TEXT NOT NULL,
    provider            TEXT NOT NULL,
    provider_team_id    TEXT NOT NULL,
    fixtures_checked    INTEGER NOT NULL,
    importer_version    TEXT NOT NULL,
    PRIMARY KEY (history_label, original_name)
);
"""


def verified_merges(conn: sqlite3.Connection, fixture_sides: dict, extra_evidence=()) -> tuple[dict, list, Counter]:
    """One spelling per club inside a history label, decided by provider team id.

    Why: the live pipeline loads state with a substring fallback (src/db/team_state.get_team_state), so it
    folded results stored as "Bayern München" into the "FC Bayern München" record. The replay uses exact
    lookups and would split them again. Rule:
      1. Every stored history fixture that the cached lists know (API-Football id space, same league id)
         gives the provider team id of each side. All spellings of one id inside one label get one canonical
         spelling: the spelling of the one existing team_state record among them, otherwise the most frequent.
      2. If a spelling or a normalised name is used by two different ids inside one label (two clubs), none
         of those spellings is touched.
      3. A spelling with no identifiable fixture (older feeds) is never merged: a name alone does not prove
         the club (an "Olimpia" in a worldwide label can be one of several clubs). It stays a separate record.
    `extra_evidence`: (label, provider team id, record name) of accepted imports, whose record was chosen by
    team-identity-v1 (provenance-verified) for that provider id.
    Returns ({(label, lower name): canonical name}, provenance rows, outcome counts)."""
    from src.identity.teams import normalise_team

    by_id: dict[tuple, Counter] = defaultdict(Counter)
    ids_of_lower: dict[tuple, set] = defaultdict(set)
    for label, mid, home, away, lid in conn.execute(
            "SELECT h.league, h.match_id, h.home_team, h.away_team, m.league_id FROM match_history h "
            "JOIN matches m ON m.id = h.match_id WHERE m.provider IN ('api_football', 'matches_db')"):
        sides = fixture_sides.get(str(mid))
        if not sides or lid is None or int(lid) != sides[0]:
            continue
        for name, tid in ((home, sides[1]), (away, sides[2])):
            if tid:
                by_id[(label, tid)][name] += 1
                ids_of_lower[(label, name.lower().strip())].add(tid)
    for label, tid, name in extra_evidence:
        by_id[(label, tid)][name] += 1
        ids_of_lower[(label, name.lower().strip())].add(tid)
    records: dict[tuple, set] = defaultdict(set)
    spellings: dict[tuple, set] = defaultdict(set)
    for name, label in conn.execute("SELECT team_name, league FROM team_state WHERE venue = 'overall'"):
        records[(label, normalise_team(name))].add(name)
        spellings[(label, normalise_team(name))].add(name)
    for label, home, away in conn.execute("SELECT league, home_team, away_team FROM match_history"):
        spellings[(label, normalise_team(home))].add(home)
        spellings[(label, normalise_team(away))].add(away)
    ids_of_key: dict[tuple, set] = defaultdict(set)
    for (label, tid), names in by_id.items():
        for n in names:
            ids_of_key[(label, normalise_team(n))].add(tid)
    mapping, rows, outcome = {}, [], Counter()
    canon_of_key: dict[tuple, str] = {}
    for (label, tid), names in by_id.items():
        lowers = {n.lower().strip() for n in names}
        keys = {normalise_team(n) for n in names}
        if any(len(ids_of_lower[(label, lo)]) > 1 for lo in lowers) or any(len(ids_of_key[(label, k)]) > 1 for k in keys):
            outcome["not_merged_name_shared_by_several_ids"] += 1
            continue
        existing = set().union(*(records[(label, k)] for k in keys))
        own = sorted(e for e in existing if e.lower().strip() in lowers)
        if len(own) == 1:
            canonical = own[0]
        else:
            canonical = sorted(names.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        for k in keys:
            canon_of_key[(label, k)] = canonical
        changed = False
        for n in names:
            mapping[(label, n.lower().strip())] = canonical
            if n.lower().strip() != canonical.lower().strip():
                rows.append((label, n, canonical, "api_football", tid, sum(names.values()), IMPORTER_VERSION))
                changed = True
        if changed:
            outcome["merged_id_groups"] += 1
    for (label, key), names in spellings.items():
        canonical = canon_of_key.get((label, key))
        if canonical and any(n.lower().strip() != canonical.lower().strip() and (label, n.lower().strip()) not in mapping
                             for n in names):
            outcome["not_merged_spelling_without_provider_id"] += 1   # names alone never merge records
    return mapping, rows, outcome


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def competition_of(lid: int, league_name: str, country: str):
    from src.identity.competitions import Competition, competition_for_fixture

    return competition_for_fixture("api_football", lid) or Competition(f"api-football-{lid}", country or "", lid, -1,
                                                                       (league_name,))


def candidates(cache: Path, import_before: str):
    """Finished API-Football fixtures from the cache, merged by fixture id, with their source files."""
    from api import fixture_time
    from src.identity.teams import normalise_team

    seen: dict[str, dict] = {}
    scores: dict[str, set] = defaultdict(set)
    files: dict[str, set] = defaultdict(set)
    rejected = Counter()
    fixture_sides: dict[str, tuple] = {}                  # fixture id -> (league id, home id, away id)
    names_by_id: dict[tuple, set] = defaultdict(set)      # (league id, team id) -> spellings
    ids_by_name: dict[tuple, set] = defaultdict(set)      # (league id, normalised name) -> team ids
    hashes = {}
    for p in sorted(glob.glob(str(cache / "fixtures-*.json"))):
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rejected["unreadable_cache_file"] += 1
            continue
        if not isinstance(data, list):
            continue
        hashes[Path(p).name] = sha256(p)
        basis = fixture_time.infer_basis(data, Path(p).name[9:19])
        for f in data:
            if not isinstance(f, dict) or f.get("source") != "api_football":
                continue
            try:
                lid = int(f.get("league_id_apifootball") or (f.get("league") or {}).get("id"))
            except (TypeError, ValueError):
                continue
            fixture_sides[str(f.get("id"))] = (lid, str((f.get("home_team") or {}).get("id") or ""),
                                               str((f.get("away_team") or {}).get("id") or ""))
            for s in ("home_team", "away_team"):
                t = f.get(s) or {}
                if str(t.get("id") or "").isdigit() and t.get("name"):
                    names_by_id[(lid, str(t["id"]))].add(t["name"])
                    ids_by_name[(lid, normalise_team(t["name"]))].add(str(t["id"]))
            if f.get("status") != "FT":
                continue
            fid = str(f.get("id") or "")
            hg, ag = f.get("home_goals"), f.get("away_goals")
            if not fid.isdigit():
                rejected["invalid_match_id"] += 1
                continue
            if not isinstance(hg, int) or not isinstance(ag, int) or hg < 0 or ag < 0:
                rejected["no_final_score"] += 1
                continue
            hid, aid = str((f.get("home_team") or {}).get("id") or ""), str((f.get("away_team") or {}).get("id") or "")
            if not hid.isdigit() or not aid.isdigit():
                rejected["no_provider_team_id"] += 1
                continue
            utc, _how = fixture_time.to_utc(f, basis)
            if utc is None:
                rejected["kickoff_time_basis_unknown"] += 1
                continue
            ko = utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            scores[fid].add((hg, ag))
            files[fid].add(Path(p).name)
            seen.setdefault(fid, {"id": fid, "league_id": lid, "kickoff_utc": ko, "date": ko[:10],
                                  "league_name": f["league"].get("name") or "", "country": f["league"].get("country") or "",
                                  "home": f["home_team"]["name"], "away": f["away_team"]["name"],
                                  "home_id": hid, "away_id": aid, "home_goals": hg, "away_goals": ag,
                                  "status": f.get("status")})
    out = []
    for fid, c in seen.items():
        if len(scores[fid]) > 1:
            rejected["conflicting_scores_in_cache"] += 1
            continue
        if c["date"] >= import_before:
            rejected["kickoff_not_before_cutoff"] += 1
            continue
        c["source_files"] = sorted(files[fid])
        out.append(c)
    return out, rejected, names_by_id, ids_by_name, hashes, fixture_sides


def _side(conn, comp, cand, side, names_by_id, ids_by_name, reg, records_by_label, merges):
    """(record name, label, identity tag) or (None, None, reason)."""
    from src.identity.teams import AMBIGUOUS, NO_HISTORY, normalise_team, resolve_team

    name, tid, lid = cand[side], cand[f"{side}_id"], cand["league_id"]
    r = resolve_team(conn, comp, name, "api_football", tid, False, reg)
    if r.status == AMBIGUOUS and r.evidence.get("review") == "same-club spelling split":
        keys = [k.split(" @ ", 1) for k in r.evidence["candidate_verification"]]
        canon = {merges.get((label, lower)) for lower, label in keys}
        labels = {label for _l, label in keys}
        if len(labels) == 1 and len(canon) == 1 and None not in canon:
            return canon.pop(), labels.pop(), "existing:verified_merge"
    if r.resolved:
        row = conn.execute("SELECT team_name FROM team_state WHERE team_name_lower = ? AND league = ? AND "
                           "venue = 'overall'", (r.team_name_lower, r.history_league)).fetchone()
        return row[0], r.history_league, f"existing:{r.source}"
    if r.status != NO_HISTORY:
        return None, None, f"{r.status}"
    # New record, keyed by the provider team id.
    if len(names_by_id[(lid, tid)]) != 1:
        return None, None, "new_record_id_has_several_names"
    if len(ids_by_name[(lid, normalise_team(name))]) != 1:
        return None, None, "new_record_name_shared_by_several_ids"
    if cand["league_name"] not in comp.history_labels:
        return None, None, "new_record_label_not_in_competition"
    clash = [n for n in records_by_label.get(cand["league_name"], ()) if normalise_team(n) == normalise_team(name)]
    if clash:
        return None, None, "new_record_name_collides_with_existing_record"
    return name, cand["league_name"], f"new:provider_id:{tid}"


def select(conn: sqlite3.Connection, cands: list[dict], names_by_id, ids_by_name, merges=None):
    from src.identity import registry
    from src.identity.teams import normalise_team

    conn.row_factory = sqlite3.Row
    reg = registry.load()
    ids = {r[0] for r in conn.execute("SELECT id FROM matches")} | {r[0] for r in conn.execute("SELECT match_id FROM match_history")}
    existing = defaultdict(set)
    for r in conn.execute("SELECT date, home_team, away_team, home_goals, away_goals FROM matches WHERE status='FT' "
                          "UNION ALL SELECT match_date, home_team, away_team, home_goals, away_goals FROM match_history"):
        existing[(normalise_team(r[1]), normalise_team(r[2]))].add((str(r[0])[:10], r[3], r[4]))
    records_by_label = defaultdict(set)
    for r in conn.execute("SELECT team_name, league FROM team_state WHERE venue = 'overall'"):
        records_by_label[r[1]].add(r[0])
    accepted, rejected, details, dup = [], Counter(), [], Counter()
    keys_taken = set()
    for c in sorted(cands, key=lambda x: (x["kickoff_utc"], x["id"])):
        if c["id"] in ids:
            dup["same_match_id_already_stored"] += 1
            continue
        comp = competition_of(c["league_id"], c["league_name"], c["country"])
        res = {s: _side(conn, comp, c, s, names_by_id, ids_by_name, reg, records_by_label, merges or {})
               for s in ("home", "away")}
        bad = [f"{s}:{v[2]}" for s, v in res.items() if v[0] is None]
        if bad:
            rejected["team_identity_unresolved"] += 1
            details.append({**c, "reason": "team_identity_unresolved", "detail": bad})
            continue
        if res["home"][1] != res["away"][1]:
            rejected["teams_under_different_history_labels"] += 1
            details.append({**c, "reason": "teams_under_different_history_labels",
                            "detail": [res["home"][1], res["away"][1]]})
            continue
        if res["home"][0] == res["away"][0]:
            rejected["same_record_both_sides"] += 1
            continue
        nk = (normalise_team(res["home"][0]), normalise_team(res["away"][0]))
        d0 = date.fromisoformat(c["date"])
        near = {(d0 + timedelta(days=k)).isoformat() for k in (-1, 0, 1)}
        same = [e for e in existing.get(nk, set()) | existing.get((normalise_team(c["home"]), normalise_team(c["away"])), set())
                if e[0] in near]
        if same:
            if any((e[1], e[2]) != (c["home_goals"], c["away_goals"]) for e in same):
                rejected["conflicts_with_stored_score"] += 1
                details.append({**c, "reason": "conflicts_with_stored_score", "detail": sorted(same)})
            else:
                dup["same_match_stored_under_another_id"] += 1
            continue
        if (c["date"], nk) in keys_taken:
            dup["same_match_twice_in_cache"] += 1
            continue
        keys_taken.add((c["date"], nk))
        accepted.append({**c, "label": res["home"][1], "home_record": res["home"][0], "away_record": res["away"][0],
                         "home_identity": res["home"][2], "away_identity": res["away"][2]})
    # Two different clubs (league id, team id) must never become one new record under a shared label
    # (two countries' "Premier League" each with a "Police").
    claims: dict[tuple, set] = defaultdict(set)
    for c in accepted:
        for s in ("home", "away"):
            if c[f"{s}_identity"].startswith("new:"):
                claims[(c["label"], normalise_team(c[f"{s}_record"]))].add((c["league_id"], c[f"{s}_id"]))
    clash = {k for k, v in claims.items() if len(v) > 1}
    if clash:
        keep = []
        for c in accepted:
            if any(c[f"{s}_identity"].startswith("new:") and (c["label"], normalise_team(c[f"{s}_record"])) in clash
                   for s in ("home", "away")):
                rejected["new_record_claimed_by_two_clubs"] += 1
                details.append({**c, "reason": "new_record_claimed_by_two_clubs", "detail": []})
            else:
                keep.append(c)
        accepted = keep
    return accepted, rejected, details, dup


def replay_with_ids(db: Path, rows: list[dict]) -> None:
    """rebuild_team_history.replay, but each result carries its provider team ids, so on_match_finished
    files it by id (src/engine/live_updater.resolve_record) and records the binding in team_state_ids."""
    from src.engine import live_updater

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=OFF")
    orphan_states = conn.execute(
        """SELECT * FROM team_state s WHERE NOT EXISTS (SELECT 1 FROM match_history h WHERE
           (h.home_team = s.team_name OR h.away_team = s.team_name) AND h.league = s.league)""").fetchall()
    conn.execute("DELETE FROM match_history")
    conn.execute("DELETE FROM team_state")
    conn.execute("DROP TABLE IF EXISTS team_state_ids")
    conn.commit()
    for r in rows:
        ids = r.get("home_team_id") is not None or r.get("away_team_id") is not None
        live_updater.on_match_finished(conn, r["match_id"], r["match_date"], r["league"], r["home_team"],
                                       r["away_team"], r["home_goals"], r["away_goals"], r.get("home_xg"),
                                       r.get("away_xg"), r.get("home_corners"), r.get("away_corners"),
                                       r.get("home_cards"), r.get("away_cards"),
                                       provider="api_football" if ids else None,
                                       home_team_id=r.get("home_team_id"), away_team_id=r.get("away_team_id"))
    if orphan_states:
        cols = orphan_states[0].keys()
        conn.executemany(f"INSERT OR IGNORE INTO team_state ({', '.join(c for c in cols if c != 'id')}) VALUES "
                         f"({', '.join('?' for c in cols if c != 'id')})",
                         [tuple(r[c] for c in cols if c != "id") for r in orphan_states])
    conn.commit()
    conn.close()


def main() -> int:
    from src.identity import club_membership

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--cache", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--import-before", default="2026-11-03")
    a = p.parse_args()
    logging.getLogger("football_predictor").setLevel(logging.WARNING)
    out = a.out.resolve()
    if out == a.source.resolve().parent or (PROJECT_ROOT / "data").resolve() in out.parents or out == a.cache.resolve():
        raise SystemExit("refusing: --out must be a separate directory")
    out.mkdir(parents=True, exist_ok=True)
    snap, rec = out / "snapshot.db", out / "recovered.db"
    backup(a.source.resolve(), snap)
    report = {"importer_version": IMPORTER_VERSION, "source": str(a.source), "cache": str(a.cache),
              "import_before": a.import_before, "snapshot_integrity": integrity(snap)}
    if report["snapshot_integrity"]["integrity_check"] != "ok":
        raise SystemExit("snapshot failed integrity_check")

    cands, pre_rejected, names_by_id, ids_by_name, hashes, fixture_sides = candidates(a.cache, a.import_before)
    sconn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
    merges, merge_rows, merge_outcome = verified_merges(sconn, fixture_sides)
    accepted, rejected, details, dup = select(sconn, cands, names_by_id, ids_by_name, merges)
    base_rows = history_rows(sconn)
    evidence = [(c["label"], c[f"{s}_id"], c[f"{s}_record"]) for c in accepted for s in ("home", "away")]
    merges, merge_rows, merge_outcome = verified_merges(sconn, fixture_sides, evidence)
    sconn.close()
    renamed = 0
    for r in base_rows:          # one spelling per verified club inside a label; originals stay in `matches`
        for side in ("home_team", "away_team"):
            canon = merges.get((r["league"], r[side].lower().strip()))
            if canon and canon != r[side]:
                r[side] = canon
                renamed += 1
    report["merges"] = {**dict(merge_outcome), "renamed_history_sides": renamed, "merged_spellings": len(merge_rows)}
    rejected.update(pre_rejected)
    top = {39, 40, 140, 135, 78, 61, 88, 94, 144, 203, 179, 2, 3, 848}
    report.update(candidates=len(cands), accepted=len(accepted), accepted_top14=sum(c["league_id"] in top for c in accepted),
                  accepted_other=sum(c["league_id"] not in top for c in accepted),
                  accepted_new_record_sides=sum(c[f"{s}_identity"].startswith("new:") for c in accepted for s in ("home", "away")),
                  rejected=dict(rejected), deduplicated=dict(dup),
                  rejected_top14=dict(Counter(d["reason"] + ":" + ",".join(d["detail"]) if isinstance(d["detail"], list) and d["reason"] == "team_identity_unresolved" else d["reason"]
                                              for d in details if d["league_id"] in top)),
                  cache_files=len(hashes))

    for c in accepted:
        for side in ("home_record", "away_record"):
            c[side] = merges.get((c["label"], c[side].lower().strip()), c[side])
    imported = [{"match_id": c["id"], "match_date": c["date"], "league": c["label"], "home_team": c["home_record"],
                 "away_team": c["away_record"], "home_goals": c["home_goals"], "away_goals": c["away_goals"],
                 "home_team_id": c["home_id"], "away_team_id": c["away_id"], "_order": 1} for c in accepted]
    # Provider team ids of stored history fixtures that the cached lists know (API-Football id space, same league).
    sconn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
    lids = {r[0]: r[1] for r in sconn.execute("SELECT id, league_id FROM matches WHERE provider IN "
                                              "('api_football', 'matches_db') AND league_id IS NOT NULL")}
    sconn.close()
    with_ids = 0
    for r in base_rows:
        sides = fixture_sides.get(str(r["match_id"]))
        if sides and str(r["match_id"]) in lids and int(lids[str(r["match_id"])]) == sides[0]:
            r["home_team_id"], r["away_team_id"] = sides[1] or None, sides[2] or None
            with_ids += 1
    report["history_rows_with_provider_ids"] = with_ids
    ordered = sorted([{**r, "_order": 0} for r in base_rows] + imported,
                     key=lambda r: (r["match_date"], r["_order"], r.get("id") or 0, r["match_id"]))

    backup(snap, rec, readonly=False)
    conn = sqlite3.connect(rec)
    conn.executemany("INSERT INTO matches (id, date, kickoff, home_team, away_team, league_name, league_id, status, "
                     "home_goals, away_goals, provider) VALUES (?, ?, ?, ?, ?, ?, ?, 'FT', ?, ?, 'api_football')",
                     [(c["id"], c["date"], c["kickoff_utc"][11:16], c["home_record"], c["away_record"], c["label"],
                       c["league_id"], c["home_goals"], c["away_goals"]) for c in accepted])
    conn.executescript(PROVENANCE_SCHEMA)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    conn.executemany("INSERT INTO recovered_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     [(c["id"], c["kickoff_utc"], c["date"], c["league_id"], c["league_name"], c["country"], c["label"],
                       c["home_id"], c["away_id"], c["home"], c["away"], c["home_record"], c["away_record"],
                       c["home_identity"], c["away_identity"], c["home_goals"], c["away_goals"], c["status"],
                       json.dumps({f: hashes.get(f) for f in c["source_files"]}), IMPORTER_VERSION, now)
                      for c in accepted])
    conn.executescript(MERGE_SCHEMA)
    conn.executemany("INSERT INTO team_record_merges VALUES (?, ?, ?, ?, ?, ?, ?)", merge_rows)
    report["membership_rows"] = club_membership.write(conn, club_membership.collect(a.cache), f"cache:{a.cache}")
    conn.commit()
    conn.close()
    replay_with_ids(rec, ordered)
    report["recovered_integrity"] = integrity(rec)
    (out / "recovered.json").write_text(json.dumps(accepted, indent=1, ensure_ascii=False))
    (out / "rejected.json").write_text(json.dumps(details, indent=1, ensure_ascii=False))
    (out / "recovery_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

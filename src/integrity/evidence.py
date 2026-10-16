"""Classify historical outcome evidence and quarantine everything that is not verified.

Two tables feed calibration artifacts:

* ``prediction_errors`` -> ``model_confidence_adjustments`` (league 1X2 shrink + BILQE reliability)
* ``prediction_log``    -> ``calibration_models`` (isotonic market calibration)

A row is *verified* only when all of these hold:

* it carries a real model probability (not the 33/33/33 "home" placeholder that
  ``results_service`` wrote before 927e402);
* it was provably recorded before kickoff;
* its settlement fields are internally consistent;
* it is the first record of that match (later duplicates would double count).

Timing is tested conservatively. ``created_at`` is SQLite ``CURRENT_TIMESTAMP``
(UTC). Fixture dates and kickoff times are naive local times (the app uses
Iran Standard Time, +03:30). A row is pre-match only if
``created_at <= kickoff_naive - 3h30``, which holds for any kickoff offset in
[UTC, UTC+03:30]. A row is retrospective only when it is after kickoff under
every such offset. Everything in between is unverifiable, never verified.

Nothing here deletes data silently. ``quarantine`` copies every non-verified
row (full original JSON, original primary key, classification and reason).
``move_quarantined`` removes only rows whose current content still hashes to
the quarantined copy. ``restore_quarantined`` puts them back with their
original primary keys and refuses to overwrite a different row.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

CLASSIFIER_VERSION = "m3-evidence-v1"

# Worst-case offset between a naive fixture time and UTC (Iran Standard Time).
LOCAL_TZ_MAX_OFFSET = timedelta(hours=3, minutes=30)

VERIFIED_SETTLED = "VERIFIED_SETTLED"
VERIFIED_UNSETTLED = "VERIFIED_UNSETTLED"
FABRICATED_PLACEHOLDER = "FABRICATED_PLACEHOLDER"
RETROSPECTIVE = "RETROSPECTIVE"
UNVERIFIABLE_TIMING = "UNVERIFIABLE_TIMING"
DUPLICATE = "DUPLICATE"
INCONSISTENT = "INCONSISTENT"

VERIFIED = frozenset({VERIFIED_SETTLED, VERIFIED_UNSETTLED})
CLASSES = (
    VERIFIED_SETTLED, VERIFIED_UNSETTLED, FABRICATED_PLACEHOLDER,
    RETROSPECTIVE, UNVERIFIABLE_TIMING, DUPLICATE, INCONSISTENT,
)
SOURCE_TABLES = ("prediction_errors", "prediction_log")

_DDL = (
    """CREATE TABLE IF NOT EXISTS evidence_classification (
        source_table       TEXT NOT NULL,
        original_pk        INTEGER NOT NULL,
        classification     TEXT NOT NULL,
        reason             TEXT NOT NULL,
        classifier_version TEXT NOT NULL,
        classified_at      TEXT NOT NULL,
        PRIMARY KEY (source_table, original_pk, classifier_version)
    )""",
    """CREATE TABLE IF NOT EXISTS evidence_quarantine (
        source_table       TEXT NOT NULL,
        original_pk        INTEGER NOT NULL,
        classification     TEXT NOT NULL,
        reason             TEXT NOT NULL,
        classifier_version TEXT NOT NULL,
        original_row_json  TEXT NOT NULL,
        row_sha256         TEXT NOT NULL,
        quarantined_at     TEXT NOT NULL,
        moved_at           TEXT,
        restored_at        TEXT,
        PRIMARY KEY (source_table, original_pk)
    )""",
)


@dataclass(frozen=True)
class Classified:
    source_table: str
    pk: int
    classification: str
    reason: str


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_ts(value) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).replace("T", " ").split(".")[0].replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _kickoff_naive(date_text, kickoff_text) -> Optional[datetime]:
    if not date_text:
        return None
    if kickoff_text:
        parsed = _parse_ts(f"{str(date_text)[:10]} {str(kickoff_text)[:5]}")
        if parsed:
            return parsed
    return None


def timing_class(created_at, match_date, kickoff: Optional[datetime]) -> tuple[str, str]:
    """('pre_match' | 'retrospective' | 'unverifiable', reason) under the conservative rule."""
    created = _parse_ts(created_at)
    if created is None:
        return "unverifiable", "created_at missing or unparseable"
    if kickoff is not None:
        if created <= kickoff - LOCAL_TZ_MAX_OFFSET:
            return "pre_match", "created at least 3h30 before naive kickoff"
        if created >= kickoff:
            return "retrospective", "created after kickoff under every offset in [UTC, UTC+03:30]"
        return "unverifiable", "created within 3h30 before naive kickoff; offset unknown"
    day = _parse_ts(match_date)
    if day is None:
        return "unverifiable", "match_date missing or unparseable"
    if created <= day - LOCAL_TZ_MAX_OFFSET:
        return "pre_match", "no kickoff time; created before local matchday began"
    if created >= day + timedelta(days=1):
        return "retrospective", "no kickoff time; created after local matchday ended"
    return "unverifiable", "no kickoff time; created on matchday"


def load_kickoffs(conn: sqlite3.Connection) -> dict[str, datetime]:
    """fixture id -> naive kickoff, from ``matches`` first, then ``fixtures_master``."""
    out: dict[str, datetime] = {}
    for sql in (
        "SELECT fixture_id, date, kickoff FROM fixtures_master",
        "SELECT id, date, kickoff FROM matches",  # later wins: matches is the settlement source
    ):
        try:
            rows = conn.execute(sql).fetchall()
        except sqlite3.OperationalError:
            continue
        for fid, date_text, kickoff_text in rows:
            parsed = _kickoff_naive(date_text, kickoff_text)
            if parsed is not None:
                out[str(fid)] = parsed
    return out


def _is_placeholder(row) -> bool:
    return (
        row["home_win_pct"] == 33.0 and row["draw_pct"] == 33.0
        and row["away_win_pct"] == 33.0 and row["predicted_result"] == "home"
    )


def _result_from_goals(home_goals, away_goals) -> Optional[str]:
    if home_goals is None or away_goals is None:
        return None
    if home_goals > away_goals:
        return "home"
    if home_goals < away_goals:
        return "away"
    return "draw"


def _argmax_result(row) -> str:
    h, d, a = row["home_win_pct"], row["draw_pct"], row["away_win_pct"]
    if h >= d and h >= a:
        return "home"
    return "away" if a >= d else "draw"


def classify_prediction_errors(conn: sqlite3.Connection, kickoffs: dict[str, datetime]) -> list[Classified]:
    rows = conn.execute("SELECT * FROM prediction_errors ORDER BY created_at, id").fetchall()
    out: list[Classified] = []
    seen_matches: dict[tuple, int] = {}
    for row in rows:
        pk = row["id"]

        def emit(cls, reason):
            out.append(Classified("prediction_errors", pk, cls, reason))

        if _is_placeholder(row):
            emit(FABRICATED_PLACEHOLDER, "33/33/33 'home' placeholder written by results verification before 927e402")
            continue
        total = row["home_win_pct"] + row["draw_pct"] + row["away_win_pct"]
        if abs(total - 100.0) > 1.5:
            emit(INCONSISTENT, f"1X2 probabilities sum to {total:.1f}")
            continue
        if row["predicted_result"] != _argmax_result(row):
            emit(INCONSISTENT, "predicted_result is not the most likely outcome")
            continue
        if row["actual_result"] is not None:
            from_goals = _result_from_goals(row["home_goals"], row["away_goals"])
            if from_goals is None:
                emit(INCONSISTENT, "settled without goals")
                continue
            if from_goals != row["actual_result"]:
                emit(INCONSISTENT, "actual_result disagrees with goals")
                continue
            if row["correct"] != int(row["predicted_result"] == row["actual_result"]):
                emit(INCONSISTENT, "correct flag disagrees with prediction and result")
                continue

        timing, why = timing_class(row["created_at"], row["match_date"], kickoffs.get(str(row["fixture_id"])))
        if timing == "retrospective":
            emit(RETROSPECTIVE, why)
            continue
        if timing == "unverifiable":
            emit(UNVERIFIABLE_TIMING, why)
            continue

        key = (row["home_team"].strip().lower(), row["away_team"].strip().lower(), str(row["match_date"])[:10])
        if key in seen_matches:
            emit(DUPLICATE, f"same match already recorded as prediction_errors.id={seen_matches[key]}")
            continue
        seen_matches[key] = pk
        if row["actual_result"] is None:
            emit(VERIFIED_UNSETTLED, f"pre-match ({why}); not settled")
        else:
            emit(VERIFIED_SETTLED, f"pre-match ({why}); settlement consistent")
    return out


def classify_prediction_log(conn: sqlite3.Connection, kickoffs: dict[str, datetime]) -> list[Classified]:
    rows = conn.execute(
        "SELECT id, match_id, match_date, market, predicted_prob, actual_outcome, created_at "
        "FROM prediction_log ORDER BY created_at, id"
    ).fetchall()
    out: list[Classified] = []
    seen: dict[tuple, int] = {}
    for row in rows:
        pk = row["id"]

        def emit(cls, reason):
            out.append(Classified("prediction_log", pk, cls, reason))

        prob = row["predicted_prob"]
        if prob is None or not (0.0 <= prob <= 100.0):
            emit(INCONSISTENT, f"predicted_prob {prob!r} outside 0-100")
            continue
        if row["actual_outcome"] not in (None, 0, 1):
            emit(INCONSISTENT, f"actual_outcome {row['actual_outcome']!r} not 0/1")
            continue
        timing, why = timing_class(row["created_at"], row["match_date"], kickoffs.get(str(row["match_id"])))
        if timing == "retrospective":
            emit(RETROSPECTIVE, why)
            continue
        if timing == "unverifiable":
            emit(UNVERIFIABLE_TIMING, why)
            continue
        key = (str(row["match_id"]), row["market"])
        if key in seen:
            emit(DUPLICATE, f"same match and market already recorded as prediction_log.id={seen[key]}")
            continue
        seen[key] = pk
        emit(VERIFIED_UNSETTLED if row["actual_outcome"] is None else VERIFIED_SETTLED, f"pre-match ({why})")
    return out


def classify_all(conn: sqlite3.Connection) -> list[Classified]:
    conn.row_factory = sqlite3.Row
    kickoffs = load_kickoffs(conn)
    return classify_prediction_errors(conn, kickoffs) + classify_prediction_log(conn, kickoffs)


def summarize(classified: Iterable[Classified]) -> dict[str, dict[str, int]]:
    out: dict[str, Counter] = {}
    for c in classified:
        out.setdefault(c.source_table, Counter())[c.classification] += 1
    return {table: {cls: counts.get(cls, 0) for cls in CLASSES} | {"total": sum(counts.values())}
            for table, counts in out.items()}


def _row_json(conn: sqlite3.Connection, table: str, pk: int) -> Optional[str]:
    conn.row_factory = sqlite3.Row
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (pk,)).fetchone()
    if row is None:
        return None
    return json.dumps({k: row[k] for k in row.keys()}, sort_keys=True, ensure_ascii=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ensure_tables(conn: sqlite3.Connection) -> None:
    for ddl in _DDL:
        conn.execute(ddl)


def record_classification(conn: sqlite3.Connection, classified: list[Classified]) -> None:
    ensure_tables(conn)
    now = _now()
    conn.executemany(
        "INSERT OR REPLACE INTO evidence_classification VALUES (?, ?, ?, ?, ?, ?)",
        [(c.source_table, c.pk, c.classification, c.reason, CLASSIFIER_VERSION, now) for c in classified],
    )
    conn.commit()


def quarantine(conn: sqlite3.Connection, classified: list[Classified]) -> int:
    """Copy every non-verified row into ``evidence_quarantine``. Source rows are untouched."""
    ensure_tables(conn)
    now = _now()
    batch = []
    for c in classified:
        if c.classification in VERIFIED:
            continue
        payload = _row_json(conn, c.source_table, c.pk)
        if payload is None:
            raise RuntimeError(f"{c.source_table}.id={c.pk} vanished during quarantine")
        batch.append((c.source_table, c.pk, c.classification, c.reason, CLASSIFIER_VERSION,
                      payload, _sha(payload), now))
    conn.executemany(
        "INSERT INTO evidence_quarantine (source_table, original_pk, classification, reason, "
        "classifier_version, original_row_json, row_sha256, quarantined_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(source_table, original_pk) DO NOTHING",
        batch,
    )
    conn.commit()
    return len(batch)


def move_quarantined(conn: sqlite3.Connection, source_table: str) -> int:
    """Remove quarantined rows from ``source_table``. Refuses if any row changed since quarantine."""
    assert source_table in SOURCE_TABLES, source_table
    rows = conn.execute(
        "SELECT original_pk, row_sha256 FROM evidence_quarantine "
        "WHERE source_table = ? AND moved_at IS NULL AND restored_at IS NULL",
        (source_table,),
    ).fetchall()
    mismatched = []
    for pk, digest in rows:
        current = _row_json(conn, source_table, pk)
        if current is None or _sha(current) != digest:
            mismatched.append(pk)
    if mismatched:
        raise RuntimeError(
            f"{len(mismatched)} {source_table} rows differ from their quarantined copy "
            f"(first: {mismatched[:5]}); nothing moved"
        )
    now = _now()
    with conn:
        for pk, _ in rows:
            conn.execute(f"DELETE FROM {source_table} WHERE id = ?", (pk,))
        conn.execute(
            "UPDATE evidence_quarantine SET moved_at = ? WHERE source_table = ? AND moved_at IS NULL "
            "AND restored_at IS NULL",
            (now, source_table),
        )
    return len(rows)


def restore_quarantined(conn: sqlite3.Connection, source_table: str,
                        pks: Optional[Iterable[int]] = None) -> int:
    """Reinsert moved rows with their original primary keys. Never overwrites a different row."""
    assert source_table in SOURCE_TABLES, source_table
    sql = ("SELECT original_pk, original_row_json FROM evidence_quarantine "
           "WHERE source_table = ? AND moved_at IS NOT NULL AND restored_at IS NULL")
    params: list = [source_table]
    rows = conn.execute(sql, params).fetchall()
    wanted = set(pks) if pks is not None else None
    restored = 0
    now = _now()
    with conn:
        for pk, payload in rows:
            if wanted is not None and pk not in wanted:
                continue
            existing = _row_json(conn, source_table, pk)
            if existing is not None:
                if existing != payload:
                    raise RuntimeError(f"{source_table}.id={pk} is occupied by a different row; not restored")
            else:
                data = json.loads(payload)
                cols = list(data)
                conn.execute(
                    f"INSERT INTO {source_table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                    [data[c] for c in cols],
                )
            conn.execute(
                "UPDATE evidence_quarantine SET restored_at = ? WHERE source_table = ? AND original_pk = ?",
                (now, source_table, pk),
            )
            restored += 1
    return restored

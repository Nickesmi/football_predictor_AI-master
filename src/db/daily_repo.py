"""
Daily predictions & results repository helpers.
"""

import sqlite3
import json
from typing import Optional, List, Dict


def insert_prediction(conn: sqlite3.Connection, match_id: str, predictions: Dict) -> None:
    conn.execute(
        """INSERT INTO daily_predictions (match_id, predictions_json) VALUES (?, ?)""",
        (match_id, json.dumps(predictions)),
    )
    conn.commit()


def has_prediction(conn: sqlite3.Connection, match_id: str) -> bool:
    row = conn.execute(
        """SELECT predictions_json FROM daily_predictions
           WHERE match_id = ? ORDER BY generated_at DESC LIMIT 1""",
        (match_id,),
    ).fetchone()
    if not row:
        return False
    try:
        pred = json.loads(row[0])
    except Exception:
        return False
    hw = float(pred.get("home_win_pct") or pred.get("home_win") or 0)
    dr = float(pred.get("draw_pct") or pred.get("draw") or 0)
    aw = float(pred.get("away_win_pct") or pred.get("away_win") or 0)
    return (hw + dr + aw) > 0


def get_predictions_for_date(conn: sqlite3.Connection, date: str) -> List[Dict]:
    rows = conn.execute(
        """SELECT dp.id, dp.match_id, dp.generated_at, dp.predictions_json
           FROM daily_predictions dp
           JOIN matches m ON m.id = dp.match_id
           WHERE m.date = ? ORDER BY dp.generated_at DESC""",
        (date,),
    ).fetchall()
    result = []
    for r in rows:
        try:
            pj = json.loads(r[3])
        except Exception:
            pj = {}
        result.append({"id": r[0], "match_id": r[1], "generated_at": r[2], "predictions": pj})
    return result


def insert_result(conn: sqlite3.Connection, match_id: str, home_goals: int, away_goals: int, predictions: Dict, hit: bool) -> None:
    conn.execute(
        """INSERT INTO daily_results (match_id, actual_home_goals, actual_away_goals, predictions_json, hit)
           VALUES (?, ?, ?, ?, ?)""",
        (match_id, home_goals, away_goals, json.dumps(predictions), int(bool(hit))),
    )
    conn.commit()


def get_performance_summary(conn: sqlite3.Connection, date: Optional[str] = None) -> Dict:
    # Simple accuracy / ROI summary for a date or overall
    q = "SELECT COUNT(*) as total, SUM(hit) as hits FROM daily_results"
    params = []
    if date:
        q += " WHERE DATE(recorded_at) = ?"
        params.append(date)
    row = conn.execute(q, params).fetchone()
    total = row[0] or 0
    hits = row[1] or 0
    accuracy = (hits / total * 100) if total else 0.0
    return {"total": total, "hits": hits, "accuracy": round(accuracy, 2)}

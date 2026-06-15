"""
tennis_feature_builder.py
==========================
Builds the feature vector for tennis match predictions.

V1 approved feature set (user-governed):
  - Surface Elo (primary signal)
  - Ranking difference + bucket
  - Last 5 / Last 10 win rates (overall + surface-specific)
  - H2H win rate + last 3 H2H results
  - Fatigue (matches + sets last 7 days)
  - Tournament context (tier + best-of)

DEFERRED to research backlog (post 500 settled predictions):
  - Ace rate, double fault rate, first-serve %, break conversion, return games won %

Data quality shrinkage is applied: missing features pull probability toward 0.5.
No Poisson. No football features. No serve/return statistics.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("football_predictor.tennis")

# ── Default Elo rating for new players ───────────────────────────────────────
_DEFAULT_ELO = 1500.0

# ── Tournament tier mapping ───────────────────────────────────────────────────
_TIER_MAP = {
    "grand slam": 4,
    "atp 1000": 3, "atp masters": 3, "wta 1000": 3,
    "atp 500": 2,  "wta 500": 2,
    "atp 250": 1,  "wta 250": 1,
    "challenger": 0, "itf": -1,
    "exhibition": -2,
}


def _tournament_tier(tournament: Optional[str]) -> int:
    if not tournament:
        return 1  # default to ATP 250 level
    t = tournament.lower()
    for key, val in _TIER_MAP.items():
        if key in t:
            return val
    return 1


def _rank_bucket(rank: Optional[int]) -> int:
    """Convert ATP/WTA rank to ordered bucket: 4=Top10, 3=Top50, 2=Top100, 1=Other, 0=Unknown"""
    if rank is None:
        return 0
    if rank <= 10:
        return 4
    if rank <= 50:
        return 3
    if rank <= 100:
        return 2
    return 1


def _get_player_elo(conn: sqlite3.Connection, player: str, surface: str) -> float:
    """Retrieve surface-adjusted Elo. Returns default if player is new."""
    try:
        row = conn.execute(
            "SELECT elo FROM tennis_player_state WHERE player_name=? AND surface=?",
            (player, surface)
        ).fetchone()
        if row:
            return float(row[0])
        # Fall back to overall Elo
        row = conn.execute(
            "SELECT elo FROM tennis_player_state WHERE player_name=? AND surface='overall'",
            (player,)
        ).fetchone()
        return float(row[0]) if row else _DEFAULT_ELO
    except Exception:
        return _DEFAULT_ELO


def _get_recent_results(
    conn: sqlite3.Connection,
    player: str,
    surface: Optional[str] = None,
    n: int = 10,
    days: int = 365,
) -> list[int]:
    """
    Get last N results (1=win, 0=loss) optionally filtered by surface.
    Looks back at most `days` days.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        if surface:
            rows = conn.execute(
                """
                SELECT p.result
                FROM tennis_predictions p
                JOIN tennis_results r ON p.match_id = r.match_id
                JOIN tennis_matches m ON p.match_id = m.match_id
                WHERE p.selection = ?
                  AND m.surface = ?
                  AND m.date >= ?
                  AND p.result IS NOT NULL
                  AND p.market_type = 'match_winner'
                ORDER BY m.date DESC
                LIMIT ?
                """,
                (player, surface, since, n)
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT p.result
                FROM tennis_predictions p
                JOIN tennis_results r ON p.match_id = r.match_id
                JOIN tennis_matches m ON p.match_id = m.match_id
                WHERE p.selection = ?
                  AND m.date >= ?
                  AND p.result IS NOT NULL
                  AND p.market_type = 'match_winner'
                ORDER BY m.date DESC
                LIMIT ?
                """,
                (player, since, n)
            ).fetchall()
        return [int(r[0]) for r in rows]
    except Exception:
        return []


def _get_h2h(conn: sqlite3.Connection, p1: str, p2: str, n: int = 10) -> list[int]:
    """
    Get last N H2H results from p1's perspective (1=p1 won, 0=p1 lost).
    """
    try:
        rows = conn.execute(
            """
            SELECT r.winner
            FROM tennis_results r
            JOIN tennis_matches m ON r.match_id = m.match_id
            WHERE (m.player_1 = ? AND m.player_2 = ?)
               OR (m.player_1 = ? AND m.player_2 = ?)
            ORDER BY m.date DESC
            LIMIT ?
            """,
            (p1, p2, p2, p1, n)
        ).fetchall()
        return [1 if r[0] == p1 else 0 for r in rows]
    except Exception:
        return []


def _get_fatigue(conn: sqlite3.Connection, player: str) -> dict:
    """Matches + sets played in last 7 days."""
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) as matches_played,
                   SUM(COALESCE(sets_1, 0) + COALESCE(sets_2, 0)) as total_sets
            FROM tennis_matches m
            JOIN tennis_results r ON m.match_id = r.match_id
            WHERE (m.player_1 = ? OR m.player_2 = ?)
              AND m.date >= ?
            """,
            (player, player, since)
        ).fetchone()
        return {
            "matches_last_7d": int(row[0]) if row else 0,
            "sets_last_7d":    int(row[1]) if row and row[1] else 0,
        }
    except Exception:
        return {"matches_last_7d": 0, "sets_last_7d": 0}


# ── Main feature builder ──────────────────────────────────────────────────────

def build_features(
    conn: sqlite3.Connection,
    player_1: str,
    player_2: str,
    surface: str,
    tournament: Optional[str] = None,
    rank_1: Optional[int] = None,
    rank_2: Optional[int] = None,
    best_of: int = 3,
) -> dict:
    """
    Build the complete feature vector for a tennis match.

    Returns a dict with:
      - all raw feature values
      - data_quality_score (0-100)
      - missing_features list
    """
    features = {}
    missing = []

    # ── Surface Elo ───────────────────────────────────────────────────────────
    s = surface or "hard"
    elo_1 = _get_player_elo(conn, player_1, s)
    elo_2 = _get_player_elo(conn, player_2, s)
    features["elo_1"] = elo_1
    features["elo_2"] = elo_2
    features["elo_diff"] = elo_1 - elo_2

    if elo_1 == _DEFAULT_ELO:
        missing.append("elo_p1_new_player")
    if elo_2 == _DEFAULT_ELO:
        missing.append("elo_p2_new_player")

    # ── Ranking ───────────────────────────────────────────────────────────────
    features["rank_1"] = rank_1
    features["rank_2"] = rank_2
    features["rank_diff"] = (rank_1 or 500) - (rank_2 or 500)
    features["rank_bucket_1"] = _rank_bucket(rank_1)
    features["rank_bucket_2"] = _rank_bucket(rank_2)

    if rank_1 is None:
        missing.append("rank_p1")
    if rank_2 is None:
        missing.append("rank_p2")

    # ── Recent form — overall ──────────────────────────────────────────────────
    last5_p1  = _get_recent_results(conn, player_1, n=5)
    last10_p1 = _get_recent_results(conn, player_1, n=10)
    last5_p2  = _get_recent_results(conn, player_2, n=5)
    last10_p2 = _get_recent_results(conn, player_2, n=10)

    features["win_rate_last5_p1"]  = (sum(last5_p1)  / len(last5_p1))  if last5_p1  else None
    features["win_rate_last10_p1"] = (sum(last10_p1) / len(last10_p1)) if last10_p1 else None
    features["win_rate_last5_p2"]  = (sum(last5_p2)  / len(last5_p2))  if last5_p2  else None
    features["win_rate_last10_p2"] = (sum(last10_p2) / len(last10_p2)) if last10_p2 else None

    if not last5_p1:
        missing.append("form_last5_p1")
    if not last10_p1:
        missing.append("form_last10_p1")
    if not last5_p2:
        missing.append("form_last5_p2")
    if not last10_p2:
        missing.append("form_last10_p2")

    # ── Recent form — surface specific ────────────────────────────────────────
    surf_p1 = _get_recent_results(conn, player_1, surface=s, n=20)
    surf_p2 = _get_recent_results(conn, player_2, surface=s, n=20)

    features["surface_win_rate_p1"] = (sum(surf_p1) / len(surf_p1)) if surf_p1 else None
    features["surface_win_rate_p2"] = (sum(surf_p2) / len(surf_p2)) if surf_p2 else None

    if not surf_p1:
        missing.append("surface_form_p1")
    if not surf_p2:
        missing.append("surface_form_p2")

    # ── H2H ──────────────────────────────────────────────────────────────────
    h2h = _get_h2h(conn, player_1, player_2, n=10)
    features["h2h_total"]   = len(h2h)
    features["h2h_win_p1"]  = (sum(h2h) / len(h2h)) if h2h else None
    features["h2h_recent3"] = h2h[:3]  # last 3 results

    if not h2h:
        missing.append("h2h")

    # ── Fatigue ───────────────────────────────────────────────────────────────
    fat_1 = _get_fatigue(conn, player_1)
    fat_2 = _get_fatigue(conn, player_2)
    features.update({
        "fatigue_matches_p1": fat_1["matches_last_7d"],
        "fatigue_sets_p1":    fat_1["sets_last_7d"],
        "fatigue_matches_p2": fat_2["matches_last_7d"],
        "fatigue_sets_p2":    fat_2["sets_last_7d"],
    })

    # ── Tournament context ────────────────────────────────────────────────────
    features["tournament_tier"] = _tournament_tier(tournament)
    features["best_of"]         = best_of
    features["surface"]         = s

    # ── Data quality score ────────────────────────────────────────────────────
    # High-impact missing features penalise quality more than low-impact ones.
    penalties = {
        "elo_p1_new_player":  10,
        "elo_p2_new_player":  10,
        "form_last5_p1":       8,
        "form_last5_p2":       8,
        "form_last10_p1":      5,
        "form_last10_p2":      5,
        "surface_form_p1":     7,
        "surface_form_p2":     7,
        "rank_p1":             5,
        "rank_p2":             5,
        "h2h":                 5,
    }
    penalty = sum(penalties.get(m, 3) for m in missing)
    data_quality = max(0, min(100, 100 - penalty))

    features["data_quality_score"] = data_quality
    features["missing_features"]   = missing

    return features

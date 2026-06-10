"""
Dynamic Team Learning System

Tracks true team strength and momentum dynamically based on actual vs. expected results.
The expected result is derived directly from the platform's pre-match probabilities,
allowing the system to learn and adapt to upsets and shifting team strengths.
"""

from __future__ import annotations

import sqlite3
import logging
import statistics
from datetime import datetime
from typing import Tuple

logger = logging.getLogger("football_predictor")

# Constants
INITIAL_RATING = 1500.0
K_CALIBRATION = 40.0   # Fast learning for first 10 matches
K_STANDARD = 20.0      # Standard learning rate
CALIBRATION_GAMES = 10
MOMENTUM_MAX = 100.0


def _get_k_factor(matches_played: int) -> float:
    return K_CALIBRATION if matches_played < CALIBRATION_GAMES else K_STANDARD


def get_team_rating(conn: sqlite3.Connection, team_name: str, league: str) -> Tuple[float, float, float, int]:
    """
    Returns (rating, momentum_score, volatility_score, matches_played) for a team.
    If the team doesn't exist yet, returns defaults and safely initializes it.
    """
    # ── Apply Recency Decay on read ──
    _apply_recency_decay(conn, team_name, league)

    row = conn.execute(
        """SELECT rating, momentum_score, volatility_score, matches_played
           FROM team_learning_state
           WHERE team_name = ? AND league = ?""",
        (team_name, league)
    ).fetchone()

    if row:
        return row[0], row[1], row[2], row[3]
    
    return INITIAL_RATING, 0.0, 0.0, 0


def _apply_recency_decay(conn: sqlite3.Connection, team_name: str, league: str) -> None:
    """
    If a team hasn't played in a long time (>30 days), their rating
    slowly drifts back toward INITIAL_RATING (1500).
    Drift is 2 points per day over 30 days.
    """
    row = conn.execute(
        """SELECT rating, last_updated 
           FROM team_learning_state 
           WHERE team_name = ? AND league = ?""",
        (team_name, league)
    ).fetchone()
    
    if not row:
        return
        
    rating = row[0]
    last_updated_str = row[1]
    
    if not last_updated_str:
        return
        
    try:
        # Convert timestamp strings safely
        if "T" in last_updated_str:
            last_date = datetime.fromisoformat(last_updated_str.replace("Z", "+00:00")).replace(tzinfo=None)
        else:
            last_date = datetime.strptime(last_updated_str, "%Y-%m-%d %H:%M:%S")
            
        now = datetime.utcnow()
        days_since = (now - last_date).days
        
        if days_since > 30:
            # Drift rating towards 1500 by 2 points per day after 30 days
            # Cap drift at 50 points per query to avoid total resets on weird dates
            drift_days = min(days_since - 30, 25)
            drift_amount = drift_days * 2.0
            
            if rating > INITIAL_RATING:
                new_rating = max(INITIAL_RATING, rating - drift_amount)
            else:
                new_rating = min(INITIAL_RATING, rating + drift_amount)
                
            if new_rating != rating:
                conn.execute(
                    """UPDATE team_learning_state
                       SET rating = ?, last_updated = CURRENT_TIMESTAMP
                       WHERE team_name = ? AND league = ?""",
                    (new_rating, team_name, league)
                )
                conn.commit()
    except Exception as e:
        logger.debug(f"Recency decay skipped for {team_name}: {e}")


def update_team_ratings(
    conn: sqlite3.Connection,
    fixture_id: str,
    match_date: str,
    league: str,
    home_team: str,
    away_team: str,
    home_win_pct: float,
    draw_pct: float,
    away_win_pct: float,
    home_goals: int,
    away_goals: int
) -> None:
    """
    Core function to update Team Ratings and Momentum after a match.
    Uses pre-match probabilities to calculate Expected Result.
    """
    # 1. Fetch current states
    h_rating, h_momentum, h_volatility, h_matches = get_team_rating(conn, home_team, league)
    a_rating, a_momentum, a_volatility, a_matches = get_team_rating(conn, away_team, league)
    
    # 2. Calculate Expected Results (1X2 to 0.0-1.0 scale)
    # Expected Result = Win% + (0.5 * Draw%)
    exp_h = (home_win_pct + 0.5 * draw_pct) / 100.0
    exp_a = (away_win_pct + 0.5 * draw_pct) / 100.0
    
    # Ensure they roughly sum to 1.0
    # The probabilities might not perfectly sum due to margins, so we normalize
    total_exp = exp_h + exp_a
    if total_exp > 0:
        exp_h = exp_h / total_exp
        exp_a = exp_a / total_exp

    # 3. Calculate Actual Results
    if home_goals > away_goals:
        act_h, act_a = 1.0, 0.0
    elif home_goals < away_goals:
        act_h, act_a = 0.0, 1.0
    else:
        act_h, act_a = 0.5, 0.5
        
    # 4. K-Factors
    k_h = _get_k_factor(h_matches)
    k_a = _get_k_factor(a_matches)
    
    # 5. Rating Updates
    change_h = k_h * (act_h - exp_h)
    change_a = k_a * (act_a - exp_a)
    
    new_h_rating = h_rating + change_h
    new_a_rating = a_rating + change_a
    
    # 6. Save to History (to compute momentum next)
    h_goal_diff = home_goals - away_goals
    a_goal_diff = away_goals - home_goals
    try:
        conn.execute(
            """INSERT INTO team_rating_history 
               (fixture_id, match_date, team_name, opponent, 
                rating_before, rating_after, momentum_before, momentum_after,
                expected_result, actual_result, goal_diff, rating_change, matches_played)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fixture_id, match_date, home_team, away_team,
             h_rating, new_h_rating, h_momentum, 0.0, # Momentum after computed later
             exp_h, act_h, h_goal_diff, change_h, h_matches + 1)
        )
        conn.execute(
            """INSERT INTO team_rating_history 
               (fixture_id, match_date, team_name, opponent, 
                rating_before, rating_after, momentum_before, momentum_after,
                expected_result, actual_result, goal_diff, rating_change, matches_played)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fixture_id, match_date, away_team, home_team,
             a_rating, new_a_rating, a_momentum, 0.0,
             exp_a, act_a, a_goal_diff, change_a, a_matches + 1)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # Fixture already processed
        return
        
    # 7. Compute New Momentum Score
    new_h_momentum = _calculate_momentum(conn, home_team)
    new_a_momentum = _calculate_momentum(conn, away_team)
    
    # Update History with new momentum
    conn.execute("UPDATE team_rating_history SET momentum_after = ? WHERE fixture_id = ? AND team_name = ?", (new_h_momentum, fixture_id, home_team))
    conn.execute("UPDATE team_rating_history SET momentum_after = ? WHERE fixture_id = ? AND team_name = ?", (new_a_momentum, fixture_id, away_team))
    
    # 8. Compute New Volatility Score
    new_h_volatility = _calculate_volatility(conn, home_team)
    new_a_volatility = _calculate_volatility(conn, away_team)
    
    # 8.5 Split Metrics
    h_row = conn.execute("SELECT home_matches, away_matches, home_points, away_points, home_goal_diff, away_goal_diff FROM team_learning_state WHERE team_name = ?", (home_team,)).fetchone()
    a_row = conn.execute("SELECT home_matches, away_matches, home_points, away_points, home_goal_diff, away_goal_diff FROM team_learning_state WHERE team_name = ?", (away_team,)).fetchone()
    
    h_hm, h_am, h_hp, h_ap, h_hgd, h_agd = h_row if h_row else (0, 0, 0, 0, 0, 0)
    a_hm, a_am, a_hp, a_ap, a_hgd, a_agd = a_row if a_row else (0, 0, 0, 0, 0, 0)
    
    if act_h == 1.0: h_pts, a_pts = 3, 0
    elif act_h == 0.5: h_pts, a_pts = 1, 1
    else: h_pts, a_pts = 0, 3
    
    new_h_hm, new_h_hp, new_h_hgd = h_hm + 1, h_hp + h_pts, h_hgd + h_goal_diff
    new_a_am, new_a_ap, new_a_agd = a_am + 1, a_ap + a_pts, a_agd + a_goal_diff
    
    # 9. Upsert Learning State
    conn.execute(
        """INSERT INTO team_learning_state 
               (team_name, league, rating, momentum_score, volatility_score, matches_played, last_updated,
                home_matches, away_matches, home_points, away_points, home_goal_diff, away_goal_diff)
           VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(team_name) DO UPDATE SET
               league = excluded.league,
               rating = excluded.rating,
               momentum_score = excluded.momentum_score,
               volatility_score = excluded.volatility_score,
               matches_played = excluded.matches_played,
               home_matches = excluded.home_matches,
               away_matches = excluded.away_matches,
               home_points = excluded.home_points,
               away_points = excluded.away_points,
               home_goal_diff = excluded.home_goal_diff,
               away_goal_diff = excluded.away_goal_diff,
               last_updated = CURRENT_TIMESTAMP""",
        (home_team, league, new_h_rating, new_h_momentum, new_h_volatility, h_matches + 1,
         new_h_hm, h_am, new_h_hp, h_ap, new_h_hgd, h_agd)
    )
    conn.execute(
        """INSERT INTO team_learning_state 
               (team_name, league, rating, momentum_score, volatility_score, matches_played, last_updated,
                home_matches, away_matches, home_points, away_points, home_goal_diff, away_goal_diff)
           VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(team_name) DO UPDATE SET
               league = excluded.league,
               rating = excluded.rating,
               momentum_score = excluded.momentum_score,
               volatility_score = excluded.volatility_score,
               matches_played = excluded.matches_played,
               home_matches = excluded.home_matches,
               away_matches = excluded.away_matches,
               home_points = excluded.home_points,
               away_points = excluded.away_points,
               home_goal_diff = excluded.home_goal_diff,
               away_goal_diff = excluded.away_goal_diff,
               last_updated = CURRENT_TIMESTAMP""",
        (away_team, league, new_a_rating, new_a_momentum, new_a_volatility, a_matches + 1,
         a_hm, new_a_am, a_hp, new_a_ap, a_hgd, new_a_agd)
    )
    conn.commit()


def _calculate_momentum(conn: sqlite3.Connection, team_name: str) -> float:
    """
    Tracks the sum of (Actual - Expected) for the last 5 matches.
    Max sum is +5.0 (5 extreme upsets), min is -5.0.
    Scales to -100 to +100.
    """
    rows = conn.execute(
        """SELECT expected_result, actual_result 
           FROM team_rating_history
           WHERE team_name = ?
           ORDER BY match_date DESC, id DESC
           LIMIT 5""",
        (team_name,)
    ).fetchall()
    
    if not rows:
        return 0.0
        
    diff_sum = sum(row[1] - row[0] for row in rows)
    # diff_sum is between -5.0 and +5.0 (if 5 matches played)
    
    # We always divide by 5.0 to scale it properly, even if fewer matches played
    momentum = (diff_sum / 5.0) * MOMENTUM_MAX
    
    return round(momentum, 1)


def _calculate_volatility(conn: sqlite3.Connection, team_name: str) -> float:
    """
    Computes the team volatility based on the standard deviation of their last 10 rating changes.
    Maps a max standard deviation of 15.0 to a score of 100.
    """
    rows = conn.execute(
        """SELECT rating_change 
           FROM team_rating_history
           WHERE team_name = ?
           ORDER BY match_date DESC, id DESC
           LIMIT 10""",
        (team_name,)
    ).fetchall()
    
    if len(rows) < 3:
        return 0.0 # Need at least 3 matches to compute meaningful variance
        
    changes = [r[0] for r in rows]
    try:
        std_dev = statistics.stdev(changes)
    except statistics.StatisticsError:
        return 0.0
        
    # Scale: std_dev of 15.0 => 100 volatility
    score = (std_dev / 15.0) * 100.0
    return min(100.0, max(0.0, round(score, 1)))


def get_home_advantage(conn: sqlite3.Connection, team_name: str) -> float:
    """
    Returns home_advantage_score (-100 to +100).
    Requires at least 3 home and 3 away matches, else returns 0.0.
    """
    row = conn.execute(
        """SELECT home_matches, away_matches, home_points, away_points
           FROM team_learning_state WHERE team_name = ?""",
        (team_name,)
    ).fetchone()
    
    if not row:
        return 0.0
        
    hm, am, hp, ap = row
    if hm < 3 or am < 3:
        return 0.0
        
    hppg = hp / float(hm)
    appg = ap / float(am)
    
    # Scale: max difference is ~3.0 points
    score = ((hppg - appg) / 3.0) * 100.0
    return max(-100.0, min(100.0, round(score, 1)))


def get_team_diagnostics(conn: sqlite3.Connection, team_name: str) -> dict:
    """For GET /api/debug/team-rating?team=..."""
    # Ensure up-to-date recency
    row = conn.execute("SELECT league FROM team_learning_state WHERE team_name = ?", (team_name,)).fetchone()
    if row:
        _apply_recency_decay(conn, team_name, row[0])
        
    state = conn.execute(
        """SELECT rating, momentum_score, volatility_score, matches_played, last_updated,
                  home_matches, away_matches, home_points, away_points, home_goal_diff, away_goal_diff
           FROM team_learning_state WHERE team_name = ?""",
        (team_name,)
    ).fetchone()
    
    if not state:
        return {"error": f"No learning state found for {team_name}"}
        
    history = conn.execute(
        """SELECT match_date, opponent, expected_result, actual_result, rating_change, rating_after
           FROM team_rating_history
           WHERE team_name = ?
           ORDER BY match_date DESC, id DESC
           LIMIT 10""",
        (team_name,)
    ).fetchall()
    
    hist_list = []
    for r in history:
        act = r[3]
        if act == 1.0: res_str = "W"
        elif act == 0.5: res_str = "D"
        else: res_str = "L"
        
        hist_list.append({
            "date": r[0],
            "opponent": r[1],
            "expected_points": round(r[2], 2),
            "actual_points": act,
            "result": res_str,
            "rating_change": round(r[4], 1),
            "rating_after": round(r[5], 1)
        })
        
    vol_score = round(state[2], 1)
    if vol_score <= 30:
        vol_status = "Stable"
    elif vol_score <= 60:
        vol_status = "Normal"
    else:
        vol_status = "Volatile"
        
    adv_score = get_home_advantage(conn, team_name)
    if adv_score > 20:
        adv_status = "Strong Home Team"
    elif adv_score < -20:
        adv_status = "Strong Away Team"
    else:
        adv_status = "Neutral"
        
    return {
        "team": team_name,
        "current_rating": round(state[0], 1),
        "momentum": round(state[1], 1),
        "volatility": vol_score,
        "volatility_status": vol_status,
        "home_advantage_score": adv_score,
        "home_advantage_status": adv_status,
        "home_matches": state[5],
        "away_matches": state[6],
        "home_points": state[7],
        "away_points": state[8],
        "home_goal_diff": state[9],
        "away_goal_diff": state[10],
        "matches_played": state[3],
        "last_updated": state[4],
        "history": hist_list
    }

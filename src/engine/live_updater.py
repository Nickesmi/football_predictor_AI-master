"""
Live Updater — Phase 4 Core Event-Driven Pipeline

This module is the HEART of the live adaptive system.
When a match finishes, `on_match_finished()` triggers the full
update cascade:

    match finishes
         ↓
    store in match_history
         ↓
    update ELO ratings (shared cross-venue pool)
         ↓
    update rolling scored/conceded (venue-specific)
         ↓
    update OVERALL blended state (used by predictions)
         ↓
    update form metrics
         ↓
    update fatigue
         ↓
    future predictions auto-change

Design decisions:
    - ELO is shared across venues (one pool per team per league)
    - venue='overall' row is the AUTHORITATIVE state used by get_team_stats()
    - Rolling averages use exponential decay (α = 0.85)
    - Goal difference multiplier in ELO (bigger wins = bigger swing)
    - venue='home' and venue='away' are also tracked for venue-specific analysis
    - Idempotent: re-ingesting same match_id is a no-op
    - Threshold to use live data: ≥1 match (immediate activation)
"""

import math
import sqlite3
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from src.db.team_state import TeamState, get_team_state_exact, upsert_team_state

logger = logging.getLogger("football_predictor")

# ═══════════════════════════════════════════════════════════════════════
# ELO CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════

ELO_K = 20                # sensitivity: how much one match changes ratings
ELO_DEFAULT = 1500.0      # starting ELO for unknown teams
ELO_HOME_ADVANTAGE = 65   # ELO points added to home team's expected score

# ═══════════════════════════════════════════════════════════════════════
# ROLLING AVERAGE CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════

ROLLING_DECAY = 0.85      # weight of previous average (0.85 = last 7 matches dominate)
#
# Effective weights by match age:
#   Last match:   1.00 (new observation weight = 1 - DECAY = 0.15)
#   2 ago:        0.85
#   5 ago:        0.44
#   10 ago:       0.20
#   15 ago:       0.09

# ═══════════════════════════════════════════════════════════════════════
# COLD-START DEFAULTS (used when no history exists)
# ═══════════════════════════════════════════════════════════════════════
# These are league-average values; actual team stats come from static DB
# and then transition to live values after ≥1 ingested match.

HOME_DEFAULTS = dict(rolling_scored=1.35, rolling_conceded=1.05,
                     rolling_corners=5.2, rolling_cards=2.0)
AWAY_DEFAULTS = dict(rolling_scored=1.05, rolling_conceded=1.35,
                     rolling_corners=4.8, rolling_cards=2.2)
OVERALL_DEFAULTS = dict(rolling_scored=1.2, rolling_conceded=1.2,
                        rolling_corners=5.0, rolling_cards=2.1)


def _update_elo(
    elo_home: float,
    elo_away: float,
    home_goals: int,
    away_goals: int,
) -> tuple[float, float]:
    """Update ELO ratings after a match.

    Uses the standard ELO formula with:
    - Home advantage boost (ELO_HOME_ADVANTAGE points)
    - Goal difference multiplier (bigger wins = bigger update)

    Returns:
        (new_elo_home, new_elo_away)
    """
    # Expected scores (with home advantage)
    exp_home = 1.0 / (1 + 10 ** ((elo_away - elo_home - ELO_HOME_ADVANTAGE) / 400))
    exp_away = 1.0 - exp_home

    # Actual result (win=1, draw=0.5, loss=0)
    if home_goals > away_goals:
        actual_home, actual_away = 1.0, 0.0
    elif home_goals == away_goals:
        actual_home, actual_away = 0.5, 0.5
    else:
        actual_home, actual_away = 0.0, 1.0

    # Goal difference multiplier (bigger wins = bigger rating change)
    gd = abs(home_goals - away_goals)
    gd_mult = math.log(max(gd, 1) + 1) + 1  # 0→1.0, 1→1.69, 2→2.10, 3→2.39

    new_home = elo_home + ELO_K * gd_mult * (actual_home - exp_home)
    new_away = elo_away + ELO_K * gd_mult * (actual_away - exp_away)

    return round(new_home, 1), round(new_away, 1)


def _update_rolling(current: float, new_value: float) -> float:
    """Exponentially weighted rolling average update.

    new_avg = DECAY × old_avg + (1 - DECAY) × new_observation
    """
    return round(ROLLING_DECAY * current + (1 - ROLLING_DECAY) * new_value, 3)


def _compute_form_points(result: str) -> float:
    """Convert match result to form points: W=3, D=1, L=0."""
    if result == "W":
        return 3.0
    elif result == "D":
        return 1.0
    return 0.0


def _get_recent_results(
    conn: sqlite3.Connection,
    team_name: str,
    n: int = 10,
) -> list[str]:
    """Get last N match results for a team (W/D/L).

    Checks both home and away appearances in match_history.
    """
    rows = conn.execute(
        """SELECT home_team, away_team, home_goals, away_goals
           FROM match_history
           WHERE home_team = ? OR away_team = ?
           ORDER BY match_date DESC, id DESC
           LIMIT ?""",
        (team_name, team_name, n),
    ).fetchall()

    results = []
    for r in rows:
        is_home = r["home_team"] == team_name
        own_goals = r["home_goals"] if is_home else r["away_goals"]
        opp_goals = r["away_goals"] if is_home else r["home_goals"]

        if own_goals > opp_goals:
            results.append("W")
        elif own_goals == opp_goals:
            results.append("D")
        else:
            results.append("L")

    return results


def _count_matches_last_14d(
    conn: sqlite3.Connection,
    team_name: str,
    match_date: str,
) -> int:
    """Count how many matches a team played in the last 14 days."""
    cutoff = (datetime.strptime(match_date, "%Y-%m-%d") - timedelta(days=14)).strftime("%Y-%m-%d")
    row = conn.execute(
        """SELECT COUNT(*) FROM match_history
           WHERE (home_team = ? OR away_team = ?)
                 AND match_date >= ? AND match_date <= ?""",
        (team_name, team_name, cutoff, match_date),
    ).fetchone()
    return row[0] if row else 0


def _compute_rest_days(
    conn: sqlite3.Connection,
    team_name: str,
    current_date: str,
) -> int:
    """Days since team's last match before current_date."""
    row = conn.execute(
        """SELECT MAX(match_date) FROM match_history
           WHERE (home_team = ? OR away_team = ?)
                 AND match_date < ?""",
        (team_name, team_name, current_date),
    ).fetchone()

    if row and row[0]:
        last = datetime.strptime(row[0], "%Y-%m-%d")
        current = datetime.strptime(current_date, "%Y-%m-%d")
        return max(0, (current - last).days)
    return 7  # default: assume normal rest


TEAM_STATE_IDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS team_state_ids (
    provider          TEXT NOT NULL,
    provider_team_id  TEXT NOT NULL,
    league            TEXT NOT NULL,
    team_name_lower   TEXT NOT NULL,
    team_name         TEXT NOT NULL,
    how               TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    PRIMARY KEY (provider, provider_team_id, league)
);
"""


def _norm(name: str) -> str:
    from src.identity.teams import normalise_team

    return _norm_cache.get(name) or _norm_cache.setdefault(name, normalise_team(name))


_norm_cache: dict[str, str] = {}


def resolve_record(conn: sqlite3.Connection, team_name: str, league: str, provider: Optional[str] = None,
                   provider_team_id=None) -> tuple[str, str]:
    """The team_state record (name) a result is filed under, and how it was chosen (2026-11-05).

    Order: 1. the record already bound to this provider team id in this label; 2. without a binding, the
    record with exactly this name, then the single record whose normalised name is equal
    (src/identity/teams.normalise_team: accents, FC/SC, a leading "1."; youth, reserve and women's markers
    are kept); 3. otherwise a new record. A record bound to a different id of the same provider is never
    used: that is another club, and its new record is named "<name> #<id>". Never a substring match.
    When an id is given, the choice is bound to it in team_state_ids."""
    name = (team_name or "").strip()
    pid = None if provider_team_id in (None, "") else str(provider_team_id)
    if provider and pid:
        conn.execute(TEAM_STATE_IDS_SCHEMA)
        row = conn.execute("SELECT team_name FROM team_state_ids WHERE provider = ? AND provider_team_id = ? "
                           "AND league = ?", (provider, pid, league)).fetchone()
        if row:
            return row[0], "provider_id"
        claimed = {r[0]: r[1] for r in conn.execute(
            "SELECT team_name_lower, provider_team_id FROM team_state_ids WHERE provider = ? AND league = ?",
            (provider, league))}
    else:
        claimed = {}
    free = lambda lower: lower not in claimed   # noqa: E731  (bound to another id = another club)
    exact = conn.execute("SELECT team_name FROM team_state WHERE team_name_lower = ? AND league = ? AND "
                         "venue = 'overall'", (name.lower(), league)).fetchone()
    if exact and free(name.lower()):
        record, how = exact[0], "exact_name"
    else:
        key = _norm(name)
        same = {r[0].lower(): r[0] for r in conn.execute(
            "SELECT team_name FROM team_state WHERE league = ? AND venue = 'overall'", (league,))
                if _norm(r[0]) == key and free(r[0].lower())}
        if len(same) == 1 and not exact:
            record, how = next(iter(same.values())), "normalised_name"
        else:
            record, how = (f"{name} #{pid}" if (exact or same) and pid else name), "new_record"
    if provider and pid:
        conn.execute("INSERT OR IGNORE INTO team_state_ids VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (provider, pid, league, record.lower().strip(), record, how,
                      datetime.utcnow().replace(microsecond=0).isoformat() + "Z"))
    return record, how


def _load_or_create_state(
    conn: sqlite3.Connection,
    team_name: str,
    league: str,
    venue: str,
    defaults: dict,
) -> TeamState:
    """Load the record with exactly this name (resolve_record chose it), or create a fresh one."""
    state = get_team_state_exact(conn, team_name, league, venue)
    if state is None:
        state = TeamState(
            team_name=team_name,
            league=league,
            venue=venue,
            elo=ELO_DEFAULT,
            **defaults,
        )
    return state


def _update_form_and_streaks(state: TeamState, results: list[str]) -> None:
    """Update form metrics from a results list in-place."""
    if len(results) >= 5:
        state.form_last5 = round(
            sum(_compute_form_points(r) for r in results[:5]) / 15.0, 3
        )
    if len(results) >= 10:
        state.form_last10 = round(
            sum(_compute_form_points(r) for r in results[:10]) / 30.0, 3
        )

    # Win/unbeaten streaks
    streak_w, streak_u = 0, 0
    for r in results:
        if r == "W":
            streak_w += 1
            streak_u += 1
        elif r == "D":
            streak_w = 0
            streak_u += 1
        else:
            break
    state.win_streak = streak_w
    state.unbeaten_streak = streak_u


# ═══════════════════════════════════════════════════════════════════════
# MAIN EVENT HANDLER
# ═══════════════════════════════════════════════════════════════════════

def on_match_finished(
    conn: sqlite3.Connection,
    match_id: str,
    match_date: str,
    league: str,
    home_team: str,
    away_team: str,
    home_goals: int,
    away_goals: int,
    home_xg: float = None,
    away_xg: float = None,
    home_corners: int = None,
    away_corners: int = None,
    home_cards: int = None,
    away_cards: int = None,
    *,
    provider: str = None,
    home_team_id=None,
    away_team_id=None,
) -> dict:
    """Core event handler: a match has finished.

    Each team is filed under the record resolve_record() chooses (provider team id first, then exact or
    exact normalised name; never a substring). History, form and fatigue use that record's name.

    This is the function that makes the system LIVE.

    Pipeline:
        1. Check idempotency (skip if already ingested)
        2. Load ALL three states per team: home, away, overall
        3. Update ELO (shared ELO pool across venues for same team)
        4. Update venue-specific rolling stats (home scored when home, etc.)
        5. Update OVERALL blended state (powers all predictions)
        6. Store match in history
        7. Recompute form from history
        8. Update fatigue
        9. Persist all six states (2 teams × 3 venues)
       10. Return summary

    Args:
        conn: database connection
        match_id: unique match identifier
        match_date: "YYYY-MM-DD"
        league: league name
        home_team, away_team: team names
        home_goals, away_goals: final score
        home_xg, away_xg: expected goals (optional)
        home_corners, away_corners: corner count (optional)
        home_cards, away_cards: card count (optional)

    Returns:
        dict with update summary, or {"skipped": True} if already ingested
    """
    # ── 1. Idempotency check ──
    existing = conn.execute(
        "SELECT id FROM match_history WHERE match_id = ?",
        (match_id,),
    ).fetchone()

    if existing:
        logger.debug(f"Match {match_id} already ingested, skipping")
        return {"skipped": True, "match_id": match_id}

    # ── 1b. Which record each team's result belongs to ──
    home_team, _home_how = resolve_record(conn, home_team, league, provider, home_team_id)
    away_team, _away_how = resolve_record(conn, away_team, league, provider, away_team_id)
    if home_team.lower() == away_team.lower():
        logger.warning(f"Match {match_id}: both sides resolve to {home_team!r} in {league!r}; not ingested")
        return {"skipped": True, "match_id": match_id, "reason": "both sides resolve to one record"}

    # ── 2. Load all states for both teams ──
    # Venue-specific (for granular analysis)
    home_h_state = _load_or_create_state(conn, home_team, league, "home", HOME_DEFAULTS)
    home_a_state = _load_or_create_state(conn, home_team, league, "away", AWAY_DEFAULTS)
    away_h_state = _load_or_create_state(conn, away_team, league, "home", HOME_DEFAULTS)
    away_a_state = _load_or_create_state(conn, away_team, league, "away", AWAY_DEFAULTS)

    # Overall (authoritative — used by get_team_stats → Poisson model)
    home_overall = _load_or_create_state(conn, home_team, league, "overall", OVERALL_DEFAULTS)
    away_overall = _load_or_create_state(conn, away_team, league, "overall", OVERALL_DEFAULTS)

    # ── 3. ELO update (use shared ELO from 'overall' pool) ──
    elo_home_before = home_overall.elo
    elo_away_before = away_overall.elo

    new_elo_home, new_elo_away = _update_elo(
        home_overall.elo, away_overall.elo, home_goals, away_goals
    )

    # Propagate shared ELO to all venue states
    for state in [home_h_state, home_a_state, home_overall]:
        state.elo = new_elo_home
    for state in [away_h_state, away_a_state, away_overall]:
        state.elo = new_elo_away

    # ── 4. Update venue-specific rolling stats ──
    # Home team played at home → update their home-venue stats
    home_h_state.rolling_scored   = _update_rolling(home_h_state.rolling_scored, home_goals)
    home_h_state.rolling_conceded = _update_rolling(home_h_state.rolling_conceded, away_goals)
    if home_corners is not None:
        home_h_state.rolling_corners = _update_rolling(home_h_state.rolling_corners, home_corners)
    if home_cards is not None:
        home_h_state.rolling_cards = _update_rolling(home_h_state.rolling_cards, home_cards)

    # Away team played away → update their away-venue stats
    away_a_state.rolling_scored   = _update_rolling(away_a_state.rolling_scored, away_goals)
    away_a_state.rolling_conceded = _update_rolling(away_a_state.rolling_conceded, home_goals)
    if away_corners is not None:
        away_a_state.rolling_corners = _update_rolling(away_a_state.rolling_corners, away_corners)
    if away_cards is not None:
        away_a_state.rolling_cards = _update_rolling(away_a_state.rolling_cards, away_cards)

    # xG (venue-specific + overall)
    if home_xg is not None:
        home_h_state.rolling_xg  = _update_rolling(home_h_state.rolling_xg, home_xg)
        away_a_state.rolling_xga = _update_rolling(away_a_state.rolling_xga, home_xg)
    if away_xg is not None:
        away_a_state.rolling_xg  = _update_rolling(away_a_state.rolling_xg, away_xg)
        home_h_state.rolling_xga = _update_rolling(home_h_state.rolling_xga, away_xg)

    # ── 5. Update OVERALL blended state ──
    # For prediction purposes, overall = blend of home and away observations
    home_overall.rolling_scored   = _update_rolling(home_overall.rolling_scored, home_goals)
    home_overall.rolling_conceded = _update_rolling(home_overall.rolling_conceded, away_goals)
    away_overall.rolling_scored   = _update_rolling(away_overall.rolling_scored, away_goals)
    away_overall.rolling_conceded = _update_rolling(away_overall.rolling_conceded, home_goals)

    total_corners = (home_corners or 0) + (away_corners or 0)
    if home_corners is not None:
        home_overall.rolling_corners = _update_rolling(home_overall.rolling_corners, home_corners)
    if away_corners is not None:
        away_overall.rolling_corners = _update_rolling(away_overall.rolling_corners, away_corners)

    if home_cards is not None:
        home_overall.rolling_cards = _update_rolling(home_overall.rolling_cards, home_cards)
    if away_cards is not None:
        away_overall.rolling_cards = _update_rolling(away_overall.rolling_cards, away_cards)

    if home_xg is not None:
        home_overall.rolling_xg  = _update_rolling(home_overall.rolling_xg, home_xg)
        away_overall.rolling_xga = _update_rolling(away_overall.rolling_xga, home_xg)
    if away_xg is not None:
        away_overall.rolling_xg  = _update_rolling(away_overall.rolling_xg, away_xg)
        home_overall.rolling_xga = _update_rolling(home_overall.rolling_xga, away_xg)

    # ── 6. Store match in history ──
    conn.execute(
        """INSERT INTO match_history
               (match_id, match_date, league, home_team, away_team,
                home_goals, away_goals, home_xg, away_xg,
                home_corners, away_corners, home_cards, away_cards,
                home_elo_before, away_elo_before, home_elo_after, away_elo_after)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (match_id, match_date, league, home_team, away_team,
         home_goals, away_goals, home_xg, away_xg,
         home_corners, away_corners, home_cards, away_cards,
         elo_home_before, elo_away_before, new_elo_home, new_elo_away),
    )
    conn.commit()

    # ── 7. Recompute form from full history ──
    home_results = _get_recent_results(conn, home_team, 10)
    away_results = _get_recent_results(conn, away_team, 10)

    _update_form_and_streaks(home_h_state,  home_results)
    _update_form_and_streaks(home_a_state,  home_results)
    _update_form_and_streaks(home_overall,  home_results)
    _update_form_and_streaks(away_h_state,  away_results)
    _update_form_and_streaks(away_a_state,  away_results)
    _update_form_and_streaks(away_overall,  away_results)

    # ── 8. Update fatigue ──
    home_matches_14d = _count_matches_last_14d(conn, home_team, match_date)
    away_matches_14d = _count_matches_last_14d(conn, away_team, match_date)
    home_rest = _compute_rest_days(conn, home_team, match_date)
    away_rest = _compute_rest_days(conn, away_team, match_date)

    for state in [home_h_state, home_a_state, home_overall]:
        state.matches_last_14d = home_matches_14d
        state.rest_days = home_rest
        state.matches_played += 1
        state.last_match_date = match_date
        state.last_match_id = match_id

    for state in [away_h_state, away_a_state, away_overall]:
        state.matches_last_14d = away_matches_14d
        state.rest_days = away_rest
        state.matches_played += 1
        state.last_match_date = match_date
        state.last_match_id = match_id

    # ── 9. Persist all six states ──
    for state in [home_h_state, home_a_state, home_overall,
                  away_h_state, away_a_state, away_overall]:
        upsert_team_state(conn, state)

    logger.info(
        f"✅ Ingested {home_team} {home_goals}-{away_goals} {away_team} "
        f"| ELO: {elo_home_before:.0f}→{new_elo_home:.0f} / "
        f"{elo_away_before:.0f}→{new_elo_away:.0f} "
        f"| Form: H={home_overall.form_last5:.2f} A={away_overall.form_last5:.2f}"
    )

    return {
        "skipped": False,
        "match_id": match_id,
        "score": f"{home_goals}-{away_goals}",
        "elo_changes": {
            "home": {"before": elo_home_before, "after": new_elo_home,
                     "change": round(new_elo_home - elo_home_before, 1)},
            "away": {"before": elo_away_before, "after": new_elo_away,
                     "change": round(new_elo_away - elo_away_before, 1)},
        },
        "rolling_scored": {
            "home": home_overall.rolling_scored,
            "away": away_overall.rolling_scored,
        },
        "form": {
            "home_last5": home_overall.form_last5,
            "away_last5": away_overall.form_last5,
        },
    }


def ingest_day_results(conn: sqlite3.Connection, matches: list[dict]) -> dict:
    """Batch-ingest all finished matches from a day.

    Each match dict should have:
        match_id, match_date, league, home_team, away_team,
        home_goals, away_goals, (optional: xg, corners, cards)

    Returns summary with count of ingested/skipped.
    """
    ingested = 0
    skipped = 0

    for m in matches:
        result = on_match_finished(
            conn=conn,
            match_id=m["match_id"],
            match_date=m["match_date"],
            league=m.get("league", "Unknown"),
            home_team=m["home_team"],
            away_team=m["away_team"],
            home_goals=m["home_goals"],
            away_goals=m["away_goals"],
            home_xg=m.get("home_xg"),
            away_xg=m.get("away_xg"),
            home_corners=m.get("home_corners"),
            away_corners=m.get("away_corners"),
            home_cards=m.get("home_cards"),
            away_cards=m.get("away_cards"),
            provider=m.get("provider"),
            home_team_id=m.get("home_team_id"),
            away_team_id=m.get("away_team_id"),
        )

        if result.get("skipped"):
            skipped += 1
        else:
            ingested += 1

    logger.info(f"Day ingestion complete: {ingested} ingested, {skipped} skipped")
    return {"ingested": ingested, "skipped": skipped, "total": len(matches)}


def get_ingestion_stats(conn: sqlite3.Connection) -> dict:
    """Get summary statistics about the match history database."""
    total = conn.execute("SELECT COUNT(*) FROM match_history").fetchone()[0]
    teams = conn.execute(
        "SELECT COUNT(DISTINCT team_name_lower) FROM team_state"
    ).fetchone()[0]
    latest = conn.execute(
        "SELECT MAX(match_date) FROM match_history"
    ).fetchone()[0]
    leagues = conn.execute(
        "SELECT DISTINCT league FROM match_history ORDER BY league"
    ).fetchall()

    return {
        "total_matches": total,
        "tracked_teams": teams,
        "latest_match": latest,
        "leagues": [r[0] for r in leagues],
    }

"""
Results & Pipeline Service.

Provides business logic for result verification, evaluation against historical picks,
and executing the investment pipeline.
"""

import math
import re
import json
import logging
import ssl
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import BackgroundTasks, HTTPException
from src.config import logger, APIFOOTBALL_API_KEY, APIFOOTBALL_HOST
from src.db.database import get_db
from src.db.picks_repo import (
    get_picks_by_date,
    get_unsettled_picks,
    settle_pick,
    get_portfolio_summary,
    get_league_pnl,
)
from src.engine.pipeline import run_pipeline as _run_pipeline
from api.services.match_analysis_service import _compute_match_analysis
from api.services.fixtures_service import (
    _read_fixture_cache,
    _fetch_sofascore_fixtures,
    _sofascore_to_fixture,
    _get_istanbul_today,
    get_fixtures_by_date_service as get_fixtures_by_date,
    _coerce_fixture_list,
)

# ── Results Verification ──────────────────────────

def _fetch_event_statistics(event_id: str) -> dict:
    """Fetch match statistics (corners, cards) from SofaScore or API-Football.

    Falls back to an empty dict on error.
    """
    # ── 1. Mock intercept ──
    if str(event_id).startswith("999"):
        import hashlib
        def get_hash_num(s: str, mod: int) -> int:
            return int(hashlib.md5(s.encode("utf-8")).hexdigest(), 16) % mod
        
        home_corners = get_hash_num(f"stats-corn-h-{event_id}", 7) + 2
        away_corners = get_hash_num(f"stats-corn-a-{event_id}", 6) + 1
        home_yellow = get_hash_num(f"stats-yel-h-{event_id}", 3)
        away_yellow = get_hash_num(f"stats-yel-a-{event_id}", 3)
        home_red = 1 if get_hash_num(f"stats-red-h-{event_id}", 20) == 0 else 0
        away_red = 1 if get_hash_num(f"stats-red-a-{event_id}", 20) == 0 else 0
        
        home_cards = home_yellow + home_red
        away_cards = away_yellow + away_red
        
        return {
            "corners":      home_corners + away_corners,
            "home_corners": home_corners,
            "away_corners": away_corners,
            "cards":        home_cards + away_cards,
            "home_cards":   home_cards,
            "away_cards":   away_cards,
            "yellow_cards": home_yellow + away_yellow,
            "red_cards":    home_red + away_red,
        }

    # ── 2. SofaScore Fetch (Primary) ──
    from curl_cffi import requests
    url = f"https://api.sofascore.com/api/v1/event/{event_id}/statistics"
    headers = {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.sofascore.com/",
        "Origin": "https://www.sofascore.com",
    }
    try:
        resp = requests.get(url, headers=headers, impersonate="chrome", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            periods = data.get("statistics", [])
            all_period = None
            for p in periods:
                if p.get("period") == "ALL":
                    all_period = p
                    break
            if all_period:
                home_corners = 0
                away_corners = 0
                home_yellow = 0
                away_yellow = 0
                home_red = 0
                away_red = 0
                
                for group in all_period.get("groups", []):
                    for item in group.get("statisticsItems", []):
                        name = item.get("name", "").lower()
                        home_val = item.get("home")
                        away_val = item.get("away")
                        
                        def _to_int(val) -> int:
                            if val is None: return 0
                            try:
                                return int(str(val).replace("%", ""))
                            except Exception:
                                return 0
                                
                        if "corner" in name:
                            home_corners = _to_int(home_val)
                            away_corners = _to_int(away_val)
                        elif "yellow card" in name:
                            home_yellow = _to_int(home_val)
                            away_yellow = _to_int(away_val)
                        elif "red card" in name:
                            home_red = _to_int(home_val)
                            away_red = _to_int(away_val)
                            
                home_cards = home_yellow + home_red
                away_cards = away_yellow + away_red
                
                return {
                    "corners":      home_corners + away_corners,
                    "home_corners": home_corners,
                    "away_corners": away_corners,
                    "cards":        home_cards + away_cards,
                    "home_cards":   home_cards,
                    "away_cards":   away_cards,
                    "yellow_cards": home_yellow + away_yellow,
                    "red_cards":    home_red + away_red,
                }
    except Exception as e:
        logger.warning(f"SofaScore stats fetch failed for {event_id}: {e}")

    # ── 3. API-Football Fallback ──
    if APIFOOTBALL_API_KEY:
        url_api = f"https://v3.football.api-sports.io/fixtures/statistics?fixture={event_id}"
        req_api = urllib.request.Request(url_api, headers={
            "x-apisports-key": APIFOOTBALL_API_KEY,
            "Accept": "application/json",
        })
        try:
            ctx = ssl.create_default_context()
            resp_api = urllib.request.urlopen(req_api, timeout=10, context=ctx)
            data_api = json.loads(resp_api.read())
            statistics = data_api.get("response", [])
            if len(statistics) >= 2:
                def _parse_team_stats(team_stats_list: list) -> dict:
                    out = {}
                    for item in team_stats_list:
                        key = item.get("type", "").lower().replace(" ", "_")
                        val = item.get("value")
                        try:
                            out[key] = int(str(val).replace("%", "")) if val is not None else 0
                        except (ValueError, TypeError):
                            out[key] = 0
                    return out
                home_s = _parse_team_stats(statistics[0].get("statistics", []))
                away_s = _parse_team_stats(statistics[1].get("statistics", []))
                
                home_corners = home_s.get("corner_kicks", 0)
                away_corners = away_s.get("corner_kicks", 0)
                home_yellow  = home_s.get("yellow_cards", 0)
                away_yellow  = away_s.get("yellow_cards", 0)
                home_red     = home_s.get("red_cards", 0)
                away_red     = away_s.get("red_cards", 0)
                
                home_cards = home_yellow + home_red
                away_cards = away_yellow + away_red
                
                return {
                    "corners":      home_corners + away_corners,
                    "home_corners": home_corners,
                    "away_corners": away_corners,
                    "cards":        home_cards + away_cards,
                    "home_cards":   home_cards,
                    "away_cards":   away_cards,
                    "yellow_cards": home_yellow + away_yellow,
                    "red_cards":    home_red + away_red,
                }
        except Exception as e_api:
            logger.warning(f"API-Football stats fallback failed for {event_id}: {e_api}")

    return {}


def _evaluate_prediction(pick: dict, home_name: str, away_name: str, home_goals: int, away_goals: int,
                          fh_home_goals: Optional[int], fh_away_goals: Optional[int],
                          stats: dict) -> dict:
    """
    Evaluate a single predicted market against actual match results.
    Returns the pick dict enriched with 'result': True/False/None.
    """
    market = pick.get("market", "")
    total_goals = home_goals + away_goals
    result = None

    # Corners & Cards from stats dictionary
    total_corners = stats.get("corners")
    home_corners = stats.get("home_corners")
    away_corners = stats.get("away_corners")
    total_cards = stats.get("cards")
    home_cards = stats.get("home_cards")
    away_cards = stats.get("away_cards")

    # ── Goals markets ──
    if market == "Over 0.5 Goals": result = total_goals > 0.5
    elif market == "Under 0.5 Goals": result = total_goals < 0.5
    elif market == "Over 1.5 Goals": result = total_goals > 1.5
    elif market == "Under 1.5 Goals": result = total_goals < 1.5
    elif market == "Over 2.5 Goals": result = total_goals > 2.5
    elif market == "Under 2.5 Goals": result = total_goals < 2.5
    elif market == "Over 3.5 Goals": result = total_goals > 3.5
    elif market == "Under 3.5 Goals": result = total_goals < 3.5
    elif market == "Over 4.5 Goals": result = total_goals > 4.5
    elif market == "Under 4.5 Goals": result = total_goals < 4.5
    elif market == "BTTS - Yes": result = home_goals > 0 and away_goals > 0
    elif market == "BTTS - No": result = not (home_goals > 0 and away_goals > 0)

    # ── Advanced Goals ──
    for g in range(7):
        if market == f"Exact Total Goals: {g}": result = total_goals == g
    if market == "Goal Range 0-1": result = total_goals <= 1
    elif market == "Goal Range 2-3": result = 2 <= total_goals <= 3
    elif market == "Goal Range 4+": result = total_goals >= 4
    elif market == "Odd/Even Total Goals: Odd": result = total_goals % 2 == 1
    elif market == "Odd/Even Total Goals: Even": result = total_goals % 2 == 0

    # ── Result & Double Chance ──
    if market == "Home Win": result = home_goals > away_goals
    elif market == "Draw" and "FH" not in market and "SH" not in market: result = home_goals == away_goals
    elif market == "Away Win": result = away_goals > home_goals
    elif "1X" in market and "FH" not in market and "SH" not in market and "Corner" not in market and "Card" not in market:
        result = home_goals >= away_goals
    elif "X2" in market and "FH" not in market and "SH" not in market and "Corner" not in market and "Card" not in market:
        result = away_goals >= home_goals
    elif "12 " in market and "FH" not in market and "SH" not in market and "Corner" not in market and "Card" not in market:
        result = home_goals != away_goals

    # ── First Half Markets ──
    if fh_home_goals is not None and fh_away_goals is not None:
        fh_total = fh_home_goals + fh_away_goals
        if market == "FH Over 0.5 Goals": result = fh_total > 0.5
        elif market == "FH Under 0.5 Goals": result = fh_total < 0.5
        elif market == "FH Over 1.5 Goals": result = fh_total > 1.5
        elif market == "FH Under 1.5 Goals": result = fh_total < 1.5
        elif market == "FH BTTS - Yes": result = fh_home_goals > 0 and fh_away_goals > 0
        elif market == "FH BTTS - No": result = not (fh_home_goals > 0 and fh_away_goals > 0)
        elif market == "FH Home Win": result = fh_home_goals > fh_away_goals
        elif market == "FH Draw": result = fh_home_goals == fh_away_goals
        elif market == "FH Away Win": result = fh_away_goals > fh_home_goals
        elif "FH 1X" in market: result = fh_home_goals >= fh_away_goals
        elif "FH X2" in market: result = fh_away_goals >= fh_home_goals
        for t in [0, 1]:
            if market == f"FH {home_name} Over {t}.5 Goals": result = fh_home_goals > t
            elif market == f"FH {home_name} Under {t}.5 Goals": result = fh_home_goals <= t
            elif market == f"FH {away_name} Over {t}.5 Goals": result = fh_away_goals > t
            elif market == f"FH {away_name} Under {t}.5 Goals": result = fh_away_goals <= t

    # ── Second Half Markets ──
    if fh_home_goals is not None and fh_away_goals is not None:
        sh_home = home_goals - fh_home_goals
        sh_away = away_goals - fh_away_goals
        sh_total = sh_home + sh_away
        fh_total_eval = fh_home_goals + fh_away_goals
        if market == "SH Home Win": result = sh_home > sh_away
        elif market == "SH Draw": result = sh_home == sh_away
        elif market == "SH Away Win": result = sh_away > sh_home
        elif "SH 1X" in market: result = sh_home >= sh_away
        elif "SH X2" in market: result = sh_away >= sh_home
        elif "SH 12" in market: result = sh_home != sh_away
        elif market == "SH BTTS - Yes": result = sh_home > 0 and sh_away > 0
        elif market == "SH BTTS - No": result = not (sh_home > 0 and sh_away > 0)
        for t in [0, 1, 2]:
            if market == f"SH Over {t}.5 Goals": result = sh_total > t
            elif market == f"SH Under {t}.5 Goals": result = sh_total <= t
        for t in [0, 1]:
            if market == f"SH {home_name} Over {t}.5 Goals": result = sh_home > t
            elif market == f"SH {home_name} Under {t}.5 Goals": result = sh_home <= t
            elif market == f"SH {away_name} Over {t}.5 Goals": result = sh_away > t
            elif market == f"SH {away_name} Under {t}.5 Goals": result = sh_away <= t
        if market == f"SH {home_name} to Score": result = sh_home >= 1
        elif market == f"SH {away_name} to Score": result = sh_away >= 1
        elif market == "SH No Goal": result = sh_total == 0
        # Half comparisons & cross-half markets
        if market == "Half with Most Goals: 1st Half": result = fh_total_eval > sh_total
        elif market == "Half with Most Goals: 2nd Half": result = sh_total > fh_total_eval
        elif market == "Half with Most Goals: Equal": result = fh_total_eval == sh_total
        if market == "Goal in Both Halves - Yes": result = fh_total_eval >= 1 and sh_total >= 1
        elif market == "Goal in Both Halves - No": result = fh_total_eval == 0 or sh_total == 0

    # ── Corners ──
    if "Corner" in market and "FH" not in market and "SH" not in market and "Half" not in market:
        if "Over" in market or "Under" in market:
            if home_name in market and home_corners is not None:
                for c in range(2, 10):
                    if market == f"{home_name} Over {c}.5 Corners": result = home_corners > c
                    if market == f"{home_name} Under {c}.5 Corners": result = home_corners <= c
            elif away_name in market and away_corners is not None:
                for c in range(2, 10):
                    if market == f"{away_name} Over {c}.5 Corners": result = away_corners > c
                    if market == f"{away_name} Under {c}.5 Corners": result = away_corners <= c
            elif "Corners" in market and total_corners is not None:
                for c in range(5, 15):
                    if market == f"Over {c}.5 Corners": result = total_corners > c
                    if market == f"Under {c}.5 Corners": result = total_corners <= c
        elif "1X" in market and total_corners is not None:
            # 1X Corner Double Chance = Home takes >= corners than away.
            if home_corners is not None and away_corners is not None:
                result = home_corners >= away_corners
        elif "X2" in market and total_corners is not None:
            if home_corners is not None and away_corners is not None:
                result = away_corners >= home_corners

    # ── Cards ──
    if "Card" in market and "FH" not in market and "SH" not in market and "Half" not in market:
        if "Over" in market or "Under" in market:
            if home_name in market and home_cards is not None:
                for c in range(0, 6):
                    if market == f"{home_name} Over {c}.5 Cards": result = home_cards > c
                    if market == f"{home_name} Under {c}.5 Cards": result = home_cards <= c
            elif away_name in market and away_cards is not None:
                for c in range(0, 6):
                    if market == f"{away_name} Over {c}.5 Cards": result = away_cards > c
                    if market == f"{away_name} Under {c}.5 Cards": result = away_cards <= c
            elif "Cards" in market and total_cards is not None:
                for c in range(1, 10):
                    if market == f"Over {c}.5 Cards": result = total_cards > c
                    if market == f"Under {c}.5 Cards": result = total_cards <= c
        elif "1X" in market and total_cards is not None:
            if home_cards is not None and away_cards is not None:
                result = home_cards >= away_cards
        elif "X2" in market and total_cards is not None:
            if home_cards is not None and away_cards is not None:
                result = away_cards >= home_cards

    # ── Team Goals ──
    if "Over" in market and "Goals" in market and "FH" not in market and "SH" not in market:
        for t in [0, 1, 2, 3]:
            if market == f"{home_name} Over {t}.5 Goals": result = home_goals > t
            elif market == f"{away_name} Over {t}.5 Goals": result = away_goals > t
    elif "Under" in market and "Goals" in market and "FH" not in market and "SH" not in market:
        for t in [0, 1, 2, 3]:
            if market == f"{home_name} Under {t}.5 Goals": result = home_goals <= t
            elif market == f"{away_name} Under {t}.5 Goals": result = away_goals <= t

    # ── Correct Score ──
    if market.startswith("CS ") and market != "CS Other":
        parts = market[3:].split("-")
        if len(parts) == 2:
            try:
                cs_h, cs_a = int(parts[0]), int(parts[1])
                result = home_goals == cs_h and away_goals == cs_a
            except ValueError:
                pass
    elif market == "CS Other":
        result = home_goals >= 5 or away_goals >= 5

    # ── Winning Margin ──
    if market == f"{home_name} Win by 1": result = home_goals - away_goals == 1
    elif market == f"{home_name} Win by 2": result = home_goals - away_goals == 2
    elif market == f"{home_name} Win by 3+": result = home_goals - away_goals >= 3
    elif market == f"{away_name} Win by 1": result = away_goals - home_goals == 1
    elif market == f"{away_name} Win by 2": result = away_goals - home_goals == 2
    elif market == f"{away_name} Win by 3+": result = away_goals - home_goals >= 3
    elif market == "Exact Draw 0-0": result = home_goals == 0 and away_goals == 0

    # ── Clean Sheet & Fail to Score ──
    if market == f"{home_name} Clean Sheet": result = away_goals == 0
    elif market == f"{away_name} Clean Sheet": result = home_goals == 0
    elif market == f"{home_name} Fails to Score": result = home_goals == 0
    elif market == f"{away_name} Fails to Score": result = away_goals == 0

    # ── Exact Team Goals ──
    for n in range(4):
        if market == f"{home_name} Exact {n} Goals": result = home_goals == n
        elif market == f"{away_name} Exact {n} Goals": result = away_goals == n
    if market == f"{home_name} Exact 3+ Goals": result = home_goals >= 3
    elif market == f"{away_name} Exact 3+ Goals": result = away_goals >= 3

    # ── Result + Goals Combos ──
    if market == "Home Win & Over 1.5": result = home_goals > away_goals and total_goals > 1
    elif market == "Home Win & Over 2.5": result = home_goals > away_goals and total_goals > 2
    elif market == "Home Win & Under 2.5": result = home_goals > away_goals and total_goals <= 2
    elif market == "Away Win & Over 1.5": result = away_goals > home_goals and total_goals > 1
    elif market == "Away Win & Over 2.5": result = away_goals > home_goals and total_goals > 2
    elif market == "Away Win & Under 2.5": result = away_goals > home_goals and total_goals <= 2
    elif market == "Draw & Over 2.5": result = home_goals == away_goals and total_goals > 2
    elif market == "Draw & Under 2.5": result = home_goals == away_goals and total_goals <= 2

    # ── Result + BTTS Combos ──
    btts_actual = home_goals >= 1 and away_goals >= 1
    if market == "Home Win & BTTS": result = home_goals > away_goals and btts_actual
    elif market == "Away Win & BTTS": result = away_goals > home_goals and btts_actual
    elif market == "Draw & BTTS": result = home_goals == away_goals and btts_actual

    # ── BTTS + Goals Combos ──
    if market == "BTTS & Over 2.5": result = btts_actual and total_goals > 2
    elif market == "BTTS & Under 2.5": result = btts_actual and total_goals <= 2

    # ── Score in Both Halves ──
    if fh_home_goals is not None and fh_away_goals is not None:
        sh_home_eval = home_goals - fh_home_goals
        sh_away_eval = away_goals - fh_away_goals
        if market == f"{home_name} Score in Both Halves":
            result = fh_home_goals >= 1 and sh_home_eval >= 1
        elif market == f"{away_name} Score in Both Halves":
            result = fh_away_goals >= 1 and sh_away_eval >= 1

    # ── Asian Handicap ──
    import re
    ah_match = re.match(r'AH (.+?) ([+-]\d+\.5)', market)
    if ah_match:
        team_name = ah_match.group(1)
        spread = float(ah_match.group(2))
        if team_name == home_name:
            adjusted_diff = home_goals + spread - away_goals
        elif team_name == away_name:
            adjusted_diff = away_goals + spread - home_goals
        else:
            adjusted_diff = None
        if adjusted_diff is not None:
            # Positive spread (+X): team gets advantage
            # Negative spread (-X): team gives advantage
            result = adjusted_diff > 0

    # ── European Handicap ──
    eh_match = re.match(r'EH (.+?) (-\d+) \((Win|Draw|Lose)\)', market)
    if eh_match:
        team_name = eh_match.group(1)
        spread = int(eh_match.group(2))
        outcome = eh_match.group(3)
        if team_name == home_name:
            adj_home = home_goals + spread
            adj_away = away_goals
        elif team_name == away_name:
            adj_home = home_goals
            adj_away = away_goals + spread
        else:
            adj_home = adj_away = None
        if adj_home is not None:
            if team_name == home_name:
                if outcome == 'Win': result = adj_home > adj_away
                elif outcome == 'Draw': result = adj_home == adj_away
                elif outcome == 'Lose': result = adj_home < adj_away
            else:
                if outcome == 'Win': result = adj_away > adj_home
                elif outcome == 'Draw': result = adj_away == adj_home
                elif outcome == 'Lose': result = adj_away < adj_home

    return {
        **pick,
        "result": result,
    }


def _is_match_result_audit_market(market: str) -> bool:
    """Markets counted in the Results audit's match-result bucket."""
    m = (market or "").lower()
    return (
        m in {"home win", "draw", "away win", "12 (any team to win)"}
        or m.startswith("1x ")
        or m.startswith("x2 ")
    )


def get_results_verification_service(date_str: str, background_tasks: BackgroundTasks):
    """
    For all finished matches on a given date, regenerate predictions
    and compare them against actual results.

    CLEAN EVALUATION UNIVERSE:
    - Only settled picks (result = True/False) count in stats
    - Leagues with >25% NA matches are excluded entirely
    - Invariant: correct + wrong == total_picks (always)
    """
    # ── Use cached/live API-Football fixtures (same source as daily matches) ──
    # First try the .cache file (populated by get_fixtures_by_date).
    # If not cached, fetch live from API-Football now.
    try:
        all_fixtures = get_fixtures_by_date(date_str, background_tasks)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    all_fixtures = _coerce_fixture_list(all_fixtures)

    if not all_fixtures:
        return {"date": date_str, "matches": [], "summary": {}}

    # ── Phase 1: Build raw results per match ────────────────────
    raw_results = []
    seen_ids = set()

    for fixture in all_fixtures:
        status_str = str(fixture.get("status", ""))
        is_finished = status_str in ("FT", "AET", "PEN")
        
        if not is_finished:
            continue

        if fixture["id"] in seen_ids:
            continue
        seen_ids.add(fixture["id"])

        home_goals = fixture.get("home_goals")
        away_goals = fixture.get("away_goals")

        if home_goals is None or away_goals is None:
            continue

        home_name = fixture["home_team"]["name"]
        away_name = fixture["away_team"]["name"]
        league_name = fixture["league"]["name"]
        event_id = fixture["id"]

        fh_home_goals = fixture.get("fh_home_goals")
        fh_away_goals = fixture.get("fh_away_goals")
        stats = _fetch_event_statistics(event_id)
        
        # Regenerate predictions for this match
        try:
            analysis = _compute_match_analysis(home_name, away_name, league_name, shuffle_tiers=False)
            tier_layers = analysis.get("tiers", [])
        except Exception as e:
            logger.warning(f"Could not compute analysis for {home_name} vs {away_name}: {e}")
            continue

        # Evaluate the same rank-based tier universe shown in Predictions.
        all_evaluated_picks = []
        evaluated_tiers = []
        category_pick_map = {
            "Result": [],
            "Goals": [],
            "Team Goals": [],
            "Handicaps": [],
        }

        for tier in tier_layers:
            tier_picks_evaluated = []
            for pick in tier.get("picks", []):
                evaluated = _evaluate_prediction(pick, home_name, away_name, home_goals, away_goals, fh_home_goals, fh_away_goals, stats)
                evaluated["isSettled"] = evaluated["result"] is not None
                evaluated["isValidForEvaluation"] = evaluated["result"] is not None
                evaluated["category"] = evaluated.get("section")
                evaluated["tier"] = tier.get("name")
                evaluated["tier_id"] = tier.get("id")
                evaluated["tier_rank"] = evaluated.get("tier_rank")
                tier_picks_evaluated.append(evaluated)
                all_evaluated_picks.append(evaluated)
                if evaluated.get("section") == "Result" and not _is_match_result_audit_market(evaluated.get("market")):
                    pass
                elif evaluated.get("section") in category_pick_map:
                    category_pick_map[evaluated["section"]].append(evaluated)

            settled = [p for p in tier_picks_evaluated if p["isSettled"]]
            correct = sum(1 for p in settled if p["result"] is True)
            wrong = sum(1 for p in settled if p["result"] is False)

            evaluated_tiers.append({
                "id": tier.get("id"),
                "name": tier.get("name"),
                "label": tier.get("label"),
                "range": tier.get("range"),
                "picks": tier_picks_evaluated,
                "summary": {
                    "correct": correct,
                    "wrong": wrong,
                    "settled": len(settled),
                    "unsettled": len(tier_picks_evaluated) - len(settled),
                    "accuracy": round(correct / len(settled) * 100, 1) if len(settled) > 0 else 0,
                },
            })

        evaluated_categories = []
        for category_name, cat_picks_evaluated in category_pick_map.items():
            settled = [p for p in cat_picks_evaluated if p["isSettled"]]
            correct = sum(1 for p in settled if p["result"] is True)
            wrong = sum(1 for p in settled if p["result"] is False)

            evaluated_categories.append({
                "category": category_name,
                "picks": cat_picks_evaluated,
                "summary": {
                    "correct": correct,
                    "wrong": wrong,
                    "settled": len(settled),
                    "unsettled": len(cat_picks_evaluated) - len(settled),
                    "accuracy": round(correct / len(settled) * 100, 1) if len(settled) > 0 else 0,
                },
            })

        raw_results.append({
            "fixture": fixture,
            "league_name": league_name,
            "actual": {
                "home_goals": home_goals,
                "away_goals": away_goals,
                "fh_home_goals": fh_home_goals,
                "fh_away_goals": fh_away_goals,
                "total_goals": home_goals + away_goals,
                "total_corners": stats.get("corners"),
                "total_cards": stats.get("cards"),
                "yellow_cards": stats.get("yellow_cards"),
                "red_cards": stats.get("red_cards"),
            },
            "categories": evaluated_categories,
            "tiers": evaluated_tiers,
            "picks": all_evaluated_picks,
        })

        # ── Log ALL markets (not just tiered picks) for unbiased calibration ──
        # This is critical: calibration quality depends on the full probability
        # distribution, not just the top-36 filtered picks.
        try:
            from src.db.prediction_logger import log_predictions
            db = get_db()

            # Evaluate ALL markets from full_analysis
            full_analysis = analysis.get("full_analysis", {})
            all_markets_evaluated = []
            for section_name, section_markets in full_analysis.items():
                for market_item in section_markets:
                    evaluated_full = _evaluate_prediction(
                        market_item, home_name, away_name,
                        home_goals, away_goals, fh_home_goals, fh_away_goals, stats
                    )
                    # Check if this market was in a tier
                    tier_num = None
                    for ep in all_evaluated_picks:
                        if ep.get("market") == market_item.get("market"):
                            tier_num = ep.get("tier")
                            break
                    evaluated_full["tier"] = tier_num
                    all_markets_evaluated.append(evaluated_full)

            log_predictions(
                db, event_id, date_str, home_name, away_name, league_name,
                all_markets_evaluated,
            )
        except Exception as log_err:
            logger.warning(f"Prediction logging failed for {event_id}: {log_err}")

        # ── Phase 1.5: LIVE PIPELINE — Feed result back into team state ──
        # This is what makes the system adaptive: each finished match
        # updates ELO, rolling averages, and form metrics.
        try:
            from src.engine.live_updater import on_match_finished
            db = get_db()

            on_match_finished(
                conn=db,
                match_id=str(event_id),
                match_date=date_str,
                league=league_name,
                home_team=home_name,
                away_team=away_name,
                home_goals=home_goals,
                away_goals=away_goals,
                home_corners=stats.get("home_corners"),
                away_corners=stats.get("away_corners"),
                home_cards=stats.get("home_cards"),
                away_cards=stats.get("away_cards"),
            )
        except Exception as live_err:
            logger.warning(f"Live ingestion failed for {event_id}: {live_err}")

        # ── Phase 1.6: ERROR INTELLIGENCE — settle 1X2 prediction ──
        try:
            from src.db.error_intelligence import store_prediction_record, settle_prediction
            db = get_db()
            # Get 1X2 probs from the fresh analysis
            result_mkt = analysis.get("prediction", {}).get("result", {})
            h_pct = float(result_mkt.get("home_win", 33.0))
            d_pct = float(result_mkt.get("draw", 33.0))
            a_pct = float(result_mkt.get("away_win", 33.0))
            country = fixture.get("league", {}).get("country", "")
            # Upsert the prediction record (safe if already stored from UI click)
            store_prediction_record(
                conn=db,
                fixture_id=str(event_id),
                match_date=date_str,
                league_name=league_name,
                country=country,
                home_team=home_name,
                away_team=away_name,
                home_win_pct=h_pct,
                draw_pct=d_pct,
                away_win_pct=a_pct,
            )
            # Settle it with the actual goals
            pred_h = analysis.get("prediction", {}).get("home_goals", None)
            pred_a = analysis.get("prediction", {}).get("away_goals", None)
            settle_prediction(
                conn=db,
                fixture_id=str(event_id),
                home_goals=home_goals,
                away_goals=away_goals,
                predicted_home_goals=float(pred_h) if pred_h is not None else None,
                predicted_away_goals=float(pred_a) if pred_a is not None else None,
            )
        except Exception as ei_err:
            logger.debug(f"Error intelligence settlement skipped for {event_id}: {ei_err}")

    # ── Phase 2: League-level quality filter ────────────────────
    # Group by league, exclude leagues with >25% NA matches
    league_stats = {}
    for match in raw_results:
        league = match["league_name"]
        if league not in league_stats:
            league_stats[league] = {"total_matches": 0, "na_matches": 0}
        league_stats[league]["total_matches"] += 1
        # A match is "NA" if all its picks are unsettled
        settled_count = sum(1 for p in match["picks"] if p["isSettled"])
        if settled_count == 0 and len(match["picks"]) > 0:
            league_stats[league]["na_matches"] += 1

    excluded_leagues = set()
    league_quality = {}
    for league, stats_data in league_stats.items():
        total = stats_data["total_matches"]
        na = stats_data["na_matches"]
        na_rate = na / total if total > 0 else 0
        is_excluded = na_rate > 0.25
        league_quality[league] = {
            "total_matches": total,
            "na_matches": na,
            "na_rate": round(na_rate * 100, 1),
            "excluded": is_excluded,
        }
        if is_excluded:
            excluded_leagues.add(league)

    # ── Phase 3: Build clean evaluation results ─────────────────
    clean_results = []
    total_correct = 0
    total_wrong = 0
    total_settled_picks = 0
    total_na_excluded = 0

    # Per-category global accumulators
    category_global = {
        "Result": {"correct": 0, "wrong": 0, "settled": 0},
        "Goals": {"correct": 0, "wrong": 0, "settled": 0},
        "Team Goals": {"correct": 0, "wrong": 0, "settled": 0},
        "Handicaps": {"correct": 0, "wrong": 0, "settled": 0},
    }

    tier_global = {
        "tier1": {"tier": "Tier 1", "label": "Top Ranked Group", "correct": 0, "wrong": 0, "settled": 0, "order": 1},
        "tier2": {"tier": "Tier 2", "label": "Second Ranked Group", "correct": 0, "wrong": 0, "settled": 0, "order": 2},
        "tier3": {"tier": "Tier 3", "label": "Third Ranked Group", "correct": 0, "wrong": 0, "settled": 0, "order": 3},
    }

    for match in raw_results:
        league = match["league_name"]
        league_excluded = league in excluded_leagues

        settled_picks = [p for p in match["picks"] if p["isSettled"]]
        na_picks = [p for p in match["picks"] if not p["isSettled"]]

        if league_excluded:
            total_na_excluded += len(match["picks"])
            continue

        match_correct = sum(1 for p in settled_picks if p["result"] is True)
        match_wrong = sum(1 for p in settled_picks if p["result"] is False)

        # Skip matches that have 0/0 hits
        if len(settled_picks) == 0:
            continue

        total_correct += match_correct
        total_wrong += match_wrong
        total_settled_picks += len(settled_picks)
        total_na_excluded += len(na_picks)

        # Accumulate per-category global stats
        for cat_data in match.get("categories", []):
            cat_name = cat_data["category"]
            if cat_name in category_global:
                category_global[cat_name]["correct"] += cat_data["summary"]["correct"]
                category_global[cat_name]["wrong"] += cat_data["summary"]["wrong"]
                category_global[cat_name]["settled"] += cat_data["summary"]["settled"]

        # Accumulate per-tier global stats from the actual rank tiers.
        for p in settled_picks:
            tier_id = p.get("tier_id")
            if tier_id not in tier_global:
                continue
            tier_global[tier_id]["settled"] += 1
            if p["result"] is True:
                tier_global[tier_id]["correct"] += 1
            elif p["result"] is False:
                tier_global[tier_id]["wrong"] += 1

        clean_results.append({
            "fixture": match["fixture"],
            "actual": match["actual"],
            "categories": match.get("categories", []),
            "tiers": match.get("tiers", []),
            "picks": match["picks"],
            "summary": {
                "correct": match_correct,
                "wrong": match_wrong,
                "unknown": len(na_picks),
                "total": len(settled_picks),
            },
        })

    # ── Phase 4: overall summary + per-category summary ─────────────
    accuracy = round(
        (total_correct / total_settled_picks * 100), 1
    ) if total_settled_picks > 0 else 0.0

    category_summary = []
    for cat_name in ["Result", "Goals", "Team Goals", "Handicaps"]:
        cg = category_global[cat_name]
        cat_acc = round(cg["correct"] / cg["settled"] * 100, 1) if cg["settled"] > 0 else 0
        category_summary.append({
            "category": cat_name,
            "correct": cg["correct"],
            "wrong": cg["wrong"],
            "settled": cg["settled"],
            "accuracy": cat_acc,
        })

    tier_summary = []
    for tier_id, tg in tier_global.items():
        t_acc = round(tg["correct"] / tg["settled"] * 100, 1) if tg["settled"] > 0 else 0
        tier_summary.append({
            "id": tier_id,
            "tier": tg["tier"],
            "label": tg["label"],
            "correct": tg["correct"],
            "wrong": tg["wrong"],
            "settled": tg["settled"],
            "accuracy": t_acc,
            "order": tg["order"],
        })
    tier_summary.sort(key=lambda x: x["order"])

    # ── Update daily performance stats ──
    try:
        from src.db.prediction_logger import update_daily_performance
        db = get_db()
        update_daily_performance(db, date_str)
    except Exception as perf_err:
        logger.warning(f"Daily performance update failed: {perf_err}")

    return {
        "date": date_str,
        "matches": clean_results,
        "summary": {
            "total_matches": len(clean_results),
            "total_picks": total_settled_picks,
            "total_correct": total_correct,
            "total_wrong": total_wrong,
            "total_unknown": 0,
            "accuracy_pct": accuracy,
            "na_excluded": total_na_excluded,
            "leagues_excluded": len(excluded_leagues),
        },
        "category_summary": category_summary,
        "tier_summary": tier_summary,
        "league_quality": league_quality,
    }



# ═══════════════════════════════════════════════════════════════════════
# Investment Engine Endpoints
# ═══════════════════════════════════════════════════════════════════════



def run_investment_pipeline_service(date_str: str):
    """Run the full investment pipeline for a date → returns graded picks."""
    fixtures = _read_fixture_cache(date_str)
    if not fixtures:
        raw_api = _fetch_sofascore_fixtures(date_str)
        if not raw_api:
            return {"date": date_str, "error": "No events found", "picks": []}
        fixtures = [_sofascore_to_fixture(f) for f in raw_api]

    conn = get_db()
    result = _run_pipeline(date_str, fixtures, conn)
    return result




# ═══════════════════════════════════════════════════════════════════════

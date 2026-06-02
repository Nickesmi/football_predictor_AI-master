"""
Football Predictor AI - API v5.0
Fetches REAL daily matches from SofaScore. Computes per-match unique predictions
using Poisson (goals) + statistical models (corners, cards).

COVERS: Premier League, La Liga, Serie A, Bundesliga, Ligue 1, UCL, UEL
"""

from __future__ import annotations

import json
import math
import urllib.request
from datetime import date, datetime, timezone
from typing import Optional, Union
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from src.config import logger, APIFOOTBALL_API_KEY
from src.data.api_football_fetcher import APIFootballFetcher
from src.data.fixtures_fetcher import fetch_fixtures_for_date
from src.db.database import get_db, get_db_debug_info, get_match_history_date_coverage
from src.db.daily_repo import insert_prediction, get_predictions_for_date, has_prediction, insert_result, get_performance_summary
from src.db.odds_repo import get_latest_odds, get_odds_for_match
from src.data.odds_fetcher import fetch_and_store_odds
from src.processing.pattern_analyzer import PatternAnalyzer
from src.processing.factor_analyzer import FactorAnalyzer
from src.reporting.report_formatter import ReportFormatter
from src.processing.value_detector import ValueDetector
from src.ml.predictor import XGBoostPredictor
from src.ml.poisson_model import PoissonGoalModel
from src.ml.team_stats_db import get_team_stats
from src.ml.feature_builder import TeamProfile

app = FastAPI(title="Football Predictor AI", version="5.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pipeline components
fetcher = APIFootballFetcher()
pattern_analyzer = PatternAnalyzer()
factor_analyzer = FactorAnalyzer()
value_detector = ValueDetector()
xgb_predictor = XGBoostPredictor()

# ── Top leagues (SofaScore uniqueTournament IDs) ─────────
TOP_LEAGUES = {
    17: "Premier League",
    8: "LaLiga",
    23: "Serie A",
    35: "Bundesliga",
    34: "Ligue 1",
    7: "Champions League",
    679: "Europa League",
    37: "Eredivisie",
    238: "Primeira Liga",
    238: "Primeira Liga",
}

# Per-date in-memory cache for daily matches
_daily_matches_cache: dict[str, dict] = {}


def _latest_date_with_matches(conn) -> Optional[str]:
    row = conn.execute(
        """SELECT date FROM matches GROUP BY date ORDER BY date DESC LIMIT 1"""
    ).fetchone()
    if row and row[0]:
        return row[0]
    history = conn.execute(
        """SELECT name FROM sqlite_master WHERE type='table' AND name='match_history'"""
    ).fetchone()
    if not history:
        return None
    row = conn.execute(
        """SELECT match_date FROM match_history
           WHERE match_date IS NOT NULL AND match_date != ''
           GROUP BY match_date ORDER BY match_date DESC LIMIT 1"""
    ).fetchone()
    return row[0] if row else None


def _most_likely_score(lambda_home: float, lambda_away: float, max_goals: int = 8) -> str:
    from src.ml.poisson_model import _poisson_pmf

    best_h, best_a = 0, 0
    best_p = -1.0
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            prob = _poisson_pmf(h, lambda_home) * _poisson_pmf(a, lambda_away)
            if prob > best_p:
                best_p = prob
                best_h, best_a = h, a
    return f"{best_h}-{best_a}"


def _build_prediction_summary(analysis: dict) -> dict:
    poisson = analysis.get("poisson") or {}
    result = poisson.get("result") or {}
    btts = poisson.get("btts") or {}
    goals_markets = {
        item.get("market"): item.get("probability")
        for item in poisson.get("goals_markets", [])
        if isinstance(item, dict)
    }

    hw = float(result.get("home_win") or poisson.get("home_win") or 0)
    dr = float(result.get("draw") or poisson.get("draw") or 0)
    aw = float(result.get("away_win") or poisson.get("away_win") or 0)
    lh = float(poisson.get("lambda_home") or 0)
    la = float(poisson.get("lambda_away") or 0)

    outcome_probs = [("Home Win", hw), ("Draw", dr), ("Away Win", aw)]
    predicted_result = max(outcome_probs, key=lambda item: item[1])[0]
    confidence = max(hw, dr, aw)
    predicted_score = _most_likely_score(lh, la) if (lh or la) else f"{round(lh)}-{round(la)}"

    return {
        "home_win": round(hw, 1),
        "draw": round(dr, 1),
        "away_win": round(aw, 1),
        "home_win_pct": round(hw, 1),
        "draw_pct": round(dr, 1),
        "away_win_pct": round(aw, 1),
        "predicted_result": predicted_result,
        "predicted_score": predicted_score,
        "confidence_pct": round(confidence, 1),
        "btts": btts.get("yes") if isinstance(btts, dict) else poisson.get("btts_yes"),
        "over_2_5": goals_markets.get("Over 2.5 Goals"),
        "expected_goals": {
            "home": round(lh, 2),
            "away": round(la, 2),
        },
    }


def _ensure_predictions_for_date(conn, target_date: str) -> int:
    rows = conn.execute(
        """SELECT id, home_team, away_team, league_name
           FROM matches WHERE date = ? ORDER BY kickoff""",
        (target_date,),
    ).fetchall()
    created = 0
    for row in rows:
        match_id = row[0]
        if has_prediction(conn, match_id):
            continue
        home = row[1]
        away = row[2]
        league = row[3] or "Premier League"
        try:
            analysis = _compute_match_analysis(home, away, league)
            insert_prediction(conn, match_id, _build_prediction_summary(analysis))
            created += 1
        except Exception as exc:
            logger.error("Prediction failed for %s vs %s: %s", home, away, exc)
    return created


def _load_daily_matches(conn, target_date: str, ensure_predictions: bool = True) -> list[dict]:
    if ensure_predictions:
        _ensure_predictions_for_date(conn, target_date)

    rows = conn.execute(
        """SELECT id as match_id, league_name as league, home_team, away_team,
                  kickoff, status, NULL as home_logo, NULL as away_logo, NULL as league_logo
           FROM matches WHERE date = ? ORDER BY kickoff""",
        (target_date,),
    ).fetchall()
    data = [dict(r) for r in rows]
    try:
        preds = get_predictions_for_date(conn, target_date)
        pred_map: dict[str, dict] = {}
        for pred in preds:
            if pred["match_id"] not in pred_map:
                pred_map[pred["match_id"]] = pred.get("predictions", {})
        for item in data:
            item["prediction"] = pred_map.get(item["match_id"], {})
    except Exception:
        for item in data:
            item["prediction"] = {}
    return data

# Provider status tracking
_provider_status = {
    "provider": "api-football",
    "connected": False,
    "last_matches_refresh": None,
    "last_odds_refresh": None,
}

# Map SofaScore league names → our profile keys
LEAGUE_NAME_MAP = {
    "Premier League": "Premier League",
    "LaLiga": "LaLiga",
    "La Liga": "LaLiga",
    "Serie A": "Serie A",
    "Bundesliga": "Bundesliga",
    "Ligue 1": "Ligue 1",
    "Champions League": "Champions League",
    "UEFA Champions League": "Champions League",
    "Europa League": "Champions League",
    "UEFA Europa League": "Champions League",
    "Eredivisie": "Eredivisie",
    "VriendenLoterij Eredivisie": "Eredivisie",
    "Liga Profesional de Fútbol": "Liga Profesional",
    "Liga Profesional": "Liga Profesional",
}


def _poisson_over(lam: float, threshold: int) -> float:
    """P(X > threshold) for Poisson distributed X with rate lam."""
    if lam <= 0:
        return 0.0
    cum = sum(math.exp(-lam) * (lam ** k) / math.factorial(k) for k in range(threshold + 1))
    return max(0.0, min(100.0, (1 - cum) * 100))


def _categorize_market(market_name: str) -> str:
    """Assign a market to one of 7 display sections."""
    m = market_name.lower()
    if "handicap" in m or "ah " in m:
        return "Handicaps"
    if "corner" in m:
        return "Corners"
    if "card" in m:
        return "Cards"
    if m.startswith("fh "):
        return "First Half"
    if "1x " in m or "x2 " in m or "12 " in m:
        return "Result"
    if m in ("home win", "away win", "draw"):
        return "Result"
    if ("over" in m or "under" in m) and "goals" in m:
        if not m.startswith("over") and not m.startswith("under"):
            return "Team Goals"
    if "btts" in m:
        return "Goals"
    return "Goals"


def _compute_match_analysis(home_name: str, away_name: str, league_name: str = "Premier League") -> dict:
    """
    Per-match analysis. Clean modular pipeline:
      1. Team stats + Poisson + Corners + Cards + XGBoost
      2. Generate ALL markets across 7 sections
      3. Filter: probability >= 80% ONLY
      4. Group by section for display
      5. Top picks = ALL >=80%, shuffled randomly
    """
    import random
    from src.ml.poisson_model import _poisson_pmf

    league_key = LEAGUE_NAME_MAP.get(league_name, league_name)

    # ── Step 1: Team stats ──
    home_stats = get_team_stats(home_name, "home", league_key)
    away_stats = get_team_stats(away_name, "away", league_key)

    logger.info(
        "⚽ %s (H: %.1f/%.1f, C:%.1f, K:%.1f) vs %s (A: %.1f/%.1f, C:%.1f, K:%.1f) [%s]",
        home_name, home_stats.scored, home_stats.conceded, home_stats.corners, home_stats.cards,
        away_name, away_stats.scored, away_stats.conceded, away_stats.corners, away_stats.cards,
        league_key,
    )

    # ── Step 2: Poisson ──
    poisson_model = PoissonGoalModel(league_key)
    pred = poisson_model.predict(
        home_scored=home_stats.scored, home_conceded=home_stats.conceded,
        away_scored=away_stats.scored, away_conceded=away_stats.conceded,
        home_team=home_name, away_team=away_name,
    )

    # ── Step 3: Corners & Cards lambdas ──
    exp_h_corn, exp_a_corn = home_stats.corners, away_stats.corners
    exp_total_corn = exp_h_corn + exp_a_corn
    exp_h_card, exp_a_card = home_stats.cards, away_stats.cards
    exp_total_card = exp_h_card + exp_a_card

    # ── Step 4: XGBoost ──
    home_profile = TeamProfile(
        team_name=home_name, matches_played=19,
        avg_scored=home_stats.scored, avg_conceded=home_stats.conceded,
        avg_total_goals=home_stats.scored + home_stats.conceded,
        btts_rate=round(pred.btts_yes / 100, 3),
        clean_sheet_rate=round(pred.home_clean_sheet / 100, 3),
        failed_to_score_rate=round(max(0.05, 1 - pred.over_0_5 / 100), 3),
        over_1_5_rate=round(pred.over_1_5 / 100, 3),
        over_2_5_rate=round(pred.over_2_5 / 100, 3),
        over_0_5_ht_rate=round(min(0.95, pred.over_1_5 / 100 * 0.85), 3),
        form_last5=round(pred.home_win / 100 * 12, 1),
        goal_diff=round((home_stats.scored - home_stats.conceded) * 19, 1),
    )
    away_profile = TeamProfile(
        team_name=away_name, matches_played=18,
        avg_scored=away_stats.scored, avg_conceded=away_stats.conceded,
        avg_total_goals=away_stats.scored + away_stats.conceded,
        btts_rate=round(pred.btts_yes / 100, 3),
        clean_sheet_rate=round(pred.away_clean_sheet / 100, 3),
        failed_to_score_rate=round(max(0.05, 1 - pred.over_0_5 / 100), 3),
        over_1_5_rate=round(pred.over_1_5 / 100, 3),
        over_2_5_rate=round(pred.over_2_5 / 100, 3),
        over_0_5_ht_rate=round(min(0.95, pred.over_1_5 / 100 * 0.85), 3),
        form_last5=round(pred.away_win / 100 * 12, 1),
        goal_diff=round((away_stats.scored - away_stats.conceded) * 18, 1),
    )
    xgb_pred = xgb_predictor.predict(home_profile, away_profile)

    # ══════════════════════════════════════════════════════
    # Step 5: GENERATE ALL MARKETS
    # ══════════════════════════════════════════════════════

    def p_over(lam, threshold):
        if lam <= 0: return 0.0
        return max(0, min(100, (1 - sum(_poisson_pmf(k, lam) for k in range(threshold + 1))) * 100))

    MG = 8
    ft = {(h, a): _poisson_pmf(h, pred.lambda_home) * _poisson_pmf(a, pred.lambda_away) for h in range(MG+1) for a in range(MG+1)}
    fh_lh, fh_la = pred.lambda_home * 0.45, pred.lambda_away * 0.45
    fhm = {(h, a): _poisson_pmf(h, fh_lh) * _poisson_pmf(a, fh_la) for h in range(MG+1) for a in range(MG+1)}
    MC = 24
    cm = {(h, a): _poisson_pmf(h, exp_h_corn) * _poisson_pmf(a, exp_a_corn) for h in range(MC+1) for a in range(MC+1)}
    MK = 14
    km = {(h, a): _poisson_pmf(h, exp_h_card) * _poisson_pmf(a, exp_a_card) for h in range(MK+1) for a in range(MK+1)}

    def mx(matrix, mx_val, cond):
        return sum(matrix[(h, a)] for h in range(mx_val+1) for a in range(mx_val+1) if cond(h, a)) * 100

    raw = []
    def add(name, prob):
        prob = round(max(0, min(100, prob)), 1)
        if prob > 0:
            raw.append({"market": name, "probability": prob})

    # ━━ RESULT ━━
    add("Home Win", pred.home_win)
    add("Draw", pred.draw)
    add("Away Win", pred.away_win)
    add(f"1X ({home_name} or Draw)", pred.home_win + pred.draw)
    add(f"X2 ({away_name} or Draw)", pred.away_win + pred.draw)
    add("12 (Any Team to Win)", pred.home_win + pred.away_win)

    # ━━ GOALS ━━
    for label, val in [("Over 0.5", pred.over_0_5), ("Under 0.5", pred.under_0_5),
                       ("Over 1.5", pred.over_1_5), ("Under 1.5", pred.under_1_5),
                       ("Over 2.5", pred.over_2_5), ("Under 2.5", pred.under_2_5),
                       ("Over 3.5", pred.over_3_5), ("Under 3.5", pred.under_3_5),
                       ("Over 4.5", pred.over_4_5), ("Under 4.5", 100 - pred.over_4_5)]:
        add(f"{label} Goals", val)
    add("BTTS - Yes", pred.btts_yes)
    add("BTTS - No", pred.btts_no)

    # ━━ TEAM GOALS ━━
    for t in [0, 1, 2]:
        add(f"{home_name} Over {t}.5 Goals", p_over(pred.lambda_home, t))
        add(f"{home_name} Under {t}.5 Goals", 100 - p_over(pred.lambda_home, t))
        add(f"{away_name} Over {t}.5 Goals", p_over(pred.lambda_away, t))
        add(f"{away_name} Under {t}.5 Goals", 100 - p_over(pred.lambda_away, t))

    # ━━ FIRST HALF ━━
    add("FH Over 0.5 Goals", pred.fh_over_0_5)
    add("FH Under 0.5 Goals", 100 - pred.fh_over_0_5)
    add("FH Over 1.5 Goals", pred.fh_over_1_5)
    add("FH Under 1.5 Goals", 100 - pred.fh_over_1_5)
    add("FH Home Win", pred.fh_home_win)
    add("FH Draw", pred.fh_draw)
    add("FH Away Win", pred.fh_away_win)
    add(f"FH 1X ({home_name} or Draw)", pred.fh_home_win + pred.fh_draw)
    add(f"FH X2 ({away_name} or Draw)", pred.fh_away_win + pred.fh_draw)
    add("FH BTTS - Yes", mx(fhm, MG, lambda h, a: h >= 1 and a >= 1))
    add("FH BTTS - No", mx(fhm, MG, lambda h, a: h == 0 or a == 0))
    add(f"FH {home_name} to Score", mx(fhm, MG, lambda h, a: h >= 1))
    add(f"FH {away_name} to Score", mx(fhm, MG, lambda h, a: a >= 1))
    add("FH No Goal", mx(fhm, MG, lambda h, a: h == 0 and a == 0))
    for t in [0, 1]:
        add(f"FH {home_name} Over {t}.5 Goals", p_over(fh_lh, t))
        add(f"FH {home_name} Under {t}.5 Goals", 100 - p_over(fh_lh, t))
        add(f"FH {away_name} Over {t}.5 Goals", p_over(fh_la, t))
        add(f"FH {away_name} Under {t}.5 Goals", 100 - p_over(fh_la, t))

    # ━━ CORNERS ━━
    for t in [7, 8, 9, 10, 11]:
        add(f"Over {t}.5 Corners", _poisson_over(exp_total_corn, t))
        add(f"Under {t}.5 Corners", 100 - _poisson_over(exp_total_corn, t))
    for t in [3, 4, 5, 6]:
        add(f"{home_name} Over {t}.5 Corners", p_over(exp_h_corn, t))
        add(f"{home_name} Under {t}.5 Corners", 100 - p_over(exp_h_corn, t))
        add(f"{away_name} Over {t}.5 Corners", p_over(exp_a_corn, t))
        add(f"{away_name} Under {t}.5 Corners", 100 - p_over(exp_a_corn, t))
    c_hw = mx(cm, MC, lambda h, a: h > a); c_dr = mx(cm, MC, lambda h, a: h == a); c_aw = mx(cm, MC, lambda h, a: h < a)
    add(f"More Corners: {home_name}", c_hw); add("Corners Draw", c_dr); add(f"More Corners: {away_name}", c_aw)
    add(f"1X Corners ({home_name} or Draw)", c_hw + c_dr)
    add(f"X2 Corners ({away_name} or Draw)", c_aw + c_dr)
    for hc in [1, 2, 3]:
        add(f"{home_name} Corner Handicap (+{hc}.5)", mx(cm, MC, lambda h, a: (h + hc) > a))
        add(f"{away_name} Corner Handicap (+{hc}.5)", mx(cm, MC, lambda h, a: (a + hc) > h))
        add(f"{home_name} Corner Handicap (-{hc}.5)", mx(cm, MC, lambda h, a: (h - hc) > a))
        add(f"{away_name} Corner Handicap (-{hc}.5)", mx(cm, MC, lambda h, a: (a - hc) > h))

    # ━━ CARDS ━━
    for t in [2, 3, 4, 5, 6]:
        add(f"Over {t}.5 Cards", _poisson_over(exp_total_card, t))
        add(f"Under {t}.5 Cards", 100 - _poisson_over(exp_total_card, t))
    for t in [0, 1, 2, 3]:
        add(f"{home_name} Over {t}.5 Cards", p_over(exp_h_card, t))
        add(f"{home_name} Under {t}.5 Cards", 100 - p_over(exp_h_card, t))
        add(f"{away_name} Over {t}.5 Cards", p_over(exp_a_card, t))
        add(f"{away_name} Under {t}.5 Cards", 100 - p_over(exp_a_card, t))
    k_hw = mx(km, MK, lambda h, a: h > a); k_dr = mx(km, MK, lambda h, a: h == a); k_aw = mx(km, MK, lambda h, a: h < a)
    add(f"More Cards: {home_name}", k_hw); add("Cards Draw", k_dr); add(f"More Cards: {away_name}", k_aw)
    add(f"1X Cards ({home_name} or Draw)", k_hw + k_dr)
    add(f"X2 Cards ({away_name} or Draw)", k_aw + k_dr)
    for hc in [1, 2]:
        add(f"{home_name} Card Handicap (+{hc}.5)", mx(km, MK, lambda h, a: (h + hc) > a))
        add(f"{away_name} Card Handicap (+{hc}.5)", mx(km, MK, lambda h, a: (a + hc) > h))

    # ━━ HANDICAPS ━━
    for hc in [1, 2, 3]:
        add(f"{home_name} Handicap (-{hc}.5)", mx(ft, MG, lambda h, a: (h - a) > hc))
        add(f"{home_name} Handicap (+{hc}.5)", mx(ft, MG, lambda h, a: (h - a) > -hc - 1))
        add(f"{away_name} Handicap (-{hc}.5)", mx(ft, MG, lambda h, a: (a - h) > hc))
        add(f"{away_name} Handicap (+{hc}.5)", mx(ft, MG, lambda h, a: (a - h) > -hc - 1))
    add(f"{home_name} AH -0.5", pred.home_win)
    add(f"{away_name} AH -0.5", pred.away_win)
    add(f"{home_name} AH +0.5", pred.home_win + pred.draw)
    add(f"{away_name} AH +0.5", pred.away_win + pred.draw)

    # ══════════════════════════════════════════════════════
    # Step 6: CATEGORIZE → FILTER ≥80% → GROUP → SHUFFLE
    # ══════════════════════════════════════════════════════

    for m in raw:
        m["section"] = _categorize_market(m["market"])

    qualified = [m for m in raw if m["probability"] >= 80.0]

    section_order = ["Goals", "Team Goals", "First Half", "Corners", "Cards", "Handicaps", "Result"]
    sections = {}
    for sec in section_order:
        items = [m for m in qualified if m["section"] == sec]
        items.sort(key=lambda x: x["probability"], reverse=True)
        sections[sec] = items

    top_picks = list(qualified)
    random.shuffle(top_picks)

    # ══════════════════════════════════════════════════════
    # RETURN
    # ══════════════════════════════════════════════════════

    return {
        "disclaimer": f"Poisson (λ={pred.lambda_home:.2f}+{pred.lambda_away:.2f}) + XGBoost | {league_key}",
        "total_markets_scanned": len(raw),
        "total_qualified": len(qualified),
        "top_picks": top_picks,
        "sections": sections,
        "poisson": pred.to_dict(),
        "xgboost_predictions": xgb_pred.to_dict().get("predictions", []),
        "averages": {
            "home": {"avg_goals_scored": home_stats.scored, "avg_goals_conceded": home_stats.conceded, "avg_corners": home_stats.corners, "avg_cards": home_stats.cards},
            "away": {"avg_goals_scored": away_stats.scored, "avg_goals_conceded": away_stats.conceded, "avg_corners": away_stats.corners, "avg_cards": away_stats.cards},
        },
    }


def _fetch_sofascore_events(date_str: str) -> list[dict]:
    url = f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{date_str}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.sofascore.com/",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        return data.get("events", [])
    except Exception as e:
        logger.error(f"SofaScore fetch failed: {e}")
        return []


def _sofascore_to_fixture(event: dict) -> dict:
    tournament = event.get("tournament", {})
    unique_tournament = tournament.get("uniqueTournament", {})
    ut_id = unique_tournament.get("id", 0)
    category = tournament.get("category", {})
    home = event.get("homeTeam", {})
    away = event.get("awayTeam", {})
    status = event.get("status", {})
    
    ts = event.get("startTimestamp", 0)
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        from datetime import timedelta
        local_dt = dt + timedelta(hours=3)
        time_str = local_dt.strftime("%H:%M")
        date_str = local_dt.strftime("%Y-%m-%d")
    except Exception:
        time_str = "TBD"
        date_str = ""
    
    status_type = status.get("type", "")
    if status_type == "finished": short_status = "FT"
    elif status_type == "inprogress": short_status = "LIVE"
    elif status_type == "notstarted": short_status = "NS"
    else: short_status = status.get("description", "")[:4]
    
    home_score = event.get("homeScore", {})
    away_score = event.get("awayScore", {})
    
    return {
        "id": str(event.get("id", "")),
        "date": date_str,
        "time": time_str,
        "status": short_status,
        "home_goals": home_score.get("current") if status_type != "notstarted" else None,
        "away_goals": away_score.get("current") if status_type != "notstarted" else None,
        "fh_home_goals": home_score.get("period1") if status_type != "notstarted" else None,
        "fh_away_goals": away_score.get("period1") if status_type != "notstarted" else None,
        "league": {
            "id": str(ut_id),
            "name": unique_tournament.get("name", tournament.get("name", "")),
            "country": category.get("name", ""),
            "logo": f"https://api.sofascore.com/api/v1/unique-tournament/{ut_id}/image",
        },
        "home_team": {
            "id": str(home.get("id", "")),
            "name": home.get("name", ""),
            "logo": f"https://api.sofascore.com/api/v1/team/{home.get('id', 0)}/image",
        },
        "away_team": {
            "id": str(away.get("id", "")),
            "name": away.get("name", ""),
            "logo": f"https://api.sofascore.com/api/v1/team/{away.get('id', 0)}/image",
        },
    }


# ── Endpoints ──────────────────────────

@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "version": "5.0.0",
        "fixture_loader": "local-first-v2",
        "data_source": "local-first",
        "analysis_mode": "live" if APIFOOTBALL_API_KEY else "per_match_poisson",
        "engine": "Hybrid Poisson Goals + Corners + Cards v5.0",
        "leagues": list(TOP_LEAGUES.values()),
    }


@app.get("/api/leagues")
def get_supported_leagues():
    return [{"id": str(k), "name": v} for k, v in TOP_LEAGUES.items()]


@app.get("/api/fixtures/{date_str}")
def get_fixtures_by_date(date_str: str, force_refresh: bool = False):
    fixtures = fetch_fixtures_for_date(date_str, force_refresh=force_refresh)
    logger.info(f"Returning {len(fixtures)} fixtures for {date_str}")
    return fixtures


@app.get("/api/debug/db")
def get_db_debug():
    return get_db_debug_info()


@app.get("/api/debug/date-coverage")
def get_date_coverage():
    return get_match_history_date_coverage()


@app.post("/api/data/import-csv")
async def import_csv_data(file: UploadFile = File(...), league: str = Form(None)):
    # Historical CSV import endpoint removed in minimal mode.
    raise HTTPException(status_code=404, detail="CSV import disabled in minimal deployment")


@app.get("/api/fixtures/today")
def get_today_fixtures():
    return get_fixtures_by_date(date.today().isoformat())


@app.get("/api/daily/matches")
def api_daily_matches(refresh: bool = False, match_date: Optional[str] = None):
    """Return matches for a date, fetching providers when the DB is empty."""
    target_date = match_date or date.today().isoformat()
    now = datetime.utcnow()
    cache = _daily_matches_cache.setdefault(target_date, {"ts": None, "data": None})

    stale = (
        refresh
        or not cache["ts"]
        or (now - cache["ts"]).total_seconds() > 1800
    )
    if stale:
        conn = get_db()
        data = _load_daily_matches(conn, target_date)
        if refresh or not data:
            fetch_fixtures_for_date(target_date, force_refresh=refresh)
            data = _load_daily_matches(conn, target_date)
        cache["data"] = data
        cache["ts"] = now

    fallback_date = None
    if not cache["data"] and target_date == date.today().isoformat():
        conn = get_db()
        fallback_date = _latest_date_with_matches(conn)
        if fallback_date and fallback_date != target_date:
            fb_cache = _daily_matches_cache.setdefault(fallback_date, {"ts": None, "data": None})
            if refresh or not fb_cache["data"]:
                fetch_fixtures_for_date(fallback_date, force_refresh=False)
                fb_cache["data"] = _load_daily_matches(conn, fallback_date)
                fb_cache["ts"] = now
            cache["data"] = fb_cache["data"]
            cache["ts"] = fb_cache["ts"]

    return {
        "date": target_date,
        "fallback_date": fallback_date if fallback_date and fallback_date != target_date else None,
        "cached_at": cache["ts"].isoformat() if cache["ts"] else None,
        "matches": cache["data"] or [],
    }


@app.get("/api/daily/odds")
def api_daily_odds(refresh: bool = False):
    """Return latest odds snapshots for today's matches. If refresh=True, fetch odds from providers once and store."""
    conn = get_db()
    if refresh:
        # Attempt to fetch and store odds for tracked leagues
        try:
            fetch_and_store_odds(conn)
            from datetime import datetime
            _provider_status["last_odds_refresh"] = datetime.utcnow().isoformat()
        except Exception as e:
            logger.error("Odds fetch failed: %s", e)

    # Build a map of match_id -> markets
    rows = conn.execute("SELECT id FROM matches WHERE date = ?", (date.today().isoformat(),)).fetchall()
    result = {}
    for r in rows:
        match_id = r[0]
        # markets: 1X2 and O/U 2.5
        home = get_latest_odds(conn, match_id, "1X2", "home")
        draw = get_latest_odds(conn, match_id, "1X2", "draw")
        away = get_latest_odds(conn, match_id, "1X2", "away")
        over = get_latest_odds(conn, match_id, "O/U 2.5", "over")
        under = get_latest_odds(conn, match_id, "O/U 2.5", "under")
        result[match_id] = {
            "home": home["odds"] if home else None,
            "draw": draw["odds"] if draw else None,
            "away": away["odds"] if away else None,
            "over": over["odds"] if over else None,
            "under": under["odds"] if under else None,
        }
    return {"date": date.today().isoformat(), "odds": result}


@app.get("/api/debug/provider-status")
def api_provider_status(refresh: int = 0):
    """Return provider connectivity and cache status.

    If `refresh=1` perform a lightweight connectivity test against
    API-Football using the API key present in the process environment.
    This does NOT trigger any full fixtures/odds refreshes.
    """
    import os
    from datetime import datetime

    conn = get_db()
    # cached matches for today
    try:
        matches_count = conn.execute("SELECT COUNT(*) FROM matches WHERE date = ?", (date.today().isoformat(),)).fetchone()[0]
    except Exception:
        matches_count = 0
    try:
        odds_count = conn.execute("SELECT COUNT(*) FROM odds_snapshots WHERE date(recorded_at) = ?", (date.today().isoformat(),)).fetchone()[0]
    except Exception:
        odds_count = 0

    status = dict(_provider_status)
    status.update({"cached_matches": matches_count, "cached_odds": odds_count})

    # If no refresh requested, return cached status quickly
    if not refresh:
        # include last_check if present
        if "last_check" not in status:
            status["last_check"] = None
        # map last_refresh to last_matches_refresh for compatibility
        status["last_refresh"] = status.get("last_matches_refresh")
        return status

    # Perform a quick connectivity re-check using any API key present in the environment
    api_key = os.environ.get("API_FOOTBALL_KEY") or os.environ.get("APIFOOTBALL_API_KEY")
    checked_at = datetime.utcnow().isoformat()
    status["last_check"] = checked_at

    if not api_key:
        status.update({"connected": False, "error": "no API key found in environment (API_FOOTBALL_KEY or APIFOOTBALL_API_KEY)", "last_refresh": status.get("last_matches_refresh")})
        # update provider cache
        _provider_status["connected"] = False
        _provider_status["last_check"] = checked_at
        return status

    # Use the lightweight client to call /status. Do not touch cache or stored data.
    from src.data.api_client import APIFootballClient

    try:
        client = APIFootballClient(api_key=api_key)
        # call status endpoint directly via session to avoid cache reads/writes
        url = f"{client._base_url}/status"
        resp = client._session.get(url, headers=client._build_headers(), timeout=10)
        body = None
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:1024]

        if resp.status_code == 200:
            errors = None
            try:
                probe = client._session.get(
                    f"{client._base_url}/fixtures",
                    headers=client._build_headers(),
                    params={"date": date.today().isoformat()},
                    timeout=10,
                )
                probe_body = probe.json()
                errors = probe_body.get("errors")
            except Exception as exc:
                errors = {"probe": str(exc)}

            if errors:
                err = json.dumps(errors) if not isinstance(errors, str) else errors
                status.update({
                    "connected": False,
                    "error": f"API key rejected for fixtures: {err}",
                    "last_refresh": status.get("last_matches_refresh"),
                })
                _provider_status["connected"] = False
                _provider_status["last_check"] = checked_at
            else:
                status.update({"connected": True, "error": None, "last_refresh": status.get("last_matches_refresh")})
                _provider_status["connected"] = True
                _provider_status["last_check"] = checked_at
        else:
            # API returned non-200; capture provider error details if available
            err = None
            if isinstance(body, dict) and body.get("errors"):
                err = json.dumps(body.get("errors"))
            else:
                err = f"HTTP {resp.status_code}: {str(body)[:400]}"
            status.update({"connected": False, "error": err, "last_refresh": status.get("last_matches_refresh")})
            _provider_status["connected"] = False
            _provider_status["last_check"] = checked_at
    except Exception as exc:
        status.update({"connected": False, "error": str(exc), "last_refresh": status.get("last_matches_refresh")})
        _provider_status["connected"] = False
        _provider_status["last_check"] = checked_at

    return status


@app.on_event("startup")
def startup_check_providers():
    """Validate API-Football connectivity at startup and start background refresher."""
    from threading import Thread
    from datetime import datetime

    client_connected = False
    if APIFOOTBALL_API_KEY:
        try:
            ac = APIFootballFetcher()
            client = getattr(ac, '_client', None)
            session = getattr(client, '_session', None)
            base = getattr(client, '_base_url', None)
            if session and base:
                url = f"{base}/status"
                resp = session.get(url, headers=client._build_headers(), timeout=10)
                client_connected = resp.status_code == 200
                logger.info("API-Football startup status=%s", resp.status_code)
                remaining = resp.headers.get("x-ratelimit-remaining") or resp.headers.get("x-request-count") or resp.headers.get("x-ratelimit-limit")
                logger.info("API-Football quota headers: remaining=%s", remaining)
        except Exception as exc:
            logger.warning("API-Football startup connectivity check failed: %s", exc)

    _provider_status["connected"] = bool(client_connected)

    def _refresher_loop():
        import time
        from datetime import date
        conn = get_db()
        while True:
            try:
                today = date.today().isoformat()
                logger.info("Background refresher: fetching fixtures and odds for %s", today)
                try:
                    fetch_fixtures_for_date(today, force_refresh=True)
                    _provider_status["last_matches_refresh"] = datetime.utcnow().isoformat()
                except Exception as e:
                    logger.warning("Background fixtures refresh failed: %s", e)
                try:
                    fetch_and_store_odds(conn)
                    _provider_status["last_odds_refresh"] = datetime.utcnow().isoformat()
                except Exception as e:
                    logger.warning("Background odds refresh failed: %s", e)
            except Exception as e:
                logger.warning("Background refresher loop error: %s", e)
            # Sleep 30 minutes
            time.sleep(60 * 30)

    # Start background thread as daemon
    try:
        t = Thread(target=_refresher_loop, daemon=True)
        t.start()
        logger.info("Background refresher thread started")
    except Exception as e:
        logger.warning("Could not start background refresher: %s", e)


@app.post("/api/daily/predict")
def api_daily_predict(match_date: Optional[str] = None):
    """Generate missing predictions for all matches on a date."""
    target_date = match_date or date.today().isoformat()
    conn = get_db()
    created = _ensure_predictions_for_date(conn, target_date)
    return {"date": target_date, "predictions_created": created}


@app.get("/api/daily/predictions")
def api_daily_get_predictions():
    """Return stored predictions for today."""
    conn = get_db()
    preds = get_predictions_for_date(conn, date.today().isoformat())
    return {"date": date.today().isoformat(), "predictions": preds}


@app.get("/api/daily/results")
def api_daily_get_results():
    """Return today's recorded results from `daily_results`."""
    conn = get_db()
    rows = conn.execute("SELECT match_id, actual_home_goals, actual_away_goals, predictions_json, hit, recorded_at FROM daily_results WHERE DATE(recorded_at) = ? ORDER BY recorded_at DESC", (date.today().isoformat(),)).fetchall()
    out = []
    for r in rows:
        preds = {}
        try:
            preds = json.loads(r[3]) if r[3] else {}
        except Exception:
            preds = {}
        out.append({"match_id": r[0], "home_goals": r[1], "away_goals": r[2], "predictions": preds, "hit": bool(r[4]), "recorded_at": r[5]})

    # Summary: total predictions recorded today and accuracy
    perf = get_performance_summary(conn, date.today().isoformat())
    summary = {"total_recorded": perf.get('total', 0), "correct": perf.get('hits', 0), "accuracy_pct": perf.get('accuracy', 0.0)}
    return {"date": date.today().isoformat(), "results": out, "summary": summary}


@app.get("/api/daily/opportunities")
def api_daily_opportunities(edge_threshold: float = 0.05, ev_threshold: float = 0.0, min_odds: float = 1.2, max_odds: float = 10.0):
    """Return simple opportunities based on latest predictions and odds."""
    conn = get_db()
    # Load today's predictions (latest per match)
    preds = get_predictions_for_date(conn, date.today().isoformat())
    opps = []
    for p in preds:
        match_id = p["match_id"]
        predictions = p["predictions"]
        # get odds
        odds = {
            "home": get_latest_odds(conn, match_id, "1X2", "home")
        }
        home_od = odds["home"]["odds"] if odds["home"] else None
        # For each market of interest compute edge
        markets = []
        if predictions.get("home_win") and home_od:
            implied = 1.0 / home_od if home_od and home_od > 0 else None
            edge = (predictions["home_win"]/100.0 - implied) if implied is not None else None
            ev = edge * home_od if edge is not None else None
            if edge is not None and edge >= edge_threshold and home_od >= min_odds and home_od <= max_odds:
                markets.append({"market": "Home Win", "edge": round(edge,3), "ev": round(ev,3) if ev is not None else None, "odds": home_od})
        if markets:
            opps.append({"match_id": match_id, "markets": markets})
    return {"date": date.today().isoformat(), "opportunities": opps}


@app.post("/api/daily/finalize-match")
def api_daily_finalize(match_id: str, home_goals: int, away_goals: int):
    """Record final score for a match and compute hit/miss against stored predictions."""
    conn = get_db()
    # find latest prediction for match
    preds = conn.execute("SELECT predictions_json FROM daily_predictions WHERE match_id = ? ORDER BY generated_at DESC LIMIT 1", (match_id,)).fetchone()
    predictions = {}
    if preds and preds[0]:
        try:
            predictions = json.loads(preds[0])
        except Exception:
            predictions = {}

    # simple hit: check if predicted winner matches actual
    actual = 'draw' if home_goals == away_goals else ('home' if home_goals > away_goals else 'away')
    predicted_winner = None
    if predictions:
        hw = predictions.get('home_win')
        dw = predictions.get('draw')
        aw = predictions.get('away_win')
        if hw is not None and dw is not None and aw is not None:
            # choose max
            maxv = max(hw, dw, aw)
            if maxv == hw:
                predicted_winner = 'home'
            elif maxv == aw:
                predicted_winner = 'away'
            else:
                predicted_winner = 'draw'

    hit = (predicted_winner == actual)
    insert_result(conn, match_id, home_goals, away_goals, predictions, hit)
    # update matches table
    conn.execute("UPDATE matches SET status = 'FT', home_goals = ?, away_goals = ? WHERE id = ?", (home_goals, away_goals, match_id))
    conn.commit()

    perf = get_performance_summary(conn, date.today().isoformat())
    return {"match_id": match_id, "hit": bool(hit), "performance": perf}


@app.get("/api/daily/performance")
def api_daily_performance(days: int = 30):
    """Return performance summary for recent days and overall."""
    from datetime import timedelta

    conn = get_db()
    today = date.today()
    series = []
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        perf = get_performance_summary(conn, d)
        perf["date"] = d
        series.append(perf)

    overall = get_performance_summary(conn, None)
    return {"overall": overall, "series": series}


@app.get("/api/analysis/match/{fixture_id}")
def analyze_match(
    fixture_id: str, 
    home: str = "", 
    away: str = "", 
    league: str = "Premier League",
    status: str = "",
    start_time: str = ""
):
    """Per-match prediction. Every match gets UNIQUE probabilities."""
    home_name = home or "Unknown Home"
    away_name = away or "Unknown Away"
    
    try:
        logger.info(f"Per-match engine: {home_name} vs {away_name} [{league}]")
        analysis = _compute_match_analysis(
            home_name=home_name, 
            away_name=away_name, 
            league_name=league
        )
        analysis["match"] = {
            "home_team": home_name,
            "away_team": away_name,
            "league_name": league,
            "season": "2024/25",
            "date": date.today().isoformat(),
        }
        return analysis
    except Exception as e:
        logger.error(f"Error analyzing fixture {fixture_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Results Verification ──────────────────────────

def _fetch_event_statistics(event_id: str) -> dict:
    """Fetch match statistics (corners, cards) from SofaScore for a finished event."""
    url = f"https://api.sofascore.com/api/v1/event/{event_id}/statistics"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.sofascore.com/",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        statistics = data.get("statistics", [])

        if not statistics:
            return {}

        total_corners = None; home_corners = None; away_corners = None
        total_yellow = None; home_yellow = None; away_yellow = None
        total_red = None; home_red = None; away_red = None

        for period in statistics:
            period_name = period.get("period", "")
            if period_name != "ALL":
                continue
            groups = period.get("groups", [])
            for group in groups:
                for item in group.get("statisticsItems", []):
                    stat_name = item.get("name", "")
                    try:
                        h = int(str(item.get("home", "0")).replace("%", ""))
                        a = int(str(item.get("away", "0")).replace("%", ""))
                    except (ValueError, TypeError):
                        continue

                    if stat_name == "Corner kicks":
                        total_corners = (total_corners or 0) + h + a
                        home_corners = (home_corners or 0) + h
                        away_corners = (away_corners or 0) + a
                    elif stat_name in ("Yellow cards", "Total yellow cards"):
                        total_yellow = (total_yellow or 0) + h + a
                        home_yellow = (home_yellow or 0) + h
                        away_yellow = (away_yellow or 0) + a
                    elif stat_name in ("Red cards", "Total red cards"):
                        total_red = (total_red or 0) + h + a
                        home_red = (home_red or 0) + h
                        away_red = (away_red or 0) + a

        total_cards = None
        if total_yellow is not None or total_red is not None:
            total_cards = (total_yellow or 0) + (total_red or 0)
        home_cards = None
        if home_yellow is not None or home_red is not None:
            home_cards = (home_yellow or 0) + (home_red or 0)
        away_cards = None
        if away_yellow is not None or away_red is not None:
            away_cards = (away_yellow or 0) + (away_red or 0)

        return {
            "corners": total_corners, "home_corners": home_corners, "away_corners": away_corners,
            "cards": total_cards, "home_cards": home_cards, "away_cards": away_cards,
            "yellow_cards": total_yellow, "red_cards": total_red
        }
    except Exception as e:
        logger.warning(f"Could not fetch stats for event {event_id}: {e}")
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

    # ── BTTS ──
    elif market == "BTTS - Yes" and "FH" not in market: result = home_goals > 0 and away_goals > 0
    elif market == "BTTS - No" and "FH" not in market: result = not (home_goals > 0 and away_goals > 0)

    # ── Result & Double Chance ──
    elif market == "Home Win" and "FH" not in market: result = home_goals > away_goals
    elif market == "Draw" and "FH" not in market: result = home_goals == away_goals
    elif market == "Away Win" and "FH" not in market: result = away_goals > home_goals
    elif "1X" in market and "FH" not in market and "Corner" not in market and "Card" not in market:
        result = home_goals >= away_goals
    elif "X2" in market and "FH" not in market and "Corner" not in market and "Card" not in market:
        result = away_goals >= home_goals
    elif "12 " in market and "FH" not in market and "Corner" not in market and "Card" not in market:
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
        elif market == "FH 1X (Home or Draw)": result = fh_home_goals >= fh_away_goals
        elif market == "FH X2 (Away or Draw)": result = fh_away_goals >= fh_home_goals
        elif "FH 1X" in market: result = fh_home_goals >= fh_away_goals
        elif "FH X2" in market: result = fh_away_goals >= fh_home_goals
        elif f"FH {home_name} Over" in market:
            for t in [0, 1]:
                if f"Over {t}.5" in market: result = fh_home_goals > t
        elif f"FH {home_name} Under" in market:
            for t in [0, 1, 2]:
                if f"Under {t}.5" in market: result = fh_home_goals <= t
        elif f"FH {away_name} Over" in market:
            for t in [0, 1]:
                if f"Over {t}.5" in market: result = fh_away_goals > t
        elif f"FH {away_name} Under" in market:
            for t in [0, 1, 2]:
                if f"Under {t}.5" in market: result = fh_away_goals <= t

    # ── Corners ──
    if "Corner" in market and "FH" not in market:
        if "Handicap" in market:
            if home_corners is not None and away_corners is not None:
                for hcap in [1, 2, 3, 4]:
                    if market == f"{home_name} Corner Handicap (-{hcap}.5)": result = (home_corners - away_corners) > hcap
                    elif market == f"{home_name} Corner Handicap (+{hcap}.5)": result = (home_corners - away_corners) > -hcap - 1
                    elif market == f"{away_name} Corner Handicap (-{hcap}.5)": result = (away_corners - home_corners) > hcap
                    elif market == f"{away_name} Corner Handicap (+{hcap}.5)": result = (away_corners - home_corners) > -hcap - 1
        elif "Over" in market or "Under" in market:
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
    if "Card" in market and "FH" not in market:
        if "Handicap" in market:
            if home_cards is not None and away_cards is not None:
                for hcap in [1, 2, 3]:
                    if market == f"{home_name} Card Handicap (-{hcap}.5)": result = (home_cards - away_cards) > hcap
                    elif market == f"{home_name} Card Handicap (+{hcap}.5)": result = (home_cards - away_cards) > -hcap - 1
                    elif market == f"{away_name} Card Handicap (-{hcap}.5)": result = (away_cards - home_cards) > hcap
                    elif market == f"{away_name} Card Handicap (+{hcap}.5)": result = (away_cards - home_cards) > -hcap - 1
        elif "Over" in market or "Under" in market:
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
    if "Over" in market and "Goals" in market and "FH" not in market:
        for t in [0, 1, 2, 3]:
            if market == f"{home_name} Over {t}.5 Goals": result = home_goals > t
            elif market == f"{away_name} Over {t}.5 Goals": result = away_goals > t
    elif "Under" in market and "Goals" in market and "FH" not in market:
        for t in [0, 1, 2, 3]:
            if market == f"{home_name} Under {t}.5 Goals": result = home_goals <= t
            elif market == f"{away_name} Under {t}.5 Goals": result = away_goals <= t

    # ── Goal Handicaps ──
    if "Handicap" in market and "Corner" not in market and "Card" not in market:
        for hcap in [1, 2, 3, 4]:
            if market == f"{home_name} Handicap (-{hcap}.5)": result = (home_goals - away_goals) > hcap
            elif market == f"{home_name} Handicap (+{hcap}.5)": result = (home_goals - away_goals) > -hcap - 1
            elif market == f"{away_name} Handicap (-{hcap}.5)": result = (away_goals - home_goals) > hcap
            elif market == f"{away_name} Handicap (+{hcap}.5)": result = (away_goals - home_goals) > -hcap - 1
    elif "AH " in market:
        for hcap in [0.5, 1.5, 2.5]:
            if market == f"{home_name} AH -{hcap}": result = home_goals - away_goals > hcap
            elif market == f"{home_name} AH +{hcap}": result = home_goals - away_goals > -hcap
            elif market == f"{away_name} AH -{hcap}": result = away_goals - home_goals > hcap
            elif market == f"{away_name} AH +{hcap}": result = away_goals - home_goals > -hcap

    return {
        **pick,
        "result": result,
    }


@app.get("/api/results/{date_str}")
def get_results_verification(date_str: str):
    """
    For all finished matches on a given date, regenerate predictions
    and compare them against actual results.

    CLEAN EVALUATION UNIVERSE:
    - Only settled picks (result = True/False) count in stats
    - Leagues with >25% NA matches are excluded entirely
    - Invariant: correct + wrong == total_picks (always)
    """
    events = _fetch_sofascore_events(date_str)
    if not events:
        return {"date": date_str, "matches": [], "summary": {}}

    # ── Phase 1: Build raw results per match ────────────────────
    raw_results = []

    for ev in events:
        status = ev.get("status", {})
        if status.get("type") != "finished":
            continue

        fixture = _sofascore_to_fixture(ev)
        home_goals = fixture.get("home_goals")
        away_goals = fixture.get("away_goals")

        if home_goals is None or away_goals is None:
            continue

        home_name = fixture["home_team"]["name"]
        away_name = fixture["away_team"]["name"]
        league_name = fixture["league"]["name"]
        event_id = fixture["id"]

        # Fetch actual match statistics (corners, cards)
        # Extract newly added FH goals
        fh_home_goals = fixture.get("fh_home_goals")
        fh_away_goals = fixture.get("fh_away_goals")

        # Fetch actual match statistics (corners, cards)
        stats = _fetch_event_statistics(event_id)
        
        # Regenerate predictions for this match
        try:
            analysis = _compute_match_analysis(home_name, away_name, league_name)
            top_picks = analysis.get("top_picks", [])
        except Exception as e:
            logger.warning(f"Could not compute analysis for {home_name} vs {away_name}: {e}")
            continue

        # Evaluate each pick and tag settlement status
        evaluated_picks = []
        for pick in top_picks:
            evaluated = _evaluate_prediction(pick, home_name, away_name, home_goals, away_goals, fh_home_goals, fh_away_goals, stats)
            # Add settlement fields
            evaluated["isSettled"] = evaluated["result"] is not None
            evaluated["isValidForEvaluation"] = evaluated["result"] is not None
            evaluated_picks.append(evaluated)

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
            "picks": evaluated_picks,
        })

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

    for match in raw_results:
        league = match["league_name"]
        league_excluded = league in excluded_leagues

        # Filter picks: only settled picks count
        settled_picks = [p for p in match["picks"] if p["isSettled"]]
        na_picks = [p for p in match["picks"] if not p["isSettled"]]

        if league_excluded:
            total_na_excluded += len(match["picks"])
            continue  # Skip entire league

        match_correct = sum(1 for p in settled_picks if p["result"] is True)
        match_wrong = sum(1 for p in settled_picks if p["result"] is False)

        total_correct += match_correct
        total_wrong += match_wrong
        total_settled_picks += len(settled_picks)
        total_na_excluded += len(na_picks)

        # Match summary uses ONLY settled picks
        clean_results.append({
            "fixture": match["fixture"],
            "actual": match["actual"],
            "picks": match["picks"],  # Keep all for display, but tag them
            "summary": {
                "correct": match_correct,
                "wrong": match_wrong,
                "unknown": len(na_picks),
                "total": len(settled_picks),  # Only settled count
            },
        })

    # ── Phase 4: overall summary using ONLY settled picks ───────
    # INVARIANT: correct + wrong == total_settled_picks
    accuracy = round(
        (total_correct / total_settled_picks * 100), 1
    ) if total_settled_picks > 0 else 0.0

    return {
        "date": date_str,
        "matches": clean_results,
        "summary": {
            "total_matches": len(clean_results),
            "total_picks": total_settled_picks,
            "total_correct": total_correct,
            "total_wrong": total_wrong,
            "total_unknown": 0,  # Always 0 — NAs are excluded
            "accuracy_pct": accuracy,
            "na_excluded": total_na_excluded,
            "leagues_excluded": len(excluded_leagues),
        },
        "league_quality": league_quality,
    }



# ═══════════════════════════════════════════════════════════════════════
# Investment Engine Endpoints
# ═══════════════════════════════════════════════════════════════════════

from src.db.database import get_db
from src.db.picks_repo import get_picks_by_date, get_unsettled_picks, settle_pick, get_portfolio_summary, get_league_pnl
from src.engine.pipeline import run_pipeline as _run_pipeline


@app.get("/api/pipeline/run/{date_str}")
def run_investment_pipeline(date_str: str):
    """Run the full investment pipeline for a date → returns graded picks."""
    events = _fetch_sofascore_events(date_str)
    if not events:
        return {"date": date_str, "error": "No events found", "picks": []}

    conn = get_db()
    result = _run_pipeline(date_str, events, conn)
    return result


@app.get("/api/picks/{date_str}")
def get_picks_for_date(date_str: str):
    """Get all stored picks for a date (from DB)."""
    conn = get_db()
    picks = get_picks_by_date(conn, date_str)
    return {"date": date_str, "picks": picks, "count": len(picks)}


@app.get("/api/portfolio/summary")
def get_portfolio():
    """Bankroll state: total P&L, ROI, hit rate, CLV."""
    conn = get_db()
    summary = get_portfolio_summary(conn)
    return summary


@app.post("/api/picks/settle")
def auto_settle_picks():
    """Auto-settle picks where match results are available."""
    conn = get_db()
    unsettled = get_unsettled_picks(conn)
    settled_count = 0

    for pick in unsettled:
        home_goals = pick.get("home_goals")
        away_goals = pick.get("away_goals")
        if home_goals is None or away_goals is None:
            continue

        result = _evaluate_pick_result(
            pick["market"], pick["selection"],
            home_goals, away_goals
        )
        if result is None:
            continue

        # Calculate P&L
        if result == "won":
            pnl = round(pick["stake_units"] * (pick["odds_at_pick"] - 1), 3)
        elif result == "lost":
            pnl = round(-pick["stake_units"], 3)
        else:
            pnl = 0.0

        settle_pick(conn, pick["id"], result, pnl)
        settled_count += 1

    return {"settled": settled_count, "remaining": len(unsettled) - settled_count}


@app.get("/api/leagues/profiles")
def get_league_profiles():
    """Get all league reliability profiles."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM league_profiles ORDER BY reliability_score DESC"
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/analytics/league-pnl")
def get_league_pnl_analytics():
    """P&L breakdown by league."""
    conn = get_db()
    return get_league_pnl(conn)


def _evaluate_pick_result(
    market: str, selection: str, home_goals: int, away_goals: int
) -> str | None:
    """Determine if a pick won or lost based on match result."""
    total_goals = home_goals + away_goals

    if market == "1X2":
        if selection == "home":
            return "won" if home_goals > away_goals else "lost"
        elif selection == "draw":
            return "won" if home_goals == away_goals else "lost"
        elif selection == "away":
            return "won" if away_goals > home_goals else "lost"

    elif market == "O/U 2.5":
        if selection == "over":
            return "won" if total_goals > 2.5 else "lost"
        elif selection == "under":
            return "won" if total_goals < 2.5 else "lost"

    elif market == "BTTS":
        both_scored = home_goals > 0 and away_goals > 0
        if selection == "yes":
            return "won" if both_scored else "lost"
        elif selection == "no":
            return "won" if not both_scored else "lost"

    return None


# ═══════════════════════════════════════════════════════════════════════
# ML Analytics Endpoints
# ═══════════════════════════════════════════════════════════════════════

from src.engine.calibration import ProbabilityCalibrator, ConfidenceBucketer
from src.data.odds_fetcher import fetch_and_store_odds, get_api_key as get_odds_key


@app.get("/api/analytics/calibration")
def get_calibration_report():
    """How well-calibrated are our probabilities? Predicted vs actual."""
    conn = get_db()
    calibrator = ProbabilityCalibrator(n_bins=10)
    calibrator.fit_from_db(conn)
    return {
        "report": calibrator.get_calibration_report(conn),
        "fitted": calibrator._fitted,
    }


@app.get("/api/analytics/confidence-buckets")
def get_confidence_buckets():
    """Performance breakdown by model confidence range."""
    conn = get_db()
    bucketer = ConfidenceBucketer()
    return {"buckets": bucketer.analyze(conn)}


@app.post("/api/odds/fetch")
def trigger_odds_fetch():
    """Manually trigger odds fetch from The Odds API."""
    if not get_odds_key():
        return {"error": "ODDS_API_KEY not set in .env", "status": "failed"}

    conn = get_db()
    result = fetch_and_store_odds(conn)
    count = conn.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone()[0]
    return {
        "status": "ok",
        "leagues_fetched": result,
        "total_odds_in_db": count,
    }

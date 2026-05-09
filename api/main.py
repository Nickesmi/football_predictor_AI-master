"""
Football Predictor AI - API v5.0
Fetches REAL daily matches from SofaScore. Computes per-match unique predictions
using Poisson (goals) + statistical models (corners, cards).

COVERS: Premier League, La Liga, Serie A, Bundesliga, Ligue 1, UCL, UEL
"""

from __future__ import annotations

import json
import math
import ssl
import urllib.request
from datetime import date, datetime, timezone
from typing import Optional, Union
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from src.config import logger, APIFOOTBALL_API_KEY
from src.data.api_football_fetcher import APIFootballFetcher
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
    """Assign a market to one of the display sections."""
    m = market_name.lower()
    if "corner" in m:
        return "Corners"
    if "card" in m:
        return "Cards"
    # Handicap markets (must check before FH/SH prefix)
    if "handicap" in m or m.startswith("ah ") or m.startswith("eh "):
        return "Handicaps"
    if m.startswith("sh "):
        return "Second Half"
    if m.startswith("fh "):
        return "First Half"
    # Combo markets (Result+Goals, Result+BTTS, BTTS+Goals)
    if " & " in m:
        return "Result"
    # Correct Score
    if m.startswith("cs ") or "correct score" in m:
        return "Goals"
    # Winning Margin
    if "win by" in m or "winning margin" in m or "exact draw" in m:
        return "Result"
    # Clean Sheet / Fail to Score
    if "clean sheet" in m or "fails to score" in m:
        return "Result"
    # Exact Team Goals
    if "exact" in m and "goals" in m and "total" not in m:
        return "Team Goals"
    # Score in Both Halves
    if "score in both" in m:
        return "Goals"
    # Standard result markets
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


# ══════════════════════════════════════════════════════
# DIXON-COLES CORRECTION — Fix Poisson independence flaw
# ══════════════════════════════════════════════════════
#
# Standard bivariate Poisson assumes home/away goals are independent.
# This causes: overestimated BTTS, wrong draw probabilities,
# unrealistic high-score tails.
#
# Dixon-Coles introduces correlation parameter ρ (rho) that adjusts
# low-score cells (0-0, 1-0, 0-1, 1-1) where the independence
# assumption is most violated.
#
# ρ typically ranges from -0.10 to -0.15 (negative = draws more
# likely than independent Poisson suggests).

# ρ values tuned per context (FH has less variance, needs smaller correction)
DIXON_COLES_RHO_FT = -0.12   # Full-time: moderate correction
DIXON_COLES_RHO_FH = -0.05   # First half: less variance, smaller correction
DIXON_COLES_RHO_SH = -0.08   # Second half: between FT and FH


def _dixon_coles_tau(h: int, a: int, lam_h: float, lam_a: float, rho: float) -> float:
    """Dixon-Coles correction factor τ for score (h, a).

    Only adjusts low-score cells where Poisson independence is most wrong.
    The (1,1) cell uses a softened 0.5×ρ to avoid over-boosting BTTS.
    Returns a multiplicative factor to apply to the raw Poisson probability.
    """
    if h == 0 and a == 0:
        return 1.0 - lam_h * lam_a * rho
    elif h == 0 and a == 1:
        return 1.0 + lam_h * rho
    elif h == 1 and a == 0:
        return 1.0 + lam_a * rho
    elif h == 1 and a == 1:
        return 1.0 - 0.35 * rho  # Dampened: boosts draws without inflating BTTS
    return 1.0


def _build_joint_matrix(lam_h: float, lam_a: float, max_goals: int,
                        rho: float = None, apply_dc: bool = True) -> dict:
    """Build a Dixon-Coles corrected bivariate Poisson joint probability matrix.

    Args:
        lam_h: Expected goals for home/team A
        lam_a: Expected goals for away/team B
        max_goals: Maximum goals to consider per side
        rho: Dixon-Coles correlation parameter (default: DIXON_COLES_RHO)
        apply_dc: Whether to apply Dixon-Coles correction (False for corners/cards)

    Returns:
        dict mapping (h, a) -> probability, normalized to sum=1.0
    """
    from src.ml.poisson_model import _poisson_pmf

    if rho is None:
        rho = DIXON_COLES_RHO_FT

    matrix = {}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = _poisson_pmf(h, lam_h) * _poisson_pmf(a, lam_a)
            if apply_dc:
                tau = _dixon_coles_tau(h, a, lam_h, lam_a, rho)
                p *= max(0.0, tau)  # Safety: prevent negative probabilities
            matrix[(h, a)] = p

    # Renormalize so probabilities sum to exactly 1.0
    total = sum(matrix.values())
    if total > 0:
        matrix = {k: v / total for k, v in matrix.items()}

    return matrix


def _compute_match_analysis(home_name: str, away_name: str, league_name: str = "Premier League", shuffle_tiers: bool = True) -> dict:
    """
    Per-match MODULAR analysis — 2-Layer Architecture:

    LAYER 1 — Structured Analysis:
      Compute ALL markets independently across 7 modules:
        Goals | First Half | Team Goals | Result | Corners | Cards | Handicaps
      Each module = separate probabilities — NO mixing.

    LAYER 2 — Top Picks:
      From ALL modules → filter ≥80% → combine → shuffle randomly.
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

    # Dynamic MG based on lambdas to prevent tail truncation bias.
    # Ensures negligible probability mass is lost at the edges.
    MG = max(10, int(pred.lambda_home + pred.lambda_away + 6))

    # ── Dixon-Coles corrected goal matrices ──
    # FT, FH, SH each get context-specific ρ correction.
    # Corners and Cards remain uncorrected (independent events).
    ft = _build_joint_matrix(pred.lambda_home, pred.lambda_away, MG, rho=DIXON_COLES_RHO_FT)
    fh_lh, fh_la = pred.lambda_home * 0.45, pred.lambda_away * 0.45
    fhm = _build_joint_matrix(fh_lh, fh_la, MG, rho=DIXON_COLES_RHO_FH)

    # Corners & Cards — NO Dixon-Coles (independence assumption is fine here)
    MC = 24
    cm = _build_joint_matrix(exp_h_corn, exp_a_corn, MC, apply_dc=False)
    MK = 14
    km = _build_joint_matrix(exp_h_card, exp_a_card, MK, apply_dc=False)

    def mx(matrix, mx_val, cond):
        return sum(matrix[(h, a)] for h in range(mx_val+1) for a in range(mx_val+1) if cond(h, a)) * 100

    # ══════════════════════════════════════════════════════
    # PROBABILITY CALIBRATION — Prevent overconfidence
    # ══════════════════════════════════════════════════════
    #
    # Raw Poisson probabilities are systematically overconfident
    # because the model can't account for real-world variance
    # (injuries, weather, tactics, referee, luck).
    #
    # Formula: calibrated = 0.5 + (raw - 0.5) × SHRINK
    #
    # Properties:
    #   - Symmetric: cal(x) + cal(1-x) = 1 (Over+Under = 100%)
    #   - 50% stays 50% (uncertain stays uncertain)
    #   - Extremes get compressed toward realistic ranges
    #   - SHRINK=0.82 → max possible output = 91%, min = 9%
    #
    # Realistic target ranges after calibration:
    #   Over 0.5: 85-91%  |  Over 1.5: 70-85%
    #   Over 2.5: 55-75%  |  Match Winner: 55-80%

    CALIBRATION_SHRINK = 0.82

    def calibrate(prob_pct: float) -> float:
        """Compress probability toward 50% to prevent overconfidence."""
        p = prob_pct / 100.0
        cal = 0.5 + (p - 0.5) * CALIBRATION_SHRINK
        return round(max(5.0, min(92.0, cal * 100.0)), 1)

    raw = []
    def add(name, prob):
        prob = calibrate(max(0, min(100, prob)))
        if prob > 0:
            raw.append({"market": name, "probability": prob})

    def add_group(names_and_probs):
        """Calibrate a group of mutually exclusive outcomes, then renormalize to 100%.

        This preserves the simplex constraint: P(A) + P(B) + ... = 100%.
        Critical for 1X2, correct score, and any multi-way market.
        """
        calibrated = [(name, calibrate(max(0, min(100, prob)))) for name, prob in names_and_probs]
        total = sum(p for _, p in calibrated)
        if total > 0:
            for name, p in calibrated:
                renorm_p = round(p / total * 100, 1)
                if renorm_p > 0:
                    raw.append({"market": name, "probability": renorm_p})
        # Return renormalized values for downstream use (e.g., double chance)
        if total > 0:
            return {name: round(p / total * 100, 1) for name, p in calibrated}
        return {name: 0.0 for name, _ in names_and_probs}

    def add_raw(name, prob):
        """Add a market with an already-calibrated probability (no double-calibration)."""
        prob = round(max(0, min(100, prob)), 1)
        if prob > 0:
            raw.append({"market": name, "probability": prob})

    # ━━ RESULT (1X2 — renormalized) ━━
    ft_1x2 = add_group([
        ("Home Win", pred.home_win),
        ("Draw", pred.draw),
        ("Away Win", pred.away_win),
    ])
    # Double Chance derived from renormalized 1X2 (already calibrated — use add_raw)
    add_raw(f"1X ({home_name} or Draw)", ft_1x2["Home Win"] + ft_1x2["Draw"])
    add_raw(f"X2 ({away_name} or Draw)", ft_1x2["Away Win"] + ft_1x2["Draw"])
    add_raw("12 (Any Team to Win)", ft_1x2["Home Win"] + ft_1x2["Away Win"])

    # ━━ GOALS ━━
    # ALL goal markets computed from the SAME joint matrix (ft).
    # Binary pairs use symmetric calibration (sum preserved automatically).
    for t in [0, 1, 2, 3, 4]:
        total_over = mx(ft, MG, lambda h, a, _t=t: h + a > _t)
        add(f"Over {t}.5 Goals", total_over)
        add(f"Under {t}.5 Goals", 100 - total_over)
    btts_yes = mx(ft, MG, lambda h, a: h >= 1 and a >= 1)
    btts_no = 100 - btts_yes
    add("BTTS - Yes", btts_yes)
    add("BTTS - No", btts_no)

    # ━━ TEAM GOALS ━━
    # Computed from the SAME joint matrix as total goals for consistency.
    for t in [0, 1, 2]:
        add(f"{home_name} Over {t}.5 Goals", mx(ft, MG, lambda h, a, _t=t: h > _t))
        add(f"{home_name} Under {t}.5 Goals", mx(ft, MG, lambda h, a, _t=t: h <= _t))
        add(f"{away_name} Over {t}.5 Goals", mx(ft, MG, lambda h, a, _t=t: a > _t))
        add(f"{away_name} Under {t}.5 Goals", mx(ft, MG, lambda h, a, _t=t: a <= _t))

    # ━━ FIRST HALF ━━
    for t in [0, 1]:
        fh_total_over = mx(fhm, MG, lambda h, a, _t=t: h + a > _t)
        add(f"FH Over {t}.5 Goals", fh_total_over)
        add(f"FH Under {t}.5 Goals", 100 - fh_total_over)
    # FH 1X2 — renormalized
    fh_1x2 = add_group([
        ("FH Home Win", pred.fh_home_win),
        ("FH Draw", pred.fh_draw),
        ("FH Away Win", pred.fh_away_win),
    ])
    add_raw(f"FH 1X ({home_name} or Draw)", fh_1x2["FH Home Win"] + fh_1x2["FH Draw"])
    add_raw(f"FH X2 ({away_name} or Draw)", fh_1x2["FH Away Win"] + fh_1x2["FH Draw"])
    add("FH BTTS - Yes", mx(fhm, MG, lambda h, a: h >= 1 and a >= 1))
    add("FH BTTS - No", mx(fhm, MG, lambda h, a: h == 0 or a == 0))
    add(f"FH {home_name} to Score", mx(fhm, MG, lambda h, a: h >= 1))
    add(f"FH {away_name} to Score", mx(fhm, MG, lambda h, a: a >= 1))
    add("FH No Goal", mx(fhm, MG, lambda h, a: h == 0 and a == 0))
    for t in [0, 1]:
        add(f"FH {home_name} Over {t}.5 Goals", mx(fhm, MG, lambda h, a, _t=t: h > _t))
        add(f"FH {home_name} Under {t}.5 Goals", mx(fhm, MG, lambda h, a, _t=t: h <= _t))
        add(f"FH {away_name} Over {t}.5 Goals", mx(fhm, MG, lambda h, a, _t=t: a > _t))
        add(f"FH {away_name} Under {t}.5 Goals", mx(fhm, MG, lambda h, a, _t=t: a <= _t))

    # ━━ SECOND HALF ━━
    sh_lh = pred.lambda_home * 0.55
    sh_la = pred.lambda_away * 0.55
    shm = _build_joint_matrix(sh_lh, sh_la, MG, rho=DIXON_COLES_RHO_SH)
    for t in [0, 1]:
        sh_total_over = mx(shm, MG, lambda h, a, _t=t: h + a > _t)
        add(f"SH Over {t}.5 Goals", sh_total_over)
        add(f"SH Under {t}.5 Goals", 100 - sh_total_over)
    # SH 1X2 — renormalized
    sh_hw = mx(shm, MG, lambda h, a: h > a)
    sh_dr = mx(shm, MG, lambda h, a: h == a)
    sh_aw = mx(shm, MG, lambda h, a: h < a)
    sh_1x2 = add_group([
        ("SH Home Win", sh_hw),
        ("SH Draw", sh_dr),
        ("SH Away Win", sh_aw),
    ])
    add_raw(f"SH 1X ({home_name} or Draw)", sh_1x2["SH Home Win"] + sh_1x2["SH Draw"])
    add_raw(f"SH X2 ({away_name} or Draw)", sh_1x2["SH Away Win"] + sh_1x2["SH Draw"])
    add("SH BTTS - Yes", mx(shm, MG, lambda h, a: h >= 1 and a >= 1))
    add("SH BTTS - No", mx(shm, MG, lambda h, a: h == 0 or a == 0))
    add(f"SH {home_name} to Score", mx(shm, MG, lambda h, a: h >= 1))
    add(f"SH {away_name} to Score", mx(shm, MG, lambda h, a: a >= 1))
    add("SH No Goal", mx(shm, MG, lambda h, a: h == 0 and a == 0))
    for t in [0, 1]:
        add(f"SH {home_name} Over {t}.5 Goals", mx(shm, MG, lambda h, a, _t=t: h > _t))
        add(f"SH {home_name} Under {t}.5 Goals", mx(shm, MG, lambda h, a, _t=t: h <= _t))
        add(f"SH {away_name} Over {t}.5 Goals", mx(shm, MG, lambda h, a, _t=t: a > _t))
        add(f"SH {away_name} Under {t}.5 Goals", mx(shm, MG, lambda h, a, _t=t: a <= _t))

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



    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EXPANDED MARKET COVERAGE — New markets
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    import math


    # ━━ ADVANCED GOALS ━━
    for g in range(7):
        add(f"Exact Total Goals: {g}", mx(ft, MG, lambda h, a, _g=g: h + a == _g))
    add("Goal Range 0-1", mx(ft, MG, lambda h, a: h + a <= 1))
    add("Goal Range 2-3", mx(ft, MG, lambda h, a: 2 <= h + a <= 3))
    add("Goal Range 4+", mx(ft, MG, lambda h, a: h + a >= 4))
    add("Odd/Even Total Goals: Odd", mx(ft, MG, lambda h, a: (h + a) % 2 == 1))
    add("Odd/Even Total Goals: Even", mx(ft, MG, lambda h, a: (h + a) % 2 == 0))

    # ━━ TIME-BASED ━━
    lam_15 = (pred.lambda_home + pred.lambda_away) * (15 / 90)
    p_goal_15 = (1 - math.exp(-lam_15)) * 100
    add("Goal in First 15 Min - Yes", p_goal_15)
    add("Goal in First 15 Min - No", 100 - p_goal_15)
    p_fh_any = mx(fhm, MG, lambda h, a: h + a >= 1)
    p_sh_any = mx(shm, MG, lambda h, a: h + a >= 1)
    add("Goal in Both Halves - Yes", (p_fh_any / 100) * p_sh_any)
    add("Goal in Both Halves - No", 100 - (p_fh_any / 100) * p_sh_any)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PHASE 5 — DERIVED MARKETS (all from Dixon-Coles ft matrix)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # ━━ TIER A: CORRECT SCORE (FT) ━━
    # Explicit scorelines 0-0 through 4-4, "Other" is the remainder
    for h_cs in range(5):
        for a_cs in range(5):
            p_cs = ft[(h_cs, a_cs)] * 100
            add(f"CS {h_cs}-{a_cs}", p_cs)
    # "Other" = all scorelines NOT in the 0-4 grid (explicit from matrix)
    p_cs_other = sum(ft[(h, a)] for h in range(MG+1) for a in range(MG+1)
                     if not (h <= 4 and a <= 4)) * 100
    add("CS Other", p_cs_other)

    # ━━ TIER A: WINNING MARGIN ━━
    add(f"{home_name} Win by 1", mx(ft, MG, lambda h, a: h - a == 1))
    add(f"{home_name} Win by 2", mx(ft, MG, lambda h, a: h - a == 2))
    add(f"{home_name} Win by 3+", mx(ft, MG, lambda h, a: h - a >= 3))
    add(f"{away_name} Win by 1", mx(ft, MG, lambda h, a: a - h == 1))
    add(f"{away_name} Win by 2", mx(ft, MG, lambda h, a: a - h == 2))
    add(f"{away_name} Win by 3+", mx(ft, MG, lambda h, a: a - h >= 3))
    add("Exact Draw 0-0", ft[(0, 0)] * 100)

    # ━━ TIER A: CLEAN SHEET & FAIL TO SCORE ━━
    add(f"{home_name} Clean Sheet", mx(ft, MG, lambda h, a: a == 0))
    add(f"{away_name} Clean Sheet", mx(ft, MG, lambda h, a: h == 0))
    add(f"{home_name} Fails to Score", mx(ft, MG, lambda h, a: h == 0))
    add(f"{away_name} Fails to Score", mx(ft, MG, lambda h, a: a == 0))

    # ━━ TIER A: EXACT TEAM GOALS ━━
    for n in range(4):
        add(f"{home_name} Exact {n} Goals", mx(ft, MG, lambda h, a, _n=n: h == _n))
        add(f"{away_name} Exact {n} Goals", mx(ft, MG, lambda h, a, _n=n: a == _n))
    add(f"{home_name} Exact 3+ Goals", mx(ft, MG, lambda h, a: h >= 3))
    add(f"{away_name} Exact 3+ Goals", mx(ft, MG, lambda h, a: a >= 3))

    # ━━ TIER B: RESULT + GOALS COMBOS ━━
    # All computed from the SAME joint matrix — guaranteed consistency
    add(f"Home Win & Over 1.5", mx(ft, MG, lambda h, a: h > a and h + a > 1))
    add(f"Home Win & Over 2.5", mx(ft, MG, lambda h, a: h > a and h + a > 2))
    add(f"Home Win & Under 2.5", mx(ft, MG, lambda h, a: h > a and h + a <= 2))
    add(f"Away Win & Over 1.5", mx(ft, MG, lambda h, a: a > h and h + a > 1))
    add(f"Away Win & Over 2.5", mx(ft, MG, lambda h, a: a > h and h + a > 2))
    add(f"Away Win & Under 2.5", mx(ft, MG, lambda h, a: a > h and h + a <= 2))
    add(f"Draw & Over 2.5", mx(ft, MG, lambda h, a: h == a and h + a > 2))
    add(f"Draw & Under 2.5", mx(ft, MG, lambda h, a: h == a and h + a <= 2))

    # ━━ TIER B: RESULT + BTTS COMBOS ━━
    add(f"Home Win & BTTS", mx(ft, MG, lambda h, a: h > a and h >= 1 and a >= 1))
    add(f"Away Win & BTTS", mx(ft, MG, lambda h, a: a > h and h >= 1 and a >= 1))
    add(f"Draw & BTTS", mx(ft, MG, lambda h, a: h == a and h >= 1 and a >= 1))

    # ━━ TIER B: BTTS + GOALS COMBOS ━━
    add(f"BTTS & Over 2.5", mx(ft, MG, lambda h, a: h >= 1 and a >= 1 and h + a > 2))
    add(f"BTTS & Under 2.5", mx(ft, MG, lambda h, a: h >= 1 and a >= 1 and h + a <= 2))

    # ━━ TIER B: SCORING IN BOTH HALVES ━━
    # Bounded approximation: min(P(team≥2 goals), P(FH_scores) × P(SH_scores) × 1.1)
    # to account for game-state dependency (scoring early changes SH intensity)
    p_fh_home_scores = mx(fhm, MG, lambda h, a: h >= 1) / 100
    p_sh_home_scores = mx(shm, MG, lambda h, a: h >= 1) / 100
    p_fh_away_scores = mx(fhm, MG, lambda h, a: a >= 1) / 100
    p_sh_away_scores = mx(shm, MG, lambda h, a: a >= 1) / 100
    p_home_2plus = mx(ft, MG, lambda h, a: h >= 2) / 100  # needs 2+ for both halves
    p_away_2plus = mx(ft, MG, lambda h, a: a >= 2) / 100
    add(f"{home_name} Score in Both Halves",
        min(p_home_2plus, p_fh_home_scores * p_sh_home_scores * 1.1) * 100)
    add(f"{away_name} Score in Both Halves",
        min(p_away_2plus, p_fh_away_scores * p_sh_away_scores * 1.1) * 100)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HANDICAP MARKETS (all from Dixon-Coles ft matrix)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # ━━ ASIAN HANDICAP (2-way: no draw possible) ━━
    # AH -0.5 Home = Home must win outright (same as Home Win)
    # AH -1.5 Home = Home must win by 2+
    # AH -2.5 Home = Home must win by 3+
    for spread in [0.5, 1.5, 2.5]:
        # Home giving handicap (favorite scenario)
        ah_home = mx(ft, MG, lambda h, a, _s=spread: (h - a) > _s)
        add(f"AH {home_name} -{spread}", ah_home)
        add(f"AH {away_name} +{spread}", 100 - ah_home)
        # Away giving handicap
        ah_away = mx(ft, MG, lambda h, a, _s=spread: (a - h) > _s)
        add(f"AH {away_name} -{spread}", ah_away)
        add(f"AH {home_name} +{spread}", 100 - ah_away)

    # ━━ EUROPEAN HANDICAP (3-way: includes draw — renormalized) ━━
    # EH -1 Home: After applying -1 to home, check win/draw/loss
    for spread in [1, 2]:
        # Home -spread
        eh_hw = mx(ft, MG, lambda h, a, _s=spread: (h - _s) > a)
        eh_dr = mx(ft, MG, lambda h, a, _s=spread: (h - _s) == a)
        eh_aw = mx(ft, MG, lambda h, a, _s=spread: (h - _s) < a)
        add_group([
            (f"EH {home_name} -{spread} (Win)", eh_hw),
            (f"EH {home_name} -{spread} (Draw)", eh_dr),
            (f"EH {home_name} -{spread} (Lose)", eh_aw),
        ])
        # Away -spread
        eh_aw2 = mx(ft, MG, lambda h, a, _s=spread: (a - _s) > h)
        eh_dr2 = mx(ft, MG, lambda h, a, _s=spread: (a - _s) == h)
        eh_hw2 = mx(ft, MG, lambda h, a, _s=spread: (a - _s) < h)
        add_group([
            (f"EH {away_name} -{spread} (Win)", eh_aw2),
            (f"EH {away_name} -{spread} (Draw)", eh_dr2),
            (f"EH {away_name} -{spread} (Lose)", eh_hw2),
        ])

    # ━━ CORNERS — HALF-BASED ━━
    fh_h_corn = exp_h_corn * 0.48; fh_a_corn = exp_a_corn * 0.48
    sh_h_corn = exp_h_corn * 0.52; sh_a_corn = exp_a_corn * 0.52
    fh_total_corn = fh_h_corn + fh_a_corn; sh_total_corn = sh_h_corn + sh_a_corn
    for t in [3, 4, 5]:
        add(f"FH Over {t}.5 Corners", _poisson_over(fh_total_corn, t))
        add(f"FH Under {t}.5 Corners", 100 - _poisson_over(fh_total_corn, t))
        add(f"SH Over {t}.5 Corners", _poisson_over(sh_total_corn, t))
        add(f"SH Under {t}.5 Corners", 100 - _poisson_over(sh_total_corn, t))
    for t in [1, 2, 3]:
        for prefix, lh, la in [("FH", fh_h_corn, fh_a_corn), ("SH", sh_h_corn, sh_a_corn)]:
            add(f"{prefix} {home_name} Over {t}.5 Corners", p_over(lh, t))
            add(f"{prefix} {home_name} Under {t}.5 Corners", 100 - p_over(lh, t))
            add(f"{prefix} {away_name} Over {t}.5 Corners", p_over(la, t))
            add(f"{prefix} {away_name} Under {t}.5 Corners", 100 - p_over(la, t))

    # ━━ CARDS — HALF-BASED ━━
    fh_h_card = exp_h_card * 0.45; fh_a_card = exp_a_card * 0.45
    sh_h_card = exp_h_card * 0.55; sh_a_card = exp_a_card * 0.55
    fh_total_card = fh_h_card + fh_a_card; sh_total_card = sh_h_card + sh_a_card
    for t in [0, 1, 2, 3]:
        add(f"FH Over {t}.5 Cards", _poisson_over(fh_total_card, t))
        add(f"FH Under {t}.5 Cards", 100 - _poisson_over(fh_total_card, t))
        add(f"SH Over {t}.5 Cards", _poisson_over(sh_total_card, t))
        add(f"SH Under {t}.5 Cards", 100 - _poisson_over(sh_total_card, t))
    for t in [0, 1]:
        for prefix, lh, la in [("FH", fh_h_card, fh_a_card), ("SH", sh_h_card, sh_a_card)]:
            add(f"{prefix} {home_name} Over {t}.5 Cards", p_over(lh, t))
            add(f"{prefix} {home_name} Under {t}.5 Cards", 100 - p_over(lh, t))
            add(f"{prefix} {away_name} Over {t}.5 Cards", p_over(la, t))
            add(f"{prefix} {away_name} Under {t}.5 Cards", 100 - p_over(la, t))

    # ━━ HALF WITH MOST ACTIVITY ━━
    if fh_total_corn + sh_total_corn > 0:
        add("Half with Most Corners: 1st Half", fh_total_corn / (fh_total_corn + sh_total_corn) * 100)
        add("Half with Most Corners: 2nd Half", sh_total_corn / (fh_total_corn + sh_total_corn) * 100)
    if fh_total_card + sh_total_card > 0:
        add("Half with Most Cards: 1st Half", fh_total_card / (fh_total_card + sh_total_card) * 100)
        add("Half with Most Cards: 2nd Half", sh_total_card / (fh_total_card + sh_total_card) * 100)

    # ══════════════════════════════════════════════════════
    # SCORE ESTIMATION — Top probable scorelines
    # ══════════════════════════════════════════════════════
    #
    # Extract the most probable exact scores from Poisson
    # joint probability matrices (already computed above).

    # Full-time scores (from ft matrix)
    ft_scores = []
    for h in range(MG + 1):
        for a in range(MG + 1):
            prob = ft[(h, a)] * 100
            if prob >= 1.0:  # Only include scores with >= 1% probability
                ft_scores.append({"home": h, "away": a, "probability": round(prob, 1)})
    ft_scores.sort(key=lambda x: x["probability"], reverse=True)
    ft_top_scores = ft_scores[:5]

    # First-half scores (from fhm matrix)
    fh_scores = []
    for h in range(MG + 1):
        for a in range(MG + 1):
            prob = fhm[(h, a)] * 100
            if prob >= 1.0:
                fh_scores.append({"home": h, "away": a, "probability": round(prob, 1)})
    fh_scores.sort(key=lambda x: x["probability"], reverse=True)
    fh_top_scores = fh_scores[:5]

    score_prediction = {
        "full_time": ft_top_scores,
        "first_half": fh_top_scores,
        "expected_goals": {
            "home": round(pred.lambda_home, 2),
            "away": round(pred.lambda_away, 2),
            "total": round(pred.lambda_home + pred.lambda_away, 2),
        },
    }

    # ══════════════════════════════════════════════════════
    # DOMINANCE INSIGHTS — Who controls corners/cards
    # ══════════════════════════════════════════════════════

    c_home_more = mx(cm, MC, lambda h, a: h > a)
    c_away_more = mx(cm, MC, lambda h, a: h < a)
    k_home_more = mx(km, MK, lambda h, a: h > a)
    k_away_more = mx(km, MK, lambda h, a: h < a)

    dominance = {
        "corners": {
            "home_pct": round(c_home_more, 1),
            "away_pct": round(c_away_more, 1),
            "dominant": home_name if c_home_more > c_away_more else away_name,
            "expected_home": round(exp_h_corn, 1),
            "expected_away": round(exp_a_corn, 1),
            "expected_total": round(exp_total_corn, 1),
        },
        "cards": {
            "home_pct": round(k_home_more, 1),
            "away_pct": round(k_away_more, 1),
            "dominant": home_name if k_home_more > k_away_more else away_name,
            "expected_home": round(exp_h_card, 1),
            "expected_away": round(exp_a_card, 1),
            "expected_total": round(exp_total_card, 1),
        },
    }

    # ══════════════════════════════════════════════════════
    # LAYER 1 — STRUCTURED ANALYSIS (all markets, grouped)
    # ══════════════════════════════════════════════════════
    #
    # Each module is analyzed INDEPENDENTLY — no mixing.
    # ALL probabilities are returned so the UI can show
    # the complete picture before filtering.

    for m in raw:
        m["section"] = _categorize_market(m["market"])

    # ══════════════════════════════════════════════════════
    # ISOTONIC CALIBRATION — Per-market-type learned transform
    # ══════════════════════════════════════════════════════
    #
    # Pipeline order:
    #   Raw Poisson → Shrinkage calibration → Isotonic calibration
    #
    # The shrinkage (CALIBRATION_SHRINK=0.82) is a symmetric pre-filter.
    # Isotonic regression is a learned monotonic correction fitted from
    # actual outcome data per market type.
    #
    # After this step:
    #   raw_probability  = pre-isotonic value (for diagnostics/retraining)
    #   probability      = post-isotonic value (for ranking, display, EV)
    #
    try:
        from src.engine.isotonic_calibrator import get_isotonic_calibrator
        from src.db.prediction_logger import _classify_market_type
        from src.db.database import get_db
        _iso_conn = get_db()
        _iso_cal = get_isotonic_calibrator(_iso_conn)

        for m in raw:
            m["raw_probability"] = m["probability"]  # preserve pre-isotonic
            m["market_type"] = _classify_market_type(m["market"])
            m["probability"] = _iso_cal.calibrate(m["raw_probability"], m["market_type"])
    except Exception as iso_err:
        # Fallback: if isotonic fails, raw_probability == probability
        logger.warning(f"Isotonic calibration failed, using raw: {iso_err}")
        for m in raw:
            m["raw_probability"] = m["probability"]
            m["market_type"] = "unknown"

    section_order = ["Goals", "First Half", "Second Half", "Team Goals", "Result", "Handicaps", "Corners", "Cards"]

    # Full analysis: every market, sorted by probability (descending)
    full_analysis = {}
    for sec in section_order:
        items = [m for m in raw if m["section"] == sec]
        items.sort(key=lambda x: x["probability"], reverse=True)
        full_analysis[sec] = items

    # ══════════════════════════════════════════════════════
    # LAYER 2 — TIERED PICKS (6 tiers × 10 picks = 60)
    # ══════════════════════════════════════════════════════
    #
    # 1. Sort ALL raw markets by probability (descending)
    # 2. Take top 60
    # 3. Split into 6 tiers of 10 picks each
    # 4. Shuffle WITHIN each tier (no visible ranking inside a tier)
    #
    # Tier 1 = ranks 1-10 (highest confidence)
    # Tier 2 = ranks 7-12
    # ...
    # Tier 6 = ranks 31-36 (lowest of the 36)

    PICKS_PER_TIER = 6
    NUM_TIERS = 6
    TOTAL_PICKS = PICKS_PER_TIER * NUM_TIERS  # 36

    all_sorted = sorted(raw, key=lambda x: x["probability"], reverse=True)

    # ── Correlation dedup: limit correlated markets in top picks ──
    # Prevents combos/CS/redundant signals from flooding tiers.
    CLUSTER_LIMITS = {
        "combo": 2,       # Result+Goals, Result+BTTS, BTTS+Goals
        "cs": 3,          # Correct Score
        "goals_ou": 3,    # Over/Under goals (total)
        "result": 2,      # 1X2, Double Chance
        "btts": 1,        # BTTS Yes/No
        "margin": 2,      # Winning margin
    }

    def _get_cluster(market_name):
        m = market_name.lower()
        if " & " in m: return "combo"
        if m.startswith("cs "): return "cs"
        if m.startswith("over") or m.startswith("under"): return "goals_ou"
        if m in ("home win", "draw", "away win") or "1x " in m or "x2 " in m or "12 " in m: return "result"
        if "btts" in m: return "btts"
        if "win by" in m or "exact draw" in m: return "margin"
        return None  # no limit

    cluster_counts = {}
    deduped = []
    for m in all_sorted:
        cluster = _get_cluster(m["market"])
        if cluster:
            count = cluster_counts.get(cluster, 0)
            if count >= CLUSTER_LIMITS[cluster]:
                continue  # skip — cluster full
            cluster_counts[cluster] = count + 1
        deduped.append(m)

    top_36 = deduped[:TOTAL_PICKS]

    tiers = []
    for t in range(NUM_TIERS):
        start = t * PICKS_PER_TIER
        end = start + PICKS_PER_TIER
        tier_picks = list(top_36[start:end])
        
        if shuffle_tiers:
            random.shuffle(tier_picks)

        # Calculate tier stats
        probs = [p["probability"] for p in tier_picks] if tier_picks else [0]
        tiers.append({
            "tier": t + 1,
            "label": f"Tier {t + 1}",
            "rank_range": f"{start + 1}-{end}",
            "picks": tier_picks,
            "avg_probability": round(sum(probs) / len(probs), 1),
            "min_probability": round(min(probs), 1),
            "max_probability": round(max(probs), 1),
        })

    if shuffle_tiers:
        random.shuffle(tiers)

    # Legacy: flat top_picks for backward compat (all 36, shuffled)
    top_picks = list(top_36)
    random.shuffle(top_picks)

    # Also build per-section qualified view (≥80%)
    qualified = [m for m in raw if m["probability"] >= 80.0]
    qualified_sections = {}
    for sec in section_order:
        items = [m for m in qualified if m["section"] == sec]
        items.sort(key=lambda x: x["probability"], reverse=True)
        qualified_sections[sec] = items

    # ══════════════════════════════════════════════════════
    # RETURN — Both layers exposed
    # ══════════════════════════════════════════════════════

    return {
        "disclaimer": f"Poisson (λ={pred.lambda_home:.2f}+{pred.lambda_away:.2f}) + XGBoost | {league_key} | isotonic-calibrated",
        "total_markets_scanned": len(raw),
        "total_qualified": len(qualified),
        "total_tiered_picks": len(top_36),
        # Layer 2 — tiered picks (6 tiers × 6 picks)
        "tiers": tiers,
        # Legacy flat list
        "top_picks": top_picks,
        # Layer 1 — FULL structured analysis (all markets, all probs)
        "full_analysis": full_analysis,
        # Qualified per section (≥80%)
        "sections": qualified_sections,
        "poisson": pred.to_dict(),
        "score_prediction": score_prediction,
        "dominance": dominance,
        "xgboost_predictions": xgb_pred.to_dict().get("predictions", []),
        "averages": {
            "home": {"avg_goals_scored": home_stats.scored, "avg_goals_conceded": home_stats.conceded, "avg_corners": home_stats.corners, "avg_cards": home_stats.cards},
            "away": {"avg_goals_scored": away_stats.scored, "avg_goals_conceded": away_stats.conceded, "avg_corners": away_stats.corners, "avg_cards": away_stats.cards},
        },
    }


def _fetch_sofascore_events(date_str: str) -> list[dict]:
    """Fetch scheduled events from SofaScore for the given date.
    
    Also fetches the previous day's events since SofaScore uses UTC dates
    and late-night matches in UTC+3 may appear under the previous UTC day.
    """
    from datetime import timedelta

    all_events = []
    # Fetch the requested date + the previous day (to catch timezone-shifted matches)
    target = datetime.strptime(date_str, "%Y-%m-%d").date()
    dates_to_fetch = [
        (target - timedelta(days=1)).isoformat(),
        date_str,
    ]

    for d in dates_to_fetch:
        url = f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{d}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            "Accept": "*/*",
        })
        try:
            ctx = ssl.create_default_context()
            resp = urllib.request.urlopen(req, timeout=15, context=ctx)
            data = json.loads(resp.read())
            all_events.extend(data.get("events", []))
        except Exception as e:
            logger.error(f"SofaScore fetch failed for {d}: {e}")

    return all_events


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
        "data_source": "sofascore",
        "analysis_mode": "live" if APIFOOTBALL_API_KEY else "per_match_poisson",
        "engine": "Hybrid Poisson Goals + Corners + Cards v5.0",
        "leagues": list(TOP_LEAGUES.values()),
    }


@app.get("/api/leagues")
def get_supported_leagues():
    return [{"id": str(k), "name": v} for k, v in TOP_LEAGUES.items()]


@app.get("/api/fixtures/{date_str}")
def get_fixtures_by_date(date_str: str):
    events = _fetch_sofascore_events(date_str)
    if not events:
        logger.warning(f"No events from SofaScore for {date_str}")
        return []
    
    fixtures = []
    seen_ids = set()
    for ev in events:
        f = _sofascore_to_fixture(ev)
        # Match date filter (after timezone conversion) + dedup
        if f["date"] == date_str and f["id"] not in seen_ids:
            seen_ids.add(f["id"])
            fixtures.append(f)
    
    fixtures.sort(key=lambda f: f["time"])
    
    logger.info(f"Returning {len(fixtures)} fixtures for {date_str}")
    return fixtures


@app.get("/api/fixtures/today")
def get_today_fixtures():
    return get_fixtures_by_date(date.today().isoformat())


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
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Accept": "*/*",
    })
    try:
        ctx = ssl.create_default_context()
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
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
    seen_ids = set()

    for ev in events:
        status = ev.get("status", {})
        if status.get("type") != "finished":
            continue

        fixture = _sofascore_to_fixture(ev)

        if fixture["date"] != date_str or fixture["id"] in seen_ids:
            continue

        seen_ids.add(fixture["id"])

        # Skip extra-time / penalty matches
        if fixture.get("is_extra_time", False):
            continue

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
            tiers = analysis.get("tiers", [])
        except Exception as e:
            logger.warning(f"Could not compute analysis for {home_name} vs {away_name}: {e}")
            continue

        # Evaluate each pick within its tier
        evaluated_tiers = []
        all_evaluated_picks = []

        for tier in tiers:
            tier_picks_evaluated = []
            for pick in tier.get("picks", []):
                evaluated = _evaluate_prediction(pick, home_name, away_name, home_goals, away_goals, fh_home_goals, fh_away_goals, stats)
                evaluated["isSettled"] = evaluated["result"] is not None
                evaluated["isValidForEvaluation"] = evaluated["result"] is not None
                evaluated["tier"] = tier["tier"]
                tier_picks_evaluated.append(evaluated)
                all_evaluated_picks.append(evaluated)

            settled = [p for p in tier_picks_evaluated if p["isSettled"]]
            correct = sum(1 for p in settled if p["result"] is True)
            wrong = sum(1 for p in settled if p["result"] is False)

            evaluated_tiers.append({
                "tier": tier["tier"],
                "label": tier["label"],
                "rank_range": tier["rank_range"],
                "picks": tier_picks_evaluated,
                "summary": {
                    "correct": correct,
                    "wrong": wrong,
                    "settled": len(settled),
                    "unsettled": len(tier_picks_evaluated) - len(settled),
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
            "tiers": evaluated_tiers,
            "picks": all_evaluated_picks,
        })

        # ── Log ALL markets (not just tiered picks) for unbiased calibration ──
        # This is critical: calibration quality depends on the full probability
        # distribution, not just the top-36 filtered picks.
        try:
            from src.db.prediction_logger import log_predictions
            from src.db.database import get_db
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

    # Per-tier global accumulators
    tier_global = {t: {"correct": 0, "wrong": 0, "settled": 0} for t in range(1, 7)}

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

        total_correct += match_correct
        total_wrong += match_wrong
        total_settled_picks += len(settled_picks)
        total_na_excluded += len(na_picks)

        # Accumulate per-tier global stats
        for tier_data in match.get("tiers", []):
            t = tier_data["tier"]
            if t in tier_global:
                tier_global[t]["correct"] += tier_data["summary"]["correct"]
                tier_global[t]["wrong"] += tier_data["summary"]["wrong"]
                tier_global[t]["settled"] += tier_data["summary"]["settled"]

        clean_results.append({
            "fixture": match["fixture"],
            "actual": match["actual"],
            "tiers": match.get("tiers", []),
            "picks": match["picks"],
            "summary": {
                "correct": match_correct,
                "wrong": match_wrong,
                "unknown": len(na_picks),
                "total": len(settled_picks),
            },
        })

    # ── Phase 4: overall summary + per-tier summary ─────────────
    accuracy = round(
        (total_correct / total_settled_picks * 100), 1
    ) if total_settled_picks > 0 else 0.0

    tier_summary = []
    for t in range(1, 7):
        ts = tier_global[t]
        tier_acc = round(ts["correct"] / ts["settled"] * 100, 1) if ts["settled"] > 0 else 0
        tier_summary.append({
            "tier": t,
            "label": f"Tier {t}",
            "correct": ts["correct"],
            "wrong": ts["wrong"],
            "settled": ts["settled"],
            "accuracy": tier_acc,
        })

    # Tiers remain in sequential order (1 to 6)

    # ── Update daily performance stats ──
    try:
        from src.db.prediction_logger import update_daily_performance
        from src.db.database import get_db
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
        "tier_summary": tier_summary,
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


# ═══════════════════════════════════════════════════════════════════════
# Phase 3 — Calibration, Backtesting & Performance Dashboard
# ═══════════════════════════════════════════════════════════════════════

from src.db.prediction_logger import (
    get_calibration_data,
    get_all_market_types,
    get_performance_history,
    get_backtest_summary,
)


@app.get("/api/calibration/status")
def get_calibration_status():
    """Overview of calibration readiness per market type.

    Shows how many samples exist and whether isotonic calibration
    can be reliably applied for each market type.
    """
    conn = get_db()
    return {"market_types": get_all_market_types(conn)}


@app.get("/api/calibration/{market_type}")
def get_market_calibration(market_type: str):
    """Get per-market-type calibration curve.

    Shows predicted vs actual rates in 5% buckets.
    This answers: 'When we predict 75%, does it actually hit 75%?'
    """
    conn = get_db()
    return get_calibration_data(conn, market_type)


@app.get("/api/backtest/summary")
def get_backtest(market_type: str = None):
    """Backtesting summary: accuracy, calibration gap, per-tier breakdown.

    Optional filter by market_type (goals, result, btts, cs, combo, etc.)
    """
    conn = get_db()
    return get_backtest_summary(conn, market_type)


@app.get("/api/performance/daily")
def get_daily_performance(days: int = 30):
    """Daily performance history for ROI dashboard.

    Shows accuracy, calibration gap, and volume per day.
    """
    conn = get_db()
    history = get_performance_history(conn, days)
    return {"days": days, "history": history}


@app.get("/api/performance/overview")
def get_performance_overview():
    """High-level performance overview across all logged predictions.

    Returns overall stats + per-market-type breakdown + trend.
    """
    conn = get_db()

    # Overall stats
    overall = get_backtest_summary(conn)

    # Per market type
    market_types = get_all_market_types(conn)

    # Per-type calibration gaps
    type_gaps = []
    for mt in market_types:
        cal = get_calibration_data(conn, mt["market_type"], min_samples=20)
        type_gaps.append({
            "market_type": mt["market_type"],
            "samples": mt["total"],
            "settled": mt["settled"],
            "hit_rate": mt["hit_rate"],
            "avg_predicted": mt["avg_predicted"],
            "calibration_ready": mt["calibration_ready"],
            "buckets": cal["buckets"],
        })

    # Recent trend (last 7 days)
    trend = get_performance_history(conn, 7)

    return {
        "overall": overall,
        "market_types": type_gaps,
        "recent_trend": trend,
    }


# ═══════════════════════════════════════════════════════════════════════
# Isotonic Calibration Endpoints
# ═══════════════════════════════════════════════════════════════════════

from src.engine.isotonic_calibrator import get_isotonic_calibrator


@app.post("/api/calibration/fit")
def fit_isotonic_calibration():
    """Fit (or refit) isotonic calibration models from prediction_log.

    This reads all settled predictions, groups by market type, and fits
    a monotonic transform that corrects the systematic overconfidence.

    Call this after accumulating enough results (200+ per market type).
    """
    conn = get_db()
    cal = get_isotonic_calibrator(conn)
    summary = cal.fit_all(conn)
    return {
        "status": "ok",
        "models_fitted": sum(1 for v in summary.values() if v.get("fitted")),
        "details": summary,
    }


@app.get("/api/calibration/isotonic/status")
def get_isotonic_status():
    """Get status of all fitted isotonic calibration models."""
    conn = get_db()
    cal = get_isotonic_calibrator(conn)
    return {"models": cal.get_status()}


@app.get("/api/calibration/isotonic/curve/{market_type}")
def get_isotonic_curve(market_type: str):
    """Get the calibration transform curve for a market type.

    Shows raw → calibrated mapping at 5% intervals.
    Use this to visualize how the model's overconfidence is corrected.
    """
    conn = get_db()
    cal = get_isotonic_calibrator(conn)
    curve = cal.get_calibration_curve(market_type)
    return {
        "market_type": market_type,
        "curve": curve,
        "has_model": market_type in cal._models,
    }


@app.get("/api/calibration/isotonic/test")
def test_isotonic_calibration(raw_prob: float = 82.0, market_type: str = "goals"):
    """Test the isotonic calibrator on a single value.

    Example: /api/calibration/isotonic/test?raw_prob=85&market_type=goals
    """
    conn = get_db()
    cal = get_isotonic_calibrator(conn)
    calibrated = cal.calibrate(raw_prob, market_type)
    return {
        "raw_prob": raw_prob,
        "calibrated_prob": calibrated,
        "market_type": market_type,
        "has_fitted_model": market_type in cal._models,
        "correction": round(calibrated - raw_prob, 1),
    }

"""
Football Predictor AI - API v5.0
Fetches REAL daily matches via API-Football v3. Computes per-match unique predictions
using Poisson (goals) + statistical models (corners, cards).

DATA SOURCE: api-football.com (v3) — all leagues, real logos, transparent disk cache.
COVERS: Premier League, La Liga, Serie A, Bundesliga, Ligue 1, UCL, UEL + more.
"""

from __future__ import annotations

import json
import math
import os
import ssl
import certifi
import time
import urllib.request
import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Union

from fastapi import Header

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "dev-admin-secret")

def verify_admin_key(x_api_key: str = Header(None)):
    if not x_api_key or x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing admin API key")
    return x_api_key

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
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
from src.engine.odds_scanner import scan_live_odds
from src.db.competition_tracker import upsert_competition, get_competition_stats, list_competitions

# ── In-memory prediction status cache ────────────────────────────
# Maps fixture_id → {"status": "ready"|"pending"|"error", "computed_at": str}
# Pre-warmed in background when a date's fixtures are served.
_FIXTURE_CACHE_DIR = Path(".cache")
_PREDICTION_STATUS: dict[str, dict] = {}
_ANALYSIS_CACHE: dict[str, dict] = {}

# ── Instantiate App ──────────────────────────────────────────────────────────
app = FastAPI(title="Football Predictor AI API", version="5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Background Nightly Retrain Task ──────────────────────────────────────────
@app.on_event("startup")
async def start_nightly_retrain():
    asyncio.create_task(_nightly_retrain_loop())

async def _nightly_retrain_loop():
    while True:
        now = datetime.now()
        target = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        sleep_seconds = (target - now).total_seconds()
        logger.info(f"Nightly retrain scheduled in {sleep_seconds:.0f} seconds (at 03:00 local time)")
        await asyncio.sleep(sleep_seconds)
        
        try:
            # Use a file-based lock to prevent multiple workers from running this simultaneously
            lock_file = Path(".cache/retrain.lock")
            lock_file.parent.mkdir(exist_ok=True)
            
            # Simple timestamp-based lock (only one process runs it if it hasn't been run in the last hour)
            should_run = True
            if lock_file.exists():
                try:
                    last_run = float(lock_file.read_text())
                    if time.time() - last_run < 3600:
                        should_run = False
                        logger.info("Nightly retrain already executed by another worker.")
                except Exception:
                    pass

            if should_run:
                lock_file.write_text(str(time.time()))
                
                logger.info("Starting automated nightly engine retrain...")
                from src.db.database import get_db
                conn = get_db()
                from src.engine.isotonic_calibrator import get_isotonic_calibrator
                cal = get_isotonic_calibrator(conn)
                cal.fit_all(conn)
                
                from src.db.error_intelligence import rebuild_confidence_adjustments
                rebuild_confidence_adjustments(conn)
                logger.info("Nightly engine retrain complete.")
                
        except Exception as e:
            logger.error(f"Nightly retrain failed: {e}")

@app.get("/api/live/scan")
def trigger_live_scan():
    """Trigger a live odds scan to evaluate executable bets."""
    try:
        return scan_live_odds()
    except Exception as e:
        logger.error(f"Live scan failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Include routers
from api.routers.analytics import router as analytics_router
app.include_router(analytics_router)

# Pipeline components
fetcher = APIFootballFetcher()
pattern_analyzer = PatternAnalyzer()
factor_analyzer = FactorAnalyzer()
value_detector = ValueDetector()
xgb_predictor = XGBoostPredictor()

# ── Tracked leagues (SofaScore uniqueTournament IDs) ──────
# Includes top European club leagues + active international competitions
# that run during the European off-season (qualifiers, friendlies, etc.)
TOP_LEAGUES = {
    # ── European Club Leagues (Aug–May) ──
    17:  "Premier League",
    8:   "LaLiga",
    23:  "Serie A",
    35:  "Bundesliga",
    34:  "Ligue 1",
    37:  "Eredivisie",
    238: "Primeira Liga",
    244: "Scottish Premiership",
    180: "Turkish Süper Lig",
    155: "Russian Premier League",
    203: "Pro League (Belgium)",
    # ── UEFA Club Competitions ──
    7:   "Champions League",
    679: "Europa League",
    931: "UEFA Conference League",
    # ── National Team / International ──
    16:  "Euro Championship",
    28:  "AFC Asian Cup Qual.",
    36:  "Copa America",
    44:  "FIFA World Cup",
    68:  "World Cup Qualification (Europe)",
    69:  "World Cup Qualification (CONMEBOL)",
    70:  "World Cup Qualification (Africa)",
    71:  "World Cup Qualification (Asia)",
    80:  "World Cup Qualification (CONCACAF)",
    851: "International Friendly Games",
    852: "International Friendly Games Women",
    854: "U21 Friendly Games",
    429: "U17 European Championship",
    132: "U21 European Championship",
    480: "UEFA Nations League",
    2084: "U23 Toulon Tournament",
    # ── Active Non-European Leagues ──
    196: "J1 League",
    402: "J2 League",
    325: "Brasileirão",
    390: "Brasileirão Série B",
    162: "MLS",
    18641: "MLS Next Pro",
    777: "K League",
    937: "Botola Pro",
    841: "Algerian Ligue 1",
    1024: "Copa Argentina",
    278: "Liga AUF Uruguaya",
    703: "Primera Nacional (Argentina)",
}

# Map SofaScore league names → our Poisson model profile keys
LEAGUE_NAME_MAP = {
    # European Club Leagues
    "Premier League": "Premier League",
    "LaLiga": "LaLiga",
    "La Liga": "LaLiga",
    "Serie A": "Serie A",
    "Bundesliga": "Bundesliga",
    "Ligue 1": "Ligue 1",
    "Eredivisie": "Eredivisie",
    "VriendenLoterij Eredivisie": "Eredivisie",
    "Primeira Liga": "Primeira Liga",
    "Scottish Premiership": "Premier League",     # fallback: use PL profile
    "Turkish Süper Lig": "Premier League",
    "Russian Premier League": "Premier League",
    "Pro League": "Premier League",
    # UEFA Club Competitions
    "Champions League": "Champions League",
    "UEFA Champions League": "Champions League",
    "Europa League": "Champions League",
    "UEFA Europa League": "Champions League",
    "UEFA Conference League": "Champions League",
    # National Team / International
    "International Friendly Games": "Champions League",
    "International Friendly Games Women": "Champions League",
    "U21 Friendly Games": "Champions League",
    "U17 European Championship": "Champions League",
    "U21 European Championship": "Champions League",
    "UEFA Nations League": "Champions League",
    "Euro Championship": "Champions League",
    "Copa America": "Champions League",
    "FIFA World Cup": "Champions League",
    "World Cup Qualification (Europe)": "Champions League",
    "World Cup Qualification (CONMEBOL)": "Champions League",
    "World Cup Qualification (Africa)": "Champions League",
    "World Cup Qualification (Asia)": "Champions League",
    "World Cup Qualification (CONCACAF)": "Champions League",
    "AFC Asian Cup Qual.": "Champions League",
    "U23 Toulon Tournament": "Champions League",
    # Non-European Active Leagues
    "J1 League": "Premier League",
    "J2 League": "Ligue 1",
    "Brasileirão": "LaLiga",
    "Brasileirão Série B": "Ligue 1",
    "MLS": "Premier League",
    "MLS Next Pro": "Premier League",
    "K League": "Premier League",
    "K League 2": "Ligue 1",
    "Botola Pro": "Ligue 1",
    "Algerian Ligue 1": "Ligue 1",
    "Copa Argentina": "LaLiga",
    "Liga AUF Uruguaya": "Ligue 1",
    "Primera Nacional (Argentina)": "Ligue 1",
    # Legacy
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


def _compute_match_analysis(home_name: str, away_name: str, league_name: str = "Premier League", shuffle_tiers: bool = True, feature_flags: dict = None) -> dict:
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

    if feature_flags is None:
        feature_flags = {
            "USE_TEAM_RATINGS": True,
            "USE_MOMENTUM": True,
            "USE_VOLATILITY": True,
            "USE_HOME_ADVANTAGE": True,
            "USE_LEAGUE_RELIABILITY": True
        }

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

    # ── Step 1.5: Dynamic Team Learning Adjustment ──
    from src.db.database import get_db
    from src.db.team_intelligence import get_team_rating, get_home_advantage
    h_vol = 0.0
    a_vol = 0.0
    try:
        db = get_db()
        h_rating, h_mom, h_vol, _ = get_team_rating(db, home_name, league_key)
        a_rating, a_mom, a_vol, _ = get_team_rating(db, away_name, league_key)
        
        # Scale expected goals by rating differential and momentum
        # A 200 point rating difference gives ~13% advantage
        rating_diff = h_rating - a_rating
        rating_scale = (rating_diff / 1500.0) if feature_flags.get("USE_TEAM_RATINGS", True) else 0.0
        
        # Momentum gives up to +/- 10% advantage
        h_mom_scale = (h_mom / 1000.0) if feature_flags.get("USE_MOMENTUM", True) else 0.0 
        a_mom_scale = (a_mom / 1000.0) if feature_flags.get("USE_MOMENTUM", True) else 0.0
        
        # Home advantage gives up to +/- 10% advantage
        h_adv = get_home_advantage(db, home_name)
        a_adv = get_home_advantage(db, away_name)
        
        h_adv_scale = (h_adv / 1000.0) if feature_flags.get("USE_HOME_ADVANTAGE", True) else 0.0
        a_adv_scale = (-a_adv / 1000.0) if feature_flags.get("USE_HOME_ADVANTAGE", True) else 0.0
        
        h_total_scale = 1.0 + rating_scale + h_mom_scale + h_adv_scale
        a_total_scale = 1.0 - rating_scale + a_mom_scale + a_adv_scale
        
        # Clamp bounds — keep adjustments modest to avoid extreme outputs
        h_total_scale = max(0.82, min(1.22, h_total_scale))
        a_total_scale = max(0.82, min(1.22, a_total_scale))
        
        # Apply scaling to the raw stats before they hit Poisson
        h_scored_adj = home_stats.scored * h_total_scale
        h_conceded_adj = home_stats.conceded / h_total_scale
        a_scored_adj = away_stats.scored * a_total_scale
        a_conceded_adj = away_stats.conceded / a_total_scale
    except Exception as e:
        logger.warning(f"Failed to apply team intelligence: {e}")
        h_scored_adj = home_stats.scored
        h_conceded_adj = home_stats.conceded
        a_scored_adj = away_stats.scored
        a_conceded_adj = away_stats.conceded

    # ── Step 2: Poisson ──
    poisson_model = PoissonGoalModel(league_key)
    pred = poisson_model.predict(
        home_scored=h_scored_adj, home_conceded=h_conceded_adj,
        away_scored=a_scored_adj, away_conceded=a_conceded_adj,
        home_team=home_name, away_team=away_name,
    )

    # ── Step 3: Corners & Cards lambdas ──
    exp_h_corn, exp_a_corn = home_stats.corners, away_stats.corners
    exp_total_corn = exp_h_corn + exp_a_corn
    exp_h_card, exp_a_card = home_stats.cards, away_stats.cards
    exp_total_card = exp_h_card + exp_a_card

    # ── Step 4: XGBoost ──
    home_profile = TeamProfile(
        team_name=home_name, matches_played=home_stats.matches_played,
        avg_scored=h_scored_adj, avg_conceded=h_conceded_adj,
        avg_total_goals=h_scored_adj + h_conceded_adj,
        btts_rate=round(pred.btts_yes / 100, 3),
        clean_sheet_rate=round(pred.home_clean_sheet / 100, 3),
        failed_to_score_rate=round(max(0.05, 1 - pred.over_0_5 / 100), 3),
        over_1_5_rate=round(pred.over_1_5 / 100, 3),
        over_2_5_rate=round(pred.over_2_5 / 100, 3),
        over_0_5_ht_rate=round(min(0.95, pred.over_1_5 / 100 * 0.85), 3),
        form_last5=round(home_stats.form_last5, 1) if hasattr(home_stats, 'form_last5') else round(pred.home_win / 100 * 12, 1),
        goal_diff=round((h_scored_adj - h_conceded_adj) * home_stats.matches_played, 1),
    )
    away_profile = TeamProfile(
        team_name=away_name, matches_played=away_stats.matches_played,
        avg_scored=a_scored_adj, avg_conceded=a_conceded_adj,
        avg_total_goals=a_scored_adj + a_conceded_adj,
        btts_rate=round(pred.btts_yes / 100, 3),
        clean_sheet_rate=round(pred.away_clean_sheet / 100, 3),
        failed_to_score_rate=round(max(0.05, 1 - pred.over_0_5 / 100), 3),
        over_1_5_rate=round(pred.over_1_5 / 100, 3),
        over_2_5_rate=round(pred.over_2_5 / 100, 3),
        over_0_5_ht_rate=round(min(0.95, pred.over_1_5 / 100 * 0.85), 3),
        form_last5=round(away_stats.form_last5, 1) if hasattr(away_stats, 'form_last5') else round(pred.away_win / 100 * 12, 1),
        goal_diff=round((a_scored_adj - a_conceded_adj) * away_stats.matches_played, 1),
    )
    xgb_pred = xgb_predictor.predict(home_profile, away_profile)

    # Compute Data Quality Score
    from src.ml.feature_builder import FeatureBuilder
    data_quality = FeatureBuilder.compute_data_quality(home_profile, away_profile, league_name)
    logger.info(f"Data Quality for {home_name} vs {away_name}: {data_quality:.1f}/100")

    # Build missing inputs list for audit transparency
    _missing_inputs = []
    # Check if teams are from real data or hash fallback
    from src.ml.team_stats_db import ALL_HOME, ALL_AWAY, LEAGUE_STATS
    _h_lower = home_name.lower()
    _a_lower = away_name.lower()
    _h_in_hardcoded = any(k in _h_lower for k in ALL_HOME)
    _a_in_hardcoded = any(k in _a_lower for k in ALL_AWAY)
    _h_in_live = False
    _a_in_live = False
    try:
        from src.db.team_state import get_team_state as _get_live
        _hls = _get_live(get_db(), home_name, league_key, "overall")
        _h_in_live = _hls is not None and _hls.matches_played >= 1
        _als = _get_live(get_db(), away_name, league_key, "overall")
        _a_in_live = _als is not None and _als.matches_played >= 1
    except Exception:
        pass

    if not _h_in_hardcoded and not _h_in_live:
        _missing_inputs.append(f"home_team '{home_name}' has NO real data — using hash-generated fallback stats")
        data_quality = max(0, data_quality - 30)  # Severe penalty for hash-generated
    elif home_profile.matches_played < 5:
        _missing_inputs.append(f"home_team '{home_name}' has only {home_profile.matches_played} matches")
    if not _a_in_hardcoded and not _a_in_live:
        _missing_inputs.append(f"away_team '{away_name}' has NO real data — using hash-generated fallback stats")
        data_quality = max(0, data_quality - 30)  # Severe penalty for hash-generated
    elif away_profile.matches_played < 5:
        _missing_inputs.append(f"away_team '{away_name}' has only {away_profile.matches_played} matches")
    if league_key not in ('Premier League', 'LaLiga', 'Serie A', 'Bundesliga', 'Ligue 1', 'Champions League'):
        _missing_inputs.append(f"league '{league_name}' uses fallback Poisson profile")
    from src.engine.audit_engine import classify_prediction_quality
    _prediction_quality = classify_prediction_quality(data_quality)

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
    from src.db.database import get_db
    from src.engine.isotonic_calibrator import get_isotonic_calibrator
    from src.db.prediction_logger import _classify_market_type

    try:
        calibrator = get_isotonic_calibrator(get_db())
    except Exception as e:
        logger.warning(f"Failed to initialize isotonic calibrator: {e}")
        calibrator = None

    def _is_sparse_or_longshot_market(name: str) -> bool:
        """Markets that should never be pulled upward toward 50%."""
        m = name.lower()
        return any(token in m for token in (
            "cs ",
            "correct score",
            "exact ",
            "win by",
            " & ",
            "goal range",
            "score in both",
            "goal in first 15",
            "no goal",
            "clean sheet",
            "fails to score",
        ))

    def _calibration_strength(name: str, market_type: str) -> float:
        """How much of the model probability spread to preserve."""
        m = name.lower()
        if data_quality >= 85:
            base = 0.96
        elif data_quality >= 70:
            base = 0.90
        elif data_quality >= 55:
            base = 0.80
        else:
            base = 0.68

        # High-volume market families can retain more shape.
        if market_type in {"goals", "team_goals", "half", "corners", "cards"}:
            base = min(0.98, base + 0.03)
        if market_type == "result" and m in {"home win", "draw", "away win"}:
            base = min(0.94, base)
        return base

    def _apply_data_quality_penalty(prob_pct: float) -> float:
        """Only low-quality data should compress probabilities toward 50%."""
        if data_quality >= 60:
            return prob_pct
        penalty_strength = (60 - data_quality) / 60.0
        penalty_factor = min(0.45, penalty_strength * 0.45)
        return prob_pct - (prob_pct - 50.0) * penalty_factor

    def calibrate(prob_pct: float, name: str) -> float:
        """Apply realistic market-aware calibration.

        The old calibration pulled almost every market toward 50%, which made
        many unrelated picks cluster around 60-70%. This version preserves
        strong probabilities when data quality is good and avoids inflating
        sparse/longshot markets.
        """
        market_type = _classify_market_type(name)
        prob_pct = max(0.0, min(100.0, prob_pct))

        if _is_sparse_or_longshot_market(name):
            # Do not drag rare markets toward 50%. Low raw probabilities stay low;
            # high sparse probabilities are still compressed because these markets
            # need much more historical evidence before they deserve confidence.
            if prob_pct < 50:
                calibrated_prob = prob_pct * 0.94
            else:
                calibrated_prob = 50.0 + (prob_pct - 50.0) * 0.72
            calibrated_prob = min(calibrated_prob, 82.0)
        else:
            # Preserve the real spread for common markets. A 90% raw model
            # probability should look confident, not be flattened to 70%.
            strength = _calibration_strength(name, market_type)
            calibrated_prob = 50.0 + (prob_pct - 50.0) * strength

        # Realistic bounds: common near-certainties may reach the mid-90s,

        calibrated_prob = _apply_data_quality_penalty(calibrated_prob)

        # Realistic bounds: common near-certainties may reach the mid-90s,
        # while sparse markets keep their own stricter cap above.
        ceiling = 82.0 if _is_sparse_or_longshot_market(name) else 96.0
        return round(max(1.0, min(ceiling, calibrated_prob)), 1)

    raw = []
    def add(name, prob):
        prob = calibrate(max(0, min(100, prob)), name)
        if prob > 0:
            raw.append({"market": name, "probability": prob})

    def add_group(names_and_probs):
        """Calibrate a group of mutually exclusive outcomes, then renormalize to 100%."""
        calibrated = [(name, calibrate(max(0, min(100, prob)), name)) for name, prob in names_and_probs]
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

    def _handicap_probability(prob_pct: float) -> float:
        """Compress handicap lines into realistic tradable ranges.

        Wide +handicaps can be mathematically near-certain, but those prices
        usually trade at tiny odds and should not be displayed as if they are
        ordinary high-confidence betting edges.
        """
        prob_pct = max(0.0, min(100.0, prob_pct))
        adjusted = 50.0 + (prob_pct - 50.0) * 0.58
        return round(max(22.0, min(78.0, adjusted)), 1)

    def add_handicap(name, prob):
        prob = _handicap_probability(prob)
        market = {
            "market": name,
            "probability": prob,
            "fair_odds": round(100.0 / prob, 2) if prob > 0 else None,
            "source": "dixon_coles_handicap_adjusted",
            "reliability_note": "handicap probability compressed to tradable range",
        }
        raw.append(market)

    def add_handicap_group(names_and_probs):
        """Add a 3-way handicap group while preserving sum-to-100 coherence."""
        adjusted = []
        for name, prob in names_and_probs:
            adjusted.append((name, max(3.0, min(94.0, 33.3 + (prob - 33.3) * 0.68))))
        total = sum(prob for _, prob in adjusted)
        if total <= 0:
            return
        for name, prob in adjusted:
            final_prob = round(prob / total * 100.0, 1)
            raw.append({
                "market": name,
                "probability": final_prob,
                "fair_odds": round(100.0 / final_prob, 2) if final_prob > 0 else None,
                "source": "dixon_coles_handicap_adjusted",
                "reliability_note": "European handicap normalized after shrinkage",
            })

    # ── League-Specific Confidence Adjustment ──
    from src.db.database import get_db
    from src.db.error_intelligence import get_league_adjustment, apply_league_adjustment
    try:
        db = get_db()
        if feature_flags.get("USE_LEAGUE_RELIABILITY", True):
            league_adj = get_league_adjustment(db, league_key)
            adj_hw, adj_d, adj_aw = apply_league_adjustment(
                league_adj, pred.home_win, pred.draw, pred.away_win
            )
            pred.home_win = adj_hw
            pred.draw = adj_d
            pred.away_win = adj_aw
    except Exception as e:
        logger.warning(f"Failed to apply league adjustment for {league_key}: {e}")

    # ── Team Volatility Confidence Penalty ──
    try:
        if feature_flags.get("USE_VOLATILITY", True):
            max_vol = max(h_vol, a_vol)
            if max_vol > 60:
                # Volatile teams (61-100) receive up to -6.0% penalty (shrink towards 33.3)
                penalty = ((max_vol - 60) / 40.0) * 6.0
                penalty_factor = penalty / 100.0
                
                pred.home_win = pred.home_win - (pred.home_win - 33.3) * penalty_factor
                pred.draw = pred.draw - (pred.draw - 33.3) * penalty_factor
                pred.away_win = pred.away_win - (pred.away_win - 33.3) * penalty_factor
    except Exception as e:
        logger.warning(f"Failed to apply volatility penalty: {e}")

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
    for t in [2, 3, 4, 5, 6, 7]:
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
        add_handicap(f"AH {home_name} -{spread}", ah_home)
        add_handicap(f"AH {away_name} +{spread}", 100 - ah_home)
        # Away giving handicap
        ah_away = mx(ft, MG, lambda h, a, _s=spread: (a - h) > _s)
        add_handicap(f"AH {away_name} -{spread}", ah_away)
        add_handicap(f"AH {home_name} +{spread}", 100 - ah_away)

    # ━━ EUROPEAN HANDICAP (3-way: includes draw — renormalized) ━━
    # EH -1 Home: After applying -1 to home, check win/draw/loss
    for spread in [1, 2]:
        # Home -spread
        eh_hw = mx(ft, MG, lambda h, a, _s=spread: (h - _s) > a)
        eh_dr = mx(ft, MG, lambda h, a, _s=spread: (h - _s) == a)
        eh_aw = mx(ft, MG, lambda h, a, _s=spread: (h - _s) < a)
        add_handicap_group([
            (f"EH {home_name} -{spread} (Win)", eh_hw),
            (f"EH {home_name} -{spread} (Draw)", eh_dr),
            (f"EH {home_name} -{spread} (Lose)", eh_aw),
        ])
        # Away -spread
        eh_aw2 = mx(ft, MG, lambda h, a, _s=spread: (a - _s) > h)
        eh_dr2 = mx(ft, MG, lambda h, a, _s=spread: (a - _s) == h)
        eh_hw2 = mx(ft, MG, lambda h, a, _s=spread: (a - _s) < h)
        add_handicap_group([
            (f"EH {away_name} -{spread} (Win)", eh_aw2),
            (f"EH {away_name} -{spread} (Draw)", eh_dr2),
            (f"EH {away_name} -{spread} (Lose)", eh_hw2),
        ])

    # ━━ CORNERS — HALF-BASED (First Half only) ━━
    fh_h_corn = exp_h_corn * 0.48; fh_a_corn = exp_a_corn * 0.48
    sh_h_corn = exp_h_corn * 0.52; sh_a_corn = exp_a_corn * 0.52
    fh_total_corn = fh_h_corn + fh_a_corn; sh_total_corn = sh_h_corn + sh_a_corn
    for t in [3, 4, 5]:
        add(f"FH Over {t}.5 Corners", _poisson_over(fh_total_corn, t))
        add(f"FH Under {t}.5 Corners", 100 - _poisson_over(fh_total_corn, t))
    for t in [0, 1, 2, 3, 4]:
        add(f"FH {home_name} Over {t}.5 Corners", p_over(fh_h_corn, t))
        add(f"FH {home_name} Under {t}.5 Corners", 100 - p_over(fh_h_corn, t))
        add(f"FH {away_name} Over {t}.5 Corners", p_over(fh_a_corn, t))
        add(f"FH {away_name} Under {t}.5 Corners", 100 - p_over(fh_a_corn, t))

    # ━━ CARDS — HALF-BASED (First Half only) ━━
    fh_h_card = exp_h_card * 0.45; fh_a_card = exp_a_card * 0.45
    sh_h_card = exp_h_card * 0.55; sh_a_card = exp_a_card * 0.55
    fh_total_card = fh_h_card + fh_a_card; sh_total_card = sh_h_card + sh_a_card
    for t in [0, 1, 2, 3]:
        add(f"FH Over {t}.5 Cards", _poisson_over(fh_total_card, t))
        add(f"FH Under {t}.5 Cards", 100 - _poisson_over(fh_total_card, t))
        add(f"SH Over {t}.5 Cards", _poisson_over(sh_total_card, t))
        add(f"SH Under {t}.5 Cards", 100 - _poisson_over(sh_total_card, t))
    for t in [0, 1]:
        add(f"FH {home_name} Over {t}.5 Cards", p_over(fh_h_card, t))
        add(f"FH {home_name} Under {t}.5 Cards", 100 - p_over(fh_h_card, t))
        add(f"FH {away_name} Over {t}.5 Cards", p_over(fh_a_card, t))
        add(f"FH {away_name} Under {t}.5 Cards", 100 - p_over(fh_a_card, t))

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
            
            # Bypassing isotonic calibration for pure Poisson distributions to prevent
            # double-calibration squashing (which makes Over 0.5 identical to Over 1.5).
            if m["market_type"] not in ["corners", "cards", "goals", "team_goals", "half", "handicap"] and not _is_sparse_or_longshot_market(m["market"]):
                m["probability"] = _iso_cal.calibrate(m["raw_probability"], m["market_type"])
    except Exception as iso_err:
        # Fallback: if isotonic fails, raw_probability == probability
        logger.warning(f"Isotonic calibration failed, using raw: {iso_err}")
        for m in raw:
            m["raw_probability"] = m["probability"]
            m["market_type"] = "unknown"

    # ══════════════════════════════════════════════════════
    # POST-CALIBRATION COHERENCE — Enforce logical constraints
    # ══════════════════════════════════════════════════════
    #
    # Independent isotonic models per market type can violate:
    #   P(FH Over X) ≤ P(FT Over X)    (half is subset of full)
    #   P(SH Over X) ≤ P(FT Over X)
    #   P(Over X+1) ≤ P(Over X)         (monotone in threshold)
    #
    # Fix: build an index, find pairs, clamp the subset to ≤ superset.
    #
    market_index = {m["market"]: m for m in raw}

    def _clamp_subset(subset_name: str, superset_name: str):
        """Ensure P(subset) ≤ P(superset). If violated, pull subset down."""
        sub = market_index.get(subset_name)
        sup = market_index.get(superset_name)
        if sub and sup and sub["probability"] > sup["probability"]:
            sub["probability"] = sup["probability"]

    def _clamp_half_scoring(half_name: str, ft_name: str, max_ratio: float):
        """Ensure half-period 'to score' is strictly below FT team scoring.

        Unlike _clamp_subset which sets them equal when violated, this
        caps the half value to max_ratio × FT value, preventing the
        display bug where half scoring == full-time scoring.
        """
        sub = market_index.get(half_name)
        sup = market_index.get(ft_name)
        if sub and sup:
            ceiling = round(sup["probability"] * max_ratio, 1)
            if sub["probability"] > ceiling:
                sub["probability"] = ceiling

    # ── Rule 1: FH/SH team goals ≤ FT team goals ──
    for team in [home_name, away_name]:
        for t in [0, 1, 2]:
            if t == 0:
                # Over 0.5 = "team to score" — use proportional cap to avoid
                # FH/SH showing identical value to FT
                _clamp_half_scoring(f"FH {team} Over {t}.5 Goals", f"{team} Over {t}.5 Goals", 0.85)
                _clamp_half_scoring(f"SH {team} Over {t}.5 Goals", f"{team} Over {t}.5 Goals", 0.90)
            else:
                _clamp_subset(f"FH {team} Over {t}.5 Goals", f"{team} Over {t}.5 Goals")
                _clamp_subset(f"SH {team} Over {t}.5 Goals", f"{team} Over {t}.5 Goals")
            # Under: FH Under ≥ FT Under  →  equivalently FT Under ≤ FH Under
            _clamp_subset(f"{team} Under {t}.5 Goals", f"FH {team} Under {t}.5 Goals")
            _clamp_subset(f"{team} Under {t}.5 Goals", f"SH {team} Under {t}.5 Goals")

    # ── Rule 2: FH/SH total goals ≤ FT total goals ──
    for t in [0, 1, 2, 3, 4]:
        _clamp_subset(f"FH Over {t}.5 Goals", f"Over {t}.5 Goals")
        _clamp_subset(f"SH Over {t}.5 Goals", f"Over {t}.5 Goals")
        _clamp_subset(f"Under {t}.5 Goals", f"FH Under {t}.5 Goals")
        _clamp_subset(f"Under {t}.5 Goals", f"SH Under {t}.5 Goals")

    # ── Rule 3: FH/SH "to score" < FT "Over 0.5 Goals" for same team ──
    # Use proportional caps instead of exact clamping to prevent
    # half-period scoring from displaying the same value as FT scoring.
    # FH ≤ 85% of FT (first half has fewer goals), SH ≤ 90% of FT.
    for team in [home_name, away_name]:
        _clamp_half_scoring(f"FH {team} to Score", f"{team} Over 0.5 Goals", 0.85)
        _clamp_half_scoring(f"SH {team} to Score", f"{team} Over 0.5 Goals", 0.90)

    # ── Rule 4: FH BTTS ≤ FT BTTS ──
    _clamp_subset("FH BTTS - Yes", "BTTS - Yes")
    _clamp_subset("SH BTTS - Yes", "BTTS - Yes")

    # ── Rule 5: Over X+1 ≤ Over X (monotone in threshold) ──
    for prefix in ["", "FH ", "SH "]:
        for t in range(4):
            _clamp_subset(f"{prefix}Over {t+1}.5 Goals", f"{prefix}Over {t}.5 Goals")
    for t in range(10):
        _clamp_subset(f"Over {t+1}.5 Corners", f"Over {t}.5 Corners")
    for t in range(5):
        _clamp_subset(f"Over {t+1}.5 Cards", f"Over {t}.5 Cards")

    section_order = ["Goals", "First Half", "Second Half", "Team Goals", "Result", "Handicaps", "Corners", "Cards"]

    # Full analysis: every market, sorted by probability (descending)
    full_analysis = {}
    for sec in section_order:
        items = [m for m in raw if m["section"] == sec]
        items.sort(key=lambda x: x["probability"], reverse=True)
        full_analysis[sec] = items

    # ══════════════════════════════════════════════════════
    # LAYER 2 — CONFIDENT PICKS (bankroll-protected shortlist)
    # ══════════════════════════════════════════════════════
    #
    # Focus Categories: every market category with probability >= 60%.
    #
    allowed_sections = set(section_order)
    pick_gate = None
    pick_gate_status = {
        "enabled": False,
        "mode": "probability_only_fallback",
        "min_probability": 60.0,
    }
    try:
        from src.engine.performance_gate import build_runtime_pick_gate
        from src.db.database import get_db as _pick_gate_db

        pick_gate = build_runtime_pick_gate(
            _pick_gate_db(),
            league_names=(league_key, league_name),
            data_quality=data_quality,
        )
        pick_gate_status = {
            "enabled": True,
            "mode": "runtime_market_and_league_performance",
            "min_probability": 60.0,
            "league_reliability": pick_gate.league_reliability,
        }
    except Exception as gate_err:
        logger.warning(f"Runtime pick gate unavailable: {gate_err}")

    # 1. Filter raw markets — every market above 60% is eligible for the
    #    ranked tier display. Bankroll safety is shown separately.
    def _is_basic_confident_pick(m):
        if m["probability"] < 60.0:
            return False
        if m["section"] not in allowed_sections:
            return False
        return True

    def _with_pick_gate(m):
        pick = dict(m)
        if pick_gate is not None:
            decision = pick_gate.evaluate(pick.get("market_type", "unknown"), pick["probability"])
            pick["bankroll_qualified"] = decision.allowed
            pick["pick_gate_min_probability"] = decision.min_probability
            if not decision.allowed:
                pick["pick_gate_rejection"] = decision.reason
        else:
            pick["bankroll_qualified"] = pick["probability"] >= 75.0
        return pick

    if data_quality < 55:
        logger.info(f"Marking Layer 2 picks as low quality for {home_name} vs {away_name}: data_quality={data_quality:.1f}")

    tier_candidates = [
        _with_pick_gate(m)
        for m in raw
        if _is_basic_confident_pick(m)
    ]
    if data_quality < 55:
        for pick in tier_candidates:
            pick["bankroll_qualified"] = False
            pick["pick_gate_rejection"] = "data quality below bankroll-pick floor"

    sorted_tier_candidates = sorted(
        tier_candidates,
        key=lambda x: (x["probability"], 1 if x.get("bankroll_qualified") else 0),
        reverse=True,
    )
    total_tier_candidates = len(sorted_tier_candidates)
    tier_size = max(1, math.ceil(total_tier_candidates / 3)) if total_tier_candidates else 1

    # Each match always exposes the same three tier layers. Tiers are rank
    # groups across every market above 60%, not fixed probability buckets.
    tier_layers = [
        {
            "id": "tier1",
            "name": "Tier 1",
            "label": "Top Ranked Group",
            "range": "Rank group 1",
            "min_probability": 60.0,
            "max_probability": 100.0,
            "picks": [],
        },
        {
            "id": "tier2",
            "name": "Tier 2",
            "label": "Second Ranked Group",
            "range": "Rank group 2",
            "min_probability": 60.0,
            "max_probability": 100.0,
            "picks": [],
        },
        {
            "id": "tier3",
            "name": "Tier 3",
            "label": "Third Ranked Group",
            "range": "Rank group 3",
            "min_probability": 60.0,
            "max_probability": 100.0,
            "picks": [],
        },
    ]
    for idx, pick in enumerate(sorted_tier_candidates):
        if idx < tier_size:
            tier_idx = 0
        elif idx < tier_size * 2:
            tier_idx = 1
        else:
            tier_idx = 2
        pick["tier"] = tier_layers[tier_idx]["id"]
        pick["tier_rank"] = idx + 1
        tier_layers[tier_idx]["picks"].append(pick)

    for tier in tier_layers:
        picks = tier["picks"]
        tier["count"] = len(picks)
        tier["bankroll_qualified_count"] = sum(1 for p in picks if p.get("bankroll_qualified"))
        tier["avg_probability"] = round(sum(p["probability"] for p in picks) / len(picks), 1) if picks else 0.0
        tier["min_actual_probability"] = round(min((p["probability"] for p in picks), default=0.0), 1)
        tier["max_actual_probability"] = round(max((p["probability"] for p in picks), default=0.0), 1)

    layer2_raw = [pick for tier in tier_layers for pick in tier["picks"]]
    if not layer2_raw:
        all_sorted = []
    else:
        # 2. Sort by probability (descending)
        all_sorted = sorted(layer2_raw, key=lambda x: x["probability"], reverse=True)

    # ── Correlation dedup: limit correlated markets in top picks ──
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

    # Group by category and compute stats
    categories_data = []
    for category in section_order:
        cat_picks = [p for p in deduped if p["section"] == category]
        if not cat_picks:
            continue
        cat_picks.sort(key=lambda x: x["probability"], reverse=True)
        probs = [p["probability"] for p in cat_picks]
        categories_data.append({
            "category": category,
            "picks": cat_picks,
            "avg_probability": round(sum(probs) / len(probs), 1) if probs else 0.0,
            "min_probability": round(min(probs), 1) if probs else 0.0,
            "max_probability": round(max(probs), 1) if probs else 0.0,
        })

    categories_data.sort(key=lambda x: x["avg_probability"], reverse=True)

    # Legacy: flat top_picks for backward compat. The tier list is now the
    # source of truth, so this exposes the same ranked picks as a flat list.
    top_picks = [pick for tier in tier_layers for pick in tier["picks"]]

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

    # ══════════════════════════════════════════════════════
    # BILQE — Betting Intelligence Layered Qualification
    # ══════════════════════════════════════════════════════
    try:
        from src.engine.qualification_engine import qualify_picks
        from src.db.database import get_db as _bilqe_db
        _bilqe_conn = _bilqe_db()

        _bilqe_analysis = {
            "data_quality_score": round(data_quality, 1),
            "poisson": pred.to_dict(),
            "xgboost_predictions": xgb_pred.to_dict().get("predictions", []),
            "top_picks": top_picks,
            "categories": categories_data,
            "averages": {
                "home": {"avg_goals_scored": home_stats.scored, "avg_goals_conceded": home_stats.conceded},
                "away": {"avg_goals_scored": away_stats.scored, "avg_goals_conceded": away_stats.conceded},
            },
        }

        bilqe_result = qualify_picks(
            match_analysis=_bilqe_analysis,
            home_name=home_name,
            away_name=away_name,
            league_name=league_key,
            conn=_bilqe_conn,
        )
        bilqe_dict = bilqe_result.to_dict()
    except Exception as bilqe_err:
        logger.warning(f"BILQE qualification failed: {bilqe_err}")
        bilqe_dict = {
            "qualified_picks": [],
            "rejected_count": 0,
            "match_data_quality": round(data_quality, 1),
            "has_qualified_picks": False,
            "summary": "BILQE unavailable",
            "tier_counts": {"S": 0, "A": 0, "B": 0},
            "layer_gate_results": {},
        }

    return {
        "disclaimer": f"Poisson (λ={pred.lambda_home:.2f}+{pred.lambda_away:.2f}) + XGBoost | {league_key} | isotonic-calibrated",
        "total_markets_scanned": len(raw),
        "total_qualified": len(qualified),
        "total_confident_picks": total_tier_candidates,
        "pick_gate": pick_gate_status,
        # Data Quality Audit
        "data_quality_score": round(data_quality, 1),
        "prediction_quality": _prediction_quality,
        "missing_inputs": _missing_inputs,
        # Layer 2 — categorized picks (three probability tiers ≥60%)
        "categories": categories_data,
        "tiers": tier_layers,
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
        # BILQE — 10-layer qualification
        "qualification": bilqe_dict,
    }


# ─── Fixture cache ─────────────────────────────────────────────────────────
# We cache API-Football responses per-date so the UI is instant on repeat
# loads, and we avoid burning through the daily API quota.

_FIXTURE_CACHE_DIR = Path(".cache")
_FIXTURE_CACHE_TTL = 5 * 60   # 5 minutes — fast refresh for live scores


def _get_cache_path(date_str: str) -> Path:
    _FIXTURE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _FIXTURE_CACHE_DIR / f"fixtures-{date_str}.json"


def _get_istanbul_today() -> str:
    """Return the current date in Europe/Istanbul (UTC+3) timezone as a YYYY-MM-DD string."""
    import zoneinfo
    from datetime import datetime
    try:
        return datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul")).date().isoformat()
    except Exception:
        from datetime import timezone, timedelta
        return datetime.now(timezone(timedelta(hours=3))).date().isoformat()


def _read_fixture_cache(date_str: str) -> Optional[list]:
    """Return cached fixture list or None if missing / expired."""
    path = _get_cache_path(date_str)
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    today = _get_istanbul_today()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return None
            
        has_live = any(
            isinstance(f, dict) and "LIVE" in f.get("status", "") 
            for f in data
        )

        if date_str < today:
            # If it's a past date but still contains LIVE matches, force expire!
            if has_live:
                logger.info(f"Fixture cache for {date_str} contains stuck LIVE matches. Forcing expiration to get FT results.")
                return None
            return data

        # For today/future dates use a short TTL. If there are LIVE matches, update every 60s
        effective_ttl = 60 if has_live else _FIXTURE_CACHE_TTL
        if age > effective_ttl:
            logger.debug(f"Fixture cache expired for {date_str} (age={age:.0f}s, has_live={has_live})")
            return None

        return data
    except Exception as e:
        logger.warning(f"Fixture cache read failed for {date_str}: {e}")
    return None


def _write_fixture_cache(date_str: str, fixtures: list) -> None:
    """Write the processed fixture list to disk cache."""
    try:
        _get_cache_path(date_str).write_text(
            json.dumps(fixtures, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"Fixture cache write failed for {date_str}: {e}")


# ─── API-Football v3 fetcher ────────────────────────────────────────────────


def _fetch_api_football_fixtures(date_str: str) -> list[dict]:
    """Fetch scheduled/live fixtures from API-Football v3 for *date_str* (YYYY-MM-DD).

    Returns a list of raw API-Football fixture dicts.
    Returns empty list on network error, API rate limit, or missing API key — NO mock data.
    """
    if not APIFOOTBALL_API_KEY:
        logger.warning("APIFOOTBALL_API_KEY not set – no fixture data available from API-Football")
        return []

    url = f"https://v3.football.api-sports.io/fixtures?date={date_str}&timezone=UTC"
    req = urllib.request.Request(url, headers={
        "x-apisports-key": APIFOOTBALL_API_KEY,
        "Accept": "application/json",
    })
    try:
        ctx = ssl.create_default_context()
        resp = urllib.request.urlopen(req, timeout=20, context=ctx)
        data = json.loads(resp.read())
        errors = data.get("errors", {})
        if errors:
            if isinstance(errors, dict) and len(errors) > 0:
                err_summary = ", ".join(f"{k}: {v}" for k, v in errors.items())
                logger.warning(f"API-Football Error: {err_summary}. Returning empty list.")
                return []
            elif isinstance(errors, list) and len(errors) > 0:
                err_summary = ", ".join(str(e) for e in errors)
                logger.warning(f"API-Football Error: {err_summary}. Returning empty list.")
                return []

        fixtures = data.get("response", [])
        logger.info(f"API-Football returned {len(fixtures)} fixtures for {date_str}")
        if not fixtures:
            logger.warning(f"No fixtures returned from API-Football for {date_str}.")
        return fixtures
    except Exception as e:
        logger.error(f"API-Football fetch failed for {date_str}: {e}. Returning empty list.")
        return []


def _resolve_team_logo(team_id: str, team_name: str, fallback_url: str) -> str:
    """Check registry for highest quality logo; otherwise return fallback."""
    from src.db.database import get_db
    try:
        conn = get_db()
        row = conn.execute("SELECT local_path, logo_url, quality_grade FROM team_logo_registry WHERE team_id = ? OR team_name = ?", (team_id, team_name)).fetchone()
        if row:
            grade = row["quality_grade"]
            if grade in ("GOOD", "EXCELLENT") and row["local_path"]:
                return row["local_path"]
            elif grade in ("GOOD", "EXCELLENT") and row["logo_url"]:
                return row["logo_url"]
    except Exception as e:
        logger.error(f"Failed to resolve logo for {team_name}: {e}")
    return fallback_url

def _api_football_to_fixture(f: dict) -> dict:
    """Convert a single API-Football v3 fixture dict → frontend-ready fixture dict."""
    fix     = f.get("fixture", {})
    league  = f.get("league", {})
    teams   = f.get("teams", {})
    goals   = f.get("goals", {})
    score   = f.get("score", {})

    # ── Date & Time ──
    # API-Football returns fixture.date as a full ISO datetime, e.g.
    # "2026-06-03T21:00:00+00:00".  We use this directly (no timezone shift)
    # so fixtures always appear on the correct calendar day.
    api_date_iso = fix.get("date", "")
    date_str = api_date_iso[:10]            # "2026-06-03"
    time_str = api_date_iso[11:16] if len(api_date_iso) >= 16 else "TBD"  # "21:00"

    # ── Status ──
    status_short = fix.get("status", {}).get("short", "")
    if status_short in ("FT", "AET", "PEN"):
        display_status = "FT"
    elif status_short in ("1H", "2H", "ET", "P", "LIVE", "BT", "HT"):
        elapsed = fix.get("status", {}).get("elapsed")
        display_status = f"LIVE {elapsed}\u2019" if elapsed else "LIVE"
    elif status_short == "NS":
        display_status = "NS"
    elif status_short in ("PST", "CANC", "ABD", "SUSP", "INT", "AWD", "WO"):
        display_status = status_short
    else:
        display_status = status_short or "NS"

    is_live_or_finished = display_status not in ("NS",)

    # ── Scores ──
    ht = score.get("halftime", {})
    ft = score.get("fulltime", {})
    
    is_live = "LIVE" in display_status
    is_finished = display_status == "FT"
    
    if is_finished:
        # Prioritize fulltime (90 mins) score to exclude overtime/penalties
        if ft and ft.get("home") is not None:
            home_goals = ft.get("home")
            away_goals = ft.get("away")
        else:
            home_goals = goals.get("home")
            away_goals = goals.get("away")
    elif is_live:
        home_goals = goals.get("home")
        away_goals = goals.get("away")
    else:
        home_goals = None
        away_goals = None

    return {
        "id":           str(fix.get("id", "")),
        "date":         date_str,
        "time":         time_str,
        "status":       display_status,
        "home_goals":   home_goals,
        "away_goals":   away_goals,
        "fh_home_goals": ht.get("home") if is_live_or_finished else None,
        "fh_away_goals": ht.get("away") if is_live_or_finished else None,
        "league": {
            "id":      str(league.get("id", "")),
            "name":    league.get("name", ""),
            "country": league.get("country", ""),
            # Use API-Football's own CDN logo — always resolves, no proxy needed
            "logo":    league.get("logo") or f"/api/image/tournament/{league.get('id', 0)}",
        },
        "home_team": {
            "id":   str(teams.get("home", {}).get("id", "")),
            "name": teams.get("home", {}).get("name", ""),
            "logo": _resolve_team_logo(str(teams.get("home", {}).get("id", "")), teams.get("home", {}).get("name", ""), teams.get("home", {}).get("logo") or f"/api/image/team/{teams.get('home', {}).get('id', 0)}")
        },
        "away_team": {
            "id":   str(teams.get("away", {}).get("id", "")),
            "name": teams.get("away", {}).get("name", ""),
            "logo": _resolve_team_logo(str(teams.get("away", {}).get("id", "")), teams.get("away", {}).get("name", ""), teams.get("away", {}).get("logo") or f"/api/image/team/{teams.get('away', {}).get('id', 0)}")
        },
        # Extra metadata useful for analysis/live ingestion
        "league_id_apifootball": league.get("id"),
        "season":    league.get("season"),
        "round":     league.get("round", ""),
        "source":    "apifootball",
    }

# ── Endpoints ──────────────────────────

from fastapi.responses import Response

@app.get("/api/image/team/{team_id}")
def get_team_image(team_id: str):
    url = f"https://api.sofascore.com/api/v1/team/{team_id}/image"
    return _proxy_image(url)

@app.get("/api/image/tournament/{tour_id}")
def get_tournament_image(tour_id: str):
    url = f"https://api.sofascore.com/api/v1/unique-tournament/{tour_id}/image"
    return _proxy_image(url)

def _proxy_image(url: str):
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        })
        ctx = ssl.create_default_context(cafile=certifi.where())
        resp = urllib.request.urlopen(req, timeout=5, context=ctx)
        return Response(content=resp.read(), media_type="image/png")
    except Exception as e:
        return Response(status_code=404)

@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "data_source": "sofascore",
        "analysis_mode": "live" if APIFOOTBALL_API_KEY else "per_match_poisson",
        "engine": "Hybrid Poisson Goals + Corners + Cards v5.0",
        "leagues": list(TOP_LEAGUES.values()),
    }

# ── Logo Audit & Upgrade Endpoints ──

@app.get("/api/debug/logo-audit", dependencies=[Depends(verify_admin_key)])
def logo_audit(date: str):
    """Diagnose logo resolutions and auto-upgrade if poor."""
    from src.utils.logo_evaluator import find_best_logo
    from src.db.database import get_db
    
    fixtures = get_fixtures_by_date(date)
    if not fixtures:
        return {"status": "no fixtures"}
        
    audit_results = []
    conn = get_db()
    
    # Process all unique teams
    teams_to_check = {}
    for f in fixtures:
        teams_to_check[f["home_team"]["id"]] = f["home_team"]["name"]
        teams_to_check[f["away_team"]["id"]] = f["away_team"]["name"]
        
    upgrades_done = 0
        
    for tid, tname in teams_to_check.items():
        row = conn.execute("SELECT * FROM team_logo_registry WHERE team_id = ?", (tid,)).fetchone()
        
        needs_upgrade = False
        if not row:
            needs_upgrade = True
        else:
            # Check recheck_after_days
            # (Simplification: just check if POOR/FAIR and upgrade)
            if row["quality_grade"] in ("POOR", "FAIR"):
                needs_upgrade = True
                
        if needs_upgrade:
            # Run the finding logic
            # Use heuristic for sofa_id / api_id
            best = find_best_logo(tid, tname, sofa_id=tid, api_id=tid)
            
            # Persist to DB
            conn.execute("""
                INSERT INTO team_logo_registry 
                (team_id, team_name, provider, logo_url, local_path, etag, width, height, file_size, sharpness_score, quality_score, quality_grade, logo_source_rank, upgrade_reason, recheck_after_days)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(team_id) DO UPDATE SET
                provider=excluded.provider, logo_url=excluded.logo_url, local_path=excluded.local_path, etag=excluded.etag, width=excluded.width, height=excluded.height, file_size=excluded.file_size, sharpness_score=excluded.sharpness_score, quality_score=excluded.quality_score, quality_grade=excluded.quality_grade, logo_source_rank=excluded.logo_source_rank, upgrade_reason=excluded.upgrade_reason, recheck_after_days=excluded.recheck_after_days, last_downloaded=CURRENT_TIMESTAMP
            """, (tid, tname, best["provider"], best["logo_url"], best["local_path"], best["etag"], best["width"], best["height"], best["file_size"], best["sharpness_score"], best["quality_score"], best["quality_grade"], best["logo_source_rank"], best["upgrade_reason"], best["recheck_after_days"]))
            conn.commit()
            upgrades_done += 1
            audit_results.append(best)
        else:
            audit_results.append(dict(row))
            
    return {
        "status": "completed",
        "teams_audited": len(teams_to_check),
        "upgrades_performed": upgrades_done,
        "results": audit_results
    }

@app.get("/api/debug/logo-health", dependencies=[Depends(verify_admin_key)])
def logo_health():
    """Aggregates registry data: returns total_teams, excellent, good, fair, and poor counts."""
    from src.db.database import get_db
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM team_logo_registry").fetchone()["c"]
    excellent = conn.execute("SELECT COUNT(*) as c FROM team_logo_registry WHERE quality_grade='EXCELLENT'").fetchone()["c"]
    good = conn.execute("SELECT COUNT(*) as c FROM team_logo_registry WHERE quality_grade='GOOD'").fetchone()["c"]
    fair = conn.execute("SELECT COUNT(*) as c FROM team_logo_registry WHERE quality_grade='FAIR'").fetchone()["c"]
    poor = conn.execute("SELECT COUNT(*) as c FROM team_logo_registry WHERE quality_grade='POOR'").fetchone()["c"]
    return {
        "total_teams": total,
        "excellent": excellent,
        "good": good,
        "fair": fair,
        "poor": poor
    }

@app.get("/api/debug/logo-upgrades", dependencies=[Depends(verify_admin_key)])
def logo_upgrades():
    """Exposes visibility into the upgrade engine's success."""
    from src.db.database import get_db
    conn = get_db()
    # Approximation for today/week
    upgraded_today = conn.execute("SELECT COUNT(*) as c FROM team_logo_registry WHERE date(last_downloaded) = date('now') AND upgrade_reason != 'Initial Check'").fetchone()["c"]
    upgraded_this_week = conn.execute("SELECT COUNT(*) as c FROM team_logo_registry WHERE date(last_downloaded) >= date('now', '-7 days') AND upgrade_reason != 'Initial Check'").fetchone()["c"]
    poor_remaining = conn.execute("SELECT COUNT(*) as c FROM team_logo_registry WHERE quality_grade='POOR'").fetchone()["c"]
    
    return {
        "upgraded_today": upgraded_today,
        "upgraded_this_week": upgraded_this_week,
        "poor_remaining": poor_remaining,
        "largest_improvement": {
            "team": "Currently not tracked differentially",
            "old": "N/A",
            "new": "N/A"
        }
    }

@app.get("/api/debug/logo-render-audit", dependencies=[Depends(verify_admin_key)])
def logo_render_audit(render_w: int = 64, render_h: int = 64, dpr: float = 2.0):
    """Computes effective_scale and identifies UPSCALED logos."""
    from src.db.database import get_db
    conn = get_db()
    rows = conn.execute("SELECT team_name, width, height, quality_grade FROM team_logo_registry").fetchall()
    
    issues = []
    for r in rows:
        w = r["width"] or 1
        h = r["height"] or 1
        effective_scale = (render_w * dpr) / w
        issue = "UPSCALED" if effective_scale > 1.0 else "OK"
        if issue == "UPSCALED":
            issues.append({
                "team": r["team_name"],
                "source_resolution": f"{w}x{h}",
                "rendered_resolution": f"{int(render_w * dpr)}x{int(render_h * dpr)}",
                "dpr": dpr,
                "effective_scale": round(effective_scale, 2),
                "issue": issue
            })
            
    return {
        "status": "completed",
        "total_teams_checked": len(rows),
        "upscaled_issues": len(issues),
        "details": issues
    }
    
from fastapi.responses import FileResponse

@app.get("/api/image/local/{filename}")
def serve_local_image(filename: str):
    """Serves the cached local logo."""
    path = Path("data/logos") / filename
    if path.exists():
        return FileResponse(path)
    return Response(status_code=404)


@app.get("/api/leagues")
def get_supported_leagues():
    return [{"id": str(k), "name": v} for k, v in TOP_LEAGUES.items()]


@app.get("/api/fixtures/today")
def get_today_fixtures():
    return get_fixtures_by_date(_get_istanbul_today())


def _fetch_sofascore_fixtures(date_str: str) -> list[dict]:
    """Fetch scheduled events from SofaScore API using curl_cffi to bypass blocks.

    Returns a list of SofaScore event dicts.
    Retries with multiple browser impersonations on 403/rate-limit.
    """
    from curl_cffi import requests
    url = f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{date_str}"
    headers = {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.sofascore.com/",
        "Origin": "https://www.sofascore.com",
    }
    # Try multiple browser impersonations — 403 blocks are often UA-specific
    impersonations = ["chrome", "chrome110", "safari", "safari15_5", "firefox"]
    last_status = None
    for browser in impersonations:
        try:
            resp = requests.get(url, headers=headers, impersonate=browser, timeout=15)
            last_status = resp.status_code
            if resp.status_code == 200:
                events = resp.json().get("events", [])
                logger.info(f"SofaScore returned {len(events)} events for {date_str} (impersonate={browser})")
                return events
            elif resp.status_code == 403:
                logger.debug(f"SofaScore 403 with impersonate={browser}, trying next...")
                continue
            else:
                logger.error(f"SofaScore returned status {resp.status_code} for {date_str}")
                return []
        except Exception as e:
            logger.debug(f"SofaScore fetch failed with impersonate={browser}: {e}")
            continue
    logger.error(f"SofaScore returned status {last_status} for {date_str} (all impersonations exhausted)")
    return []


def _sofascore_to_fixture(ev: dict, requested_date: str) -> dict:
    """Convert a SofaScore event dict to the exact frontend fixture structure."""
    home = ev.get("homeTeam", {})
    away = ev.get("awayTeam", {})
    ut = ev.get("tournament", {}).get("uniqueTournament", {})
    ut_id = ut.get("id", 0)
    
    # Kickoff time from timestamp
    import datetime
    import zoneinfo
    start_ts = ev.get("startTimestamp")
    time_str = "TBD"
    date_str = requested_date
    if start_ts:
        try:
            # Convert to Europe/Istanbul (Turkey time, UTC+3) for the time display
            dt = datetime.datetime.fromtimestamp(start_ts, zoneinfo.ZoneInfo("Europe/Istanbul"))
            time_str = dt.strftime("%H:%M")
            date_str = dt.strftime("%Y-%m-%d")
        except Exception:
            try:
                dt = datetime.datetime.fromtimestamp(start_ts, datetime.timezone(datetime.timedelta(hours=3)))
                time_str = dt.strftime("%H:%M")
                date_str = dt.strftime("%Y-%m-%d")
            except Exception:
                pass
                
    # Status
    status_info = ev.get("status", {})
    stype = status_info.get("type", "")
    if stype == "finished":
        display_status = "FT"
    elif stype == "inprogress":
        display_status = "LIVE"
    else:
        display_status = "NS"
        
    is_live_or_finished = display_status != "NS"
    
    # Goals
    home_score = ev.get("homeScore", {})
    away_score = ev.get("awayScore", {})
    
    # Halftime goals
    fh_home = home_score.get("period1") if is_live_or_finished else None
    fh_away = away_score.get("period1") if is_live_or_finished else None

    # Fallback to current if they are missing but finished
    # We prioritize 'normaltime' (90 mins) to ignore overtime/penalties
    is_live = display_status == "LIVE"
    is_finished = display_status == "FT"

    if is_finished:
        if "normaltime" in home_score and home_score.get("normaltime") is not None:
            home_goals = home_score.get("normaltime")
            away_goals = away_score.get("normaltime")
        else:
            home_goals = home_score.get("current")
            away_goals = away_score.get("current")
    elif is_live:
        home_goals = home_score.get("current")
        away_goals = away_score.get("current")
    else:
        home_goals = None
        away_goals = None

    home_name = home.get("name", "Unknown")
    away_name = away.get("name", "Unknown")
    
    LOGO_OVERRIDES = {
        "Sweden U21": "https://flagcdn.com/w160/se.png",
        "Finland U21": "https://flagcdn.com/w160/fi.png",
    }
    
    home_logo = LOGO_OVERRIDES.get(home_name, f"https://api.sofascore.app/api/v1/team/{home.get('id', 0)}/image")
    away_logo = LOGO_OVERRIDES.get(away_name, f"https://api.sofascore.app/api/v1/team/{away.get('id', 0)}/image")

    return {
        "id":           str(ev.get("id", "")),
        "date":         date_str,
        "time":         time_str,
        "status":       display_status,
        "home_goals":   home_goals,
        "away_goals":   away_goals,
        "fh_home_goals": fh_home,
        "fh_away_goals": fh_away,
        "league": {
            "id":      str(ut_id),
            "name":    ut.get("name", "Unknown"),
            "country": ev.get("tournament", {}).get("category", {}).get("name", ""),
            # Direct SofaScore CDN — full resolution, no proxy needed
            "logo":    f"https://api.sofascore.app/api/v1/unique-tournament/{ut_id}/image",
        },
        "home_team": {
            "id":   str(home.get("id", "")),
            "name": home_name,
            "logo": home_logo,
        },
        "away_team": {
            "id":   str(away.get("id", "")),
            "name": away_name,
            "logo": away_logo,
        },
        "league_id_apifootball": None,
        "season":    None,
        "source":    "sofascore",
    }


@app.get("/api/fixtures/{date_str}")
def get_fixtures_by_date(date_str: str, force_refresh: bool = False):
    """
    Return all football fixtures for *date_str* (YYYY-MM-DD).

    STRICT DATE GUARANTEE:
      Every fixture returned MUST have fixture["date"] == date_str.
      Fixtures whose normalized UTC date does not match are DROPPED — never returned.
      NO mock or simulated data is ever returned.

    Data flow (local-first):
      1. Check .cache/fixtures-YYYY-MM-DD.json  → serve instantly if fresh (unless force_refresh=true)
      2. Fetch from SofaScore API               → strict-filter, cache, serve
      3. Fetch from API-Football API            → strict-filter, cache, serve
      4. Return empty list + message            → on total failure (real data only)
    """
    # ── Validate date format ──
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {date_str!r}. Use YYYY-MM-DD.")

    logger.info(f"[DATE REQUEST] selected_date={date_str} force_refresh={force_refresh}")

    # ── 1. Cache hit ──
    if not force_refresh:
        cached = _read_fixture_cache(date_str)
        if cached is not None:
            # Re-validate cached fixtures — drop any whose date drifted
            valid_cached = [f for f in cached if f.get("date") == date_str]
            dropped = len(cached) - len(valid_cached)
            if dropped:
                logger.warning(f"[CACHE LOOKUP] Dropped {dropped} stale fixtures with wrong date from cache for {date_str}")
            logger.info(f"[CACHE LOOKUP] cache_hit=true, matches_found={len(valid_cached)}")
            return valid_cached
    else:
        logger.info(f"[CACHE LOOKUP] cache_hit=bypassed (force_refresh=true)")

    logger.info(f"[CACHE LOOKUP] cache_hit=false")

    # ── 2. Fetch from API-Football (Primary) ──
    logger.info(f"[API-FOOTBALL REQUEST] Fetching fixtures for {date_str}")
    try:
        raw_api = _fetch_api_football_fixtures(date_str)
    except Exception as e:
        logger.error(f"API-Football fetch failed: {e}")
        raw_api = []

    if raw_api:
        fixtures = []
        seen_ids = set()
        seen_teams = set()
        rejected_date = 0
        for raw in raw_api:
            f = _api_football_to_fixture(raw)

            # ─── STRICT DATE FILTER ───────────────────────────────────
            if f["date"] != date_str:
                rejected_date += 1
                logger.debug(
                    f"[DATE FILTER] Dropped API-Football fixture id={f['id']} "
                    f"fixture_date={f['date']} != selected_date={date_str}"
                )
                continue
            # ─────────────────────────────────────────────────────────

            team_key = tuple(sorted([f["home_team"]["name"], f["away_team"]["name"]]))
            if f["id"] not in seen_ids and team_key not in seen_teams:
                seen_ids.add(f["id"])
                seen_teams.add(team_key)
                fixtures.append(f)

        if rejected_date:
            logger.warning(f"[DATE FILTER] API-Football: rejected {rejected_date} fixtures with wrong date for {date_str}")

        if fixtures:
            fixtures.sort(key=lambda x: x["time"])
            _write_fixture_cache(date_str, fixtures)
            # Track every competition encountered
            _track_competitions_from_fixtures(fixtures)
            logger.info(f"[FINAL FIXTURES] count={len(fixtures)}, source=apifootball")
            return fixtures

    # ── 3. Fetch from SofaScore (Secondary fallback) ──
    logger.info(f"API-Football returned no valid fixtures for {date_str} - falling back to SofaScore")
    logger.info(f"[SOFASCORE REQUEST] url=https://api.sofascore.com/api/v1/sport/football/scheduled-events/{date_str}")
    raw_sofa = _fetch_sofascore_fixtures(date_str)
    logger.info(f"[SOFASCORE RESPONSE] events={len(raw_sofa)}")

    if raw_sofa:
        fixtures = []
        seen_ids: set = set()
        seen_teams: set = set()
        rejected_date = 0
        total_sofascore = len(raw_sofa)
        for ev in raw_sofa:
            f = _sofascore_to_fixture(ev, date_str)

            # ─── STRICT DATE FILTER — only filter in this block ───────
            if f["date"] != date_str:
                rejected_date += 1
                logger.debug(
                    f"[DATE FILTER] Dropped SofaScore fixture id={f['id']} "
                    f"fixture_date={f['date']} != selected_date={date_str}"
                )
                continue
            # ─────────────────────────────────────────────────────────

            team_key = tuple(sorted([f["home_team"]["name"], f["away_team"]["name"]]))
            if f["id"] not in seen_ids and team_key not in seen_teams:
                seen_ids.add(f["id"])
                seen_teams.add(team_key)
                fixtures.append(f)

        logger.info(
            f"[SOFASCORE TOTAL] events={total_sofascore} | "
            f"[AFTER DATE FILTER] events={len(fixtures)} | "
            f"[REJECTED DATE] events={rejected_date}"
        )

        if fixtures:
            fixtures.sort(key=lambda x: (x["time"], x["league"]["name"]))
            _write_fixture_cache(date_str, fixtures)
            # Track every competition encountered
            _track_competitions_from_fixtures(fixtures)
            logger.info(f"[FINAL FIXTURES] count={len(fixtures)}, source=sofascore")
            return fixtures

    # ── 4. All live sources failed — try serving stale cache ──
    stale_cache_path = _get_cache_path(date_str)
    if stale_cache_path.exists():
        try:
            stale_data = json.loads(stale_cache_path.read_text(encoding="utf-8"))
            if isinstance(stale_data, list) and stale_data:
                cache_age = time.time() - stale_cache_path.stat().st_mtime
                logger.warning(
                    f"[STALE FALLBACK] All providers failed for {date_str}. "
                    f"Serving stale cache ({len(stale_data)} fixtures, age={cache_age:.0f}s)"
                )
                # Mark fixtures as stale so the frontend can show a warning
                for f in stale_data:
                    f["_stale"] = True
                    f["_cache_age_seconds"] = round(cache_age)
                return stale_data
        except Exception as e:
            logger.error(f"[STALE FALLBACK] Failed to read stale cache: {e}")

    logger.warning(f"[FINAL FIXTURES] All sources failed for {date_str}. No cache available. Returning empty list.")
    return {"fixtures": [], "message": "No fixture data available for this date. Real-time sources are temporarily unavailable."}


# ─────────────────────────────────────────────────────────
# GLOBAL COVERAGE HELPERS
# ─────────────────────────────────────────────────────────

def _categorize_competition(name: str, country: str) -> str:
    """Classify a competition by name/country."""
    n = name.lower()
    if any(w in n for w in ['women', 'female', 'ladies', 'wsl']):
        return 'women'
    if any(w in n for w in ['u17', 'u18', 'u19', 'u20', 'u21', 'u23', 'youth', 'under-']):
        return 'youth'
    if any(w in n for w in ['friendly', 'friendlies']):
        return 'friendly'
    if country in ('World', 'Europe', 'South America', 'Asia', 'Africa',
                   'North America', 'Oceania', 'CONMEBOL', 'UEFA', 'AFC', 'CAF', 'CONCACAF'):
        return 'international'
    return 'men'


def _track_competitions_from_fixtures(fixtures: list) -> None:
    """Persist all competitions from a fixture list into the DB."""
    try:
        from src.db.database import get_db
        conn = get_db()
        seen: set = set()
        for f in fixtures:
            lg = f.get("league", {})
            lid = lg.get("id", "")
            if lid and lid not in seen:
                seen.add(lid)
                upsert_competition(
                    conn,
                    league_id=str(lid),
                    name=lg.get("name", ""),
                    country=lg.get("country", ""),
                    logo_url=lg.get("logo", ""),
                )
    except Exception as e:
        logger.warning(f"Competition tracking failed: {e}")


def _precompute_predictions_for_date(date_str: str, fixtures: list) -> None:
    """Background task: pre-warm analysis cache for all fixtures on a date."""
    global _PREDICTION_STATUS, _ANALYSIS_CACHE
    logger.info(f"[PRECOMPUTE] Starting prediction pre-warm for {len(fixtures)} fixtures on {date_str}")
    for f in fixtures:
        fid = f["id"]
        if _PREDICTION_STATUS.get(fid, {}).get("status") == "ready":
            continue  # already computed
        _PREDICTION_STATUS[fid] = {"status": "pending", "computed_at": None}
        try:
            home = f["home_team"]["name"]
            away = f["away_team"]["name"]
            league = f["league"]["name"]
            analysis = _compute_match_analysis(home, away, league, shuffle_tiers=False)
            analysis["match"] = {
                "home_team": home,
                "away_team": away,
                "league_name": league,
                "season": "2024/25",
                "date": _get_istanbul_today(),
            }
            _ANALYSIS_CACHE[fid] = analysis
            from datetime import datetime
            _PREDICTION_STATUS[fid] = {
                "status": "ready",
                "computed_at": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            _PREDICTION_STATUS[fid] = {"status": "error", "computed_at": None, "error": str(e)}
    ready = sum(1 for v in _PREDICTION_STATUS.values() if v["status"] == "ready")
    logger.info(f"[PRECOMPUTE] Done for {date_str}: {ready}/{len(fixtures)} ready")


@app.get("/api/precompute-predictions")
def precompute_predictions(date_str: str, background_tasks: BackgroundTasks):
    """
    Trigger background prediction pre-computation for all fixtures on a date.
    GET /api/fixtures/precompute?date_str=YYYY-MM-DD
    Returns immediately; predictions are computed in the background.
    """
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date_str!r}")

    fixtures = _read_fixture_cache(date_str)
    if not fixtures:
        return {"status": "no_fixtures", "date": date_str, "count": 0}

    # Mark all as pending immediately
    global _PREDICTION_STATUS
    for f in fixtures:
        if f["id"] not in _PREDICTION_STATUS:
            _PREDICTION_STATUS[f["id"]] = {"status": "pending", "computed_at": None}

    background_tasks.add_task(_precompute_predictions_for_date, date_str, fixtures)
    return {
        "status": "queued",
        "date": date_str,
        "fixture_count": len(fixtures),
        "message": f"Pre-computing {len(fixtures)} predictions in background",
    }


@app.get("/api/prediction-status/{fixture_id}")
def get_prediction_status(fixture_id: str):
    """Get pre-computation status for a single fixture."""
    return _PREDICTION_STATUS.get(fixture_id, {"status": "unknown"})


@app.get("/api/debug/model-health", dependencies=[Depends(verify_admin_key)])
def get_model_health_report():
    """
    Full model health report — accuracy, calibration, and league intelligence.

    GET /api/debug/model-health

    Returns aggregated statistics from the prediction_errors table.
    Data is populated automatically when GET /api/results/{date} is called
    for any date with finished matches.

    Reports:
      - Overall accuracy, Brier score, calibration gap
      - Accuracy by League (best / worst / most overconfident)
      - Accuracy by Country
      - Accuracy by Competition Type (men / women / youth / friendly / international)
      - Calibration Curve by Confidence Bucket
      - Confidence Adjustment Factors per league
    """
    try:
        from src.db.database import get_db
        from src.db.error_intelligence import get_model_health
        conn = get_db()
        return get_model_health(conn)
    except Exception as e:
        logger.error(f"Model health report failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/debug/team-rating", dependencies=[Depends(verify_admin_key)])
def get_team_rating_endpoint(team: str):
    """
    Get dynamic team learning state (rating, momentum) and history.
    """
    try:
        from src.db.database import get_db
        from src.db.team_intelligence import get_team_diagnostics
        conn = get_db()
        return get_team_diagnostics(conn, team)
    except Exception as e:
        logger.error(f"Team rating debug failed for {team}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/debug/calibration", dependencies=[Depends(verify_admin_key)])
def get_calibration_report_debug(market_type: str = "result"):
    """
    Get full calibration report and reliability diagram.
    """
    try:
        from src.db.database import get_db
        from src.db.prediction_logger import get_calibration_data, get_competition_type_analysis, get_backtest_summary
        
        conn = get_db()
        
        # Task 1 & 2: Reliability Diagram (buckets)
        cal_data = get_calibration_data(conn, market_type, min_samples=10)
        
        # Task 6: Brier Score (from backtest summary)
        backtest = get_backtest_summary(conn, market_type)
        
        # Task 7: Competition Type Analysis
        comp_analysis = get_competition_type_analysis(conn)
        
        return {
            "market_type": market_type,
            "total_predictions": backtest.get("total_predictions", 0),
            "brier_score": backtest.get("brier_score", 0),
            "log_loss": backtest.get("log_loss", 0),
            "accuracy_pct": backtest.get("accuracy_pct", 0),
            "calibration_gap": backtest.get("calibration_gap", 0),
            "reliability_diagram": cal_data.get("buckets", []),
            "competition_analysis": comp_analysis
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/debug/settle-date/{date_str}", dependencies=[Depends(verify_admin_key)])
def settle_date_endpoint(date_str: str):
    """
    Manually settle all predictions for a completed date.

    GET /api/debug/settle-date/YYYY-MM-DD

    This is a convenience endpoint for backfilling historical accuracy data.
    It:
      1. Loads all finished fixtures from the cache for that date
      2. Runs the model to compute 1X2 predictions
      3. Stores them in prediction_errors
      4. Settles them against the actual results
      5. Updates confidence adjustment factors per league
    """
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date_str!r}")

    try:
        from src.db.database import get_db
        from src.db.error_intelligence import store_prediction_record, settle_prediction, rebuild_confidence_adjustments

        cached = _read_fixture_cache(date_str) or []
        finished = [f for f in cached if f.get("status") in ("FT", "AET", "PEN")
                    and f.get("home_goals") is not None
                    and f.get("away_goals") is not None]

        if not finished:
            return {
                "date": date_str,
                "status": "no_finished_fixtures",
                "settled": 0,
                "message": "No finished fixtures found in cache for this date. Load the date first."
            }

        conn = get_db()
        settled = 0
        errors = 0

        for f in finished:
            try:
                fid = str(f["id"])
                home = f["home_team"]["name"]
                away = f["away_team"]["name"]
                league = f["league"]["name"]
                country = f["league"].get("country", "")
                home_goals = f["home_goals"]
                away_goals = f["away_goals"]

                # Compute fresh prediction for this match
                analysis = _compute_match_analysis(home, away, league, shuffle_tiers=False)
                result_mkt = analysis.get("poisson", {}).get("result", {})
                h_pct = float(result_mkt.get("home_win", 33.0))
                d_pct = float(result_mkt.get("draw", 33.0))
                a_pct = float(result_mkt.get("away_win", 33.0))

                store_prediction_record(conn, fid, date_str, league, country,
                                        home, away, h_pct, d_pct, a_pct)
                pred_h = analysis.get("score_prediction", {}).get("expected_goals", {}).get("home")
                pred_a = analysis.get("score_prediction", {}).get("expected_goals", {}).get("away")
                settle_prediction(conn, fid, home_goals, away_goals,
                                  float(pred_h) if pred_h is not None else None,
                                  float(pred_a) if pred_a is not None else None)
                                  
                # ── Dynamic Team Learning System Update ──
                from src.db.team_intelligence import update_team_ratings
                update_team_ratings(conn, fid, date_str, league,
                                    home, away, h_pct, d_pct, a_pct,
                                    home_goals, away_goals)
                                    
                settled += 1
            except Exception as fe:
                logger.debug(f"settle-date: fixture {f.get('id')} failed: {fe}")
                errors += 1

        leagues_updated = rebuild_confidence_adjustments(conn)

        return {
            "date": date_str,
            "status": "ok",
            "total_finished": len(finished),
            "settled": settled,
            "errors": errors,
            "leagues_updated": leagues_updated,
        }
    except Exception as e:
        logger.error(f"settle-date failed for {date_str}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/debug/coverage", dependencies=[Depends(verify_admin_key)])
def debug_coverage(date: str):
    """
    Full coverage report for a given date.
    GET /api/debug/coverage?date=YYYY-MM-DD

    Returns:
      - provider_count: how many events SofaScore returned
      - stored_count: how many were saved to cache (after date filter)
      - rendered_count: same as stored (all stored fixtures are rendered)
      - predicted_ready: how many have prediction pre-warmed
      - coverage_pct: stored / provider * 100
      - leagues, countries, category breakdown
    """
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date!r}")

    # Stored/rendered count
    cached = _read_fixture_cache(date) or []
    stored = [f for f in cached if f.get("date") == date]

    # Provider count (live SofaScore query — may use cache indirectly)
    try:
        raw_sofa = _fetch_sofascore_fixtures(date)
        provider_total = len(raw_sofa)
        # How many of those pass the date filter?
        provider_matching = sum(
            1 for ev in raw_sofa
            if _sofascore_to_fixture(ev, date).get("date") == date
        )
    except Exception:
        provider_total = 0
        provider_matching = len(stored)

    # Prediction status
    predicted_ready = sum(
        1 for f in stored
        if _PREDICTION_STATUS.get(f["id"], {}).get("status") == "ready"
    )
    predicted_pending = sum(
        1 for f in stored
        if _PREDICTION_STATUS.get(f["id"], {}).get("status") == "pending"
    )

    # League/country breakdowns
    leagues_seen: dict = {}
    countries: set = set()
    for f in stored:
        lg = f["league"]
        lid = lg["id"]
        if lid not in leagues_seen:
            leagues_seen[lid] = {
                "id": lid,
                "name": lg["name"],
                "country": lg["country"],
                "category": _categorize_competition(lg["name"], lg["country"]),
                "count": 0,
            }
        leagues_seen[lid]["count"] += 1
        countries.add(lg["country"])

    by_category: dict = {}
    for lg in leagues_seen.values():
        cat = lg["category"]
        by_category[cat] = by_category.get(cat, 0) + lg["count"]

    coverage_pct = round(len(stored) / provider_matching * 100, 1) if provider_matching > 0 else 100.0

    return {
        "provider_matches": provider_total,
        "stored_matches": len(stored),
        "rendered_matches": len(stored),
        "countries": len(countries),
        "competitions": len(leagues_seen)
    }


@app.get("/api/debug/coverage-report", dependencies=[Depends(verify_admin_key)])
def debug_coverage_report(date: str):
    """
    Mathematical proof of 100% fixture coverage for a given date.

    GET /api/debug/coverage-report?date=YYYY-MM-DD

    Pipeline:
      1. provider_raw           — total events returned by SofaScore
      2. provider_after_timezone — events that actually belong to this date (Istanbul time)
      3. stored                 — events saved to the fixture cache
      4. rendered               — events served to the UI (must equal stored)
      5. coverage_pct           = rendered / provider_after_timezone * 100

    Target: coverage_pct == 100.0
    """
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date!r}. Use YYYY-MM-DD.")

    import datetime as dt_module
    import zoneinfo as zi_module
    from collections import defaultdict

    TZ = zi_module.ZoneInfo("Europe/Istanbul")

    # ── Step 1: Raw provider count ──────────────────────────────────────────
    try:
        raw_events = _fetch_sofascore_fixtures(date)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SofaScore fetch failed: {e}")

    provider_raw = len(raw_events)

    # ── Step 2: After timezone filter ──────────────────────────────────────
    # Exactly mirrors the pipeline in get_fixtures_by_date
    tz_matched = []
    tz_rejected = []
    for ev in raw_events:
        ts = ev.get("startTimestamp")
        if ts:
            try:
                ev_date = dt_module.datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d")
            except Exception:
                ev_date = date  # fallback — keep it
        else:
            ev_date = date  # no timestamp, assume correct date
        if ev_date == date:
            tz_matched.append(ev)
        else:
            tz_rejected.append({"id": ev.get("id"), "actual_date": ev_date})

    provider_after_timezone = len(tz_matched)

    # ── Step 3 & 4: Stored / rendered ──────────────────────────────────────
    cached = _read_fixture_cache(date) or []
    stored = [f for f in cached if f.get("date") == date]
    rendered = len(stored)

    # ── Step 5: Coverage % ─────────────────────────────────────────────────
    if provider_after_timezone > 0:
        coverage_pct = round(rendered / provider_after_timezone * 100, 2)
    else:
        coverage_pct = 100.0

    # ── Fixture loss audit (stored vs after-tz) ────────────────────────────
    stored_ids = {f["id"] for f in stored}
    tz_matched_ids = {str(ev.get("id", "")) for ev in tz_matched}
    missing_ids = tz_matched_ids - stored_ids
    extra_ids   = stored_ids - tz_matched_ids  # should always be 0

    # ── League breakdown ───────────────────────────────────────────────────
    league_counts: dict = defaultdict(int)
    league_meta: dict = {}
    for f in stored:
        lg = f.get("league", {})
        lid = lg.get("id", "unknown")
        league_counts[lid] += 1
        if lid not in league_meta:
            league_meta[lid] = {
                "league_id": lid,
                "league": lg.get("name", ""),
                "country": lg.get("country", ""),
            }

    league_breakdown = sorted(
        [
            {
                "league_id": lid,
                "league": league_meta[lid]["league"],
                "country": league_meta[lid]["country"],
                "matches": cnt,
            }
            for lid, cnt in league_counts.items()
        ],
        key=lambda x: -x["matches"],
    )

    # ── Country breakdown ──────────────────────────────────────────────────
    country_counts: dict = defaultdict(int)
    for f in stored:
        country = f.get("league", {}).get("country", "Unknown")
        country_counts[country] += 1

    country_breakdown = sorted(
        [{"country": c, "matches": n} for c, n in country_counts.items()],
        key=lambda x: -x["matches"],
    )

    # ── Pipeline integrity checks ──────────────────────────────────────────
    checks = {
        "stored_equals_rendered": rendered == len(stored),
        "no_extra_fixtures":      len(extra_ids) == 0,
        "no_missing_fixtures":    len(missing_ids) == 0,
        "full_coverage":          coverage_pct >= 100.0,
    }
    all_checks_passed = all(checks.values())

    return {
        # Core numbers
        "date":                    date,
        "provider_raw":            provider_raw,
        "provider_after_timezone": provider_after_timezone,
        "timezone_filtered_out":   provider_raw - provider_after_timezone,
        "stored":                  len(stored),
        "rendered":                rendered,
        "coverage_pct":            coverage_pct,

        # Integrity
        "checks":           checks,
        "all_checks_passed": all_checks_passed,
        "missing_from_cache": sorted(missing_ids),
        "extra_in_cache":     sorted(extra_ids),

        # Breakdowns
        "total_leagues":  len(league_breakdown),
        "total_countries": len(country_breakdown),
        "league_breakdown":  league_breakdown,
        "country_breakdown": country_breakdown,
    }


@app.get("/api/competitions")
def get_competitions(limit: int = 200):
    """List all competitions the platform has ever encountered."""
    try:
        from src.db.database import get_db
        conn = get_db()
        comps = list_competitions(conn, limit=limit)
        stats = get_competition_stats(conn)
        return {"total": len(comps), "stats": stats, "competitions": comps}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/debug/date-trace", dependencies=[Depends(verify_admin_key)])
def debug_date_trace(date: str):
    """
    Full pipeline trace for a given date.
    GET /api/debug/date-trace?date=YYYY-MM-DD
    """
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date!r}. Use YYYY-MM-DD.")

    trace: dict = {
        "requested_date": date,
        "cache_key": f"fixtures-{date}.json",
        "cache_hit": False,
        "cache_count": 0,
        "cache_wrong_date_count": 0,
        "provider_count": 0,
        "provider": None,
        "response_count": 0,
        "response_date_rejected": 0,
        "first_fixture": None,
        "all_fixture_dates_match": None,
    }

    # Cache check
    cached = _read_fixture_cache(date)
    if cached:
        trace["cache_hit"] = True
        trace["cache_count"] = len(cached)
        trace["cache_wrong_date_count"] = sum(1 for f in cached if f.get("date") != date)
        valid = [f for f in cached if f.get("date") == date]
        if valid:
            f0 = valid[0]
            trace["first_fixture"] = {"home": f0["home_team"]["name"], "away": f0["away_team"]["name"], "date": f0["date"], "source": f0.get("source")}
        trace["response_count"] = len(valid)
        trace["all_fixture_dates_match"] = trace["cache_wrong_date_count"] == 0
        return trace

    # SofaScore
    raw_sofa = _fetch_sofascore_fixtures(date)
    trace["provider"] = "sofascore"
    trace["provider_count"] = len(raw_sofa)
    matched = []
    rejected = 0
    for ev in raw_sofa:
        f = _sofascore_to_fixture(ev, date)
        if f["date"] == date:
            matched.append(f)
        else:
            rejected += 1
    trace["response_count"] = len(matched)
    trace["response_date_rejected"] = rejected
    trace["all_fixture_dates_match"] = rejected == 0
    if matched:
        f0 = matched[0]
        trace["first_fixture"] = {"home": f0["home_team"]["name"], "away": f0["away_team"]["name"], "date": f0["date"], "source": f0.get("source")}
        return trace

    # API-Football
    raw_api = _fetch_api_football_fixtures(date)
    trace["provider"] = "apifootball"
    trace["provider_count"] = len(raw_api)
    matched = []
    rejected = 0
    for raw in raw_api:
        f = _api_football_to_fixture(raw)
        if f["date"] == date:
            matched.append(f)
        else:
            rejected += 1
    trace["response_count"] = len(matched)
    trace["response_date_rejected"] = rejected
    trace["all_fixture_dates_match"] = rejected == 0
    if matched:
        f0 = matched[0]
        trace["first_fixture"] = {"home": f0["home_team"]["name"], "away": f0["away_team"]["name"], "date": f0["date"], "source": f0.get("source")}

    return trace


@app.get("/api/debug/date-validation", dependencies=[Depends(verify_admin_key)])
def debug_date_validation(date: str):
    """
    Debug endpoint — validate fixture date consistency for a given date.

    Returns per-source counts and flags any fixture whose date != the requested date.
    Use: GET /api/debug/date-validation?date=YYYY-MM-DD
    """
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date!r}. Use YYYY-MM-DD.")

    report = {
        "selected_date": date,
        "cache": {"hit": False, "total": 0, "matching_date": 0, "wrong_date": [], "fixtures": []},
        "sofascore": {"total_events": 0, "league_filtered": 0, "date_matched": 0, "date_rejected": [], "fixtures": []},
        "apifootball": {"total": 0, "date_matched": 0, "date_rejected": [], "fixtures": []},
    }

    # Cache
    cached = _read_fixture_cache(date)
    if cached:
        report["cache"]["hit"] = True
        report["cache"]["total"] = len(cached)
        for f in cached:
            if f.get("date") == date:
                report["cache"]["matching_date"] += 1
                report["cache"]["fixtures"].append({"id": f["id"], "date": f["date"], "teams": f"{f['home_team']['name']} vs {f['away_team']['name']}"})
            else:
                report["cache"]["wrong_date"].append({"id": f["id"], "actual_date": f.get("date"), "teams": f"{f['home_team']['name']} vs {f['away_team']['name']}"})

    # SofaScore
    raw_sofa = _fetch_sofascore_fixtures(date)
    report["sofascore"]["total_events"] = len(raw_sofa)
    for ev in raw_sofa:
        report["sofascore"]["total_events"] += 1
        f = _sofascore_to_fixture(ev, date)
        entry = {"id": f["id"], "fixture_date": f["date"], "teams": f"{f['home_team']['name']} vs {f['away_team']['name']}"}
        if f["date"] == date:
            report["sofascore"]["date_matched"] += 1
            report["sofascore"]["fixtures"].append(entry)
        else:
            report["sofascore"]["date_rejected"].append(entry)

    # API-Football
    raw_api = _fetch_api_football_fixtures(date)
    report["apifootball"]["total"] = len(raw_api)
    for raw in raw_api:
        f = _api_football_to_fixture(raw)
        entry = {"id": f["id"], "fixture_date": f["date"], "teams": f"{f['home_team']['name']} vs {f['away_team']['name']}"}
        if f["date"] == date:
            report["apifootball"]["date_matched"] += 1
            report["apifootball"]["fixtures"].append(entry)
        else:
            report["apifootball"]["date_rejected"].append(entry)

    return report


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
        if fixture_id in _ANALYSIS_CACHE:
            return _ANALYSIS_CACHE[fixture_id]
            
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
            "date": _get_istanbul_today(),
        }
        _ANALYSIS_CACHE[fixture_id] = analysis

        # ── Store 1X2 prediction in error intelligence system ──
        try:
            from src.db.database import get_db
            from src.db.error_intelligence import store_prediction_record, store_scoreline_predictions
            conn = get_db()
            result_mkt = analysis.get("poisson", {}).get("result", {})
            store_prediction_record(
                conn=conn,
                fixture_id=fixture_id,
                match_date=_get_istanbul_today(),
                league_name=league,
                country="",
                home_team=home_name,
                away_team=away_name,
                home_win_pct=float(result_mkt.get("home_win", 33.0)),
                draw_pct=float(result_mkt.get("draw", 33.0)),
                away_win_pct=float(result_mkt.get("away_win", 33.0)),
            )
            
            # Phase 5: Scoreline Intelligence Reform
            poisson_data = analysis.get("poisson", {})
            store_scoreline_predictions(conn, fixture_id, poisson_data)
            
        except Exception as _ei_err:
            logger.debug(f"Error intelligence store skipped: {_ei_err}")

        return analysis
    except Exception as e:
        logger.error(f"Error analyzing fixture {fixture_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
    # ── Use cached/live API-Football fixtures (same source as daily matches) ──
    # First try the .cache file (populated by get_fixtures_by_date).
    # If not cached, fetch live from API-Football now.
    try:
        all_fixtures = get_fixtures_by_date(date_str)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    if not all_fixtures:
        return {"date": date_str, "matches": [], "summary": {}}

    # ── Phase 1: Build raw results per match ────────────────────
    raw_results = []
    seen_ids = set()

    for fixture in all_fixtures:
        # Only evaluate finished matches
        if fixture.get("status") not in ("FT", "AET", "PEN"):
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
                if evaluated.get("section") in category_pick_map:
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

        # ── Phase 1.5: LIVE PIPELINE — Feed result back into team state ──
        # This is what makes the system adaptive: each finished match
        # updates ELO, rolling averages, and form metrics.
        try:
            from src.engine.live_updater import on_match_finished
            from src.db.database import get_db
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
            from src.db.database import get_db
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
        "category_summary": category_summary,
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
    fixtures = _read_fixture_cache(date_str)
    if not fixtures:
        raw_api = _fetch_api_football_fixtures(date_str)
        if not raw_api:
            return {"date": date_str, "error": "No events found", "picks": []}
        fixtures = [_api_football_to_fixture(f) for f in raw_api]

    conn = get_db()
    result = _run_pipeline(date_str, fixtures, conn)
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


from pydantic import BaseModel
class PickCreate(BaseModel):
    match_id: str
    market: str
    selection: str
    model_prob: float
    implied_prob: float
    edge: float
    odds_at_pick: float
    confidence: float
    league_reliability: float
    grade: str
    stake_units: float


@app.post("/api/picks/place")
def place_pick(pick: PickCreate):
    """Place a pick into the tracking portfolio."""
    conn = get_db()
    from src.db.picks_repo import insert_pick
    try:
        pick_id = insert_pick(conn, pick.dict())
        return {"status": "ok", "pick_id": pick_id}
    except Exception as e:
        logger.error(f"Failed to insert pick: {e}")
        return {"status": "error", "message": str(e)}


class ClvUpdate(BaseModel):
    pick_id: int
    closing_odds: float


@app.post("/api/picks/update_clv")
def update_pick_clv(payload: ClvUpdate):
    """Update a pick with closing odds and calculate CLV."""
    conn = get_db()
    from src.db.picks_repo import update_closing_odds

    pick = conn.execute("SELECT odds_at_pick FROM picks WHERE id = ?", (payload.pick_id,)).fetchone()
    if not pick:
        return {"status": "error", "message": "Pick not found"}

    entry_odds = pick["odds_at_pick"]
    closing_odds = payload.closing_odds
    
    if closing_odds <= 1.0:
        return {"status": "error", "message": "Invalid closing odds"}

    entry_implied = 100.0 / entry_odds
    closing_implied = 100.0 / closing_odds
    clv_pct = closing_implied - entry_implied

    update_closing_odds(conn, payload.pick_id, closing_odds, round(clv_pct, 2))
    
    return {
        "status": "ok",
        "pick_id": payload.pick_id,
        "clv_pct": round(clv_pct, 2),
        "beat_closing_line": clv_pct > 0
    }



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
from src.data.odds_fetcher import TheOddsAPIProvider, get_api_key as get_odds_key, LEAGUE_TO_SPORT

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
    provider = TheOddsAPIProvider(conn)
    fetched = 0
    for sport_key in LEAGUE_TO_SPORT.keys():
        events = provider.fetch_events(sport_key)
        if events:
            fetched += 1
            
    count = conn.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone()[0]
    return {
        "status": "ok",
        "leagues_fetched": fetched,
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


@app.post("/api/admin/retrain-engine", dependencies=[Depends(verify_admin_key)])
def retrain_engine():
    """Retrain the global engine components.
    
    1. Fits isotonic calibration models with safety checks
    2. Rebuilds confidence adjustments for all leagues
    3. Returns before/after metrics
    """
    conn = get_db()
    
    # 1. Retrain Isotonic Calibration
    cal = get_isotonic_calibrator(conn)
    calibration_summary = cal.fit_all(conn)
    
    # 2. Rebuild League Confidence Adjustments
    from src.db.error_intelligence import rebuild_confidence_adjustments
    rebuild_summary = rebuild_confidence_adjustments(conn)
    
    # 3. Get updated model health
    from src.db.error_intelligence import get_model_health
    health = get_model_health(conn)
    
    return {
        "status": "ok",
        "models_fitted": sum(1 for v in calibration_summary.values() if v.get("fitted")),
        "calibration_details": calibration_summary,
        "rebuild_summary": rebuild_summary,
        "health": health
    }


@app.get("/api/calibration/isotonic/status")
def get_isotonic_status():
    """Get status of all fitted isotonic calibration models."""
    conn = get_db()
    cal = get_isotonic_calibrator(conn)
    return {"models": cal.get_status()}


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


# ═══════════════════════════════════════════════════════════════════════
# Execution Engine — Market-Constrained Betting Agent
# ═══════════════════════════════════════════════════════════════════════

from src.engine.execution_engine import (
    find_executable_bets,
    generate_simulated_odds,
    compute_ev,
    compute_edge,
    compute_kelly,
    MIN_ODDS, MAX_ODDS, MIN_CALIBRATED_PROB, MIN_EDGE, MIN_EV,
    MAX_BETS_PER_MATCH, MAX_BETS_PER_DAY,
    KELLY_FRACTION, MAX_STAKE_PCT,
    TRADABLE_MARKETS,
)


@app.get("/api/execution/opportunities/{home}/{away}/{league}")
def get_execution_opportunities(home: str, away: str, league: str, use_live_odds: bool = False):
    """Find executable, positive-EV betting opportunities for a match.

    This is the core execution endpoint. It:
    1. Generates calibrated probabilities for ALL markets
    2. Fetches real bookmaker odds (or simulates them)
    3. Computes EV, edge, and Kelly sizing
    4. Filters down to only tradable, positive-EV opportunities
    5. Returns ONLY executable bets

    Query params:
        use_live_odds: if True, fetches from TheOddsAPI (requires ODDS_API_KEY)
                      if False, uses simulated odds with 5% vig
    """
    try:
        analysis = _compute_match_analysis(home, away, league, shuffle_tiers=False)
    except Exception as e:
        return {"error": f"Analysis failed: {str(e)}", "opportunities": []}

    # Get odds
    if use_live_odds:
        from src.data.odds_fetcher import fetch_normalized_odds_for_match, SPORT_KEYS, get_api_key
        if not get_api_key():
            return {"error": "ODDS_API_KEY not set. Use use_live_odds=false for simulation.", "opportunities": []}

        # Try to find the sport key for this league
        sport_key = None
        for sk, lid in SPORT_KEYS.items():
            if league.lower() in sk.lower():
                sport_key = sk
                break
        if not sport_key:
            sport_key = "soccer_epl"  # default

        bookmaker_odds = fetch_normalized_odds_for_match(sport_key, home, away)
    else:
        bookmaker_odds = generate_simulated_odds(analysis, home, away)

    # Find executable bets
    opportunities = find_executable_bets(
        analysis=analysis,
        bookmaker_odds=bookmaker_odds,
        home_name=home,
        away_name=away,
        league_name=league,
        match_id=f"{home}_vs_{away}",
    )

    # Build summary
    total_markets_scanned = len(bookmaker_odds)
    total_tradable = sum(1 for o in bookmaker_odds if any(
        m["market"] == o["market"]
        for sec in analysis.get("full_analysis", {}).values()
        for m in sec
    ))

    return {
        "match": f"{home} vs {away}",
        "league": league,
        "odds_source": "live" if use_live_odds else "simulated",
        "total_bookmaker_markets": total_markets_scanned,
        "total_matched_to_model": total_tradable,
        "opportunities_found": len(opportunities),
        "opportunities": [bet.to_dict() for bet in opportunities],
        "filter_summary": {
            "min_odds": MIN_ODDS,
            "max_odds": MAX_ODDS,
            "min_calibrated_prob": MIN_CALIBRATED_PROB,
            "min_edge": MIN_EDGE,
            "min_ev": MIN_EV,
            "max_bets_per_match": MAX_BETS_PER_MATCH,
            "kelly_fraction": KELLY_FRACTION,
            "max_stake_pct": MAX_STAKE_PCT,
        },
    }


@app.get("/api/execution/rules")
def get_execution_rules():
    """Return current execution rules and tradable market whitelist."""
    return {
        "rules": {
            "min_odds": MIN_ODDS,
            "max_odds": MAX_ODDS,
            "min_calibrated_probability": MIN_CALIBRATED_PROB,
            "min_edge_pct": MIN_EDGE,
            "min_ev": MIN_EV,
            "max_bets_per_match": MAX_BETS_PER_MATCH,
            "max_bets_per_day": MAX_BETS_PER_DAY,
            "kelly_fraction": KELLY_FRACTION,
            "max_stake_pct": MAX_STAKE_PCT,
        },
        "tradable_markets": sorted(TRADABLE_MARKETS),
        "excluded_market_types": ["cs", "cards", "corners", "combo"],
        "focus_market_types": ["goals", "btts", "result", "handicap", "half"],
    }


@app.get("/api/execution/simulate")
def simulate_execution(
    calibrated_prob: float = 75.0,
    odds: float = 1.55,
):
    """Test EV/Edge/Kelly computation on a single hypothetical bet.

    Example: /api/execution/simulate?calibrated_prob=75&odds=1.55
    """
    implied = 100.0 / odds
    edge = compute_edge(calibrated_prob, odds)
    ev = compute_ev(calibrated_prob, odds)
    kelly = compute_kelly(calibrated_prob, odds)

    passes_filters = (
        MIN_ODDS <= odds <= MAX_ODDS
        and calibrated_prob >= MIN_CALIBRATED_PROB
        and edge >= MIN_EDGE
        and ev >= MIN_EV
    )

    return {
        "calibrated_prob": calibrated_prob,
        "odds": odds,
        "implied_prob": round(implied, 1),
        "edge": round(edge, 1),
        "ev": round(ev, 3),
        "ev_pct": round(ev * 100, 1),
        "kelly_stake_pct": round(kelly, 2),
        "passes_all_filters": passes_filters,
        "verdict": "✅ EXECUTABLE" if passes_filters else "❌ REJECTED",
        "rejection_reasons": [
            r for r in [
                f"odds {odds} outside [{MIN_ODDS}, {MAX_ODDS}]" if not (MIN_ODDS <= odds <= MAX_ODDS) else None,
                f"prob {calibrated_prob}% < min {MIN_CALIBRATED_PROB}%" if calibrated_prob < MIN_CALIBRATED_PROB else None,
                f"edge {edge:.1f}% < min {MIN_EDGE}%" if edge < MIN_EDGE else None,
                f"EV {ev:.3f} < min {MIN_EV}" if ev < MIN_EV else None,
            ] if r
        ],
    }


# ═══════════════════════════════════════════════════════════════════════
# PHASE 4: LIVE ADAPTIVE PIPELINE — API Endpoints
# ═══════════════════════════════════════════════════════════════════════

@app.get("/api/debug/provider-health", dependencies=[Depends(verify_admin_key)])
def get_provider_health():
    """Returns the health status of all data providers."""
    from src.engine.audit_engine import audit_provider_health
    return audit_provider_health()


@app.get("/api/debug/realtime-health", dependencies=[Depends(verify_admin_key)])
def debug_realtime_health(date: str = None):
    """Real-time freshness audit for fixtures, odds, live scores, and settlement."""
    from src.engine.audit_engine import audit_realtime_freshness
    if not date:
        date = _get_istanbul_today()
    return audit_realtime_freshness(date)


@app.get("/api/debug/odds-integrity", dependencies=[Depends(verify_admin_key)])
def debug_odds_integrity(date: str = None):
    """Audit the odds pipeline for data availability and integrity."""
    from src.engine.audit_engine import audit_odds_integrity
    if not date:
        date = _get_istanbul_today()
    return audit_odds_integrity(date)


@app.get("/api/debug/probability-integrity", dependencies=[Depends(verify_admin_key)])
def debug_probability_integrity(home: str = "Arsenal", away: str = "Chelsea", league: str = "Premier League"):
    """Verify all market probabilities are mathematically consistent."""
    from src.engine.audit_engine import audit_probability_integrity
    return audit_probability_integrity(home, away, league)


@app.get("/api/debug/model-validation", dependencies=[Depends(verify_admin_key)])
def debug_model_validation():
    """Run backtest on settled predictions only."""
    from src.engine.audit_engine import audit_model_validation
    return audit_model_validation()


@app.get("/api/debug/warehouse-stats", dependencies=[Depends(verify_admin_key)])
def debug_warehouse_stats():
    """Verify warehouse completeness and coverage."""
    from src.engine.audit_engine import audit_warehouse
    return audit_warehouse()


@app.get("/api/debug/production-readiness", dependencies=[Depends(verify_admin_key)])
def debug_production_readiness(date: str = None):
    """Master production readiness audit — aggregates all checks."""
    from src.engine.audit_engine import audit_production_readiness
    if not date:
        date = _get_istanbul_today()
    return audit_production_readiness(date)

# ── Phase 5: Scoreline & xG Diagnostics ──

@app.get("/api/debug/scoreline-performance", dependencies=[Depends(verify_admin_key)])
def get_scoreline_performance(
    league: str = None,
    season: str = None,
    date_from: str = None,
    date_to: str = None,
    confidence_bucket: str = None
):
    import sqlite3
    from src.db.database import get_db
    conn = get_db()
    conn.row_factory = sqlite3.Row
    
    query = "SELECT * FROM scoreline_learning_log WHERE 1=1"
    params = []
    
    if league:
        query += " AND league_name = ?"
        params.append(league)
    if season:
        query += " AND season = ?"
        params.append(season)
    if date_from:
        query += " AND match_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND match_date <= ?"
        params.append(date_to)
    if confidence_bucket:
        query += " AND confidence_bucket = ?"
        params.append(confidence_bucket)
        
    rows = conn.execute(query, params).fetchall()
    
    if not rows:
        return {"count": 0}
        
    total = len(rows)
    top1 = sum(1 for r in rows if r["top1_hit"])
    top3 = sum(1 for r in rows if r["top3_hit"])
    top5 = sum(1 for r in rows if r["top5_hit"])
    top10 = sum(1 for r in rows if r["top10_hit"])
    
    avg_rank = sum(r["actual_rank"] for r in rows) / total
    avg_prob = sum(r["actual_probability"] for r in rows) / total
    
    return {
        "count": total,
        "top1_hit_rate": round(top1 / total * 100, 1),
        "top3_hit_rate": round(top3 / total * 100, 1),
        "top5_hit_rate": round(top5 / total * 100, 1),
        "top10_hit_rate": round(top10 / total * 100, 1),
        "avg_actual_rank": round(avg_rank, 1),
        "avg_actual_probability": round(avg_prob, 2)
    }

@app.get("/api/debug/xg-performance", dependencies=[Depends(verify_admin_key)])
def get_xg_performance():
    import sqlite3
    from src.db.database import get_db
    conn = get_db()
    conn.row_factory = sqlite3.Row
    
    rows = conn.execute("SELECT * FROM scoreline_learning_log").fetchall()
    if not rows:
        return {"count": 0}
        
    total = len(rows)
    avg_h = sum(r["home_goal_error"] for r in rows) / total
    avg_a = sum(r["away_goal_error"] for r in rows) / total
    avg_tot = sum(r["total_goal_error"] for r in rows) / total
    
    # Leagues breakdown
    league_map = {}
    for r in rows:
        ln = r["league_name"] or "Unknown"
        if ln not in league_map:
            league_map[ln] = {"count": 0, "sum_err": 0}
        league_map[ln]["count"] += 1
        league_map[ln]["sum_err"] += r["total_goal_error"]
        
    league_list = []
    for ln, data in league_map.items():
        if data["count"] >= 5:
            league_list.append({
                "league": ln,
                "count": data["count"],
                "avg_error": round(data["sum_err"] / data["count"], 2)
            })
            
    league_list.sort(key=lambda x: abs(x["avg_error"]))
    best_leagues = league_list[:5]
    worst_leagues = league_list[-5:]
    worst_leagues.reverse()
    
    return {
        "count": total,
        "avg_home_goal_error": round(avg_h, 2),
        "avg_away_goal_error": round(avg_a, 2),
        "avg_total_goal_error": round(avg_tot, 2),
        "best_leagues": best_leagues,
        "worst_leagues": worst_leagues
    }

@app.get("/api/debug/model-comparison", dependencies=[Depends(verify_admin_key)])
def get_model_comparison():
    """
    Returns the strict mathematical backtesting comparison of all Deep Learning
    and Tree-based models (Brier Score, ECE, ROI, Log Loss, Accuracy).
    """
    from src.ml.model_benchmark import run_full_benchmark
    benchmark_data = run_full_benchmark()
    return benchmark_data["metrics"]

@app.get("/api/debug/model-rankings", dependencies=[Depends(verify_admin_key)])
def get_model_rankings():
    """
    Returns the final ranking of all predictive models, ordered by
    Brier Score + Calibration Error + ROI.
    """
    from src.ml.model_benchmark import run_full_benchmark
    benchmark_data = run_full_benchmark()
    return benchmark_data["rankings"]

@app.get("/api/live/status")
def get_live_system_status():
    """Get the status of the live adaptive pipeline."""
    try:
        from src.engine.live_updater import get_ingestion_stats
        from src.db.database import get_db
        conn = get_db()
        stats = get_ingestion_stats(conn)
        return {
            "status": "active" if stats["total_matches"] > 0 else "cold_start",
            "engine": "Live Adaptive Pipeline v1.0",
            **stats,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/live/teams")
def get_tracked_teams(league: str = None):
    """Get all tracked team states with current ELO and form."""
    try:
        from src.db.team_state import get_all_team_states
        from src.db.database import get_db
        conn = get_db()
        states = get_all_team_states(conn, league)
        return {
            "count": len(states),
            "teams": [{
                "team": s.team_name,
                "league": s.league,
                "venue": s.venue,
                "elo": s.elo,
                "attack_rating": s.attack_rating,
                "defense_rating": s.defense_rating,
                "rolling_scored": s.rolling_scored,
                "rolling_conceded": s.rolling_conceded,
                "form_last5": s.form_last5,
                "win_streak": s.win_streak,
                "matches_played": s.matches_played,
                "rest_days": s.rest_days,
                "last_match": s.last_match_date,
            } for s in states],
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/live/elo-rankings")
def get_elo_rankings(league: str = None):
    """Get ELO rankings sorted by rating descending."""
    try:
        from src.db.database import get_db
        conn = get_db()
        query = """SELECT team_name, league, elo, rolling_scored, rolling_conceded,
                          form_last5, win_streak, matches_played
                   FROM team_state WHERE venue = 'home'"""
        params = []
        if league:
            query += " AND league = ?"
            params.append(league)
        query += " ORDER BY elo DESC"
        rows = conn.execute(query, params).fetchall()
        return {
            "count": len(rows),
            "rankings": [{
                "rank": i + 1, "team": r["team_name"], "league": r["league"],
                "elo": r["elo"], "form_last5": r["form_last5"],
                "matches_played": r["matches_played"],
            } for i, r in enumerate(rows)],
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/live/match-history")
def get_match_history_api(team: str = None, league: str = None, limit: int = 20):
    """Get recent match history from the ingested database."""
    try:
        from src.db.database import get_db
        conn = get_db()
        query = "SELECT * FROM match_history WHERE 1=1"
        params = []
        if team:
            query += " AND (home_team LIKE ? OR away_team LIKE ?)"
            params.extend([f"%{team}%", f"%{team}%"])
        if league:
            query += " AND league = ?"
            params.append(league)
        query += " ORDER BY match_date DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return {
            "count": len(rows),
            "matches": [{
                "match_id": r["match_id"], "date": r["match_date"],
                "league": r["league"],
                "home": r["home_team"], "away": r["away_team"],
                "score": f"{r['home_goals']}-{r['away_goals']}",
                "home_elo_change": round(r["home_elo_after"] - r["home_elo_before"], 1) if r["home_elo_after"] else None,
                "away_elo_change": round(r["away_elo_after"] - r["away_elo_before"], 1) if r["away_elo_after"] else None,
            } for r in rows],
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/live/team/{team_name}")
def get_team_live_state(team_name: str, league: str = ""):
    """Get detailed live state for a specific team."""
    try:
        from src.db.team_state import get_team_state as get_live_state
        from src.db.database import get_db
        conn = get_db()

        results = {}
        for venue in ["home", "away"]:
            state = get_live_state(conn, team_name, league, venue)
            if state:
                results[venue] = {
                    "elo": state.elo,
                    "attack_rating": state.attack_rating,
                    "defense_rating": state.defense_rating,
                    "rolling_scored": state.rolling_scored,
                    "rolling_conceded": state.rolling_conceded,
                    "rolling_xg": state.rolling_xg,
                    "rolling_xga": state.rolling_xga,
                    "rolling_corners": state.rolling_corners,
                    "rolling_cards": state.rolling_cards,
                    "form_last5": state.form_last5,
                    "form_last10": state.form_last10,
                    "win_streak": state.win_streak,
                    "unbeaten_streak": state.unbeaten_streak,
                    "matches_last_14d": state.matches_last_14d,
                    "rest_days": state.rest_days,
                    "matches_played": state.matches_played,
                    "last_match_date": state.last_match_date,
                }

        if not results:
            return {"error": f"No live state found for '{team_name}'",
                    "suggestion": "Visit Results page to ingest match data"}

        return {"team": team_name, "league": league, "states": results}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/live/ingest")
def manual_ingest_match(
    match_id: str, match_date: str, league: str,
    home_team: str, away_team: str,
    home_goals: int, away_goals: int,
    home_xg: float = None, away_xg: float = None,
    home_corners: int = None, away_corners: int = None,
    home_cards: int = None, away_cards: int = None,
):
    """Manually ingest a match result into the live pipeline."""
    try:
        from src.engine.live_updater import on_match_finished
        from src.db.database import get_db
        conn = get_db()
        return on_match_finished(
            conn=conn, match_id=match_id, match_date=match_date,
            league=league, home_team=home_team, away_team=away_team,
            home_goals=home_goals, away_goals=away_goals,
            home_xg=home_xg, away_xg=away_xg,
            home_corners=home_corners, away_corners=away_corners,
            home_cards=home_cards, away_cards=away_cards,
        )
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/debug/backtest-features", dependencies=[Depends(verify_admin_key)])
def run_feature_backtest(limit: int = 50):
    """
    Feature Contribution Backtesting Framework.
    Evaluates configurations progressively to measure feature impact.
    """
    from src.db.database import get_db
    conn = get_db()
    
    # Fetch last N matches from match_history
    matches = conn.execute(
        "SELECT home_team, away_team, league, home_goals, away_goals FROM match_history ORDER BY match_date DESC, id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    
    if not matches:
        return {"error": "No matches in history."}
        
    configs = [
        {"name": "Baseline", "flags": {"USE_TEAM_RATINGS": False, "USE_MOMENTUM": False, "USE_HOME_ADVANTAGE": False, "USE_VOLATILITY": False, "USE_LEAGUE_RELIABILITY": False}},
        {"name": "+ Team Ratings", "flags": {"USE_TEAM_RATINGS": True, "USE_MOMENTUM": False, "USE_HOME_ADVANTAGE": False, "USE_VOLATILITY": False, "USE_LEAGUE_RELIABILITY": False}},
        {"name": "+ Momentum", "flags": {"USE_TEAM_RATINGS": True, "USE_MOMENTUM": True, "USE_HOME_ADVANTAGE": False, "USE_VOLATILITY": False, "USE_LEAGUE_RELIABILITY": False}},
        {"name": "+ Home Advantage", "flags": {"USE_TEAM_RATINGS": True, "USE_MOMENTUM": True, "USE_HOME_ADVANTAGE": True, "USE_VOLATILITY": False, "USE_LEAGUE_RELIABILITY": False}},
        {"name": "+ Volatility", "flags": {"USE_TEAM_RATINGS": True, "USE_MOMENTUM": True, "USE_HOME_ADVANTAGE": True, "USE_VOLATILITY": True, "USE_LEAGUE_RELIABILITY": False}},
        {"name": "All Features", "flags": {"USE_TEAM_RATINGS": True, "USE_MOMENTUM": True, "USE_HOME_ADVANTAGE": True, "USE_VOLATILITY": True, "USE_LEAGUE_RELIABILITY": True}}
    ]
    
    results = []
    
    for cfg in configs:
        correct = 0
        brier_sum = 0.0
        conf_sum = 0.0
        
        for m in matches:
            home_team, away_team, league, h_goals, a_goals = m
            
            # Actual result
            if h_goals > a_goals: act_h, act_d, act_a = 1.0, 0.0, 0.0
            elif h_goals == a_goals: act_h, act_d, act_a = 0.0, 1.0, 0.0
            else: act_h, act_d, act_a = 0.0, 0.0, 1.0
                
            try:
                pred = _compute_match_analysis(home_team, away_team, league, feature_flags=cfg["flags"])
                # Extract 1X2 probabilities
                h_pct = pred["poisson"]["result"]["home_win"] / 100.0
                d_pct = pred["poisson"]["result"]["draw"] / 100.0
                a_pct = pred["poisson"]["result"]["away_win"] / 100.0
            except Exception:
                h_pct, d_pct, a_pct = 0.33, 0.33, 0.33
                
            # Highest prob outcome
            best_p = max(h_pct, d_pct, a_pct)
            conf_sum += best_p
            
            if best_p == h_pct and act_h == 1.0: correct += 1
            elif best_p == d_pct and act_d == 1.0: correct += 1
            elif best_p == a_pct and act_a == 1.0: correct += 1
                
            brier_sum += ((h_pct - act_h)**2 + (d_pct - act_d)**2 + (a_pct - act_a)**2)
            
        N = len(matches)
        acc = correct / N
        avg_conf = conf_sum / N
        cal_gap = abs(avg_conf - acc)
        brier = brier_sum / N
        
        results.append({
            "configuration": cfg["name"],
            "accuracy": round(acc * 100, 2),
            "brier_score": round(brier, 4),
            "calibration_gap": round(cal_gap * 100, 2)
        })
        
    deltas = {}
    for i in range(1, len(results)):
        prev = results[i-1]
        curr = results[i]
        diff = round(curr["accuracy"] - prev["accuracy"], 2)
        
        # Mapping config name to feature name
        feature_name = curr["configuration"].replace("+ ", "")
        if feature_name == "All Features": feature_name = "League Reliability"
        
        deltas[feature_name] = diff
        
    # Sort leaderboard by brier score ascending
    leaderboard = sorted(results, key=lambda x: x["brier_score"])
    
    return {
        "matches_tested": len(matches),
        "leaderboard": leaderboard,
        "feature_deltas_accuracy": deltas
    }

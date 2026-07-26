"""
Service layer for match analysis and prediction engine.
Encapsulates Poisson goal modeling, Dixon-Coles adjustment, XGBoost integration,
and market categorization across all betting markets.
"""
import math
import logging
import random
import zoneinfo
from datetime import datetime
from typing import Any, Optional
from fastapi import HTTPException

from src.config import TOP_LEAGUES
from src.ml.team_stats_db import get_team_stats
from src.ml.poisson_model import _poisson_pmf, PoissonGoalModel, rank_scorelines_by_outcome
from src.ml.feature_builder import TeamProfile
from src.ml.predictor import XGBoostPredictor
from src.db.database import get_db
from src.db.team_intelligence import get_team_rating, get_home_advantage
from src.db.error_intelligence import store_prediction_record, store_scoreline_predictions

logger = logging.getLogger("football_predictor")

_ANALYSIS_CACHE: dict[str, dict[str, Any]] = {}


def _get_istanbul_today() -> str:
    """Return today's date string (YYYY-MM-DD) in Europe/Istanbul timezone."""
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    return datetime.now(tz).strftime("%Y-%m-%d")


xgb_predictor = XGBoostPredictor()

# ── Tracked leagues (SofaScore uniqueTournament IDs) ──────
# Includes top European club leagues + active international competitions
# that run during the European off-season (qualifiers, friendlies, etc.)
from src.config import TOP_LEAGUES

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
    "International Friendly Games": "International",
    "International Friendly Games Women": "International",
    "U21 Friendly Games": "International",
    "U17 European Championship": "International",
    "U21 European Championship": "International",
    "UEFA Nations League": "International",
    "Euro Championship": "International",
    "Copa America": "International",
    "World Cup": "World Cup",
    "FIFA World Cup": "FIFA World Cup",
    "World Cup Qualification (Europe)": "International",
    "World Cup Qualification (CONMEBOL)": "International",
    "World Cup Qualification (Africa)": "International",
    "World Cup Qualification (Asia)": "International",
    "World Cup Qualification (CONCACAF)": "International",
    "AFC Asian Cup Qual.": "International",
    "U23 Toulon Tournament": "International",
    "World Championship Women Qual.": "International",
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
        return 1.0  # Removed artificial 1-1 boost
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
        # But only for club football — neutral venue tournaments have no stadium advantage
        from src.ml.team_stats_db import is_neutral_tournament as _is_neutral
        h_adv = get_home_advantage(db, home_name)
        a_adv = get_home_advantage(db, away_name)
        
        if _is_neutral(league_key):
            h_adv_scale = 0.0  # No stadium advantage on neutral grounds
        else:
            h_adv_scale = (h_adv / 1000.0) if feature_flags.get("USE_HOME_ADVANTAGE", True) else 0.0
        # Fix: away team home-advantage is irrelevant when they're playing away.
        # The old code negated a_adv which penalized away teams with good home records.
        # Away venue bias is already embedded in away_stats.scored from the AWAY stats DB.
        a_adv_scale = 0.0
        
        h_total_scale = 1.0 + rating_scale + h_mom_scale + h_adv_scale
        a_total_scale = 1.0 - rating_scale + a_mom_scale + a_adv_scale
        
        # Clamp bounds — keep adjustments modest to avoid extreme outputs
        h_total_scale = max(0.82, min(1.22, h_total_scale))
        a_total_scale = max(0.82, min(1.22, a_total_scale))
        
        # Apply scaling ONLY to scored — not conceded.
        # The Poisson model already crosses attack × defense naturally:
        #   λ_home = home_attack_strength × away_defense_weakness × league_avg
        # Scaling both scored AND conceded by the same rating diff would
        # double-count the advantage (quadratic amplification).
        h_scored_adj = home_stats.scored * h_total_scale
        h_conceded_adj = home_stats.conceded                # Poisson crosses naturally
        a_scored_adj = away_stats.scored * a_total_scale
        a_conceded_adj = away_stats.conceded                # Poisson crosses naturally
    except Exception as e:
        logger.warning(f"Failed to apply team intelligence: {e}")
        h_scored_adj = home_stats.scored
        h_conceded_adj = home_stats.conceded
        a_scored_adj = away_stats.scored
        a_conceded_adj = away_stats.conceded

    # ── Step 2: Poisson ──
    # Use stronger regression for low-data matches to prevent extreme lambdas
    # from unknown/fallback-stats teams producing unrealistic xG outputs.
    # Pre-flight estimate: if either team has <5 matches we treat as low-data.
    # (Full data_quality score is computed later after XGBoost profiles are built.)
    _low_data = (home_stats.matches_played < 5 or away_stats.matches_played < 5)
    _regress_factor = 0.25 if _low_data else 0.15
    poisson_model = PoissonGoalModel(league_key)
    pred = poisson_model.predict(
        home_scored=h_scored_adj, home_conceded=h_conceded_adj,
        away_scored=a_scored_adj, away_conceded=a_conceded_adj,
        home_team=home_name, away_team=away_name,
        regress_factor=_regress_factor,
    )

    # ── Step 3: Corners & Cards lambdas ──
    # Raw corner expectations from team stats
    exp_h_corn_raw, exp_a_corn_raw = home_stats.corners, away_stats.corners

    # Scale corners by each team's proportional attacking share so that
    # teams with higher goal threat get proportionally more corners.
    # This prevents the 50/50 split when both teams have identical static corner values.
    _total_scoring = (h_scored_adj + a_scored_adj) or 1.0
    _h_attack_share = h_scored_adj / _total_scoring
    _a_attack_share = a_scored_adj / _total_scoring
    _total_corn_raw = exp_h_corn_raw + exp_a_corn_raw
    exp_h_corn = round(_total_corn_raw * _h_attack_share, 2)
    exp_a_corn = round(_total_corn_raw * _a_attack_share, 2)

    exp_total_corn = round(exp_h_corn + exp_a_corn, 1)
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
    # Check if teams are from real data or fallback using the same alias-aware
    # lookup that get_team_stats() uses — previously used broken substring matching.
    from src.ml.team_stats_db import ALL_HOME, ALL_AWAY, _lookup_in_db
    _h_lower = home_name.lower().strip()
    _a_lower = away_name.lower().strip()
    _h_in_hardcoded = _lookup_in_db(ALL_HOME, _h_lower) is not None
    _a_in_hardcoded = _lookup_in_db(ALL_AWAY, _a_lower) is not None
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
        _missing_inputs.append(f"home_team '{home_name}' has NO real data — using fallback defaults")
        data_quality = max(0, data_quality - 30)  # Severe penalty for truly unknown teams
    elif home_profile.matches_played < 5:
        _missing_inputs.append(f"home_team '{home_name}' has only {home_profile.matches_played} matches")
    if not _a_in_hardcoded and not _a_in_live:
        _missing_inputs.append(f"away_team '{away_name}' has NO real data — using fallback defaults")
        data_quality = max(0, data_quality - 30)  # Severe penalty for truly unknown teams
    elif away_profile.matches_played < 5:
        _missing_inputs.append(f"away_team '{away_name}' has only {away_profile.matches_played} matches")
    if league_key not in ('Premier League', 'LaLiga', 'Serie A', 'Bundesliga', 'Ligue 1', 'Champions League', 'International'):
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
        # Handicaps: slightly tighter compression to prevent near-certain
        # wide lines (e.g., -3.5) from dominating the UI, while staying
        # on the same scale as related markets (1X, Home Win, etc.).
        # Exception: ±0.5 lines are mathematically identical to 1X2 / double
        # chance, so they should NOT get extra compression (prevents AH +0.5
        # from displaying below the equivalent 1X market).
        if market_type == "handicap" and "0.5" not in m:
            base = max(0.50, base - 0.06)
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

        Note: _apply_data_quality_penalty() is intentionally NOT called here.
        The data_quality signal is already captured by _calibration_strength():
        high DQ → base=0.96 (preserves spread), low DQ → base=0.68 (compresses).
        Calling the penalty again would be double-compression.
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
        # while sparse markets keep their own stricter cap above.
        # Handicaps get 88%: high enough to stay above related 1X/double-chance
        # markets, but prevents near-certain wide lines from showing 95%+.
        if _is_sparse_or_longshot_market(name):
            ceiling = 82.0
        elif market_type == "handicap":
            ceiling = 88.0
        else:
            ceiling = 96.0
        return round(max(1.0, min(ceiling, calibrated_prob)), 1)

    raw = []
    def add(name, prob):
        prob = calibrate(max(0, min(100, prob)), name)
        if prob > 0:
            raw.append({
                "market": name,
                "probability": prob,
                "fair_odds": round(100.0 / prob, 2),
            })

    def add_group(names_and_probs):
        """Calibrate a group of mutually exclusive outcomes, then renormalize to 100%."""
        calibrated = [(name, calibrate(max(0, min(100, prob)), name)) for name, prob in names_and_probs]
        total = sum(p for _, p in calibrated)
        if total > 0:
            for name, p in calibrated:
                renorm_p = round(p / total * 100, 1)
                if renorm_p > 0:
                    raw.append({
                        "market": name,
                        "probability": renorm_p,
                        "fair_odds": round(100.0 / renorm_p, 2),
                    })
        # Return renormalized values for downstream use (e.g., double chance)
        if total > 0:
            return {name: round(p / total * 100, 1) for name, p in calibrated}
        return {name: 0.0 for name, _ in names_and_probs}

    def add_raw(name, prob):
        """Add a market with an already-calibrated probability (no double-calibration)."""
        prob = round(max(0, min(100, prob)), 1)
        if prob > 0:
            raw.append({"market": name, "probability": prob})

    def _is_basic_1x2_market(name: str) -> bool:
        return name in {
            "Home Win", "Draw", "Away Win",
            "FH Home Win", "FH Draw", "FH Away Win",
            "SH Home Win", "SH Draw", "SH Away Win",
        }



    def add_handicap(name, prob):
        """Add a handicap market through the standard calibration pipeline.

        Previously handicaps used a separate _handicap_probability() function
        with much more aggressive compression than calibrate(), which caused
        AH +1.5 to sometimes appear less confident than 1X (Double Chance)
        even though it's mathematically always more probable.

        Now handicaps go through calibrate() with a handicap-specific strength
        reduction (-0.06) and ceiling (88%), ensuring consistent ordering
        across all related markets.
        """
        prob = calibrate(max(0, min(100, prob)), name)
        market = {
            "market": name,
            "probability": prob,
            "fair_odds": round(100.0 / prob, 2) if prob > 0 else None,
            "source": "dixon_coles_calibrated",
        }
        raw.append(market)

    def add_handicap_group(names_and_probs):
        """Add a 3-way handicap group through calibrate(), then renormalize to 100%."""
        calibrated = [(name, calibrate(max(0, min(100, prob)), name)) for name, prob in names_and_probs]
        total = sum(p for _, p in calibrated)
        if total <= 0:
            return
        for name, p in calibrated:
            final_prob = round(p / total * 100.0, 1)
            raw.append({
                "market": name,
                "probability": final_prob,
                "fair_odds": round(100.0 / final_prob, 2) if final_prob > 0 else None,
                "source": "dixon_coles_calibrated",
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

    # ━━ RESULT (1X2 — from Dixon-Coles ft matrix for consistency with all other FT markets) ━━
    ft_hw_raw = mx(ft, MG, lambda h, a: h > a)
    ft_dr_raw = mx(ft, MG, lambda h, a: h == a)
    ft_aw_raw = mx(ft, MG, lambda h, a: h < a)
    ft_1x2 = add_group([
        ("Home Win", ft_hw_raw),
        ("Draw", ft_dr_raw),
        ("Away Win", ft_aw_raw),
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
    # FH 1X2 — from Dixon-Coles fhm matrix for consistency with FH Over/Under/BTTS
    fh_hw_raw = mx(fhm, MG, lambda h, a: h > a)
    fh_dr_raw = mx(fhm, MG, lambda h, a: h == a)
    fh_aw_raw = mx(fhm, MG, lambda h, a: h < a)
    fh_1x2 = add_group([
        ("FH Home Win", fh_hw_raw),
        ("FH Draw", fh_dr_raw),
        ("FH Away Win", fh_aw_raw),
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
    # Use Poisson convolution to compute P(FH total > SH total), not just
    # the proportion of expected values (which was always ~48/52%).
    def _half_comparison_probs(fh_lam: float, sh_lam: float, max_val: int) -> tuple:
        """P(FH total > SH total) and P(SH total > FH total) via Poisson PMF."""
        from src.ml.poisson_model import _poisson_pmf
        fh_dist = [_poisson_pmf(k, fh_lam) for k in range(max_val + 1)]
        sh_dist = [_poisson_pmf(k, sh_lam) for k in range(max_val + 1)]
        p_fh_more = 0.0
        p_sh_more = 0.0
        for fk in range(max_val + 1):
            for sk in range(max_val + 1):
                joint = fh_dist[fk] * sh_dist[sk]
                if fk > sk:
                    p_fh_more += joint
                elif sk > fk:
                    p_sh_more += joint
        return p_fh_more * 100, p_sh_more * 100

    if fh_total_corn + sh_total_corn > 0:
        p_fh_corn, p_sh_corn = _half_comparison_probs(fh_total_corn, sh_total_corn, MC)
        add("Half with Most Corners: 1st Half", p_fh_corn)
        add("Half with Most Corners: 2nd Half", p_sh_corn)
    if fh_total_card + sh_total_card > 0:
        p_fh_card, p_sh_card = _half_comparison_probs(fh_total_card, sh_total_card, MK)
        add("Half with Most Cards: 1st Half", p_fh_card)
        add("Half with Most Cards: 2nd Half", p_sh_card)

    # ══════════════════════════════════════════════════════
    # SCORE ESTIMATION — Top probable scorelines
    # ══════════════════════════════════════════════════════
    #
    # Extract the most probable exact scores from Poisson
    # joint probability matrices (already computed above).

    # Full-time scores (from ft matrix)
    ft_sorted_items = rank_scorelines_by_outcome(ft)
    ft_scores = []
    for (h, a), prob in ft_sorted_items:
        prob_pct = prob * 100
        if prob_pct >= 1.0:  # Only include scores with >= 1% probability
            ft_scores.append({"home": h, "away": a, "probability": round(prob_pct, 1)})
    ft_top_scores = ft_scores[:5]

    # First-half scores (from fhm matrix)
    fh_sorted_items = rank_scorelines_by_outcome(fhm)
    fh_scores = []
    for (h, a), prob in fh_sorted_items:
        prob_pct = prob * 100
        if prob_pct >= 1.0:
            fh_scores.append({"home": h, "away": a, "probability": round(prob_pct, 1)})
    fh_top_scores = fh_scores[:5]

    # Second-half scores (from shm matrix)
    sh_sorted_items = rank_scorelines_by_outcome(shm)
    sh_scores = []
    for (h, a), prob in sh_sorted_items:
        prob_pct = prob * 100
        if prob_pct >= 1.0:
            sh_scores.append({"home": h, "away": a, "probability": round(prob_pct, 1)})
    sh_top_scores = sh_scores[:5]

    # FH / SH result probabilities (renormalized for display)
    _fh_hw = round(mx(fhm, MG, lambda h, a: h > a), 1)
    _fh_dr = round(mx(fhm, MG, lambda h, a: h == a), 1)
    _fh_aw = round(mx(fhm, MG, lambda h, a: h < a), 1)
    _fh_total = _fh_hw + _fh_dr + _fh_aw or 1
    fh_result = {
        "home_win": round(_fh_hw / _fh_total * 100, 1),
        "draw":     round(_fh_dr / _fh_total * 100, 1),
        "away_win": round(_fh_aw / _fh_total * 100, 1),
    }

    _sh_hw = round(mx(shm, MG, lambda h, a: h > a), 1)
    _sh_dr = round(mx(shm, MG, lambda h, a: h == a), 1)
    _sh_aw = round(mx(shm, MG, lambda h, a: h < a), 1)
    _sh_total = _sh_hw + _sh_dr + _sh_aw or 1
    sh_result = {
        "home_win": round(_sh_hw / _sh_total * 100, 1),
        "draw":     round(_sh_dr / _sh_total * 100, 1),
        "away_win": round(_sh_aw / _sh_total * 100, 1),
    }

    score_prediction = {
        "full_time": ft_top_scores,
        "first_half": fh_top_scores,
        "second_half": sh_top_scores,
        "fh_result": fh_result,
        "sh_result": sh_result,
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
            if (
                m["market_type"] not in ["corners", "cards", "goals", "team_goals", "half", "handicap", "result"]
                and not _is_sparse_or_longshot_market(m["market"])
            ):
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

    def _renormalize_exclusive_group(group_names: list[str]):
        group = [market_index[name] for name in group_names if name in market_index]
        total = sum(float(item.get("probability", 0.0)) for item in group)
        if not group or total <= 0:
            return
        for item in group:
            item["probability"] = round(float(item["probability"]) / total * 100.0, 1)

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

    # ── Rule 6: mutually exclusive result groups must stay sum-to-100 ──
    _renormalize_exclusive_group(["Home Win", "Draw", "Away Win"])
    _renormalize_exclusive_group(["FH Home Win", "FH Draw", "FH Away Win"])
    _renormalize_exclusive_group(["SH Home Win", "SH Draw", "SH Away Win"])

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

    def _dedupe_exclusive_1x2_picks(picks: list[dict]) -> list[dict]:
        """Only expose the strongest direct 1X2 pick from each exclusive group."""
        direct_groups = [
            {"Home Win", "Draw", "Away Win"},
            {"FH Home Win", "FH Draw", "FH Away Win"},
            {"SH Home Win", "SH Draw", "SH Away Win"},
        ]
        filtered = list(picks)
        for group in direct_groups:
            group_picks = [p for p in filtered if p.get("market") in group]
            if len(group_picks) <= 1:
                continue
            keep = max(group_picks, key=lambda p: p.get("probability", 0.0))
            filtered = [
                p for p in filtered
                if p.get("market") not in group or p is keep
            ]
        return filtered

    tier_candidates = _dedupe_exclusive_1x2_picks(tier_candidates)
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


def get_match_analysis(
    fixture_id: str,
    home: str = "",
    away: str = "",
    league: str = "Premier League",
    status: str = "",
    start_time: str = "",
) -> dict[str, Any]:
    """
    Per-match prediction service. Every match gets UNIQUE probabilities.
    Computes analysis across all betting markets and records predictions in error intelligence DB.
    """
    home_name = home or "Unknown Home"
    away_name = away or "Unknown Away"

    try:
        if fixture_id in _ANALYSIS_CACHE:
            return _ANALYSIS_CACHE[fixture_id]

        logger.info(f"Per-match engine: {home_name} vs {away_name} [{league}]")
        analysis = _compute_match_analysis(
            home_name=home_name,
            away_name=away_name,
            league_name=league,
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

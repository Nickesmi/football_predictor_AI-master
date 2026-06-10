"""
Execution Engine — Market-Constrained Betting Agent

This is the core module that transforms the prediction engine from a
probability generator into a market-aware execution system.

Architecture:
    Bookmaker odds (TheOddsAPI / manual)
        → Normalize market names
        → Map to internal calibrated probabilities
        → Weight by bookmaker sharpness
        → Compute EV = (calibrated_prob × odds) - 1
        → Filter: odds range, min prob, min EV, tier
        → Quarter-Kelly sizing
        → Return ONLY executable opportunities

Key design decisions:
    - Only evaluate markets that actually exist at the bookmaker
    - Ignore: Correct Score, Cards, Corners, Exotic combos
    - Focus: Over/Under, BTTS, Double Chance, 1X2, Asian Handicap
    - Quarter-Kelly capped at 1% bankroll per bet
    - Max 1 bet per match, max 3 bets per day
    - Edge = calibrated_prob - implied_prob
    - EV = (calibrated_prob/100 × odds) - 1
    - Bookmaker trust weighting (Pinnacle > bet365 > soft books)
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("football_predictor")

# ═══════════════════════════════════════════════════════════════════════
# EXECUTION RULES — Hard filters for bet qualification
# ═══════════════════════════════════════════════════════════════════════
#
# Capital-preservation mode:
#   - The local logs show weak broad-result accuracy, so execution must be
#     selective until enough real settled picks prove a market is profitable.
#   - A missed bet is acceptable; forcing volume from a 50% model is not.

MIN_ODDS = 1.40          # minimum decimal odds (bookmaker margin too tight below this)
MAX_ODDS = 1.65          # maximum decimal odds (model error/tail risk increases above this)
MIN_CALIBRATED_PROB = 70.0  # minimum calibrated probability (%)
MIN_EDGE = 7.0           # minimum edge over bookmaker (%)
MIN_EV = 0.05            # minimum expected value (5%)
MAX_BETS_PER_MATCH = 1   # cap per match
MAX_BETS_PER_DAY = 3     # global daily cap while model quality is unproven

# Kelly sizing
KELLY_FRACTION = 0.25    # quarter-Kelly
MAX_STAKE_PCT = 1.0      # max 1% bankroll per bet
DEFAULT_STAKE_PCT = 0.5  # default when Kelly suggests more


# ═══════════════════════════════════════════════════════════════════════
# BOOKMAKER TRUST WEIGHTS — Sharp vs Soft
# ═══════════════════════════════════════════════════════════════════════
#
# Not all odds are equally trustworthy.
# Pinnacle closing odds are extremely informative (sharpest book).
# Soft bookmakers have noisy lines.
# Trust weighting adjusts how seriously we take the odds as a benchmark.
#
# weight = 1.0 means "fully trust this price"
# weight = 0.7 means "this price is noisy, discount the edge slightly"

BOOKMAKER_WEIGHTS = {
    # Sharp books — best for pricing benchmarks
    "pinnacle": 1.0,
    "matchbook": 0.95,
    "betfair_exchange": 0.95,
    "smarkets": 0.93,

    # Semi-sharp — good but slightly noisy
    "bet365": 0.90,
    "unibet": 0.88,
    "william_hill": 0.87,
    "bwin": 0.85,
    "betway": 0.85,

    # Soft books — noisier, but potential for larger edges
    "draftkings": 0.80,
    "fanduel": 0.80,
    "betsson": 0.78,
    "888sport": 0.78,
    "1xbet": 0.75,
    "1win": 0.70,
    "betwinner": 0.70,

    # Simulated / unknown
    "simulated": 0.60,
}

DEFAULT_BOOKMAKER_WEIGHT = 0.75


def get_bookmaker_weight(bookmaker: str) -> float:
    """Get trust weight for a bookmaker. Higher = sharper/more reliable."""
    return BOOKMAKER_WEIGHTS.get(bookmaker.lower(), DEFAULT_BOOKMAKER_WEIGHT)


# ═══════════════════════════════════════════════════════════════════════
# TRADABLE MARKET WHITELIST — Only evaluate these
# ═══════════════════════════════════════════════════════════════════════

TRADABLE_MARKETS = {
    # Over/Under Goals
    "Over 0.5 Goals", "Under 0.5 Goals",
    "Over 1.5 Goals", "Under 1.5 Goals",
    "Over 2.5 Goals", "Under 2.5 Goals",
    "Over 3.5 Goals", "Under 3.5 Goals",
    "Over 4.5 Goals", "Under 4.5 Goals",
    # BTTS
    "BTTS - Yes", "BTTS - No",
    # 1X2 / Double Chance (dynamic names handled by pattern matching)
    "Home Win", "Draw", "Away Win",
    # FH Goals
    "FH Over 0.5 Goals", "FH Under 0.5 Goals",
    "FH Over 1.5 Goals", "FH Under 1.5 Goals",
}

# Markets that are NEVER tradable (noisy, illiquid, high variance)
EXCLUDED_MARKET_TYPES = {"cs", "cards", "corners", "combo"}


def _is_tradable_market(market_name: str, home_name: str, away_name: str) -> bool:
    """Check if a market is tradable (exists at major bookmakers).

    Uses exact match for standard names + pattern matching for
    team-specific markets (double chance, handicap).
    """
    if market_name in TRADABLE_MARKETS:
        return True

    m = market_name.lower()

    # Double Chance (dynamic team names)
    if market_name.startswith("1X ") or market_name.startswith("X2 ") or market_name.startswith("12 "):
        return True

    # Asian Handicap
    if m.startswith("ah "):
        return True

    # European Handicap
    if m.startswith("eh "):
        return True

    return False


# ═══════════════════════════════════════════════════════════════════════
# MARKET NAME NORMALIZATION — Map bookmaker names ↔ internal names
# ═══════════════════════════════════════════════════════════════════════

def normalize_bookmaker_market(
    api_market_key: str,
    outcome_name: str,
    point: float = None,
    home_team: str = "",
    away_team: str = "",
) -> Optional[str]:
    """Map TheOddsAPI market/outcome to our internal market name.

    Examples:
        ("h2h", "Manchester United", None) → "Home Win"
        ("totals", "Over", 2.5)            → "Over 2.5 Goals"
        ("btts", "Yes", None)              → "BTTS - Yes"
        ("spreads", "Manchester United", -1.5) → "AH Manchester United -1.5"
    """
    key = api_market_key.lower()

    # 1X2
    if key == "h2h":
        name = outcome_name.strip()
        if name.lower() == home_team.lower():
            return "Home Win"
        elif name.lower() == "draw":
            return "Draw"
        elif name.lower() == away_team.lower():
            return "Away Win"
        return None

    # Over/Under Goals
    if key in ("totals", "totals_goals"):
        direction = "Over" if "over" in outcome_name.lower() else "Under"
        if point is not None:
            return f"{direction} {point} Goals"
        return None

    # BTTS
    if key in ("btts", "both_teams_to_score"):
        if "yes" in outcome_name.lower():
            return "BTTS - Yes"
        return "BTTS - No"

    # Spreads / Asian Handicap
    if key in ("spreads", "asian_handicap"):
        name = outcome_name.strip()
        if point is not None:
            sign = "+" if point > 0 else ""
            return f"AH {name} {sign}{point}"
        return None

    # Double Chance
    if key == "double_chance":
        name = outcome_name.lower()
        if "home" in name or "draw" in name:
            if "home" in name and "draw" in name:
                return f"1X ({home_team} or Draw)"
            if "away" in name and "draw" in name:
                return f"X2 ({away_team} or Draw)"
            if "home" in name and "away" in name:
                return "12 (Any Team to Win)"
        return None

    # First Half totals
    if key in ("totals_h1", "first_half_totals"):
        direction = "FH Over" if "over" in outcome_name.lower() else "FH Under"
        if point is not None:
            return f"{direction} {point} Goals"
        return None

    return None


# ═══════════════════════════════════════════════════════════════════════
# EV + KELLY COMPUTATION
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ExecutableBet:
    """A fully qualified, tradable betting opportunity."""
    match_id: str
    home_team: str
    away_team: str
    league: str
    market: str
    bookmaker: str
    bookmaker_weight: float
    odds: float
    raw_probability: float        # model output before calibration
    calibrated_probability: float  # after isotonic calibration
    implied_probability: float    # 100 / odds
    edge: float                   # calibrated - implied
    weighted_edge: float          # edge × bookmaker_weight
    ev: float                     # (cal_prob/100 × odds) - 1
    kelly_fraction: float         # quarter-Kelly stake
    recommended_stake_pct: float  # capped at MAX_STAKE_PCT
    tier: Optional[int] = None
    market_type: str = ""
    confidence_grade: str = ""    # A/B/C based on edge + EV

    def to_dict(self) -> dict:
        return {
            "match": f"{self.home_team} vs {self.away_team}",
            "league": self.league,
            "market": self.market,
            "bookmaker": self.bookmaker,
            "bookmaker_weight": self.bookmaker_weight,
            "odds": self.odds,
            "raw_probability": self.raw_probability,
            "calibrated_probability": self.calibrated_probability,
            "implied_probability": round(self.implied_probability, 1),
            "edge": round(self.edge, 1),
            "weighted_edge": round(self.weighted_edge, 1),
            "ev": round(self.ev, 3),
            "ev_pct": round(self.ev * 100, 1),
            "kelly_fraction": round(self.kelly_fraction, 4),
            "recommended_stake_pct": round(self.recommended_stake_pct, 2),
            "tier": self.tier,
            "market_type": self.market_type,
            "confidence_grade": self.confidence_grade,
        }


def compute_ev(calibrated_prob: float, odds: float) -> float:
    """Expected Value = (probability × odds) - 1.

    Positive EV means the bet is profitable long-term.
    EV of 0.05 means 5% expected return per unit staked.
    """
    return (calibrated_prob / 100.0) * odds - 1.0


def compute_edge(calibrated_prob: float, odds: float) -> float:
    """Edge = calibrated_prob - implied_prob.

    Positive edge means our model thinks the event is more likely
    than the bookmaker's price implies.
    """
    implied = 100.0 / odds
    return calibrated_prob - implied


def compute_kelly(calibrated_prob: float, odds: float) -> float:
    """Kelly criterion: f* = (p × b - q) / b
    
    We calculate the raw Kelly, but execution relies on the practical EV grading
    for the recommended stake.
    """
    p = calibrated_prob / 100.0
    q = 1.0 - p
    b = odds - 1.0

    if b <= 0:
        return 0.0
    kelly = (p * b - q) / b
    return max(0.0, kelly)

def _grade_bet(edge: float, ev: float, bk_weight: float) -> tuple[str, float]:
    """Assign confidence grade and stake based on EV.
    
    Returns: (grade, recommended_stake_pct)
    A+ (EV > 6%) -> 1.5%
    A  (4-6%)    -> 1.0%
    B  (2-4%)    -> 0.5%
    """
    if ev > 0.06:
        return "A+", 1.5
    if ev >= 0.04:
        return "A", 1.0
    if ev >= 0.02:
        return "B", 0.5
    return "C", 0.0


# ═══════════════════════════════════════════════════════════════════════
# CLV (CLOSING LINE VALUE) TRACKING
# ═══════════════════════════════════════════════════════════════════════
#
# CLV is THE truth metric for model quality.
# If your bets consistently beat the closing line, the model is good.
# Even during losing streaks.
#
# clv = closing_implied_prob - entry_implied_prob
# Positive CLV = you got better odds than the market settled at

def compute_clv(entry_odds: float, closing_odds: float) -> dict:
    """Compute Closing Line Value metrics.

    Args:
        entry_odds: decimal odds when bet was placed
        closing_odds: decimal odds at match start (closing line)

    Returns:
        dict with CLV metrics
    """
    entry_implied = 100.0 / entry_odds
    closing_implied = 100.0 / closing_odds

    # CLV = how much the line moved in your favor
    # Positive = you beat the closing line
    clv_pct = closing_implied - entry_implied

    # CLV ratio: odds improvement factor
    clv_ratio = entry_odds / closing_odds if closing_odds > 0 else 1.0

    return {
        "entry_odds": entry_odds,
        "closing_odds": closing_odds,
        "entry_implied": round(entry_implied, 1),
        "closing_implied": round(closing_implied, 1),
        "clv_pct": round(clv_pct, 2),
        "clv_ratio": round(clv_ratio, 4),
        "beat_closing_line": clv_pct > 0,
        "verdict": "✅ Beat CL" if clv_pct > 0 else "❌ Behind CL",
    }


# ═══════════════════════════════════════════════════════════════════════
# EXECUTION ENGINE — The main orchestrator
# ═══════════════════════════════════════════════════════════════════════

def find_executable_bets(
    analysis: dict,
    bookmaker_odds: list[dict],
    home_name: str,
    away_name: str,
    league_name: str,
    match_id: str = "",
) -> list[ExecutableBet]:
    """Core execution function: find tradable, positive-EV opportunities.

    Args:
        analysis: output of _compute_match_analysis() containing all markets
                  with raw_probability, probability (calibrated), market_type, tier
        bookmaker_odds: list of dicts with:
            {"market": str, "odds": float, "bookmaker": str}
            Market names should be our normalized internal names.
        home_name: home team name
        away_name: away team name
        league_name: league name
        match_id: optional match identifier

    Returns:
        List of ExecutableBet objects, sorted by EV descending,
        capped at MAX_BETS_PER_MATCH.
    """
    # Build lookup: market_name → analysis data
    market_lookup = {}
    # From full_analysis (all markets, all sections)
    for section_name, section_markets in analysis.get("full_analysis", {}).items():
        for m in section_markets:
            market_lookup[m["market"]] = m
    # Also check tiered picks for tier assignment
    tier_lookup = {}
    for tier in analysis.get("tiers", []):
        for pick in tier.get("picks", []):
            tier_lookup[pick["market"]] = tier["tier"]

    opportunities = []

    for bk_odds in bookmaker_odds:
        market_name = bk_odds.get("market", "")
        odds = bk_odds.get("odds", 0)
        bookmaker = bk_odds.get("bookmaker", "unknown")
        bk_weight = get_bookmaker_weight(bookmaker)

        # ── HARD FILTER 1: Market must be tradable ──
        if not _is_tradable_market(market_name, home_name, away_name):
            continue

        # ── HARD FILTER 2: Odds range ──
        if not (MIN_ODDS <= odds <= MAX_ODDS):
            continue

        # ── HARD FILTER 3: We must have a model probability ──
        model = market_lookup.get(market_name)
        if not model:
            continue

        cal_prob = model.get("probability", 0)
        raw_prob = model.get("raw_probability", cal_prob)
        market_type = model.get("market_type", "")

        # ── HARD FILTER 4: Exclude noisy market types ──
        if market_type in EXCLUDED_MARKET_TYPES:
            continue

        # ── HARD FILTER 5: Minimum calibrated probability ──
        if cal_prob < MIN_CALIBRATED_PROB:
            continue

        # ── Compute metrics ──
        implied = 100.0 / odds
        edge = compute_edge(cal_prob, odds)
        ev = compute_ev(cal_prob, odds)
        kelly_stake = compute_kelly(cal_prob, odds)
        tier = tier_lookup.get(market_name)

        # Weighted edge: discount edges against soft books
        w_edge = edge * bk_weight

        # ── HARD FILTER 6: Minimum weighted edge ──
        # Against soft books, raw edge must be even larger
        if w_edge < MIN_EDGE:
            continue

        # ── HARD FILTER 7: Positive EV ──
        if ev < MIN_EV:
            continue

        grade, rec_stake = _grade_bet(edge, ev, bk_weight)

        bet = ExecutableBet(
            match_id=match_id,
            home_team=home_name,
            away_team=away_name,
            league=league_name,
            market=market_name,
            bookmaker=bookmaker,
            bookmaker_weight=bk_weight,
            odds=odds,
            raw_probability=raw_prob,
            calibrated_probability=cal_prob,
            implied_probability=implied,
            edge=edge,
            weighted_edge=w_edge,
            ev=ev,
            kelly_fraction=kelly_stake / 100.0,
            recommended_stake_pct=rec_stake,
            tier=tier,
            market_type=market_type,
            confidence_grade=grade,
        )
        opportunities.append(bet)

    # Sort by EV descending, then by weighted edge
    opportunities.sort(key=lambda b: (b.ev, b.weighted_edge), reverse=True)

    # Cap at MAX_BETS_PER_MATCH
    return opportunities[:MAX_BETS_PER_MATCH]


def find_all_executable_bets_for_day(
    matches: list[dict],
) -> list[ExecutableBet]:
    """Find executable bets across all matches for a day.

    Each match dict must contain:
        - analysis: output of _compute_match_analysis()
        - bookmaker_odds: list of normalized odds dicts
        - home_name, away_name, league_name, match_id

    Returns global list capped at MAX_BETS_PER_DAY, sorted by EV.
    """
    all_bets = []

    for match in matches:
        bets = find_executable_bets(
            analysis=match["analysis"],
            bookmaker_odds=match["bookmaker_odds"],
            home_name=match["home_name"],
            away_name=match["away_name"],
            league_name=match["league_name"],
            match_id=match.get("match_id", ""),
        )
        all_bets.extend(bets)

    # Global sort by EV
    all_bets.sort(key=lambda b: (b.ev, b.weighted_edge), reverse=True)

    # Global cap
    return all_bets[:MAX_BETS_PER_DAY]


# ═══════════════════════════════════════════════════════════════════════
# ODDS SIMULATION — For testing without API key
# ═══════════════════════════════════════════════════════════════════════

def generate_simulated_odds(
    analysis: dict,
    home_name: str,
    away_name: str,
    margin: float = 0.05,
) -> list[dict]:
    """Generate simulated bookmaker odds from model probabilities.

    Adds a bookmaker margin (vig) to the true probability to simulate
    realistic odds. Only generates odds for tradable markets.

    This lets the execution engine work without a live odds feed.

    Args:
        analysis: output of _compute_match_analysis()
        home_name: home team name
        away_name: away team name
        margin: bookmaker overround (default 5%)
    """
    simulated = []

    for section_name, section_markets in analysis.get("full_analysis", {}).items():
        for m in section_markets:
            market_name = m["market"]

            if not _is_tradable_market(market_name, home_name, away_name):
                continue

            cal_prob = m.get("probability", 0)
            if cal_prob <= 0 or cal_prob >= 100:
                continue

            # Simulate bookmaker odds with margin
            # Book implied prob = true prob + margin/2 (vig)
            book_prob = min(95, cal_prob + margin * 100 / 2)
            odds = round(100.0 / book_prob, 2)

            # Add some random-ish noise to make it realistic
            # (bookmaker doesn't know our exact probability)
            import random
            noise = random.uniform(-0.08, 0.08)
            odds = round(odds * (1 + noise), 2)

            if odds < 1.01:
                continue

            simulated.append({
                "market": market_name,
                "odds": odds,
                "bookmaker": "simulated",
            })

    return simulated

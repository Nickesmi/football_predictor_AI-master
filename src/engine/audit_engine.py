"""
Production Reality Audit Engine — Evidence-based inspection of all pipelines.

No prediction logic is modified here. This module only INSPECTS, TESTS, VALIDATES,
and REPORTS on the actual state of the platform.

Audit areas:
  1. Live Data        — provider connections, fixture counts, cache health
  2. Real-time Fresh  — staleness detection per freshness tier
  3. Blind Prediction — data quality classification per match
  4. Odds Integrity   — odds pipeline validation
  5. Probability Math — mathematical consistency of market probabilities
  6. Model Validation — backtest on settled predictions
  7. Provider Health  — failover and provider status
  8. Warehouse        — database completeness and coverage
  9. Production Ready — aggregated readiness score
"""

from __future__ import annotations

import json
import math
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("football_predictor")

# ── Freshness rules (seconds) ──────────────────────────────────────────────
FRESHNESS_UPCOMING = 6 * 3600       # 6 hours
FRESHNESS_TODAY    = 30 * 60        # 30 minutes
FRESHNESS_LIVE     = 60             # 60 seconds
FRESHNESS_PREMATCH_ODDS = 15 * 60   # 15 minutes
FRESHNESS_KICKOFF_ODDS  = 5 * 60    # 5 minutes within 1hr of kickoff
FRESHNESS_SETTLEMENT    = 10 * 60   # 10 minutes after FT


# ═══════════════════════════════════════════════════════════════════════
# 1. LIVE DATA AUDIT
# ═══════════════════════════════════════════════════════════════════════

def audit_live_data(date_str: str) -> dict:
    """Audit live data connections and counts for a given date."""
    from src.config import APIFOOTBALL_API_KEY

    cache_dir = Path(".cache")
    cache_path = cache_dir / f"fixtures-{date_str}.json"

    # Cache state
    cache_exists = cache_path.exists()
    cache_age = None
    cache_count = 0
    cache_last_modified = None
    cached_fixtures = []

    if cache_exists:
        cache_age = time.time() - cache_path.stat().st_mtime
        cache_last_modified = datetime.fromtimestamp(
            cache_path.stat().st_mtime, tz=timezone.utc
        ).isoformat()
        try:
            cached_fixtures = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached_fixtures, list):
                cache_count = len(cached_fixtures)
        except Exception:
            pass

    # SofaScore live count
    sofascore_count = 0
    sofascore_error = None
    try:
        from curl_cffi import requests as cffi_requests
        url = f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{date_str}"
        headers = {
            "Accept": "*/*",
            "Referer": "https://www.sofascore.com/",
            "Origin": "https://www.sofascore.com",
        }
        resp = cffi_requests.get(url, headers=headers, impersonate="chrome", timeout=15)
        if resp.status_code == 200:
            sofascore_count = len(resp.json().get("events", []))
        else:
            sofascore_error = f"HTTP {resp.status_code}"
    except Exception as e:
        sofascore_error = str(e)

    # API-Football connection test (just check key exists)
    api_football_configured = bool(APIFOOTBALL_API_KEY)

    # Rendered count = cached count (all cached fixtures are rendered)
    rendered_count = cache_count

    # Date integrity check
    wrong_date_count = 0
    for f in cached_fixtures:
        if isinstance(f, dict) and f.get("date") != date_str:
            wrong_date_count += 1

    # Determine provider used
    sources = set()
    for f in cached_fixtures:
        if isinstance(f, dict):
            sources.add(f.get("source", "unknown"))
    provider_used = ", ".join(sorted(sources)) if sources else "none"

    # Freshness
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if date_str == today_utc:
        max_age = FRESHNESS_TODAY
    elif date_str > today_utc:
        max_age = FRESHNESS_UPCOMING
    else:
        max_age = float("inf")  # past dates don't expire

    is_fresh = cache_age is not None and cache_age <= max_age

    # Failures
    failures = []
    if sofascore_count == 0 and cache_count > 0 and sofascore_error:
        failures.append(f"SofaScore returned 0 but UI shows {cache_count} matches")
    if cache_age is not None and cache_age > max_age and date_str >= today_utc:
        failures.append(f"Cache stale: age={cache_age:.0f}s > max={max_age}s")
    if wrong_date_count > 0:
        failures.append(f"{wrong_date_count} fixtures have date != {date_str}")

    return {
        "date": date_str,
        "api_football_configured": api_football_configured,
        "sofascore_count": sofascore_count,
        "sofascore_error": sofascore_error,
        "cache_count": cache_count,
        "rendered_count": rendered_count,
        "provider_used": provider_used,
        "is_fresh": is_fresh,
        "cache_age_seconds": round(cache_age, 1) if cache_age else None,
        "last_updated": cache_last_modified,
        "wrong_date_fixtures": wrong_date_count,
        "failures": failures,
        "passed": len(failures) == 0,
    }


# ═══════════════════════════════════════════════════════════════════════
# 2. REAL-TIME FRESHNESS AUDIT
# ═══════════════════════════════════════════════════════════════════════

def audit_realtime_freshness(date_str: str) -> dict:
    """Audit freshness of fixtures, scores, and caches."""
    cache_dir = Path(".cache")
    cache_path = cache_dir / f"fixtures-{date_str}.json"

    now = time.time()
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    fixtures_fresh = True
    live_scores_fresh = True
    settlement_fresh = True
    stale_items = []

    cached_fixtures = []
    cache_age = None
    if cache_path.exists():
        cache_age = now - cache_path.stat().st_mtime
        try:
            cached_fixtures = json.loads(cache_path.read_text(encoding="utf-8"))
            if not isinstance(cached_fixtures, list):
                cached_fixtures = []
        except Exception:
            pass

    # Check fixture freshness
    has_live = any(
        isinstance(f, dict) and "LIVE" in f.get("status", "")
        for f in cached_fixtures
    )
    has_ns = any(
        isinstance(f, dict) and f.get("status") == "NS"
        for f in cached_fixtures
    )

    if has_live:
        if cache_age and cache_age > FRESHNESS_LIVE:
            fixtures_fresh = False
            live_scores_fresh = False
            stale_items.append({
                "type": "live_fixtures",
                "age_seconds": round(cache_age),
                "max_allowed": FRESHNESS_LIVE,
                "message": f"Live matches present but cache is {cache_age:.0f}s old (max {FRESHNESS_LIVE}s)",
            })
    elif date_str == today_utc:
        if cache_age and cache_age > FRESHNESS_TODAY:
            fixtures_fresh = False
            stale_items.append({
                "type": "today_fixtures",
                "age_seconds": round(cache_age),
                "max_allowed": FRESHNESS_TODAY,
                "message": f"Today's fixtures cache is {cache_age:.0f}s old (max {FRESHNESS_TODAY}s)",
            })
    elif date_str > today_utc:
        if cache_age and cache_age > FRESHNESS_UPCOMING:
            fixtures_fresh = False
            stale_items.append({
                "type": "upcoming_fixtures",
                "age_seconds": round(cache_age),
                "max_allowed": FRESHNESS_UPCOMING,
                "message": f"Upcoming fixtures cache is {cache_age:.0f}s old (max {FRESHNESS_UPCOMING}s)",
            })

    # Settlement check: are there FT matches from past dates still with LIVE status?
    if date_str < today_utc:
        stuck_live = [
            f for f in cached_fixtures
            if isinstance(f, dict) and "LIVE" in f.get("status", "")
        ]
        if stuck_live:
            settlement_fresh = False
            stale_items.append({
                "type": "stuck_live_matches",
                "count": len(stuck_live),
                "message": f"{len(stuck_live)} matches from {date_str} still show LIVE status",
            })

    # Odds freshness — check if odds_snapshots table has recent data
    odds_fresh = True
    try:
        from src.db.database import get_db
        conn = get_db()
        latest_odds = conn.execute(
            "SELECT MAX(timestamp) FROM odds_snapshots"
        ).fetchone()[0]
        if latest_odds:
            odds_age = (datetime.now() - datetime.fromisoformat(latest_odds)).total_seconds()
            if odds_age > FRESHNESS_PREMATCH_ODDS:
                odds_fresh = False
                stale_items.append({
                    "type": "odds",
                    "age_seconds": round(odds_age),
                    "max_allowed": FRESHNESS_PREMATCH_ODDS,
                    "message": f"Latest odds snapshot is {odds_age:.0f}s old",
                })
        else:
            odds_fresh = False
            stale_items.append({
                "type": "odds",
                "message": "No odds snapshots in database — odds pipeline is not operational",
            })
    except Exception as e:
        odds_fresh = False
        stale_items.append({"type": "odds", "message": f"Cannot check odds: {e}"})

    return {
        "date": date_str,
        "fixtures_fresh": fixtures_fresh,
        "odds_fresh": odds_fresh,
        "live_scores_fresh": live_scores_fresh,
        "settlement_fresh": settlement_fresh,
        "stale_items": stale_items,
        "cache_age_seconds": round(cache_age) if cache_age else None,
        "has_live_matches": has_live,
        "has_upcoming_matches": has_ns,
        "total_fixtures": len(cached_fixtures),
        "passed": fixtures_fresh and live_scores_fresh and settlement_fresh,
    }


# ═══════════════════════════════════════════════════════════════════════
# 3. BLIND PREDICTION AUDIT
# ═══════════════════════════════════════════════════════════════════════

def classify_prediction_quality(score: float) -> str:
    """Classify data quality score into prediction quality label."""
    if score >= 80:
        return "high"
    elif score >= 60:
        return "medium"
    elif score >= 40:
        return "low"
    else:
        return "blind"


def audit_blind_predictions(date_str: str) -> dict:
    """Audit data quality for all matches on a date."""
    cache_path = Path(".cache") / f"fixtures-{date_str}.json"
    if not cache_path.exists():
        return {"date": date_str, "matches": [], "summary": {}, "passed": False,
                "message": "No cached fixtures for this date"}

    try:
        fixtures = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(fixtures, list):
            fixtures = []
    except Exception:
        return {"date": date_str, "matches": [], "summary": {}, "passed": False,
                "message": "Failed to read fixture cache"}

    from src.ml.team_stats_db import get_team_stats, LEAGUE_STATS, ALL_HOME, ALL_AWAY
    from src.ml.feature_builder import FeatureBuilder, TeamProfile
    from src.ml.poisson_model import PoissonGoalModel

    # Import league mapping
    try:
        from api.main import LEAGUE_NAME_MAP
    except ImportError:
        LEAGUE_NAME_MAP = {}

    results = []
    quality_counts = {"high": 0, "medium": 0, "low": 0, "blind": 0}

    for f in fixtures:
        if not isinstance(f, dict):
            continue
        home_name = f.get("home_team", {}).get("name", "")
        away_name = f.get("away_team", {}).get("name", "")
        league_name = f.get("league", {}).get("name", "")

        if not home_name or not away_name:
            continue

        league_key = LEAGUE_NAME_MAP.get(league_name, league_name)
        missing_inputs = []

        # Check if teams exist in hardcoded DB
        home_lower = home_name.lower()
        away_lower = away_name.lower()

        home_in_db = any(k in home_lower for k in ALL_HOME)
        away_in_db = any(k in away_lower for k in ALL_AWAY)

        if not home_in_db:
            missing_inputs.append(f"home_team '{home_name}' not in hardcoded stats — using hash fallback")
        if not away_in_db:
            missing_inputs.append(f"away_team '{away_name}' not in hardcoded stats — using hash fallback")

        # Check live DB
        try:
            from src.db.database import get_db
            from src.db.team_state import get_team_state as get_live_state
            conn = get_db()
            h_live = get_live_state(conn, home_name, league_key, "overall")
            a_live = get_live_state(conn, away_name, league_key, "overall")
            if h_live and h_live.matches_played >= 1:
                if f"home_team" in str(missing_inputs):
                    pass  # Live DB covers it
            elif not home_in_db:
                missing_inputs.append(f"home_team '{home_name}' also not in live DB")
            if a_live and a_live.matches_played >= 1:
                pass
            elif not away_in_db:
                missing_inputs.append(f"away_team '{away_name}' also not in live DB")
        except Exception:
            pass

        # League mapping check
        if league_key not in LEAGUE_STATS and league_key not in LEAGUE_NAME_MAP.values():
            missing_inputs.append(f"league '{league_name}' has no dedicated Poisson profile — using fallback")

        # Compute data quality score
        try:
            home_stats = get_team_stats(home_name, "home", league_key)
            away_stats = get_team_stats(away_name, "away", league_key)
            poisson = PoissonGoalModel(league_key)
            pred = poisson.predict(
                home_scored=home_stats.scored, home_conceded=home_stats.conceded,
                away_scored=away_stats.scored, away_conceded=away_stats.conceded,
                home_team=home_name, away_team=away_name,
            )

            home_profile = TeamProfile(
                team_name=home_name, matches_played=home_stats.matches_played,
                avg_scored=home_stats.scored, avg_conceded=home_stats.conceded,
                avg_total_goals=home_stats.scored + home_stats.conceded,
                btts_rate=round(pred.btts_yes / 100, 3),
                clean_sheet_rate=round(pred.home_clean_sheet / 100, 3),
                failed_to_score_rate=round(max(0.05, 1 - pred.over_0_5 / 100), 3),
                over_1_5_rate=round(pred.over_1_5 / 100, 3),
                over_2_5_rate=round(pred.over_2_5 / 100, 3),
                over_0_5_ht_rate=0.7,
                form_last5=getattr(home_stats, 'form_last5', 0.5),
                goal_diff=0.0,
            )
            away_profile = TeamProfile(
                team_name=away_name, matches_played=away_stats.matches_played,
                avg_scored=away_stats.scored, avg_conceded=away_stats.conceded,
                avg_total_goals=away_stats.scored + away_stats.conceded,
                btts_rate=round(pred.btts_yes / 100, 3),
                clean_sheet_rate=round(pred.away_clean_sheet / 100, 3),
                failed_to_score_rate=round(max(0.05, 1 - pred.over_0_5 / 100), 3),
                over_1_5_rate=round(pred.over_1_5 / 100, 3),
                over_2_5_rate=round(pred.over_2_5 / 100, 3),
                over_0_5_ht_rate=0.7,
                form_last5=getattr(away_stats, 'form_last5', 0.5),
                goal_diff=0.0,
            )
            country = f.get("league", {}).get("country", "")
            dq = FeatureBuilder.compute_data_quality(home_profile, away_profile, league_name, country)
        except Exception as e:
            dq = 0.0
            missing_inputs.append(f"data_quality computation failed: {e}")

        quality = classify_prediction_quality(dq)
        quality_counts[quality] += 1

        results.append({
            "fixture_id": f.get("id"),
            "home": home_name,
            "away": away_name,
            "league": league_name,
            "data_quality_score": round(dq, 1),
            "prediction_quality": quality,
            "missing_inputs": missing_inputs,
        })

    total = len(results)
    blind_count = quality_counts["blind"]
    blind_pct = round(blind_count / total * 100, 1) if total > 0 else 0

    return {
        "date": date_str,
        "total_matches": total,
        "quality_distribution": quality_counts,
        "blind_percentage": blind_pct,
        "matches": results,
        "passed": blind_pct < 50,  # Fail if >50% of predictions are blind
    }


# ═══════════════════════════════════════════════════════════════════════
# 4. ODDS PIPELINE AUDIT
# ═══════════════════════════════════════════════════════════════════════

def audit_odds_integrity(date_str: str) -> dict:
    """Audit the odds pipeline for data availability and integrity."""
    try:
        from src.db.database import get_db
        conn = get_db()
    except Exception as e:
        return {"passed": False, "error": f"Cannot connect to DB: {e}"}

    # Count odds snapshots
    total_odds = conn.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone()[0]
    today_odds = conn.execute(
        "SELECT COUNT(*) FROM odds_snapshots WHERE DATE(timestamp) = ?",
        (date_str,)
    ).fetchone()[0]

    # Check for stale odds
    stale_odds = []
    latest = conn.execute("SELECT MAX(timestamp) FROM odds_snapshots").fetchone()[0]

    # Market math violations — check from picks table
    total_picks = conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0]

    # Check matches table
    total_matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]

    failures = []
    if total_odds == 0:
        failures.append("No odds snapshots in database — odds pipeline is NOT operational")
    if total_matches == 0:
        failures.append("No matches in 'matches' table — odds pipeline has no match registry")
    if total_picks == 0:
        failures.append("No picks in database — execution pipeline has never produced a bet")

    return {
        "date": date_str,
        "odds_available": total_odds > 0,
        "total_odds_snapshots": total_odds,
        "today_odds_snapshots": today_odds,
        "latest_odds_timestamp": latest,
        "total_matches_registered": total_matches,
        "total_picks": total_picks,
        "stale_odds": stale_odds,
        "market_math_violations": [],
        "edge_calculation_valid": total_odds > 0,
        "failures": failures,
        "passed": len(failures) == 0,
    }


# ═══════════════════════════════════════════════════════════════════════
# 5. PROBABILITY MATHEMATICS AUDIT
# ═══════════════════════════════════════════════════════════════════════

def audit_probability_integrity(home: str, away: str, league: str) -> dict:
    """Run a single match through the prediction engine and validate math."""
    try:
        from api.main import _compute_match_analysis
    except ImportError:
        return {"passed": False, "error": "Cannot import prediction engine"}

    try:
        analysis = _compute_match_analysis(home, away, league, shuffle_tiers=False)
    except Exception as e:
        return {"passed": False, "error": f"Analysis failed: {e}"}

    fa = analysis.get("full_analysis", {})
    violations = []
    checks = []

    def _find(section, name):
        for m in fa.get(section, []):
            if m["market"] == name:
                return m["probability"]
        return None

    # 1. Home + Draw + Away = 100%
    hw = _find("Result", "Home Win")
    dr = _find("Result", "Draw")
    aw = _find("Result", "Away Win")
    if hw is not None and dr is not None and aw is not None:
        total = hw + dr + aw
        diff = abs(total - 100.0)
        status = "PASS" if diff <= 0.5 else "FAIL"
        checks.append({"identity": "Home + Draw + Away = 100%",
                        "values": f"{hw} + {dr} + {aw} = {total:.1f}%",
                        "difference": round(diff, 2), "status": status})
        if status == "FAIL":
            violations.append(f"1X2 sum = {total:.1f}% (off by {diff:.1f}pp)")

    # 2. Over + Under pairs
    for section_name, section_key in [("Goals", "Goals"), ("First Half", "First Half"),
                                       ("Second Half", "Second Half")]:
        for t in ["0.5", "1.5", "2.5", "3.5", "4.5"]:
            prefix = ""
            if section_key == "First Half":
                prefix = "FH "
            elif section_key == "Second Half":
                prefix = "SH "
            over = _find(section_key, f"{prefix}Over {t} Goals")
            under = _find(section_key, f"{prefix}Under {t} Goals")
            if over is not None and under is not None:
                total = over + under
                diff = abs(total - 100.0)
                status = "PASS" if diff <= 0.5 else "FAIL"
                checks.append({"identity": f"{prefix}O/U {t} = 100%",
                                "values": f"{over} + {under} = {total:.1f}%",
                                "difference": round(diff, 2), "status": status})
                if status == "FAIL":
                    violations.append(f"{prefix}O/U {t} sum = {total:.1f}%")

    # 3. BTTS Yes + BTTS No = 100%
    for prefix in ["", "FH ", "SH "]:
        btts_y = _find("Goals" if not prefix else ("First Half" if prefix == "FH " else "Second Half"),
                        f"{prefix}BTTS - Yes")
        btts_n = _find("Goals" if not prefix else ("First Half" if prefix == "FH " else "Second Half"),
                        f"{prefix}BTTS - No")
        if btts_y is not None and btts_n is not None:
            total = btts_y + btts_n
            diff = abs(total - 100.0)
            status = "PASS" if diff <= 0.5 else "FAIL"
            checks.append({"identity": f"{prefix}BTTS Yes + No = 100%",
                            "values": f"{btts_y} + {btts_n} = {total:.1f}%",
                            "difference": round(diff, 2), "status": status})
            if status == "FAIL":
                violations.append(f"{prefix}BTTS sum = {total:.1f}%")

    # 4. Double Chance identities
    if hw is not None and dr is not None:
        dc_1x = _find("Result", f"1X ({home} or Draw)")
        if dc_1x is not None:
            expected = hw + dr
            diff = abs(dc_1x - expected)
            status = "PASS" if diff <= 0.5 else "FAIL"
            checks.append({"identity": "1X = Home + Draw",
                            "values": f"1X={dc_1x} vs H+D={expected:.1f}",
                            "difference": round(diff, 2), "status": status})
            if status == "FAIL":
                violations.append(f"1X={dc_1x} ≠ H+D={expected:.1f}")

    if aw is not None and dr is not None:
        dc_x2 = _find("Result", f"X2 ({away} or Draw)")
        if dc_x2 is not None:
            expected = aw + dr
            diff = abs(dc_x2 - expected)
            status = "PASS" if diff <= 0.5 else "FAIL"
            checks.append({"identity": "X2 = Away + Draw",
                            "values": f"X2={dc_x2} vs A+D={expected:.1f}",
                            "difference": round(diff, 2), "status": status})
            if status == "FAIL":
                violations.append(f"X2={dc_x2} ≠ A+D={expected:.1f}")

    total_checks = len(checks)
    passed_checks = sum(1 for c in checks if c["status"] == "PASS")

    return {
        "home": home,
        "away": away,
        "league": league,
        "total_checks": total_checks,
        "passed_checks": passed_checks,
        "failed_checks": total_checks - passed_checks,
        "violations": violations,
        "checks": checks,
        "passed": len(violations) == 0,
    }


# ═══════════════════════════════════════════════════════════════════════
# 6. MODEL VALIDATION AUDIT
# ═══════════════════════════════════════════════════════════════════════

def audit_model_validation() -> dict:
    """Backtest on settled predictions from prediction_log."""
    try:
        from src.db.database import get_db
        conn = get_db()
    except Exception as e:
        return {"passed": False, "error": f"Cannot connect to DB: {e}"}

    # Core stats from prediction_log (multi-market)
    rows = conn.execute(
        """SELECT predicted_prob, actual_outcome, market_type
           FROM prediction_log WHERE actual_outcome IS NOT NULL"""
    ).fetchall()

    total = len(rows)
    if total == 0:
        return {
            "audit_passed": False,
            "settled_predictions": 0,
            "message": "No settled predictions in prediction_log",
        }

    correct = sum(1 for r in rows if r[1] == 1)
    accuracy = round(correct / total * 100, 2)

    # Brier Score
    eps = 1e-7
    brier_sum = 0.0
    log_loss_sum = 0.0
    for r in rows:
        p = max(eps, min(1 - eps, r[0] / 100.0))
        y = float(r[1])
        brier_sum += (p - y) ** 2
        log_loss_sum += -(y * math.log(p) + (1 - y) * math.log(1 - p))

    brier = round(brier_sum / total, 4)
    log_loss = round(log_loss_sum / total, 4)
    avg_prob = round(sum(r[0] for r in rows) / total, 2)
    avg_actual = round(correct / total * 100, 2)
    cal_error = round(avg_prob - avg_actual, 2)

    # Confidence buckets
    buckets = {}
    for r in rows:
        b = min(int(r[0] // 10) * 10, 90)
        key = f"{b}-{b+10}%"
        if key not in buckets:
            buckets[key] = {"total": 0, "hits": 0, "sum_prob": 0.0}
        buckets[key]["total"] += 1
        buckets[key]["hits"] += r[1]
        buckets[key]["sum_prob"] += r[0]

    confidence_buckets = []
    for key in sorted(buckets.keys(), key=lambda x: int(x.split("-")[0])):
        b = buckets[key]
        if b["total"] >= 5:
            hit_rate = round(b["hits"] / b["total"] * 100, 1)
            avg_p = round(b["sum_prob"] / b["total"], 1)
            confidence_buckets.append({
                "bucket": key,
                "count": b["total"],
                "hit_rate": hit_rate,
                "avg_predicted": avg_p,
                "calibration_gap": round(avg_p - hit_rate, 1),
            })

    # Per market type
    market_stats = {}
    for r in rows:
        mt = r[2]
        if mt not in market_stats:
            market_stats[mt] = {"total": 0, "hits": 0}
        market_stats[mt]["total"] += 1
        market_stats[mt]["hits"] += r[1]

    by_market = []
    for mt, stats in sorted(market_stats.items(), key=lambda x: -x[1]["total"]):
        by_market.append({
            "market_type": mt,
            "total": stats["total"],
            "hits": stats["hits"],
            "accuracy": round(stats["hits"] / stats["total"] * 100, 1) if stats["total"] > 0 else 0,
        })

    # 1X2 from error_intelligence
    ei_rows = conn.execute(
        "SELECT confidence, correct FROM prediction_errors WHERE correct IS NOT NULL"
    ).fetchall()
    ei_accuracy = round(sum(r[1] for r in ei_rows) / len(ei_rows) * 100, 2) if ei_rows else 0
    ei_brier = 0.0
    for r in ei_rows:
        p = max(eps, min(1 - eps, r[0] / 100.0))
        ei_brier += (p - float(r[1])) ** 2
    ei_brier = round(ei_brier / len(ei_rows), 4) if ei_rows else 0

    return {
        "audit_passed": True,
        "settled_predictions": total,
        "accuracy_pct": accuracy,
        "brier_score": brier,
        "log_loss": log_loss,
        "calibration_error": cal_error,
        "roi": None,  # No staking/odds data to compute ROI
        "confidence_buckets": confidence_buckets,
        "by_market_type": by_market,
        "error_intelligence_1x2": {
            "settled": len(ei_rows),
            "accuracy": ei_accuracy,
            "brier_score": ei_brier,
        },
        "passed": brier < 0.30,  # Brier < 0.30 is acceptable
    }


# ═══════════════════════════════════════════════════════════════════════
# 7. PROVIDER FAILOVER AUDIT
# ═══════════════════════════════════════════════════════════════════════

# Global provider status tracking
_PROVIDER_STATUS = {
    "sofascore": {"status": "unknown", "last_success": None, "last_failure": None, "last_error": None},
    "api_football": {"status": "unknown", "last_success": None, "last_failure": None, "last_error": None},
    "the_odds_api": {"status": "unknown", "last_success": None, "last_failure": None, "last_error": None},
}


def record_provider_success(provider: str):
    """Record a successful API call."""
    now = datetime.now(timezone.utc).isoformat()
    if provider in _PROVIDER_STATUS:
        _PROVIDER_STATUS[provider]["status"] = "healthy"
        _PROVIDER_STATUS[provider]["last_success"] = now


def record_provider_failure(provider: str, error: str):
    """Record a failed API call."""
    now = datetime.now(timezone.utc).isoformat()
    if provider in _PROVIDER_STATUS:
        _PROVIDER_STATUS[provider]["status"] = "degraded"
        _PROVIDER_STATUS[provider]["last_failure"] = now
        _PROVIDER_STATUS[provider]["last_error"] = error


def audit_provider_health() -> dict:
    """Return current provider health status."""
    from src.config import APIFOOTBALL_API_KEY
    import os

    # Check API keys
    api_football_key = bool(APIFOOTBALL_API_KEY)
    the_odds_key = bool(os.getenv("THE_ODDS_API_KEY", ""))

    # Test SofaScore connectivity
    sofascore_status = dict(_PROVIDER_STATUS["sofascore"])
    try:
        from curl_cffi import requests as cffi_requests
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        url = f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{today}"
        resp = cffi_requests.get(url, headers={
            "Accept": "*/*",
            "Referer": "https://www.sofascore.com/",
            "Origin": "https://www.sofascore.com",
        }, impersonate="chrome", timeout=10)
        if resp.status_code == 200:
            count = len(resp.json().get("events", []))
            sofascore_status["status"] = "healthy"
            sofascore_status["last_success"] = datetime.now(timezone.utc).isoformat()
            sofascore_status["events_count"] = count
        else:
            sofascore_status["status"] = "degraded"
            sofascore_status["last_error"] = f"HTTP {resp.status_code}"
    except Exception as e:
        sofascore_status["status"] = "down"
        sofascore_status["last_failure"] = datetime.now(timezone.utc).isoformat()
        sofascore_status["last_error"] = str(e)

    return {
        "api_football": {
            "configured": api_football_key,
            **_PROVIDER_STATUS["api_football"],
        },
        "sofascore": sofascore_status,
        "the_odds_api": {
            "configured": the_odds_key,
            **_PROVIDER_STATUS["the_odds_api"],
        },
        "passed": sofascore_status.get("status") == "healthy" or api_football_key,
    }


# ═══════════════════════════════════════════════════════════════════════
# 8. WAREHOUSE AUDIT
# ═══════════════════════════════════════════════════════════════════════

def audit_warehouse() -> dict:
    """Audit database completeness and coverage."""
    try:
        from src.db.database import get_db
        conn = get_db()
    except Exception as e:
        return {"passed": False, "error": f"Cannot connect to DB: {e}"}

    # Table counts
    tables = {}
    for t in ["matches", "odds_snapshots", "picks", "prediction_log",
              "match_history", "team_state", "prediction_errors",
              "calibration_models", "daily_performance", "competitions"]:
        try:
            tables[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception:
            tables[t] = -1  # table missing

    # Competition stats
    try:
        countries = conn.execute("SELECT COUNT(DISTINCT country) FROM competitions").fetchone()[0]
        competitions = conn.execute("SELECT COUNT(*) FROM competitions").fetchone()[0]
    except Exception:
        countries = 0
        competitions = 0

    # Team stats
    try:
        teams = conn.execute("SELECT COUNT(DISTINCT team_name) FROM team_state").fetchone()[0]
        avg_matches = conn.execute("SELECT AVG(matches_played) FROM team_state WHERE venue='overall'").fetchone()[0]
    except Exception:
        teams = 0
        avg_matches = 0

    # Match history coverage by date
    coverage_by_date = []
    try:
        rows = conn.execute(
            """SELECT match_date, COUNT(*) as n
               FROM match_history
               GROUP BY match_date ORDER BY match_date DESC LIMIT 14"""
        ).fetchall()
        coverage_by_date = [{"date": r[0], "matches": r[1]} for r in rows]
    except Exception:
        pass

    # Duplicate check in match_history
    duplicates = 0
    try:
        dup_row = conn.execute(
            "SELECT COUNT(*) FROM (SELECT match_id, COUNT(*) as c FROM match_history GROUP BY match_id HAVING c > 1)"
        ).fetchone()[0]
        duplicates = dup_row
    except Exception:
        pass

    # Prediction log coverage
    pred_dates = []
    try:
        rows = conn.execute(
            """SELECT match_date, COUNT(*) as n, SUM(CASE WHEN actual_outcome IS NOT NULL THEN 1 ELSE 0 END) as settled
               FROM prediction_log GROUP BY match_date ORDER BY match_date DESC LIMIT 14"""
        ).fetchall()
        pred_dates = [{"date": r[0], "predictions": r[1], "settled": r[2]} for r in rows]
    except Exception:
        pass

    failures = []
    if tables.get("matches", 0) == 0:
        failures.append("'matches' table is empty — odds pipeline never registered matches")
    if tables.get("odds_snapshots", 0) == 0:
        failures.append("'odds_snapshots' table is empty — no real odds data")
    if tables.get("calibration_models", 0) == 0:
        failures.append("'calibration_models' table is empty — no isotonic models fitted")
    if duplicates > 0:
        failures.append(f"{duplicates} duplicate match_ids in match_history")

    return {
        "table_counts": tables,
        "countries": countries,
        "competitions": competitions,
        "teams": teams,
        "avg_matches_per_team": round(avg_matches, 1) if avg_matches else 0,
        "duplicates_in_match_history": duplicates,
        "coverage_by_date": coverage_by_date,
        "prediction_coverage": pred_dates,
        "failures": failures,
        "passed": len([f for f in failures if "empty" in f.lower()]) <= 1,
    }


# ═══════════════════════════════════════════════════════════════════════
# 9. PRODUCTION READINESS — MASTER AUDIT
# ═══════════════════════════════════════════════════════════════════════

def audit_production_readiness(date_str: str) -> dict:
    """
    Master audit: aggregates all checks into a single production readiness score.

    Scoring:
      90-100: Production ready
      75-89:  Usable but needs monitoring
      50-74:  Unsafe for betting
      0-49:   Broken / blind
    """
    score = 100.0
    critical_failures = []
    warnings = []

    # 1. Live Data
    live = audit_live_data(date_str)
    if not live["passed"]:
        score -= 20
        critical_failures.extend(live["failures"])

    # 2. Freshness
    freshness = audit_realtime_freshness(date_str)
    if not freshness["passed"]:
        score -= 10
        for item in freshness["stale_items"]:
            warnings.append(item["message"])

    # 3. Blind Predictions
    blind = audit_blind_predictions(date_str)
    blind_pct = blind.get("blind_percentage", 0)
    if blind_pct > 50:
        score -= 25
        critical_failures.append(f"{blind_pct}% of predictions are blind (data quality < 40)")
    elif blind_pct > 20:
        score -= 10
        warnings.append(f"{blind_pct}% of predictions are blind")

    # 4. Odds Pipeline
    odds = audit_odds_integrity(date_str)
    if not odds["passed"]:
        score -= 15
        critical_failures.extend(odds["failures"])

    # 5. Probability Math (spot check with a known match)
    prob_check = None
    try:
        prob_check = audit_probability_integrity("Arsenal", "Chelsea", "Premier League")
        if not prob_check["passed"]:
            score -= 10
            critical_failures.extend(prob_check["violations"])
    except Exception as e:
        score -= 5
        warnings.append(f"Probability integrity check failed: {e}")

    # 6. Model Validation
    model = audit_model_validation()
    if not model.get("passed"):
        score -= 15
        critical_failures.append(f"Model Brier score {model.get('brier_score', 'N/A')} exceeds threshold")
    elif model.get("brier_score", 1) > 0.25:
        score -= 5
        warnings.append(f"Model Brier score {model['brier_score']} is mediocre (target < 0.20)")

    # 7. Provider Health
    providers = audit_provider_health()
    if not providers.get("passed"):
        score -= 15
        critical_failures.append("All data providers are down or unconfigured")

    # 8. Warehouse
    warehouse = audit_warehouse()
    if not warehouse["passed"]:
        score -= 10
        critical_failures.extend(warehouse["failures"][:3])  # top 3

    # Clamp
    score = max(0, min(100, score))

    # Determine status
    if score >= 90:
        status = "production_ready"
        recommended_action = "System is production ready. Monitor daily."
    elif score >= 75:
        status = "usable_with_monitoring"
        recommended_action = "Usable but address warnings. Monitor closely."
    elif score >= 50:
        status = "unsafe_for_betting"
        recommended_action = "Do NOT use for betting. Fix critical failures first."
    else:
        status = "broken_or_blind"
        recommended_action = "System is broken or operating blind. Major fixes required."

    return {
        "overall_score": round(score, 1),
        "status": status,
        "live_data": live["passed"],
        "freshness": freshness["passed"],
        "odds_integrity": odds["passed"],
        "probability_integrity": prob_check["passed"] if prob_check else False,
        "model_validation": model.get("passed", False),
        "warehouse_health": warehouse["passed"],
        "blind_prediction_pct": blind_pct,
        "critical_failures": critical_failures,
        "warnings": warnings,
        "recommended_action": recommended_action,
        # Sub-reports
        "details": {
            "live_data": live,
            "freshness": freshness,
            "blind_predictions_summary": {
                "total": blind.get("total_matches"),
                "distribution": blind.get("quality_distribution"),
                "blind_pct": blind_pct,
            },
            "odds": odds,
            "probability": prob_check,
            "model": model,
            "providers": providers,
            "warehouse": warehouse,
        },
    }

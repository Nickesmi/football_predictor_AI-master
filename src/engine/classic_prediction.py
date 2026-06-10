from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Optional

from src.db.daily_repo import insert_prediction, insert_result
from src.ml.poisson_model import LEAGUE_PROFILES, LeagueProfile, PoissonGoalModel, _poisson_pmf
from src.ml.team_stats_db import get_team_stats as fallback_team_stats


@dataclass
class ClassicTeamStats:
    team_name: str
    league_name: str
    venue: str
    matches_played: int
    goals_scored: int
    goals_conceded: int
    avg_scored: float
    avg_conceded: float
    home_form: float
    away_form: float
    attack_strength: float
    defense_strength: float
    recent_form: float


def _result_label(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "Home Win"
    if away_goals > home_goals:
        return "Away Win"
    return "Draw"


def _points_for(team_is_home: bool, home_goals: int, away_goals: int) -> int:
    if home_goals == away_goals:
        return 1
    home_won = home_goals > away_goals
    return 3 if home_won == team_is_home else 0


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _finished_rows(conn, league_name: str, cutoff_date: str) -> list[dict]:
    rows: list[dict] = []
    if _table_exists(conn, "match_history"):
        history_rows = conn.execute(
            """SELECT match_date AS date, league AS league_name, home_team, away_team,
                      home_goals, away_goals
               FROM match_history
               WHERE match_date < ?
                 AND home_goals IS NOT NULL
                 AND away_goals IS NOT NULL
                 AND (? = '' OR league = ?)
               ORDER BY match_date DESC""",
            (cutoff_date, league_name or "", league_name or ""),
        ).fetchall()
        rows.extend(dict(row) for row in history_rows)

    match_rows = conn.execute(
        """SELECT date, league_name, home_team, away_team, home_goals, away_goals
           FROM matches
           WHERE date < ?
             AND home_goals IS NOT NULL
             AND away_goals IS NOT NULL
             AND (? = '' OR league_name = ?)
           ORDER BY date DESC""",
        (cutoff_date, league_name or "", league_name or ""),
    ).fetchall()
    rows.extend(dict(row) for row in match_rows)
    return rows


def league_goal_profile(conn, league_name: str, cutoff_date: str) -> tuple[float, float]:
    rows = _finished_rows(conn, league_name, cutoff_date)
    if rows:
        avg_home = sum(float(r["home_goals"]) for r in rows) / len(rows)
        avg_away = sum(float(r["away_goals"]) for r in rows) / len(rows)
        return max(0.4, avg_home), max(0.3, avg_away)

    profile = LEAGUE_PROFILES.get(league_name) or LEAGUE_PROFILES.get("default")
    return profile.avg_home_goals, profile.avg_away_goals


def build_team_stats(
    conn,
    team_name: str,
    league_name: str,
    venue: str,
    cutoff_date: str,
) -> ClassicTeamStats:
    rows = _finished_rows(conn, league_name, cutoff_date)
    venue_rows = []
    all_team_rows = []
    for row in rows:
        is_home = row["home_team"] == team_name
        is_away = row["away_team"] == team_name
        if not is_home and not is_away:
            continue
        all_team_rows.append(row)
        if (venue == "home" and is_home) or (venue == "away" and is_away):
            venue_rows.append(row)

    league_avg_home, league_avg_away = league_goal_profile(conn, league_name, cutoff_date)
    attack_base = league_avg_home if venue == "home" else league_avg_away
    defense_base = league_avg_away if venue == "home" else league_avg_home

    if venue_rows:
        scored = 0
        conceded = 0
        form_points = 0
        for row in venue_rows:
            is_home = row["home_team"] == team_name
            gf = int(row["home_goals"] if is_home else row["away_goals"])
            ga = int(row["away_goals"] if is_home else row["home_goals"])
            scored += gf
            conceded += ga
            form_points += _points_for(is_home, int(row["home_goals"]), int(row["away_goals"]))

        matches_played = len(venue_rows)
        avg_scored = scored / matches_played
        avg_conceded = conceded / matches_played
        venue_form = form_points / max(1, matches_played)
    else:
        fallback = fallback_team_stats(team_name, venue, league_name)
        matches_played = 0
        scored = 0
        conceded = 0
        avg_scored = float(fallback.scored)
        avg_conceded = float(fallback.conceded)
        venue_form = 1.4

    recent_points = 0
    for row in all_team_rows[:5]:
        is_home = row["home_team"] == team_name
        recent_points += _points_for(is_home, int(row["home_goals"]), int(row["away_goals"]))
    recent_form = recent_points if all_team_rows else round(venue_form * 5, 1)

    attack_strength = avg_scored / max(attack_base, 0.01)
    defense_strength = avg_conceded / max(defense_base, 0.01)
    home_form = venue_form if venue == "home" else 0.0
    away_form = venue_form if venue == "away" else 0.0

    stats = ClassicTeamStats(
        team_name=team_name,
        league_name=league_name,
        venue=venue,
        matches_played=matches_played,
        goals_scored=scored,
        goals_conceded=conceded,
        avg_scored=round(avg_scored, 3),
        avg_conceded=round(avg_conceded, 3),
        home_form=round(home_form, 3),
        away_form=round(away_form, 3),
        attack_strength=round(attack_strength, 3),
        defense_strength=round(defense_strength, 3),
        recent_form=round(recent_form, 3),
    )
    store_team_stats(conn, stats)
    return stats


def store_team_stats(conn, stats: ClassicTeamStats) -> None:
    conn.execute(
        """INSERT INTO team_stats (
               team_name, league_name, venue, matches_played, goals_scored,
               goals_conceded, avg_scored, avg_conceded, home_form, away_form,
               attack_strength, defense_strength, recent_form, updated_at
           )
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(team_name, league_name, venue) DO UPDATE SET
               matches_played = excluded.matches_played,
               goals_scored = excluded.goals_scored,
               goals_conceded = excluded.goals_conceded,
               avg_scored = excluded.avg_scored,
               avg_conceded = excluded.avg_conceded,
               home_form = excluded.home_form,
               away_form = excluded.away_form,
               attack_strength = excluded.attack_strength,
               defense_strength = excluded.defense_strength,
               recent_form = excluded.recent_form,
               updated_at = CURRENT_TIMESTAMP""",
        (
            stats.team_name,
            stats.league_name,
            stats.venue,
            stats.matches_played,
            stats.goals_scored,
            stats.goals_conceded,
            stats.avg_scored,
            stats.avg_conceded,
            stats.home_form,
            stats.away_form,
            stats.attack_strength,
            stats.defense_strength,
            stats.recent_form,
        ),
    )
    conn.commit()


def _normalize_1x2(home: float, draw: float, away: float) -> tuple[float, float, float]:
    total = home + draw + away
    if total <= 0:
        return 33.4, 33.3, 33.3
    h = round(home / total * 100, 1)
    d = round(draw / total * 100, 1)
    a = round(100 - h - d, 1)
    return h, d, a


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _sample_weight(matches_played: int) -> float:
    if matches_played <= 0:
        return 0.28
    return _clamp(matches_played / (matches_played + 12), 0.18, 0.72)


def _shrink_rate(value: float, league_average: float, matches_played: int, low: float, high: float) -> float:
    weight = _sample_weight(matches_played)
    blended = (value * weight) + (league_average * (1 - weight))
    return _clamp(blended, low, high)


def _soften_1x2(home: float, draw: float, away: float) -> tuple[float, float, float]:
    # More aggressive shrinkage toward 33.3% to reduce overconfidence
    strength = 0.70
    home = 33.3 + ((home - 33.3) * strength)
    draw = 33.4 + ((draw - 33.4) * strength)
    away = 33.3 + ((away - 33.3) * strength)
    return _normalize_1x2(home, draw, away)


def _round_probability(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _calibrate_market_probability(name: str, probability: float) -> float:
    """
    Apply modern isotonic calibration to reduce overconfidence.
    Falls back to conservative shrinkage if isotonic models not available.
    """
    try:
        from src.db.database import get_db
        from src.engine.isotonic_calibrator import get_isotonic_calibrator
        from src.db.prediction_logger import _classify_market_type

        calibrator = get_isotonic_calibrator(get_db())
        market_type = _classify_market_type(name)
        
        # Apply aggressive base shrinkage first (0.65 pulls toward center)
        p = probability / 100.0
        shrunk = 0.5 + (p - 0.5) * 0.65
        shrunk_prob = round(shrunk * 100, 1)
        
        # Then apply isotonic calibration
        if calibrator and calibrator._models:
            calibrated = calibrator.calibrate(shrunk_prob, market_type)
        else:
            # Conservative fallback
            calibrated = round(0.80 * shrunk_prob + 0.20 * 50.0, 1)
        
        return _round_probability(_clamp(calibrated, 2.0, 88.0))
    except Exception as e:
        # Fallback to simple shrinkage if anything fails
        p = probability / 100.0
        shrunk = 0.5 + (p - 0.5) * 0.65
        return _round_probability(_clamp(shrunk * 100, 2.0, 88.0))


def _team_goal_over(lambda_goals: float, threshold: float) -> float:
    max_goals = int(threshold)
    return _round_probability((1 - sum(_poisson_pmf(k, lambda_goals) for k in range(max_goals + 1))) * 100)


def _team_goal_under(lambda_goals: float, threshold: float) -> float:
    return _round_probability(100 - _team_goal_over(lambda_goals, threshold))


def _build_matrix(lambda_home: float, lambda_away: float, max_goals: int = 8) -> dict[tuple[int, int], float]:
    return {
        (h, a): _poisson_pmf(h, lambda_home) * _poisson_pmf(a, lambda_away)
        for h in range(max_goals + 1)
        for a in range(max_goals + 1)
    }


def _matrix_probability(matrix: dict[tuple[int, int], float], condition) -> float:
    return _round_probability(sum(prob for (h, a), prob in matrix.items() if condition(h, a)) * 100)


def _exact_market(name: str, exact_probability: float) -> dict:
    probability = round(max(0.0, min(100.0, exact_probability)), 1)
    fair_odds = round(100 / probability, 2) if probability > 0 else None
    return {
        "market": name,
        "probability": probability,
        "fair_odds": fair_odds,
    }

def _normalized_binary_markets(name_a: str, prob_a: float, name_b: str, prob_b: float) -> list[dict]:
    # Calibrate independently
    calib_a = _calibrate_market_probability(name_a, _round_probability(prob_a))
    calib_b = _calibrate_market_probability(name_b, _round_probability(prob_b))
    
    # Force sum to exactly 100
    total = calib_a + calib_b
    if total <= 0:
        calib_a, calib_b = 50.0, 50.0
    else:
        calib_a = round(calib_a / total * 100, 1)
        calib_b = round(100.0 - calib_a, 1)
        
    return [
        _exact_market(name_a, calib_a),
        _exact_market(name_b, calib_b),
    ]

def _market(name: str, probability: float) -> dict:
    probability = _calibrate_market_probability(name, _round_probability(probability))
    fair_odds = round(100 / probability, 2) if probability > 0 else None
    return {
        "market": name,
        "probability": probability,
        "fair_odds": fair_odds,
    }


def _build_market_categories(home: str, away: str, pred, home_pct: float, draw_pct: float, away_pct: float) -> list[dict]:
    matrix = _build_matrix(pred.lambda_home, pred.lambda_away)
    goals = []
    for threshold in [0.5, 1.5, 2.5, 3.5, 4.5]:
        over = getattr(pred, f"over_{str(threshold).replace('.', '_')}", None)
        under = 100 - over if over is not None else None
        goals.extend(_normalized_binary_markets(f"Over {threshold} Goals", over, f"Under {threshold} Goals", under))

    home_team_goals = []
    away_team_goals = []
    for threshold in [0.5, 1.5, 2.5, 3.5, 4.5]:
        h_o = _team_goal_over(pred.lambda_home, threshold)
        h_u = _team_goal_under(pred.lambda_home, threshold)
        home_team_goals.extend(_normalized_binary_markets(f"{home} Over {threshold} Goals", h_o, f"{home} Under {threshold} Goals", h_u))

        a_o = _team_goal_over(pred.lambda_away, threshold)
        a_u = _team_goal_under(pred.lambda_away, threshold)
        away_team_goals.extend(_normalized_binary_markets(f"{away} Over {threshold} Goals", a_o, f"{away} Under {threshold} Goals", a_u))

    result_markets = [
        _exact_market("Home Win", home_pct),
        _exact_market("Draw", draw_pct),
        _exact_market("Away Win", away_pct),
        _exact_market(f"1X ({home} or Draw)", home_pct + draw_pct),
        _exact_market(f"X2 ({away} or Draw)", away_pct + draw_pct),
        _exact_market("12 (Any Team to Win)", home_pct + away_pct),
    ]

    handicap_markets = [
        _exact_market(f"{home} Handicap -0.5", home_pct),
        _exact_market(f"{away} Handicap -0.5", away_pct),
        _exact_market(f"{home} Handicap +0.5", home_pct + draw_pct),
        _exact_market(f"{away} Handicap +0.5", away_pct + draw_pct),
        _market(f"{home} Handicap -1.5", _matrix_probability(matrix, lambda h, a: h - a >= 2)),
        _market(f"{away} Handicap -1.5", _matrix_probability(matrix, lambda h, a: a - h >= 2)),
        _market(f"{home} Handicap +1.5", _matrix_probability(matrix, lambda h, a: h - a >= -1)),
        _market(f"{away} Handicap +1.5", _matrix_probability(matrix, lambda h, a: a - h >= -1)),
        _market(f"{home} Handicap -2.5", _matrix_probability(matrix, lambda h, a: h - a >= 3)),
        _market(f"{away} Handicap -2.5", _matrix_probability(matrix, lambda h, a: a - h >= 3)),
    ]

    # Clean sheets mathematically match "Under 0.5" goals for the opponent
    away_under_05 = next(m["probability"] for m in away_team_goals if "Under 0.5" in m["market"])
    home_under_05 = next(m["probability"] for m in home_team_goals if "Under 0.5" in m["market"])

    combined = []
    combined.extend(_normalized_binary_markets("BTTS - Yes", pred.btts_yes, "BTTS - No", pred.btts_no))
    combined.extend([
        _exact_market(f"{home} Clean Sheet", away_under_05),
        _exact_market(f"{away} Clean Sheet", home_under_05),
        _exact_market(f"{away} Fails to Score", away_under_05),
        _exact_market(f"{home} Fails to Score", home_under_05),
        _market("BTTS & Over 2.5", _matrix_probability(matrix, lambda h, a: h > 0 and a > 0 and h + a > 2.5)),
        _market("BTTS & Over 3.5", _matrix_probability(matrix, lambda h, a: h > 0 and a > 0 and h + a > 3.5)),
    ])

    categories = [
        ("Result", result_markets),
        ("Goals Over / Under", goals),
        (f"{home} Team Goals", home_team_goals),
        (f"{away} Team Goals", away_team_goals),
        ("Handicaps", handicap_markets),
        ("BTTS / Clean Sheet", combined),
    ]

    return [
        {
            "category": category,
            "markets": sorted(markets, key=lambda item: item["probability"], reverse=True),
            "total_markets": len(markets),
            "high_confidence": sum(1 for item in markets if item["probability"] >= 80),
        }
        for category, markets in categories
    ]


def _build_top_confident_picks(categories: list[dict], minimum: float = 60.0) -> list[dict]:
    picks = []
    seen = set()
    for category in categories:
        for market in category.get("markets", []):
            probability = float(market.get("probability") or 0)
            key = (category.get("category"), market.get("market"))
            if probability < minimum or key in seen:
                continue
            seen.add(key)
            picks.append({
                "category": category.get("category"),
                "market": market.get("market"),
                "probability": round(probability, 1),
                "fair_odds": market.get("fair_odds"),
            })

    picks.sort(key=lambda item: item["probability"], reverse=True)
    tiers = [
        ("Tier 1", "Highest Confidence", 80.0, 100.0),
        ("Tier 2", "Strong", 70.0, 79.9),
        ("Tier 3", "Standard", 60.0, 69.9),
    ]
    output = []
    for name, label, low, high in tiers:
        tier_picks = [
            pick for pick in picks
            if pick["probability"] >= low and pick["probability"] <= high
        ]
        if not tier_picks:
            continue
        output.append({
            "tier": name,
            "label": label,
            "range": f"{low:.0f}%-{high:.0f}%",
            "count": len(tier_picks),
            "picks": tier_picks[:12],
        })
    return output


def predict_fixture(conn, fixture: dict, target_date: str) -> dict:
    league = fixture.get("league") or fixture.get("league_name") or "default"
    home = fixture.get("home_team") or fixture.get("homeName") or "Home"
    away = fixture.get("away_team") or fixture.get("awayName") or "Away"

    home_stats = build_team_stats(conn, home, league, "home", target_date)
    away_stats = build_team_stats(conn, away, league, "away", target_date)
    league_home_avg, league_away_avg = league_goal_profile(conn, league, target_date)
    league_home_avg = _clamp(league_home_avg, 0.9, 1.85)
    league_away_avg = _clamp(league_away_avg, 0.65, 1.45)

    home_scored = _shrink_rate(home_stats.avg_scored, league_home_avg, home_stats.matches_played, 0.55, 2.35)
    home_conceded = _shrink_rate(home_stats.avg_conceded, league_away_avg, home_stats.matches_played, 0.45, 2.10)
    away_scored = _shrink_rate(away_stats.avg_scored, league_away_avg, away_stats.matches_played, 0.40, 1.95)
    away_conceded = _shrink_rate(away_stats.avg_conceded, league_home_avg, away_stats.matches_played, 0.55, 2.35)

    model = PoissonGoalModel(league if league in LEAGUE_PROFILES else "default")
    model.profile = LeagueProfile(league_home_avg, league_away_avg, league_home_avg + league_away_avg)
    pred = model.predict(
        home_scored=home_scored,
        home_conceded=home_conceded,
        away_scored=away_scored,
        away_conceded=away_conceded,
        home_team=home,
        away_team=away,
    )

    home_pct, draw_pct, away_pct = _normalize_1x2(pred.home_win, pred.draw, pred.away_win)
    home_pct, draw_pct, away_pct = _soften_1x2(home_pct, draw_pct, away_pct)

    # ── Strict Mathematical Calibration ──
    # We calibrate the base probabilities first, then perfectly derive all markets from them
    calib_h = _calibrate_market_probability("Home Win", home_pct)
    calib_d = _calibrate_market_probability("Draw", draw_pct)
    calib_a = _calibrate_market_probability("Away Win", away_pct)
    
    total_calib = calib_h + calib_d + calib_a
    if total_calib > 0:
        home_pct = round(calib_h / total_calib * 100, 1)
        draw_pct = round(calib_d / total_calib * 100, 1)
        away_pct = round(100.0 - home_pct - draw_pct, 1)
    choices = [
        ("Home Win", home_pct),
        ("Draw", draw_pct),
        ("Away Win", away_pct),
    ]
    predicted_result, confidence = max(choices, key=lambda item: item[1])
    predicted_score = pred.top_scorelines[0]["score"] if pred.top_scorelines else "0-0"
    market_categories = _build_market_categories(home, away, pred, home_pct, draw_pct, away_pct)

    return {
        "predicted_result": predicted_result,
        "predicted_score": predicted_score,
        "home_win_pct": home_pct,
        "draw_pct": draw_pct,
        "away_win_pct": away_pct,
        "confidence_pct": round(confidence, 1),
        "expected_goals": {
            "home": round(pred.lambda_home, 2),
            "away": round(pred.lambda_away, 2),
            "total": round(pred.lambda_home + pred.lambda_away, 2),
        },
        "market_categories": market_categories,
        "top_confident_picks": _build_top_confident_picks(market_categories, minimum=60.0),
    }


def ensure_classic_predictions_for_date(conn, target_date: str) -> int:
    rows = conn.execute(
        """SELECT id as match_id, league_name as league, home_team, away_team
           FROM matches
           WHERE date = ?
           ORDER BY kickoff""",
        (target_date,),
    ).fetchall()
    created = 0
    for row in rows:
        prediction = predict_fixture(conn, dict(row), target_date)
        insert_prediction(conn, row["match_id"], prediction)
        created += 1
    return created


def actual_result(home_goals: int, away_goals: int) -> str:
    return _result_label(home_goals, away_goals)


def settle_finished_predictions_for_date(conn, target_date: str) -> int:
    rows = conn.execute(
        """SELECT m.id, m.home_goals, m.away_goals, dp.predictions_json
           FROM matches m
           JOIN daily_predictions dp ON dp.match_id = m.id
           WHERE m.date = ?
             AND m.home_goals IS NOT NULL
             AND m.away_goals IS NOT NULL
             AND UPPER(COALESCE(m.status, '')) IN ('FT', 'AET', 'PEN')
           ORDER BY m.kickoff""",
        (target_date,),
    ).fetchall()
    settled = 0
    for row in rows:
        try:
            prediction = json.loads(row["predictions_json"] or "{}")
        except Exception:
            prediction = {}
        actual = actual_result(int(row["home_goals"]), int(row["away_goals"]))
        correct = prediction.get("predicted_result") == actual
        stored = {
            **prediction,
            "actual_result": actual,
            "correct": bool(correct),
        }
        insert_result(
            conn,
            row["id"],
            int(row["home_goals"]),
            int(row["away_goals"]),
            stored,
            bool(correct),
        )
        settled += 1
    return settled


def accuracy_for_date(conn, target_date: Optional[str] = None) -> dict:
    query = """SELECT COUNT(*) AS settled, SUM(dr.hit) AS correct
               FROM daily_results dr
               JOIN matches m ON m.id = dr.match_id"""
    params: list[str] = []
    if target_date:
        query += " WHERE m.date = ?"
        params.append(target_date)
    row = conn.execute(query, params).fetchone()
    settled = int(row["settled"] or 0)
    correct = int(row["correct"] or 0)
    accuracy = round((correct / settled * 100), 1) if settled else 0.0
    return {
        "settled_predictions": settled,
        "correct_predictions": correct,
        "accuracy_pct": accuracy,
    }

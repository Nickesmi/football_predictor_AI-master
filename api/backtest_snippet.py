
@app.get("/api/debug/backtest-features")
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
                h_pct = next(x["probability"] for x in pred["markets"] if x["market"] == "Home Win") / 100.0
                d_pct = next(x["probability"] for x in pred["markets"] if x["market"] == "Draw") / 100.0
                a_pct = next(x["probability"] for x in pred["markets"] if x["market"] == "Away Win") / 100.0
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

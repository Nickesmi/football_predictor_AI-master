#!/usr/bin/env bash
# Check Football Predictor Status

cd "$(dirname "$0")"
echo "====================================="
echo "      FOOTBALL PREDICTOR STATUS      "
echo "====================================="
echo ""

# Check workers
check_worker() {
    local name=$1
    local script=$2
    if pgrep -f "$script" > /dev/null; then
        echo -e "$name:\t\tRUNNING ✅"
    else
        echo -e "$name:\t\tSTOPPED ❌"
    fi
}

check_worker "Collection Worker" "collection_worker.py"
check_worker "Odds Worker" "odds_worker.py"
check_worker "Settlement Worker" "settlement_worker.py"

echo ""
echo "--- API Football Status ---"
# Note: Requires the web server to be running on 8001
API_RES=$(curl -s -H "x-api-key: dev-admin-secret" http://127.0.0.1:8001/api/debug/api-football-status || echo '{"error": "Web server offline"}')

if echo "$API_RES" | grep -q '"error"'; then
    echo "Web Server: OFFLINE ❌"
else
    CONNECTED=$(echo "$API_RES" | grep -o '"connected":[^,]*' | cut -d':' -f2)
    LIMIT=$(echo "$API_RES" | grep -o '"quota_limit":[^,]*' | cut -d':' -f2)
    USED=$(echo "$API_RES" | grep -o '"quota_used":[^,]*' | cut -d':' -f2 | tr -d '}')
    
    if [ "$CONNECTED" = "true" ]; then
        echo "Connected: ✅"
        REMAINING=$((LIMIT - USED))
        echo "Quota Remaining: $REMAINING / $LIMIT"
    else
        echo "Connected: ❌"
    fi
fi

echo ""
echo "--- Data Warehouse ---"
if [ -f "data/engine.db" ]; then
    SETTLED=$(sqlite3 data/engine.db "SELECT count(*) FROM scoreline_predictions WHERE actual_home_goals IS NOT NULL;" 2>/dev/null || echo "0")
    echo "Settled Picks: $SETTLED / 1000"
else
    echo "Settled Picks: 0 / 1000"
fi

echo ""
echo "Research Sprint:"
echo "NOT AUTHORIZED 🛑"
echo ""
echo "====================================="

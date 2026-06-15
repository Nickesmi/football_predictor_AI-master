#!/usr/bin/env bash
# Status of Tennis Prediction Engine workers
echo "══════════════════════════════════════"
echo "  TENNIS WORKER STATUS"
echo "══════════════════════════════════════"

for worker in tennis_collection_worker tennis_live_worker tennis_settlement_worker; do
  if pgrep -f "${worker}.py" > /dev/null 2>&1; then
    echo "  ✓ ${worker}: RUNNING"
  else
    echo "  ✗ ${worker}: STOPPED"
  fi
done

echo ""
echo "Settled picks:"
python3 -c "
import sys, os
sys.path.insert(0, '$(pwd)')
from dotenv import load_dotenv
load_dotenv('.env')
from src.db.database import get_db
conn = get_db()
try:
    row = conn.execute('SELECT COUNT(*) FROM tennis_results').fetchone()
    print(f'  Tennis results: {row[0]}')
    row2 = conn.execute(\"SELECT COUNT(*) FROM tennis_predictions WHERE result IS NOT NULL\").fetchone()
    print(f'  Settled predictions: {row2[0]} / 500')
except Exception as e:
    print(f'  DB error: {e}')
" 2>/dev/null || echo "  (DB not accessible)"
echo "══════════════════════════════════════"

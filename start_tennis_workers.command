#!/usr/bin/env bash
# Start Tennis Prediction Engine workers
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then PYTHON=".venv/bin/python"
elif [ -x "venv/bin/python" ]; then PYTHON="venv/bin/python"
else PYTHON="python3"; fi

echo "Starting Tennis workers..."
"$PYTHON" scripts/tennis_collection_worker.py &
echo "  ✓ Tennis Collection Worker (PID $!)"
"$PYTHON" scripts/tennis_live_worker.py &
echo "  ✓ Tennis Live Worker      (PID $!)"
"$PYTHON" scripts/tennis_settlement_worker.py &
echo "  ✓ Tennis Settlement Worker (PID $!)"

echo ""
echo "Tennis workers running. Use stop_tennis_workers.command to stop."
wait

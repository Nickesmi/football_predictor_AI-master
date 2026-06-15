#!/usr/bin/env bash
# Start Data Collection Workers

cd "$(dirname "$0")"

echo "Starting Football Predictor AI Data Collection Workers..."
echo "This terminal will keep the workers running. Press Ctrl+C to stop them."

# Run odds worker in background
./.venv/bin/python scripts/odds_worker.py &
PID1=$!

# Run settlement worker in background
./.venv/bin/python scripts/settlement_worker.py &
PID2=$!

# Wait for both
wait $PID1 $PID2

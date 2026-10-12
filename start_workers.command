#!/usr/bin/env bash
# Start Data Collection Workers

cd "$(dirname "$0")"

echo "Starting Football Predictor AI Data Collection Workers..."
echo "This terminal will keep the workers running. Press Ctrl+C to stop them."

PYTHON="./.venv/bin/python"
# shellcheck source=scripts/orchestration/check_runtime.sh
source scripts/orchestration/check_runtime.sh || exit 1

# Run odds worker in background
"$PYTHON" scripts/odds_worker.py &
PID1=$!

# Run settlement worker in background
"$PYTHON" scripts/settlement_worker.py &
PID2=$!

# Wait for both
wait $PID1 $PID2

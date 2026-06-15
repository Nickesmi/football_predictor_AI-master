#!/usr/bin/env bash
# Stop Tennis Prediction Engine workers
pkill -f tennis_collection_worker.py && echo "  ✓ Tennis Collection Worker stopped"
pkill -f tennis_live_worker.py       && echo "  ✓ Tennis Live Worker stopped"
pkill -f tennis_settlement_worker.py && echo "  ✓ Tennis Settlement Worker stopped"
echo "All tennis workers stopped."

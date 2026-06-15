#!/usr/bin/env bash
# Stop Data Collection Workers

echo "Stopping Football Predictor AI Workers..."

pkill -f collection_worker.py
pkill -f odds_worker.py
pkill -f settlement_worker.py

echo "All workers stopped successfully."

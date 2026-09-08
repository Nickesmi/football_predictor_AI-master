#!/usr/bin/env python3
"""Build models/champion_registry.json from the real walk-forward backtest
results. Re-run this any time scripts/walk_forward_backtest.py changes."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.prediction_service.champion_registry import build_registry, save_registry


def main():
    registry = build_registry()
    path = save_registry(registry)
    print(f"Wrote {path}\n")
    for market, entry in registry["markets"].items():
        champ = entry.get("champion")
        method = entry.get("method", "")
        print(f"  {market:16s} -> {champ or 'NONE'}   ({method})")


if __name__ == "__main__":
    main()

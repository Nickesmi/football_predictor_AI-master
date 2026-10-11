#!/usr/bin/env python3
"""Learn per-market confidence thresholds from folds 1-3 only (never the
frozen final holdout) and save to models/market_thresholds.json."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.prediction_service.thresholds import learn_thresholds, save_thresholds


def main():
    thresholds = learn_thresholds()
    path = save_thresholds(thresholds)
    print(f"Wrote {path}\n")
    for market, entry in thresholds["markets"].items():
        if entry.get("threshold") is not None:
            print(f"  {market:16s} champion={entry['champion']:10s} threshold>={entry['threshold']:.2f} "
                  f"(pooled hit-rate={entry['pooled_hit_rate']:.3f}, n={entry['pooled_n']})")
        else:
            print(f"  {market:16s} champion={str(entry.get('champion')):10s} NO THRESHOLD ({entry['reason']})")


if __name__ == "__main__":
    main()

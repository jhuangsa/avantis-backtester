"""Tune and test range_seller on ETH.

Rule: only while the market label is consolidation, mean_reversion or
choppy_range. On 1-hour bars, when the prior bar closed above the prior
`window` bars' high and this bar closes back inside the range, short; the
mirror below the low is a long. Take profit `take` of the way to the range
middle; a wide stop of atr_stops ATRs; out after max_bars bars.

Run: python3 examples/sharpe_hunt/run_range_seller.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hunt import TF, hunt  # noqa: E402

COINS = ["ETH"]
START = {"timeframe": TF.Hour1, "back_inside": True, "edge": 0.5, "window": 100, "atr_stops": 8.0,
         "leverage": 10.0, "max_bars": 96, "take": 1.0, "atr_period": 14, "flat": False}
KNOBS = {
    "window": [100, 72, 150],
    "take": [1.0, 0.8, 1.2],
    "atr_stops": [8.0, 6.0, 10.0],
    "max_bars": [96, 48, 0],
    "flat": [False, True],
    "leverage": [10.0, 8.0],
    "atr_period": [14, 30],
}

if __name__ == "__main__":
    print(json.dumps(hunt("range_seller", COINS, KNOBS, start=START, test=True), indent=1))

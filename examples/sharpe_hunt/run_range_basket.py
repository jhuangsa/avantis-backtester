"""Tune and test range_basket on BTC, ETH, SOL, DOGE and AVAX in one account.

Rule: the range_seller fade on every coin. Only while a coin's market label
is consolidation, mean_reversion or choppy_range: when the prior 1-hour bar
closed above the prior `window` bars' high (by at most `depth` ATRs) and
this bar closes back inside, short; the mirror below the low is a long.
Take profit `take` of the way to the range middle; stop atr_stops ATRs,
skipped when wider than max_stop; out after max_bars bars; at most
max_open positions at once.

Run: python3 examples/sharpe_hunt/run_range_basket.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hunt import TF, hunt  # noqa: E402

COINS = ["BTC", "ETH", "SOL", "DOGE", "AVAX"]
START = {"timeframe": TF.Hour1, "window": 100, "atr_period": 14, "max_bars": 96, "max_open": 3,
         "take": 1.0, "depth": 0.5, "atr_stops": 7.0, "max_stop": 0.07, "leverage": 4.0}
KNOBS = {
    "depth": [0.5, 0.4, 0.7],
    "max_stop": [0.07, 0.06, 0.08],
    "atr_stops": [7.0, 6.0, 8.0],
    "take": [1.0, 0.8, 1.2],
    "max_bars": [96, 72, 120],
    "max_open": [3, 2],
    "leverage": [4.0, 3.0, 5.0],
}

if __name__ == "__main__":
    print(json.dumps(hunt("range_basket", COINS, KNOBS, start=START, test=True), indent=1))

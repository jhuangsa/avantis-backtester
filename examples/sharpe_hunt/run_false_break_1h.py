"""Tune and test false_break_1h on BTC, ETH, SOL, DOGE and AVAX.

Rule: on 1-hour bars, a break closes above the prior `window` bars' high
(below their low). If within `within` bars a close is back inside, fade it,
only on a range market label (regime 2). Out at the range middle, at an ATR
take profit, at a wide ATR stop, or after `hold` bars; at most max_open
positions at once.

Run: python3 examples/sharpe_hunt/run_false_break_1h.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hunt import hunt  # noqa: E402

COINS = ["BTC", "ETH", "SOL", "DOGE", "AVAX"]
START = {"atr_stops": 8.0, "hold": 48, "leverage": 8.0, "max_open": 4, "regime": 2, "revert": True,
         "tp_atrs": 3.0, "window": 144, "within": 9}
KNOBS = {
    "hold": [48, 72, 96],
    "window": [144, 120, 192, 240],
    "within": [9, 7, 12, 16],
    "tp_atrs": [3.0, 2.0, 4.0],
    "atr_stops": [8.0, 7.0, 9.0, 12.0],
    "max_open": [4, 5, 3],
    "leverage": [8.0, 6.0, 4.0, 10.0],
    "atr_period": [14, 28, 7],
}

if __name__ == "__main__":
    print(json.dumps(hunt("false_break_1h", COINS, KNOBS, start=START, test=True), indent=1))

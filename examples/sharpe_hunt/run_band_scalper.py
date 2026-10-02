"""Tune and test band_scalper on AVAX 15-minute bars.

Rule: when the close comes back inside a band k ATRs around its moving
average (beyond it on the bar before), fade the stretch: long after a drop
below the band, short after a pop above it. Only on a Low or Normal
volatility label, and only with the trend label (long on Uptrend, short on
Downtrend). Small take profit (tp_atrs ATRs), wide stop (atr_stops ATRs, at
least min_stop), out after max_bars bars.

Run: python3 examples/sharpe_hunt/run_band_scalper.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hunt import TF, hunt  # noqa: E402

COINS = ["AVAX"]
START = {"instrument": "AVAX", "timeframe": TF.Min15, "window": 22, "k": 3.0, "tp_atrs": 0.5,
         "atr_stops": 4.0, "min_stop": 0.01, "trend": 2, "confirm": True, "max_bars": 48,
         "leverage": 20.0}
KNOBS = {
    "max_bars": [48, 40, 56],
    "tp_atrs": [0.5, 0.45, 0.55],
    "window": [22, 21, 23],
    "atr_stops": [4.0, 3.75, 4.25],
    "leverage": [20.0, 19.0],
}

if __name__ == "__main__":
    print(json.dumps(hunt("band_scalper", COINS, KNOBS, start=START, test=True), indent=1))

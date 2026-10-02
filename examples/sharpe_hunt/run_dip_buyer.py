"""Dip buyer on SOL, tuned on 2026, tested on 2025.

Rule: while the 1-hour close is above its 30-bar average, buy a 15-minute
close more than k ATRs below its 10-bar average (mirror short in a
downtrend). Small take profit, wide ATR stop; out when the close is back at
the average or after `hold` bars.

Run: AVBT_BUILD=<build dir> python3 examples/sharpe_hunt/run_dip_buyer.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data import TF  # noqa: E402
from hunt import hunt  # noqa: E402

START = {"instrument": "SOL", "timeframe": TF.Min15, "trend_timeframe": TF.Hour1, "trend_period": 30,
         "avg_period": 10, "atr_period": 28, "k": 2.1, "tp_atrs": 1.75, "atr_stops": 4.0, "hold": 20,
         "shorts": True, "revert": True, "leverage": 8.0}
KNOBS = {"k": [2.1, 2.0], "tp_atrs": [1.75, 1.5], "hold": [20, 24], "atr_stops": [4.0, 4.5],
         "leverage": [8.0, 10.0]}

if __name__ == "__main__":
    print(json.dumps(hunt("dip_buyer", ["SOL"], KNOBS, start=START), indent=1))

"""Dip basket on BTC, ETH, SOL, DOGE, AVAX in one account, tuned on 2026,
tested on 2025.

Rule: a market is up when its 1-hour close is above its 50-bar average.
Buy a market up whose 15-minute close fell more than `drop` ATRs over 4
bars, while at least `breadth` of the basket is up (mirror short when
down). Take profit and stop in ATRs, out after `hold` bars, at most
`max_open` at once, so a market-wide crash hits every open dip together.

Run: AVBT_BUILD=<build dir> python3 examples/sharpe_hunt/run_dip_basket.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data import TF  # noqa: E402
from hunt import hunt  # noqa: E402

COINS = ["BTC", "ETH", "SOL", "DOGE", "AVAX"]
START = {"timeframe": TF.Min15, "trend_timeframe": TF.Hour1, "trend_period": 50, "lookback": 4,
         "atr_period": 28, "drop": 4.0, "breadth": 0.2, "tp_atrs": 2.0, "atr_stops": 4.0, "hold": 32,
         "max_open": 3, "shorts": True, "revert": False, "leverage": 10.0}
KNOBS = {"drop": [4.0, 4.5], "tp_atrs": [2.0, 1.75], "atr_stops": [4.0, 5.0], "hold": [32, 48],
         "max_open": [3, 5]}

if __name__ == "__main__":
    print(json.dumps(hunt("dip_basket", COINS, KNOBS, start=START), indent=1))

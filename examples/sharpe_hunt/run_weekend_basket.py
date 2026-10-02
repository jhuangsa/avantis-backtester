"""Weekend basket on BTC, ETH, SOL, DOGE and AVAX, tuned on 2026, tested on 2025.

Rule: on 4-hour bars, on every market, fade the weekend move (last 12 bars)
at Monday 08:00 UTC, and fade a close that broke the prior `window`-bar
high (low) and came back inside within 4 bars. Small take profits, ATR
stops, time exits; at most max_open positions at once in one account.

Run: AVBT_BUILD=<build dir> python3 examples/sharpe_hunt/run_weekend_basket.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hunt import hunt  # noqa: E402

COINS = ["BTC", "ETH", "SOL", "DOGE", "AVAX"]
KNOBS = {
    "max_open": [5, 3, 4], "leverage": [3.0, 2.0, 4.0], "w_stops": [1.75, 1.5, 2.5],
    "b_stops": [3.0, 2.5, 4.0], "tp_atrs": [1.2, 1.0, 1.5], "tp_share": [0.3, 0.25, 0.4], "window": [24, 18, 36],
}

if __name__ == "__main__":
    print(json.dumps(hunt("weekend_basket", COINS, KNOBS, start={}, test=True), indent=1))

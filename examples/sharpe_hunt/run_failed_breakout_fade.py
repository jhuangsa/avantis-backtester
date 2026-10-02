"""Failed breakout fade on BTC, ETH, SOL, DOGE and AVAX, tuned on 2026, tested on 2025.

Rule: on 4-hour bars, when a close breaks the prior `window` bars' high
(low) and within `within` bars a close falls back inside, fade it back
into the range. Small ATR take profit, wide ATR stop, out after `hold` bars.

Run: AVBT_BUILD=<build dir> python3 examples/sharpe_hunt/run_failed_breakout_fade.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data import TF  # noqa: E402
from hunt import hunt  # noqa: E402

COINS = ["BTC", "ETH", "SOL", "DOGE", "AVAX"]
START = {"timeframe": TF.Hour4, "window": 24, "within": 3, "tp_atrs": 1.0, "atr_stops": 3.0, "hold": 12}
KNOBS = {
    "window": [24, 18, 36], "within": [3, 2, 4], "tp_atrs": [1.0, 0.8, 1.2],
    "atr_stops": [3.0, 2.5, 4.0], "hold": [12, 8, 18], "leverage": [3.0, 2.0, 4.0],
}

if __name__ == "__main__":
    print(json.dumps(hunt("failed_breakout_fade", COINS, KNOBS, start=START, test=True), indent=1))

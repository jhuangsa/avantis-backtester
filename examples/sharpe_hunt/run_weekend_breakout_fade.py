"""Weekend and breakout fade on DOGE, tuned on 2026, tested on 2025.

Rule: on 4-hour bars, two fades share one position. On Monday 08:00 UTC,
fade the weekend move (last 12 bars) with a take profit of a share of the
move. Otherwise, fade a close that broke the prior 24-bar high (low) and
came back inside within 4 bars, with a small ATR take profit. Both use
ATR stops and a time exit.

Run: AVBT_BUILD=<build dir> python3 examples/sharpe_hunt/run_weekend_breakout_fade.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hunt import hunt  # noqa: E402

START = {"instrument": "DOGE"}
KNOBS = {
    "b_leverage": [5.0, 3.0, 8.0], "w_leverage": [5.0, 8.0], "b_stops": [3.0, 2.5, 4.0],
    "tp_atrs": [1.2, 1.0, 1.5], "tp_share": [0.3, 0.25, 0.35], "window": [24, 18, 36], "b_hold": [8, 6, 12],
}

if __name__ == "__main__":
    print(json.dumps(hunt("weekend_breakout_fade", ["DOGE"], KNOBS, start=START, test=True), indent=1))

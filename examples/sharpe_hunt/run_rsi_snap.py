"""RSI snap on BTC, tuned on 2026, tested on 2025.

Rule: on 15-minute bars, buy when the close is above its 100-bar average and
the 7-bar RSI is below `low`; sell the mirror (below the average, RSI above
100 - low). Out when the close crosses back over its 10-bar average or
after `hold` bars; small ATR take profit, wide ATR stop.

Run: AVBT_BUILD=<build dir> python3 examples/sharpe_hunt/run_rsi_snap.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data import TF  # noqa: E402
from hunt import hunt  # noqa: E402

START = {"instrument": "BTC", "timeframe": TF.Min15, "rsi_period": 7, "trend_period": 100, "exit_period": 10,
         "low": 30.0, "tp_atrs": 2.0, "atr_stops": 6.0, "atr_period": 14, "hold": 12, "shorts": True,
         "leverage": 10.0}
KNOBS = {"rsi_period": [7, 5], "low": [30.0, 25.0], "atr_stops": [6.0, 8.0], "exit_period": [10, 8]}

if __name__ == "__main__":
    print(json.dumps(hunt("rsi_snap", ["BTC"], KNOBS, start=START), indent=1))

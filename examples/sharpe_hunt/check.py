"""Check every saved result against the six criteria on tuned 2026.

Run: python3 examples/sharpe_hunt/check.py
"""

import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"
RULES = {
    "sharpe > 4": lambda s: (s["sharpe"] or 0) > 4,
    "win rate >= 60%": lambda s: (s["win_rate"] or 0) >= 0.6,
    "payoff < 1": lambda s: s["payoff"] is not None and s["payoff"] < 1,
    "skew < 0": lambda s: s["skew"] is not None and s["skew"] < 0,
    "vol >= 30%": lambda s: (s["vol"] or 0) >= 0.3,
    "trades >= 30": lambda s: s["trades"] >= 30,
}


def passes(d):
    t = d["tuned"]
    return all(k in t for k in ("vol", "skew")) and all(f(t) for f in RULES.values())


if __name__ == "__main__":
    for f in sorted(RESULTS.glob("*.json")):
        d = json.loads(f.read_text())
        t, x = d["tuned"], d["test_2025"] or {}
        miss = [k for k, f_ in RULES.items() if "vol" not in t or not f_(t)]
        print(f"{'PASS' if not miss else 'miss'} {d['strategy']:26} {len(d['coins'])} coins  "
              f"2026 sharpe {t['sharpe']}  2025 sharpe {x.get('sharpe')}  {', '.join(miss)}")

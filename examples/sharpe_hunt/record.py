"""Score each saved strategy over 2025 and 2026 together, and write docs/strategies.md.

Adds a "full" score (2025-03-25 to 2026-10-01, tuned params, no retuning) to
each results/<name>.json, then writes one page that records every strategy:
what it does, its coins, and its tuned params. Also writes
docs/strategy_results.md (git-ignored): the score tables for 2026 (tuned),
2025 (test) and both together.

Run: python3 examples/sharpe_hunt/record.py
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hunt  # noqa: E402
from data import TF, avbt_cpp  # noqa: E402

FULL = (hunt.TEST[0], hunt.TUNE[1])
OUT = HERE.parents[1] / "docs" / "strategies.md"
RESULTS = HERE.parents[1] / "docs" / "strategy_results.md"
NAMES = {avbt_cpp.timeframe_name(t): t for t in [TF.Min15, TF.Hour1, TF.Hour4]}

# Each rule in plain words. The C++ file's top comment has the full detail.
RULES = {
    "rsi_snap": "On 15-minute bars, buy when the close is above its 100-bar average and the "
                "7-bar RSI is below 30; sell short in the mirror case. Out when the close "
                "crosses back over its 10-bar average, at 2 ATRs profit, at a 6-ATR stop, or after 12 bars.",
    "weekend_breakout_fade": "On 4-hour bars, two fades share one position. On Monday at 08:00 UTC, "
                             "fade the weekend's move. Otherwise fade a close that broke the prior "
                             "high or low and came back inside within 4 bars. Small take profit, ATR stop, time exit.",
    "weekend_basket": "The weekend fade and the failed-breakout fade on every coin, in one account, "
                      "on 4-hour bars, at most 5 positions at once.",
    "range_basket": "On 1-hour bars, while a coin's market label is a range label, fade a shallow "
                    "overshoot of the prior 100-bar range once the close is back inside. Take profit "
                    "at the range middle, wide stop, at most 3 positions.",
    "failed_breakout_fade": "On 4-hour bars on every coin: a close beyond the prior 24-bar high or low "
                            "that comes back inside within 4 bars is faded. Take profit 1.2 ATRs, "
                            "stop 3 ATRs, out after 8 bars.",
    "false_break_1h": "On 1-hour bars on every coin, with a range market label: fade a break of the "
                      "prior 120-bar high or low that is back inside within 9 bars. Out at the range "
                      "middle, at a take profit, at a wide stop or by time. 10x leverage.",
    "dip_buyer": "On SOL, while the 1-hour close is above its average, buy a 15-minute close more "
                 "than 2.1 ATRs below its 10-bar average; the mirror short in a downtrend. Out at "
                 "the average, at a small take profit, at a wide stop or by time.",
    "band_scalper": "On AVAX 15-minute bars, with a calm volatility label and with the trend label, "
                    "fade a close that comes back inside a band 3 ATRs from its average. Tiny take "
                    "profit, wide stop, 20x leverage.",
    "dip_basket": "On every coin, buy a fall of 4 ATRs over 4 bars while at least one in five coins "
                  "is above its 1-hour average; the mirror short. At most 3 positions at once.",
    "range_seller": "On ETH 1-hour bars, while the market label is a range label, sell a close back "
                    "inside after the prior bar closed above the 100-bar range, and buy the mirror. "
                    "Take profit at the range middle, 8-ATR stop, 10x leverage.",
}

COLS = [("Sharpe", "sharpe", "{:.2f}"), ("Return", "return", "{:+.0%}"), ("Volatility", "vol", "{:.0%}"),
        ("Max drawdown", "max_drawdown", "{:.0%}"), ("Trades", "trades", "{}"), ("Win rate", "win_rate", "{:.0%}"),
        ("Payoff", "payoff", "{:.2f}"), ("Skew", "skew", "{:.2f}"), ("Worst trade", "worst_trade", "{:.1%}")]


def table(rows, key):
    out = "| Strategy | Coins | " + " | ".join(c for c, _, _ in COLS) + " |\n" + "|---" * (len(COLS) + 2) + "|\n"
    for d in rows:
        s = d[key]
        out += f"| `{d['strategy']}` | {len(d['coins'])} | " + " | ".join(
            "—" if s.get(k) is None else f.format(s[k]) for _, k, f in COLS) + " |\n"
    return out


def results_page(rows):
    return f"""# Strategy results

Private: git-ignored. Built by `examples/sharpe_hunt/record.py`.

Each strategy was tuned with `optimize` on {hunt.TUNE[0]} to {hunt.TUNE[1]}, then
run once with those params on {hunt.TEST[0]} to {hunt.TEST[1]}, and once on both
together ({FULL[0]} to {FULL[1]}), with no retuning. The 2025 window starts after
the March 2025 data hole.

Settings: starting balance 10,000; 2% of the balance at risk per trade, times
leverage; fees 0.05% at open and at close; a 60% loss closes everything and
ends trading for good.

- **Sharpe:** mean daily return over its standard deviation, times √365.
- **Volatility:** yearly standard deviation of daily returns.
- **Max drawdown:** the largest fall from a peak in equity.
- **Win rate:** share of trades that made money.
- **Payoff:** average win divided by average loss.
- **Skew:** skew of trade results, each as a share of equity.
- **Worst trade:** the largest single loss as a share of equity.

## 2026, the tuning period

{table(rows, "tuned")}
## Both years together

{table(rows, "full")}
Five strategies lose 60% in 2025, which ends their trading, so they have no
2026 trades in this run.

## 2025, the test period

{table(rows, "test_2025")}"""


def main():
    rows = []
    for f in sorted((HERE / "results").glob("*.json")):
        d = json.loads(f.read_text())
        p = {k: NAMES.get(v, v) if isinstance(v, str) else v for k, v in d["params"].items()}
        d["full"] = hunt.score(hunt.run(d["strategy"], p, d["coins"], FULL, (TF.Min15, TF.Hour1, TF.Hour4)))
        d["full_window"] = list(FULL)
        f.write_text(json.dumps(d, indent=1))
        rows.append(d)
    rows.sort(key=lambda d: -d["full"]["sharpe"])

    md = ["""# Strategies

Ten strategies in the C++ strategy library. Each trades Avantis crypto markets
(BTC, ETH, SOL, DOGE, AVAX); five trade one coin and five trade every coin
passed in, in one account. Each was tuned with `optimize` on 2026 data.

Code: `cpp/include/avbt/library/<name>.hpp`. Test: `cpp/tests/test_<name>.cpp`.
Run: `python3 examples/sharpe_hunt/run_<name>.py`. Tuned params and scores:
`examples/sharpe_hunt/results/<name>.json`.
"""]
    for d in rows:
        params = ", ".join(f"{k} {v}" for k, v in d["params"].items())
        md.append(f"""
### `{d['strategy']}`

- **Coins:** {", ".join(d["coins"])}
- **What it does:** {RULES[d["strategy"]]}
- **Tuned params:** {params}
""")
    md.append("""
## Rebuild

```bash
python3 examples/sharpe_hunt/data.py
python3 examples/sharpe_hunt/record.py
```
""")
    OUT.write_text("".join(md))
    RESULTS.write_text(results_page(rows))
    print("wrote", OUT, "and", RESULTS)


if __name__ == "__main__":
    main()

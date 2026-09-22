"""Score one mean-reversion hypothesis on a ten-bar dip.

The close crosses below a 4-bar average, so the next bar is bought.
The close crosses back above that average, so the next bar is sold.
A target and a stop sit 8 percent off the entry. On this series neither is hit.

From the repository root:

    python3 examples/mean_reversion.py
"""

import math
import sys
from pathlib import Path

# The package lives at the repository root. Running this file does not require
# an editable install, and it works from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import Bar, Cross, Hypothesis, Indicator, Price, backtest, grid

# Ten hourly bars. Four quiet bars, a dip, then a climb back through the average.
# Columns are open, high, low, close.
ROWS = (
    (100, 101, 99, 100),
    (100, 101, 99, 100),
    (100, 101, 99, 100),
    (100, 101, 99, 100),
    (100, 100, 96, 97),
    (97, 98, 95, 96),
    (96, 99, 96, 98),
    (98, 102, 98, 101),
    (101, 104, 100, 103),
    (103, 105, 102, 104),
)

BAR_SIZE = "1h"
TAKE_PROFIT = 0.08
STOP_LOSS = 0.08


def series():
    return tuple(
        Bar(open=open_, high=high, low=low, close=close, volume=1)
        for open_, high, low, close in ROWS
    )


def mean_reversion(window):
    average = Indicator("sma", "average", window=window)
    return Hypothesis(
        name="mean reversion",
        side="long",
        long_entry=Cross(Price(), average, "below"),
        long_rule_exit=Cross(Price(), average, "above"),
        distance_kind="percent",
        take_profit_size=TAKE_PROFIT,
        stop_loss_size=STOP_LOSS,
    )


def _show_trade(trade):
    print(
        f"  {trade.side}: entry bar {trade.entry_bar} at {trade.entry_price:g},"
        f" exit bar {trade.exit_bar} at {trade.exit_price:g},"
        f" {trade.cause}"
    )


def main():
    bars = series()
    hypothesis = mean_reversion(4)
    result = backtest(hypothesis, bars, fee=0.0, bar_size=BAR_SIZE)

    print("Mean reversion: buy a close that crosses below the average.")
    print(f"bar size {result.bar_size}")
    print(f"ending stake {result.ending_stake:.6f}")
    if result.trades:
        for trade in result.trades:
            _show_trade(trade)
    else:
        print("  no trades")

    print()
    print("Same hypothesis, two windows. Both cells are reported.")
    cells = grid(
        hypothesis,
        bars,
        fee=0.0,
        bar_size=BAR_SIZE,
        vary={"window": {"average": [4, 5]}},
    )
    for cell in cells:
        window = cell.parameters["window"]["average"]
        count = len(cell.trades)
        noun = "trade" if count == 1 else "trades"
        print(
            f"  window {window:g}: ending stake {cell.ending_stake:.6f},"
            f" {count} {noun}"
        )

    _check(result, cells)
    return 0


def _check(result, cells):
    """The printed trade is the one this series is built to produce."""
    if len(result.trades) != 1:
        raise SystemExit(f"expected one trade, got {len(result.trades)}")
    trade = result.trades[0]
    expected = (5, 7, "long", 97.0, 98.0, "rule exit")
    actual = (
        trade.entry_bar,
        trade.exit_bar,
        trade.side,
        trade.entry_price,
        trade.exit_price,
        trade.cause,
    )
    if actual != expected:
        raise SystemExit(f"unexpected trade {actual}, expected {expected}")
    if not math.isclose(result.ending_stake, 98 / 97, rel_tol=0, abs_tol=1e-9):
        raise SystemExit(f"unexpected stake {result.ending_stake}")
    stakes = [cell.ending_stake for cell in cells]
    if len(cells) != 2 or not math.isclose(stakes[1], 1.0, rel_tol=0, abs_tol=1e-12):
        raise SystemExit(f"unexpected grid {stakes}")
    if not math.isclose(stakes[0], 98 / 97, rel_tol=0, abs_tol=1e-9):
        raise SystemExit(f"unexpected grid {stakes}")


if __name__ == "__main__":
    sys.exit(main())

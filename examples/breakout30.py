"""Score Breakout-30 on one daily series.

The close is beyond the prior channel, so the next day's open is the fill.
A long is the close above the prior high. A short is the close below the prior low.
The stop is two average-true-range units from that open. There is no take profit.
The trade also leaves at the open 30 daily bars after the fill.

The caller supplies the daily bars. From the repository root, with a CSV of
day, open, high, low, close, volume:

    python3 examples/breakout30.py btc-usd-benchmarks-1d.csv
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import Above, Bar, Below, Hypothesis, Indicator, Price, backtest

BAR_SIZE = "1d"
CHANNEL_DAYS = (20, 55)
STOP_ATR = 2
HOLD_DAYS = 30
# 6.5 bps on each fill is a 13 bp round trip. The hourly holding cost is not charged.
FEE = 0.00065
MIN_MINUTE_BARS = 1400


def load(path):
    days = []
    bars = []
    skipped = 0
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            minute_bars = row.get("bars")
            if minute_bars not in (None, "") and float(minute_bars) < MIN_MINUTE_BARS:
                skipped += 1
                continue
            days.append(row["day"])
            bars.append(
                Bar(
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
    return days, tuple(bars), skipped


def breakout(channel, stop=STOP_ATR, atr_window=14):
    high = Indicator("donchian", "high", window=channel, output="upper")
    low = Indicator("donchian", "low", window=channel, output="lower")
    return Hypothesis(
        name=f"breakout-30 {channel}",
        side="both",
        long_entry=Above(Price(), high),
        short_entry=Below(Price(), low),
        distance_kind="average true range",
        stop_loss_size=stop,
        time_exit=HOLD_DAYS,
        atr_window=atr_window,
    )


def _bps(trade):
    if trade.side == "long":
        return (trade.exit_price - trade.entry_price) / trade.entry_price * 1e4
    return (trade.entry_price - trade.exit_price) / trade.entry_price * 1e4


def _show(days, result):
    print(f"bar size {result.bar_size}")
    print(f"parameters {result.parameters}")
    print(f"trades {len(result.trades)}")
    print(f"ending stake {result.ending_stake:.6f}")
    if not result.trades:
        return
    causes = {}
    gross = []
    for trade in result.trades:
        causes[trade.cause] = causes.get(trade.cause, 0) + 1
        gross.append(_bps(trade))
        print(
            f"  {days[trade.entry_bar]} {trade.side}: {trade.entry_price:.4g} ->"
            f" {days[trade.exit_bar]} {trade.exit_price:.4g} ({trade.cause})"
        )
    print("causes " + ", ".join(f"{name} {count}" for name, count in sorted(causes.items())))
    print(f"mean gross bps {sum(gross) / len(gross):.1f}")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: python3 examples/breakout30.py bars.csv")
    days, bars, skipped = load(sys.argv[1])
    print(
        f"{Path(sys.argv[1]).name}: {len(bars)} daily bars, {days[0]} to {days[-1]},"
        f" {skipped} incomplete days left out"
    )
    print(
        "Fill is the next daily open. Stop is checked on the daily range."
        " Fee is 6.5 bps a fill. No hourly holding cost."
    )
    for channel in CHANNEL_DAYS:
        print(f"\nchannel {channel}")
        _show(days, backtest(breakout(channel), bars, fee=FEE, bar_size=BAR_SIZE))


if __name__ == "__main__":
    main()

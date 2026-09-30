"""Two Veranta strategies on two markets and two timeframes, in one account.

Strategy 3, the campaign short, trades AVNT on 4-hour bars. Strategy 4, the
spike short, trades DYM on 15-minute bars. Both markets carry all 12
timeframes, built from their Binance 1-minute candles, so the clock steps
every minute and every fill and stop uses 1-minute bars. DYM's data starts
about three weeks after AVNT's, so DYM joins the run later.

This tests the code; the parameters are the defaults, not tuned.

It reads the 1-minute candles that examples/veranta_rules_cpp.py cached under
data/candles/veranta_rules_cpp/. Build the module first (see that file), then
run from the repository root:

    python3 examples/mixed_timeframes.py

It prints a summary and writes examples/mixed_timeframes.html.
"""

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))

from timeframes import all_timeframes, avbt_cpp  # noqa: E402

TF = avbt_cpp.Timeframe
CACHE = ROOT / "data" / "candles" / "veranta_rules_cpp"
OUT = Path(__file__).with_suffix(".html")
# Instrument, cached Binance symbol, and the timeframe its strategy reads.
AVNT = ("AVNT", "AVNTUSDT", TF.Hour4)
DYM = ("DYM", "DYMUSDT", TF.Min15)


def minutes(symbol):
    """The cached 1-minute candles of one symbol, as ts/open/high/low/close."""
    path = next(CACHE.glob(f"{symbol}-*.json"))
    df = pd.DataFrame(json.loads(path.read_text()), columns=["ts", "open", "high", "low", "close"])
    return df.drop_duplicates("ts").sort_values("ts")


def main():
    markets = {name: all_timeframes(minutes(symbol)) for name, symbol, _ in (AVNT, DYM)}
    r = avbt_cpp.campaign_and_spike(markets, avbt_cpp.PortfolioSettings(),
                                    a_timeframe=AVNT[2], b_timeframe=DYM[2])
    clock = pd.to_datetime(r["clock"], unit="s", utc=True)
    trades = pd.DataFrame(r["trades"])
    trades["entry"] = clock[trades.entry_bar]
    trades["exit"] = clock[trades.exit_bar]

    print(f"base {r['timeframe']}, {len(clock):,} steps, {clock[0]} to {clock[-1]}")
    for name, _, tf in (AVNT, DYM):
        first = pd.to_datetime(markets[name][0].ts[0], unit="s", utc=True)
        mine = trades[trades.instrument == name]
        print(f"{name} on {avbt_cpp.timeframe_name(tf)}: data from {first}, {len(mine)} trades,"
              f" causes {mine.cause.value_counts().to_dict()}")
    print(f"ending balance {r['ending_balance']:.2f}")

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                        subplot_titles=[f"{n} close, {avbt_cpp.timeframe_name(tf)} bars"
                                        for n, _, tf in (AVNT, DYM)] + ["Account equity"])
    for row, (name, _, tf) in enumerate((AVNT, DYM), start=1):
        b = markets[name][[t.timeframe for t in markets[name]].index(tf)]
        fig.add_trace(go.Scatter(x=pd.to_datetime(b.ts, unit="s", utc=True), y=b.close,
                                 name=f"{name} close", line=dict(width=1, color="#888")), row, 1)
        mine = trades[trades.instrument == name]
        fig.add_trace(go.Scatter(x=mine.entry, y=mine.entry_price, mode="markers", name=f"{name} entry",
                                 marker=dict(symbol="triangle-down", size=9, color="#d73027")), row, 1)
        fig.add_trace(go.Scatter(x=mine.exit, y=mine.exit_price, mode="markers", name=f"{name} exit",
                                 text=mine.cause, marker=dict(symbol="x", size=8, color="#4575b4")), row, 1)
    # One point per minute is too many to draw; the hourly equity is enough.
    equity = pd.Series(r["equity"], index=clock).resample("1h").last()
    fig.add_trace(go.Scatter(x=equity.index, y=equity, name="equity", line=dict(color="#1a9850")), 3, 1)
    fig.update_layout(title="Campaign short (AVNT, 4 hours) + spike short (DYM, 15 minutes), one account",
                      height=900)
    fig.write_html(OUT, include_plotlyjs="cdn")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()

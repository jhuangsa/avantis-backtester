"""Run the five Veranta ideas through the C++ strategies and the Python engine.

Both engines see the same cleaned hourly bars, over the windows that
examples/veranta_rules.py scores. The script compares their trades by entry
bar, exit bar, and cause, and prints every trade that differs.

Bars: AVNT, DYM, and PAXG are Binance 1-minute candles, cached under
data/candles/veranta_rules_cpp/, bucketed into hours by
btc_bars.resample. ZORA uses Gate.io hourly candles instead, because Gate.io
1-minute history does not reach back that far. Those hours go on a full
hourly grid the way resample fills gaps (the close carries forward; an empty
hour's open, high, and low equal that close), and minutes_with_data is -1,
meaning "not counted".

Expected differences: the Python engine closes a trade still open on the last
bar as "still open"; the C++ loop leaves it open and records no trade. The C++
portfolio can also refuse an entry (no free cash) or stop everything at its
hard stop; the Python engine has neither.

Build the module first, then run from the repository root:

    cmake -S cpp -B cpp/build -Dpybind11_DIR=$(python3 -m pybind11 --cmakedir)
    cmake --build cpp/build
    python3 examples/veranta_rules_cpp.py
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cpp" / "build"))
sys.path.insert(0, str(ROOT / "examples"))

import avbt_cpp  # noqa: E402
import veranta_rules as vr  # noqa: E402
from btc_bars import resample  # noqa: E402

from backtest import Bar, backtest  # noqa: E402

CACHE = Path(__file__).resolve().parents[1] / "data" / "candles" / "veranta_rules_cpp"
MINUTES = "https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval=1m&startTime={start}&limit=1000"
HOUR = vr.HOUR
CPP = {
    vr.late_day_short: avbt_cpp.late_day_short,
    vr.rally_short: avbt_cpp.rally_short,
    vr.campaign_short: avbt_cpp.campaign_short,
    vr.spike_short: avbt_cpp.spike_short,
    vr.gold_trend_long: avbt_cpp.gold_trend_long,
}
PY_CAUSE = {"take profit": "take_profit", "stop loss": "stop", "time exit": "order",
            "rule exit": "order", "still open": "still open"}


def binance_minutes(symbol, start, end):
    path = CACHE / f"{symbol}-{start}-{end}.json"
    if path.exists():
        return json.loads(path.read_text())
    rows, cursor = [], start * 1000
    while cursor <= end * 1000:
        page = vr._get(MINUTES.format(symbol=symbol, start=cursor))
        if not page:
            break
        rows.extend([k[0] // 1000, *map(float, k[1:5])] for k in page)
        cursor = page[-1][0] + 60_000
    rows = [r for r in rows if start <= r[0] <= end]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))
    return rows


def hourly_grid(rows):
    """Gate hourly rows on a full hourly grid, filled the way resample fills."""
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"]).drop_duplicates("ts")
    grid = np.arange(df.ts.min(), df.ts.max() + 1, HOUR, dtype="int64")
    df = df.set_index("ts").reindex(grid)
    close = df["close"].ffill()
    for col in ("open", "high", "low"):
        df[col] = df[col].fillna(close)
    df["close"] = close
    df["minutes_with_data"] = -1
    return df.rename_axis("ts").reset_index()


def load(wallet, market, source, symbol):
    trades = json.loads((vr.TRADES / f"{wallet}.json").read_text())["trades"]
    opens = [t["openedAt"] for t in trades if t["symbol"] == market]
    start = min(opens) // HOUR * HOUR - vr.WARMUP
    end = max(opens) // HOUR * HOUR + 48 * HOUR
    if source == "gate":
        return hourly_grid(vr.candles(source, symbol, start, end))
    minutes = pd.DataFrame(binance_minutes(symbol, start, end + HOUR - 60),
                           columns=["ts", "open", "high", "low", "close"])
    return resample(minutes.drop_duplicates("ts").sort_values("ts"), "1h")


def compare(wallet, market, source, symbol, make):
    df = load(wallet, market, source, symbol)
    bars = avbt_cpp.Bars(HOUR, df.ts.to_numpy("int64"), df.open.to_numpy(float), df.high.to_numpy(float),
                         df.low.to_numpy(float), df.close.to_numpy(float),
                         df.minutes_with_data.to_numpy("int32"))
    cpp = CPP[make](bars, avbt_cpp.PortfolioSettings())
    py_bars = [Bar(open=o, high=h, low=l, close=c, volume=0.0)
               for o, h, l, c in zip(df.open, df.high, df.low, df.close)]
    hypothesis = make(df.ts.tolist(), py_bars)
    py = backtest(hypothesis, py_bars, fee=vr.FEE, bar_size="1h")

    c_set = {(t["entry_bar"], t["exit_bar"], t["cause"]) for t in cpp["trades"]}
    p_set = {(t.entry_bar, t.exit_bar, PY_CAUSE[t.cause]) for t in py.trades}
    print(f"{hypothesis.name}  ({symbol}, {len(df)} bars)")
    print(f"  C++ {len(c_set)} trades, Python {len(p_set)}, same {len(c_set & p_set)}, "
          f"C++ ending balance {cpp['ending_balance']:.2f}")
    for label, only in (("C++ only", c_set - p_set), ("Python only", p_set - c_set)):
        for entry, exit_, cause in sorted(only):
            print(f"    {label}: entry {entry} exit {exit_} {cause}")
    print()


def main():
    for rule in vr.RULES:
        compare(*rule)


if __name__ == "__main__":
    main()

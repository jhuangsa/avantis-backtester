"""Run the five Veranta ideas through the C++ strategies.

Each rule runs on cleaned hourly bars over the window around its wallet's
trades (80 warmup hours before the first open, 48 hours after the last). The
script prints each rule's trade count, causes, and ending balance.

Bars: AVNT, DYM, and PAXG are Binance 1-minute candles, cached under
data/candles/veranta_rules_cpp/, bucketed into hours by
btc_bars.resample. ZORA uses Gate.io hourly candles, cached under
data/candles/veranta_rules/, because Gate.io 1-minute history does not reach
back that far. Those hours go on a full hourly grid the way resample fills
gaps (the close carries forward; an empty hour's open, high, and low equal
that close), and minutes_with_data is -1, meaning "not counted".

Last, it runs strategies 3 (AVNT) and 4 (DYM) as one combined strategy in
one account, on the hours both markets share, and compares its trades with
each strategy run alone on the same hours.

Build the module first, then run from the repository root:

    cmake -S cpp -B cpp/build -Dpybind11_DIR=$(python3 -m pybind11 --cmakedir)
    cmake --build cpp/build
    python3 examples/veranta_rules_cpp.py
"""

import json
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cpp" / "build"))
sys.path.insert(0, str(ROOT / "examples"))

import avbt_cpp  # noqa: E402
from btc_bars import resample  # noqa: E402
from timeframes import run, trade_rows  # noqa: E402

DATA = ROOT / "data" / "candles"
CACHE = DATA / "veranta_rules_cpp"
TRADES = ROOT / "examples" / "veranta_trades"
MINUTES = "https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval=1m&startTime={start}&limit=1000"
GATE = "https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={symbol}&interval=1h&from={start}&to={end}"
HOUR = 3600
WARMUP = 80 * HOUR
# (wallet, market, source, symbol, C++ strategy)
RULES = (
    ("d49c1", "ZORA/USD", "gate", "ZORA_USDT", "late_day_short"),
    ("dd49e", "ZORA/USD", "gate", "ZORA_USDT", "rally_short"),
    ("eda73", "AVNT/USD", "binance", "AVNTUSDT", "campaign_short"),
    ("ffb7e", "DYM/USD", "binance", "DYMUSDT", "spike_short"),
    ("dce95", "XAU/USD", "binance", "PAXGUSDT", "gold_trend_long"),
)


def _get(url):
    request = urllib.request.Request(url, headers={"user-agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def gate_hours(symbol, start, end):
    path = DATA / "veranta_rules" / f"{symbol}-{start}-{end}.json"
    if path.exists():
        return json.loads(path.read_text())
    # Gate rows are time, quote volume, close, high, low, open, base volume, closed.
    rows, cursor = [], start
    while cursor <= end:
        stop = min(cursor + 900 * HOUR, end)
        for k in _get(GATE.format(symbol=symbol, start=cursor, end=stop)):
            rows.append([int(k[0]), float(k[5]), float(k[3]), float(k[4]), float(k[2])])
        cursor = stop + HOUR
        time.sleep(0.1)
    rows = [r for r in sorted({r[0]: r for r in rows}.values()) if start <= r[0] <= end]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))
    return rows


def binance_minutes(symbol, start, end):
    path = CACHE / f"{symbol}-{start}-{end}.json"
    if path.exists():
        return json.loads(path.read_text())
    rows, cursor = [], start * 1000
    while cursor <= end * 1000:
        page = _get(MINUTES.format(symbol=symbol, start=cursor))
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
    trades = json.loads((TRADES / f"{wallet}.json").read_text())["trades"]
    opens = [t["openedAt"] for t in trades if t["symbol"] == market]
    start = min(opens) // HOUR * HOUR - WARMUP
    end = max(opens) // HOUR * HOUR + 48 * HOUR
    if source == "gate":
        return hourly_grid(gate_hours(symbol, start, end))
    minutes = pd.DataFrame(binance_minutes(symbol, start, end + HOUR - 60),
                           columns=["ts", "open", "high", "low", "close"])
    return resample(minutes.drop_duplicates("ts").sort_values("ts"), "1h")


def cpp_bars(df):
    """One market for C++: a list holding just the hourly bars, so the base is Hour1."""
    return [avbt_cpp.Bars(avbt_cpp.Timeframe.Hour1, df.ts.to_numpy("int64"), df.open.to_numpy(float), df.high.to_numpy(float),
                         df.low.to_numpy(float), df.close.to_numpy(float),
                         df.minutes_with_data.to_numpy("int32"))]


def name_of(market):
    return market.split("/")[0].replace("XAU", "GOLD")


def report(wallet, market, source, symbol, strategy):
    df = load(wallet, market, source, symbol)
    name = name_of(market)
    result = run(strategy, {name: cpp_bars(df)}, {"instrument": name})
    trades = trade_rows(result)
    causes = dict(Counter(t["cause"] for t in trades))
    total = sum(t["result"] for t in trades)
    print(f"{strategy}  ({symbol}, {len(df)} bars): {len(trades)} trades {causes}, "
          f"sum of results {total:.4f}, ending balance {result.ending_balance:.2f}")


def combined():
    """Strategies 3 and 4 as one, on the hours AVNT and DYM both have."""
    rules = {rule[1]: rule for rule in RULES}
    avnt, dym = load(*rules["AVNT/USD"][:4]), load(*rules["DYM/USD"][:4])
    start, end = max(avnt.ts.min(), dym.ts.min()), min(avnt.ts.max(), dym.ts.max())
    avnt = avnt[avnt.ts.between(start, end)].reset_index(drop=True)
    dym = dym[dym.ts.between(start, end)].reset_index(drop=True)
    both = run("campaign_and_spike", {"AVNT": cpp_bars(avnt), "DYM": cpp_bars(dym)})
    alone = {"AVNT": run("campaign_short", {"AVNT": cpp_bars(avnt)}),
             "DYM": run("spike_short", {"DYM": cpp_bars(dym)})}
    print(f"Combined: 3-AVNT and 4-DYM in one account  ({len(avnt)} shared bars, "
          f"{pd.to_datetime(start, unit='s')} to {pd.to_datetime(end, unit='s')})")
    for name, result in alone.items():
        c_set = {(t["entry_bar"], t["exit_bar"], t["cause"]) for t in trade_rows(both)
                 if t["instrument"] == name}
        a_set = {(t["entry_bar"], t["exit_bar"], t["cause"]) for t in trade_rows(result)}
        print(f"  {name}: combined {len(c_set)} trades, alone {len(a_set)}, same {len(c_set & a_set)}, "
              f"alone ending balance {result.ending_balance:.2f}")
        for label, only in (("combined only", c_set - a_set), ("alone only", a_set - c_set)):
            for entry, exit_, cause in sorted(only):
                print(f"    {label}: entry {entry} exit {exit_} {cause}")
    print(f"  combined ending balance {both.ending_balance:.2f}")


def main():
    for rule in RULES:
        report(*rule)
    print()
    combined()


if __name__ == "__main__":
    main()

"""Load 2025 and 2026 crypto bars and states from ClickHouse, cached small.

Minute candles are pulled a month at a time, deduped (pyth_lazer wins) and
kept as 15-minute, 1-hour and 4-hour bars. States are kept as minute codes.
Both go to data/candles/hunt_<coin>.npz, a few MB each.

Run: python3 examples/sharpe_hunt/data.py
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
# AVBT_BUILD points at another build dir's avbt_cpp; it wins over cpp/build.
if os.environ.get("AVBT_BUILD"):
    sys.path.insert(0, os.environ["AVBT_BUILD"])
    import avbt_cpp  # noqa: E402,F401
import clickhouse_data as ch  # noqa: E402
from btc_bars import resample  # noqa: E402
from candles import clean_candles  # noqa: E402
from timeframes import avbt_cpp, to_bars  # noqa: E402

TF = avbt_cpp.Timeframe
COINS = {"BTC": 1, "ETH": 0, "SOL": 2, "DOGE": 5, "AVAX": 6}
START, END = "2025-01-01", "2026-10-01"
# The .env keys that hold the table names; .env is not committed.
CANDLES, STATES = "CLICKHOUSE_CANDLES_TABLE", "CLICKHOUSE_STATES_TABLE"
RULES = {TF.Min15: "15min", TF.Hour1: "1h", TF.Hour4: "4h"}
BAR_COLS = ["ts", "open", "high", "low", "close", "minutes_with_data"]


def months():
    edges = pd.date_range(START, END, freq="MS")
    return [(a.strftime("%Y-%m-%d"), b.strftime("%Y-%m-%d")) for a, b in zip(edges, edges[1:])]


def _minutes(pid, a, b):
    sql = f"""
    SELECT toUnixTimestamp(timestamp) AS ts,
           argMin(open, r) AS open, argMin(high, r) AS high,
           argMin(low, r) AS low, argMin(close, r) AS close
    FROM (SELECT *, if(source = 'pyth_lazer', 0, 1) AS r
          FROM {ch._env(CANDLES)}
          WHERE pair_id = {pid} AND timestamp >= toDateTime('{a}', 'UTC')
            AND timestamp < toDateTime('{b}', 'UTC'))
    GROUP BY timestamp ORDER BY timestamp
    SETTINGS max_execution_time = 120 FORMAT CSVWithNames"""
    return pd.read_csv(io.BytesIO(ch._query(ch.ORIGINAL, sql)))


def _states(pid, a, b):
    sql = f"""
    SELECT toUnixTimestamp(timestamp) AS ts, market_state AS market,
           trend_state AS trend, volatility_state AS volatility
    FROM {ch._env(STATES)} FINAL
    WHERE pair_id = {pid} AND timeframe = '1m'
      AND timestamp >= toDateTime('{a}', 'UTC') AND timestamp < toDateTime('{b}', 'UTC')
    ORDER BY timestamp SETTINGS max_execution_time = 120 FORMAT CSVWithNames"""
    return pd.read_csv(io.BytesIO(ch._query(ch.ENRICHED, sql)), keep_default_na=False, na_values=[])


def path(coin):
    return ch.CACHE / f"hunt_{coin.lower()}.npz"


def fetch(coin):
    if path(coin).exists():
        return
    pid = COINS[coin]
    mins = pd.concat([_minutes(pid, a, b) for a, b in months()], ignore_index=True)
    sts = pd.concat([_states(pid, a, b) for a, b in months()], ignore_index=True)
    mins = clean_candles(mins.drop_duplicates("ts"))[0]
    out = {}
    for tf, rule in RULES.items():
        bars = resample(mins, rule)
        for c in BAR_COLS:
            out[f"{rule}_{c}"] = bars[c].to_numpy()
    first, market, trend, vol = ch.clean_states(sts.drop_duplicates("ts"))
    out.update(states_start=np.int64(first), market=market, trend=trend, volatility=vol)
    np.savez_compressed(path(coin), **out)
    print(coin, len(mins), "minutes,", len(market), "state minutes")


def market(coin, start, end, tfs=(TF.Min15, TF.Hour1, TF.Hour4), with_states=True):
    """One avbt_cpp.Market on [start, end), its first timeframe as base."""
    z = np.load(path(coin))
    lo, hi = (int(pd.Timestamp(d, tz="UTC").timestamp()) for d in (start, end))
    bars = []
    for tf in tfs:
        rule = RULES[tf]
        df = pd.DataFrame({c: z[f"{rule}_{c}"] for c in BAR_COLS})
        bars.append(to_bars(tf, df[(df.ts >= lo) & (df.ts < hi)].reset_index(drop=True)))
    if not with_states:
        return avbt_cpp.Market(coin, bars)
    s0 = int(z["states_start"])
    i, j = max(0, (lo - s0) // 60), max(0, (hi - s0) // 60)
    st = avbt_cpp.States(s0 + 60 * i, z["market"][i:j], z["trend"][i:j], z["volatility"][i:j])
    return avbt_cpp.Market(coin, bars, st)


if __name__ == "__main__":
    for coin in COINS:
        fetch(coin)

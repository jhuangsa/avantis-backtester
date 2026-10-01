"""Load minute candles and minute market states from ClickHouse.

Candles come from market_data.avantis_candles_1m on the original service.
States come from market_data.market_context_1m on the enriched service.
Each query is cached in data/candles/ and reused when the file exists.
clean_states turns the state text into small integer codes for the C++ engine.

Run: python3 examples/clickhouse_data.py
"""

from __future__ import annotations

import base64
import io
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "candles"
# The .env keys that hold each ClickHouse service ID.
ORIGINAL = "CLICKHOUSE_ORIGINAL_HOST"
ENRICHED = "CLICKHOUSE_ENRICHED_HOST"
NAMES = {0: "eth", 1: "btc"}

# Code = index + 1. Code 0 means unknown. The C++ enums use the same order.
MARKET_STATES = [
    "mean_reversion", "volatility_compression", "mixed", "consolidation",
    "breakout", "trending_up", "trending_down", "failed_breakout",
    "shock_stress", "choppy_range", "stable_low_energy",
    "volatility_expansion", "transition",
]
TREND_STATES = ["uptrend", "downtrend", "non_trending", "mixed_conflicted"]
VOLATILITY_STATES = [
    "low", "normal", "high", "extreme", "compression", "expansion", "shock",
]
FILL_LIMIT = 60  # minutes a state is carried forward over a gap


def _env(key: str) -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    raise RuntimeError(f"{key} missing from .env")


def _query(host: str, sql: str) -> bytes:
    host = _env(host)
    user, password = _env("CLICKHOUSE_USER"), _env("CLIKCHOUSE_PASSWORD")
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(
        f"https://{host}.us-east-1.aws.clickhouse.cloud:8443/",
        data=sql.encode(), headers={"Authorization": f"Basic {token}"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def _cached(path: Path, host: str, sql: str) -> pd.DataFrame:
    if not path.exists():
        body = _query(host, sql)
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        part.write_bytes(body)
        part.replace(path)
    return pd.read_csv(path, keep_default_na=False, na_values=[])


def cache_path(pair_id: int, kind: str, start: str, end: str) -> Path:
    """Where a query for this pair, kind ('1m' or 'states_1m') and range is kept."""
    return CACHE / f"{NAMES.get(pair_id, f'pair{pair_id}')}_{kind}_{start}_{end}.csv"


def minutes(pair_id: int, start: str, end: str) -> pd.DataFrame:
    """Minute candles in [start, end), UTC days. pyth_lazer wins over benchmarks."""
    sql = f"""
    SELECT toUnixTimestamp(timestamp) AS ts,
           argMin(open, r) AS open, argMin(high, r) AS high,
           argMin(low, r) AS low, argMin(close, r) AS close
    FROM (SELECT *, if(source = 'pyth_lazer', 0, 1) AS r
          FROM market_data.avantis_candles_1m
          WHERE pair_id = {int(pair_id)}
            AND timestamp >= toDateTime('{start}', 'UTC')
            AND timestamp < toDateTime('{end}', 'UTC'))
    GROUP BY timestamp ORDER BY timestamp
    SETTINGS max_execution_time = 60
    FORMAT CSVWithNames"""
    return _cached(cache_path(pair_id, "1m", start, end), ORIGINAL, sql)


def states(pair_id: int, start: str, end: str) -> pd.DataFrame:
    """Minute states in [start, end), as the raw text the table holds."""
    sql = f"""
    SELECT toUnixTimestamp(timestamp) AS ts, market_state AS market,
           trend_state AS trend, volatility_state AS volatility
    FROM market_data.market_context_1m FINAL
    WHERE pair_id = {int(pair_id)} AND timeframe = '1m'
      AND timestamp >= toDateTime('{start}', 'UTC')
      AND timestamp < toDateTime('{end}', 'UTC')
    ORDER BY timestamp
    SETTINGS max_execution_time = 60
    FORMAT CSVWithNames"""
    return _cached(cache_path(pair_id, "states_1m", start, end), ENRICHED, sql)


def clean_states(df: pd.DataFrame):
    """Return (start ts, market, trend, volatility) codes, one per minute.

    Starts at the first row with all three states. Fills every minute up to
    the last row, carrying a state forward at most FILL_LIMIT minutes.
    """
    cols = {"market": MARKET_STATES, "trend": TREND_STATES,
            "volatility": VOLATILITY_STATES}
    df = df.set_index("ts")[list(cols)].replace({"unknown": np.nan, "": np.nan})
    complete = df.notna().all(axis=1)
    if not complete.any():
        raise ValueError("no row has all three states")
    df = df.loc[complete.idxmax():]
    first, last = int(df.index[0]), int(df.index[-1])
    df = df.reindex(range(first, last + 1, 60)).ffill(limit=FILL_LIMIT)
    out = []
    for col, names in cols.items():
        bad = set(df[col].dropna()) - set(names)
        if bad:
            raise ValueError(f"unlisted {col} state: {sorted(bad)}")
        codes = df[col].map({n: i + 1 for i, n in enumerate(names)})
        out.append(codes.fillna(0).to_numpy(np.uint8))
    return (first, *out)


if __name__ == "__main__":
    start, end = "2026-06-01", "2026-09-01"
    for pair_id, name in [(1, "BTC/USD"), (0, "ETH/USD")]:
        bars = minutes(pair_id, start, end)
        raw = states(pair_id, start, end)
        first, *codes = clean_states(raw)
        print(f"{name}: {len(bars)} candles  {cache_path(pair_id, '1m', start, end)}")
        print(f"{name}: {len(raw)} states   {cache_path(pair_id, 'states_1m', start, end)}")
        print(f"{name}: {len(codes[0])} minutes from ts {first}; unknown share "
              + ", ".join(f"{c} {np.mean(a == 0):.2%}"
                          for c, a in zip(["market", "trend", "volatility"], codes)))

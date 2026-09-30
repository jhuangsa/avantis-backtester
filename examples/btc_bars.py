"""Load BTC 1-minute candles and build hourly and daily bars.

The minutes come from ClickHouse, table market_data.avantis_candles_1m, pair 1.
Two sources overlap. On a shared timestamp pyth_lazer wins over benchmarks.
Empty buckets are filled with the previous close and minutes = 0.
The raw CSV is cached at ~/.cache/avantis-backtester/btc_1m.csv.

Run: python3 examples/btc_bars.py
"""

from __future__ import annotations

import base64
import io
import os
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_URL = "https://klvu1o0hu6.us-east-1.aws.clickhouse.cloud:8443"
ROOT = Path(__file__).resolve().parent.parent
CACHE = Path.home() / ".cache" / "avantis-backtester" / "btc_1m.csv"

SQL = """
SELECT toUnixTimestamp(timestamp) AS ts, source, open, high, low, close
FROM market_data.avantis_candles_1m
WHERE pair_id = 1
ORDER BY timestamp
SETTINGS max_execution_time = 120
FORMAT CSVWithNames
"""


def _env(key: str) -> str | None:
    if key in os.environ:
        return os.environ[key]
    path = ROOT / ".env"
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip().removeprefix("export ").strip() == key:
            return v.strip().strip('"').strip("'")
    return None


def fetch_minutes(refresh: bool = False) -> pd.DataFrame:
    """Return the raw minute rows, from cache unless refresh is set."""
    if refresh or not CACHE.exists():
        user = _env("CLICKHOUSE_USER")
        password = _env("CLIKCHOUSE_PASSWORD")
        if not user or password is None:
            raise RuntimeError("ClickHouse credentials missing from environment and .env")
        url = os.environ.get("CH_URL", DEFAULT_URL)
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        req = urllib.request.Request(
            url, data=SQL.encode(), method="POST",
            headers={"Authorization": f"Basic {token}"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read()
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        part = CACHE.with_suffix(".csv.part")
        part.write_bytes(body)
        part.replace(CACHE)
    return pd.read_csv(CACHE, dtype={"ts": "int64", "source": "string"})


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """One row per ts. pyth_lazer beats benchmarks; within a source, first wins."""
    rank = np.where(df["source"] == "pyth_lazer", 0, 1)
    out = (
        df.assign(_rank=rank, _pos=np.arange(len(df)))
        .sort_values(["ts", "_rank", "_pos"], kind="stable")
        .drop_duplicates("ts", keep="first")
        .drop(columns=["_rank", "_pos"])
        .reset_index(drop=True)
    )
    return out


def resample(minutes_df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Bucket minutes into UTC bars, left-labelled, and fill empty buckets forward."""
    idx = pd.to_datetime(minutes_df["ts"], unit="s", utc=True)
    s = minutes_df.set_index(idx)
    r = s.resample(rule, label="left", closed="left")
    bars = pd.DataFrame({
        "open": r["open"].first(),
        "high": r["high"].max(),
        "low": r["low"].min(),
        "close": r["close"].last(),
        "minutes": r["close"].count().astype("int32"),
    })
    real = bars["minutes"] > 0
    bars = bars.loc[real.idxmax(): real[::-1].idxmax()]
    close = bars["close"].ffill()
    for col in ("open", "high", "low"):
        bars[col] = bars[col].where(bars["minutes"] > 0, close)
    bars["close"] = close
    bars["minutes"] = bars["minutes"].astype("int32")
    ts = (bars.index.as_unit("s").asi8 if hasattr(bars.index, "as_unit")
          else bars.index.asi8 // 10**9)
    out = bars.reset_index(drop=True)
    out.insert(0, "ts", np.asarray(ts, dtype="int64"))
    return out[["ts", "open", "high", "low", "close", "minutes"]]


def load_bars(rule: str, refresh: bool = False) -> tuple[pd.DataFrame, int]:
    """Fetch, dedupe, and resample. Returns the bars and the bar size in seconds."""
    bars = resample(dedupe(fetch_minutes(refresh)), rule)
    bar_seconds = int(pd.Timedelta(pd.tseries.frequencies.to_offset(rule)).total_seconds())
    return bars, bar_seconds


if __name__ == "__main__":
    raw = fetch_minutes()
    clean = dedupe(raw)
    print(f"raw rows: {len(raw)}")
    print(f"deduped rows: {len(clean)}")
    for rule in ("1h", "1D"):
        bars = resample(clean, rule)
        start = pd.to_datetime(bars["ts"].iloc[0], unit="s", utc=True)
        end = pd.to_datetime(bars["ts"].iloc[-1], unit="s", utc=True)
        filled = int((bars["minutes"] == 0).sum())
        print(f"{rule}: {len(bars)} bars, {filled} filled, {start} to {end}")

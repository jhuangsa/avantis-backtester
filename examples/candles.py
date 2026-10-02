"""Clean minute candles before any bars or market is built.

The engine does not check for NaN prices, so every loader calls clean_candles.
"""

from __future__ import annotations

import pandas as pd

OHLC = ["open", "high", "low", "close"]


def clean_candles(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Drop leading broken rows and make each later broken candle flat.

    A candle is broken when any of open, high, low or close is NaN. It
    becomes flat at the last known close, and its "filled" column is True.
    The report gives the number filled and the longest run in a row.
    A minute with no row stays missing.
    """
    broken = df[OHLC].isna().any(axis=1)
    out = df.loc[(~broken).cummax()].reset_index(drop=True)
    broken = out[OHLC].isna().any(axis=1)
    close = out["close"].where(~broken).ffill()
    for col in OHLC:
        out[col] = out[col].where(~broken, close)
    out["filled"] = broken
    runs = broken.groupby((~broken).cumsum()).sum()
    report = {"filled": int(broken.sum()), "longest_run": int(runs.max()) if len(out) else 0}
    if report["filled"] > 0.01 * len(out):
        print(f"warning: {report['filled']} of {len(out)} candles filled, "
              f"longest run {report['longest_run']}")
    return out, report

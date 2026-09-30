"""Build the 12 C++ timeframes from one set of 1-minute candles.

Every bar is bucketed in UTC and labelled with its open time. Minute and hour
bars count from 00:00 UTC, weeks start Monday 00:00 UTC, and months start the
1st at 00:00 UTC. Empty bars are filled flat at the previous close with
minutes_with_data = 0, by btc_bars.resample.

Run (self-check on a synthetic series): python3 examples/timeframes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpp" / "build"))
sys.path.insert(0, str(ROOT / "examples"))

import avbt_cpp  # noqa: E402
from btc_bars import resample  # noqa: E402

TF = avbt_cpp.Timeframe
# Each C++ timeframe and its pandas rule, finest first. "W-MON" with left-closed
# buckets starts each week on Monday; "MS" starts each month on the 1st.
RULES = [
    (TF.Min1, "1min"), (TF.Min3, "3min"), (TF.Min5, "5min"), (TF.Min15, "15min"),
    (TF.Min30, "30min"), (TF.Hour1, "1h"), (TF.Hour4, "4h"), (TF.Hour8, "8h"),
    (TF.Hour12, "12h"), (TF.Day1, "1D"), (TF.Week1, "W-MON"), (TF.Month1, "MS"),
]


def to_bars(timeframe, df: pd.DataFrame) -> avbt_cpp.Bars:
    """One resampled DataFrame as C++ Bars."""
    return avbt_cpp.Bars(timeframe, df.ts.to_numpy("int64"), df.open.to_numpy(float),
                         df.high.to_numpy(float), df.low.to_numpy(float),
                         df.close.to_numpy(float), df.minutes_with_data.to_numpy("int32"))


def all_timeframes(minutes: pd.DataFrame) -> list[avbt_cpp.Bars]:
    """The 12 timeframes of one market, finest first, from ts/open/high/low/close minutes."""
    return [to_bars(tf, resample(minutes, rule)) for tf, rule in RULES]


if __name__ == "__main__":
    # Three weeks of made-up minutes from Wednesday 2024-01-03, with a gap.
    ts = np.arange(1704240000, 1704240000 + 21 * 86400, 60, dtype="int64")
    ts = ts[(ts < 1704300000) | (ts > 1704310000)]
    close = 100 + np.sin(np.arange(len(ts)) / 500)
    minutes = pd.DataFrame({"ts": ts, "open": close, "high": close + 1,
                            "low": close - 1, "close": close})
    bars = all_timeframes(minutes)
    assert len(bars) == 12

    # One hour equals the aggregate of its minutes.
    hour = resample(minutes, "1h").iloc[5]
    inside = minutes[(minutes.ts >= hour.ts) & (minutes.ts < hour.ts + 3600)]
    assert hour.open == inside.open.iloc[0] and hour.close == inside.close.iloc[-1]
    assert hour.high == inside.high.max() and hour.low == inside.low.min()
    assert hour.minutes_with_data == len(inside)

    # Weeks open on Monday 00:00 UTC, months on the 1st, 4h bars at multiples of 4 hours.
    week = pd.to_datetime(resample(minutes, "W-MON").ts, unit="s", utc=True)
    assert (week.dt.dayofweek == 0).all() and (week.dt.hour == 0).all()
    assert (pd.to_datetime(resample(minutes, "MS").ts, unit="s", utc=True).dt.day == 1).all()
    assert (resample(minutes, "4h").ts % (4 * 3600) == 0).all()

    # The gap is filled flat with no data.
    filled = resample(minutes, "1min").query("minutes_with_data == 0")
    assert len(filled) > 0 and (filled.open == filled.close).all()
    print("ok:", ", ".join(avbt_cpp.timeframe_name(tf) for tf, _ in RULES))

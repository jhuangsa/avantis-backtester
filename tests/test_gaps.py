"""Bars that skip time (issue 21, part 2): resample(keep_empty=False) and a
gapped Markets through avbt_cpp. Made-up bars, no data/."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))
# The build for this Python: build-py313 for 3.13, else build.
sys.path.insert(1, str(ROOT / "cpp" / f"build-py{sys.version_info.major}{sys.version_info.minor}"))
sys.path.insert(2, str(ROOT / "cpp" / "build"))

from btc_bars import resample  # noqa: E402

START = 1_699_999_200  # a whole hour, UTC
H = 3600


def minutes_without_hour_1():
    """Minutes of hours 0 and 2 from START; hour 1 has none.
    Hour 0 closes at 100 + 59 = 159; hour 2 runs 200 to 259."""
    ts = np.concatenate([START + 60 * np.arange(60), START + 2 * H + 60 * np.arange(60)])
    close = np.concatenate([100.0 + np.arange(60), 200.0 + np.arange(60)])
    return pd.DataFrame({"ts": ts.astype("int64"), "open": close, "high": close + 1,
                         "low": close - 1, "close": close})


def test_keep_empty_false_drops_the_empty_hour():
    bars = resample(minutes_without_hour_1(), "1h", keep_empty=False)
    assert bars["ts"].tolist() == [START, START + 2 * H]
    assert bars["minutes_with_data"].tolist() == [60, 60]
    # Hour 2's own prices; nothing carried over from hour 0.
    assert bars["open"].tolist() == [100.0, 200.0]
    assert bars["close"].tolist() == [159.0, 259.0]
    assert bars["high"].tolist() == [160.0, 260.0]
    assert bars["low"].tolist() == [99.0, 199.0]


@pytest.mark.parametrize("kwargs", [{}, {"keep_empty": True}])
def test_keep_empty_true_fills_flat(kwargs):
    bars = resample(minutes_without_hour_1(), "1h", **kwargs)
    assert bars["ts"].tolist() == [START, START + H, START + 2 * H]
    assert bars["minutes_with_data"].tolist() == [60, 0, 60]
    # The empty hour is flat at hour 0's close.
    row = bars.iloc[1]
    assert (row["open"], row["high"], row["low"], row["close"]) == (159.0, 159.0, 159.0, 159.0)


def test_keep_empty_false_on_four_hours():
    # Minutes at hours 0-1 and 9: the 4:00 bucket is empty and dropped.
    ts = np.concatenate([START + 60 * np.arange(120), START + 9 * H + 60 * np.arange(60)])
    close = np.arange(len(ts), dtype=float) + 1
    df = pd.DataFrame({"ts": ts.astype("int64"), "open": close, "high": close,
                       "low": close, "close": close})
    start4 = START - START % (4 * H)
    kept = resample(df, "4h", keep_empty=False)
    full = resample(df, "4h")
    assert kept["ts"].tolist() == [start4, start4 + 8 * H]
    assert full["ts"].tolist() == [start4, start4 + 4 * H, start4 + 8 * H]


@pytest.fixture
def avbt():
    return pytest.importorskip("avbt_cpp")


def hourly(avbt, hours, prices):
    ts = START + H * np.asarray(hours, dtype=np.int64)
    p = np.asarray(prices, dtype=float)
    return avbt.Bars(avbt.Timeframe.Hour1, ts, p, p, p, p, np.full(len(ts), 60, dtype=np.int32))


def test_all_timeframes_passes_keep_empty(avbt):
    from timeframes import all_timeframes
    kept = all_timeframes(minutes_without_hour_1(), keep_empty=False)
    full = all_timeframes(minutes_without_hour_1())
    hour = [b for b in kept if b.timeframe == avbt.Timeframe.Hour1][0]
    assert hour.ts.tolist() == [START, START + 2 * H]
    hour = [b for b in full if b.timeframe == avbt.Timeframe.Hour1][0]
    assert hour.ts.tolist() == [START, START + H, START + 2 * H]


def test_gapped_markets_build(avbt):
    markets = avbt.Markets([avbt.Market("X", [hourly(avbt, [0, 1, 5, 6], [1, 1, 1, 1])], None)])
    assert markets.clock.tolist() == [START, START + H, START + 5 * H, START + 6 * H]


def test_order_waits_over_the_gap(avbt):
    # X trades hours 0-9. Y has hour 0 at 200, then nothing until hours 6
    # and 7, at 210 and 220. buy_hold on Y decides at 1:00 (Y's first bar
    # closed); Y has no bar at 1:00, so the order waits and fills at Y's
    # 6:00 open, 210. Y's data ends at 7:00, before the run ends: closed at
    # its last close, 220.
    x = hourly(avbt, range(10), [100] * 10)
    y = hourly(avbt, [0, 6, 7], [200, 210, 220])
    markets = avbt.Markets([avbt.Market("X", [x], None), avbt.Market("Y", [y], None)])
    costs = {n: avbt.Costs(0.0, 0.0, spread=0) for n in ("X", "Y")}
    r = avbt.run("buy_hold", {"instrument": "Y", "timeframe": avbt.Timeframe.Hour1}, markets, costs,
                 avbt.PortfolioSettings())
    assert len(r.trades) == 1
    t = r.trades[0]
    assert t.instrument == "Y"
    assert t.entry_time == START + 6 * H
    assert t.entry_price == 210
    assert t.exit_time == START + 7 * H
    assert t.exit_price == 220
    assert t.cause == avbt.Cause.EndOfData

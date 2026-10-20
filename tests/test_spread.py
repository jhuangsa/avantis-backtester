"""The spread on avbt_cpp.Costs (issue 21, part 1), written from the spec alone.

Open fill E = P*(1 + dir*s); close fill X = P*(1 - dir*s); dir +1 long, -1 short.
Round trips run buy_hold on BTC minute bars beside a later one-bar market, so
BTC's data ends first and its trade closes at its last close (end of data).
"""

import math
import sys
from pathlib import Path

import numpy as np
import pytest

_cpp = Path(__file__).resolve().parents[1] / "cpp"
sys.path.insert(0, str(_cpp / f"build-py{sys.version_info.major}{sys.version_info.minor}"))
sys.path.insert(1, str(_cpp / "build"))
avbt = pytest.importorskip("avbt_cpp")

START = 1_699_999_200  # a whole hour, UTC
OPEN = [100.0, 100.0, 101.0, 102.0, 103.0]
CLOSE = [100.0, 101.0, 102.0, 103.0, 104.0]
# buy_hold's stop distance for stop_loss 0.5 at 1x, a hair inside.
STOP = 0.5 * (1 - 1e-9)


def minute_bars(ts, open_, close):
    open_, close = np.array(open_), np.array(close)
    n = len(ts)
    return avbt.Bars(avbt.Timeframe.Min1, np.array(ts, dtype=np.int64), open_,
                     np.maximum(open_, close), np.minimum(open_, close), close,
                     np.ones(n, dtype=np.int32))


def markets(name="BTC"):
    ts = [START + 60 * i for i in range(len(OPEN))]
    btc = avbt.Market(name, [minute_bars(ts, OPEN, CLOSE)])
    pad = avbt.Market("PAD", [minute_bars([START + 60 * len(OPEN)], [1.0], [1.0])])
    return avbt.Markets([btc, pad])


def run(costs, side="long", name="BTC"):
    params = {"instrument": name, "side": side, "stop_loss": 0.5}
    return avbt.run("buy_hold", params, markets(name), {name: costs, "PAD": avbt.Costs(spread=0.0)})


def test_costs_kwargs_and_properties():
    c = avbt.Costs()
    assert c.spread is None
    assert math.isnan(c.max_spread)
    k = avbt.Costs(spread=0.001)
    assert isinstance(k.spread, np.ndarray)
    assert list(k.spread) == [0.001]
    a = avbt.Costs(spread=np.array([0.001, 0.002, 0.003]))
    assert list(a.spread) == [0.001, 0.002, 0.003]
    e = avbt.Costs(spread=np.array([]))
    assert e.spread is not None and len(e.spread) == 0
    assert avbt.Costs(spread=0.0).spread is not None
    assert avbt.Costs(max_spread=0.002).max_spread == 0.002
    assert avbt.Costs(spread=0.01, max_spread=0.002).max_spread == 0.002
    assert avbt.Costs(spread=None).spread is None


def test_old_positional_calls():
    c = avbt.Costs(0.0005, 0.0005)
    assert (c.open_fee, c.close_fee) == (0.0005, 0.0005)
    assert c.spread is None and math.isnan(c.max_spread)
    h = avbt.Costs(0.0, 0.0, np.zeros(3), np.zeros(3))
    assert len(h.hold_long) == 3 and h.profit_share == [] and h.spread is None
    p = avbt.Costs(0.0, 0.0, None, None, [(2.5, 0.25)])
    assert p.profit_share == [(2.5, 0.25)] and p.spread is None


def test_veranta_spreads():
    rows = avbt.veranta_spreads()
    assert isinstance(rows, dict)
    assert "BTC/USD" in rows and "ETH/USD" in rows
    for market, (spread, max_spread, read_on) in rows.items():
        assert 0 <= spread < 1, market
        assert math.isnan(max_spread) or max_spread >= spread, market
        assert isinstance(read_on, str) and read_on, market


def test_long_round_trip():
    # s = 0.001. Fill at open[1] = 100: E = 100.1. Stop = 100*(1 - STOP).
    # size = 100 / (E - stop*(1 - s)). End of data at close[4] = 104: X = 103.896.
    # spread_cost = size*100*s + size*104*s.
    s = 0.001
    r = run(avbt.Costs(spread=s))
    assert len(r.trades) == 1
    t = r.trades[0]
    stop = 100 * (1 - STOP)
    size = 100 / (100 * (1 + s) - stop * (1 - s))
    assert t.cause == avbt.Cause.EndOfData
    assert t.entry_price == pytest.approx(100.1, rel=1e-12)
    assert t.exit_price == pytest.approx(103.896, rel=1e-12)
    assert t.size == pytest.approx(size, rel=1e-9)
    assert t.spread_cost == pytest.approx(size * (0.1 + 0.104), rel=1e-9)
    assert t.result == pytest.approx((103.896 - 100.1) * size, rel=1e-9)
    assert t.result + t.fees + t.holding_costs == pytest.approx((t.exit_price - t.entry_price) * t.size)


def test_short_round_trip():
    # s = 0.001. E = 100*(1 - s) = 99.9. Stop = 100*(1 + STOP).
    # size = 100 / (stop*(1 + s) - E). X = 104*(1 + s) = 104.104.
    s = 0.001
    r = run(avbt.Costs(spread=s), side="short")
    assert len(r.trades) == 1
    t = r.trades[0]
    stop = 100 * (1 + STOP)
    size = 100 / (stop * (1 + s) - 100 * (1 - s))
    assert t.side == avbt.Side.Short
    assert t.entry_price == pytest.approx(99.9, rel=1e-12)
    assert t.exit_price == pytest.approx(104.104, rel=1e-12)
    assert t.size == pytest.approx(size, rel=1e-9)
    assert t.spread_cost == pytest.approx(size * (0.1 + 0.104), rel=1e-9)
    assert t.result == pytest.approx(-(104.104 - 99.9) * size, rel=1e-9)


def test_column_and_no_spread():
    # Column per base bar: entry bar 1 at 0.002, exit bar 4 at 0.004.
    col = np.array([0.001, 0.002, 0.003, 0.003, 0.004])
    t = run(avbt.Costs(spread=col)).trades[0]
    assert t.entry_price == pytest.approx(100.2, rel=1e-12)
    assert t.exit_price == pytest.approx(104 * 0.996, rel=1e-12)
    # Empty column and zero: no spread, the oracle prices.
    for c in (avbt.Costs(spread=np.array([])), avbt.Costs(spread=0.0)):
        t = run(c).trades[0]
        assert (t.entry_price, t.exit_price, t.spread_cost) == (100.0, 104.0, 0.0)
    # Cap: 0.01 capped at 0.002.
    t = run(avbt.Costs(spread=0.01, max_spread=0.002)).trades[0]
    assert t.entry_price == pytest.approx(100.2, rel=1e-12)
    assert t.exit_price == pytest.approx(104 * 0.998, rel=1e-12)


def test_table_default():
    spread, cap, _ = avbt.veranta_spreads()["BTC/USD"]
    s = spread if math.isnan(cap) else min(spread, cap)
    t = run(avbt.Costs()).trades[0]
    assert t.entry_price == pytest.approx(100 * (1 + s), rel=1e-12)
    assert t.exit_price == pytest.approx(104 * (1 - s), rel=1e-12)


@pytest.mark.parametrize("kw, name", [
    ({}, "ZZZ"),
    ({"max_spread": 0.01}, "ZZZ"),
    ({"spread": np.array([0.001, 0.001])}, "BTC"),
    ({"spread": np.full(6, 0.001)}, "BTC"),
    ({"spread": math.nan}, "BTC"),
    ({"spread": np.array([0.001, math.nan, 0.001, 0.001, 0.001])}, "BTC"),
    ({"spread": -0.001}, "BTC"),
    ({"spread": math.inf}, "BTC"),
    ({"spread": 1.0}, "BTC"),
    ({"spread": 0.001, "max_spread": -0.1}, "BTC"),
    ({"spread": 0.001, "max_spread": math.inf}, "BTC"),
])
def test_bad_spread_raises(kw, name):
    with pytest.raises(ValueError):
        run(avbt.Costs(**kw), name=name)


def test_explicit_spread_on_unknown_market_is_valid():
    t = run(avbt.Costs(spread=np.array([])), name="ZZZ").trades[0]
    assert (t.entry_price, t.exit_price) == (100.0, 104.0)

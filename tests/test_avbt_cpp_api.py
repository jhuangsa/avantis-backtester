"""The avbt_cpp Python API: markets, costs, run, and the strategy table."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cpp" / "build"))
avbt = pytest.importorskip("avbt_cpp")

START = 1_699_999_200  # a whole hour, UTC


def bars(tf, n, seed):
    step = avbt.timeframe_seconds(tf)
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) * 1.003
    low = np.minimum(open_, close) * 0.997
    ts = START + step * np.arange(n, dtype=np.int64)
    return avbt.Bars(tf, ts, open_, high, low, close, np.full(n, step // 60, dtype=np.int32),
                     volume=np.ones(n))


def market(name, seed, minutes=6000):
    m1 = bars(avbt.Timeframe.Min1, minutes, seed)
    h1 = bars(avbt.Timeframe.Hour1, minutes // 60, seed + 1)
    codes = np.zeros(minutes, dtype=np.uint8)
    return avbt.Market(name, [m1, h1], avbt.States(START, codes, codes, codes))


@pytest.fixture(scope="module")
def setup():
    names = ["AVNT", "DYM", "BTC", "ETH", "GOLD", "ZORA"]
    markets = avbt.Markets([market(n, i) for i, n in enumerate(names)])
    costs = {n: avbt.Costs(0.0005, 0.0005, np.zeros(6000), np.zeros(6000)) for n in names}
    return markets, costs


def test_markets_and_costs(setup):
    markets, costs = setup
    assert markets.instruments == ["AVNT", "DYM", "BTC", "ETH", "GOLD", "ZORA"]
    assert markets.timeframe == avbt.Timeframe.Min1
    assert markets.clock.dtype == np.int64 and len(markets.clock) == 6000
    c = costs["BTC"]
    assert (c.open_fee, c.close_fee, len(c.hold_long), len(c.hold_short)) == (0.0005, 0.0005, 6000, 6000)
    assert len(avbt.Costs(0.0, 0.0).hold_long) == 0
    m = market("X", 9)
    assert m.instrument == "X" and len(m.timeframes) == 2 and m.states is not None
    assert isinstance(avbt.version, str)


def test_strategy_table():
    table = avbt.strategies()
    assert len(table) == 8
    for s in table:
        assert s.name and s.timeframes and s.params
        for p in s.params:
            assert p.name and p.min <= p.max
            _ = p.default


@pytest.mark.parametrize("name", [s.name for s in avbt.strategies()])
def test_run_every_strategy(setup, name):
    markets, costs = setup
    r = avbt.run(name, {}, markets, costs, avbt.PortfolioSettings())
    assert r.timeframe == avbt.Timeframe.Min1
    assert r.version == avbt.version
    assert len(r.equity) == len(r.clock) == 6000
    assert r.clock.dtype == np.int64
    assert isinstance(r.ending_balance, float)
    for t in r.trades:
        assert t.instrument in markets.instruments
        assert t.entry_bar <= t.exit_bar and t.entry_time == r.clock[t.entry_bar]
        assert isinstance(t.side, avbt.Side) and isinstance(t.cause, avbt.Cause)
        assert t.entry_price > 0 and t.exit_price > 0 and t.size > 0 and t.leverage > 0
        assert t.fees >= 0 and isinstance(t.holding_costs, float) and isinstance(t.result, float)


def test_param_types(setup):
    markets, costs = setup
    params = {"average": 20, "reward": 2.5, "flip": True,
              "signal": {"BTC": avbt.Timeframe.Hour1, "ETH": avbt.Timeframe.Min1}}
    avbt.run("state_trend", params, markets, costs)
    avbt.run("rally_short", {"instrument": "DYM", "timeframe": avbt.Timeframe.Hour1}, markets, costs)



def test_numpy_param_types(setup):
    markets, costs = setup
    params = {"average": np.int64(20), "reward": np.float32(2.5), "flip": np.bool_(True),
              "atr_period": np.arange(14, 15)[0]}
    assert np.array_equal(avbt.run("state_trend", params, markets, costs).equity,
        avbt.run("state_trend", {"average": 20, "reward": 2.5, "flip": True, "atr_period": 14},
                 markets, costs).equity)
    # An integer too big for an int converts; the range check then refuses it.
    with pytest.raises(ValueError, match="outside"):
        avbt.run("state_trend", {"min_stop": 2**40}, markets, costs)

def test_errors(setup):
    markets, costs = setup
    with pytest.raises(ValueError):
        avbt.run("no_such", {}, markets, costs)
    with pytest.raises(ValueError):
        avbt.run("state_trend", {"no_such": 1}, markets, costs)
    with pytest.raises(ValueError):
        avbt.run("state_trend", {}, markets, {k: v for k, v in costs.items() if k != "BTC"})
    one = np.ones(1)
    off = avbt.Bars(avbt.Timeframe.Hour1, np.array([START + 1800]), one, one, one, one, np.full(1, 60, dtype=np.int32))
    with pytest.raises(ValueError, match="off the timeframe grid"):
        avbt.Markets([avbt.Market("BTC", [off])])
    assert {c.name for c in avbt.Cause.__members__.values()} >= {"PartialTakeProfit", "TrailingStop"}
    assert avbt.Side.Long != avbt.Side.Short


def test_sharpe():
    # 32 days of hourly equity: 16 days rising 10% a day, then 16 flat days.
    # The 31 daily returns are 15 of 0.1 and 16 of 0: sqrt(15 * 30 / (31 * 16) * 365).
    days = 100 * 1.1 ** np.minimum(np.arange(32), 15)
    clock = 3600 * np.arange(32 * 24, dtype=np.int64)
    r = avbt.Result(equity=np.repeat(days, 24), timeframe=avbt.Timeframe.Hour1, clock=clock)
    assert avbt.sharpe(r) == pytest.approx(18.197505146266263)

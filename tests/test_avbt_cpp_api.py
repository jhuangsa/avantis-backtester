"""The avbt_cpp Python API: markets, costs, run, and the strategy table."""

import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

# The build for this Python: build-py313 for 3.13, else build.
_cpp = Path(__file__).resolve().parents[1] / "cpp"
sys.path.insert(0, str(_cpp / f"build-py{sys.version_info.major}{sys.version_info.minor}"))
sys.path.insert(1, str(_cpp / "build"))
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
    assert len(table) >= 8
    for s in table:
        assert s.name and s.timeframes and s.params
        for p in s.params:
            assert p.name and p.min <= p.max
            _ = p.default


@pytest.mark.parametrize("name", [s.name for s in avbt.strategies()[:8]])
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
    two, m60 = np.ones(2), np.full(2, 60, dtype=np.int32)
    back = avbt.Bars(avbt.Timeframe.Hour1, np.array([START + 3600, START]), two, two, two, two, m60)
    with pytest.raises(ValueError, match="timestamps must rise"):
        avbt.Markets([avbt.Market("BTC", [back])])
    nan = np.array([1.0, np.nan])
    hole = avbt.Bars(avbt.Timeframe.Hour1, START + 3600 * np.arange(2), two, two, two, nan, m60)
    with pytest.raises(ValueError, match="NaN"):
        avbt.Markets([avbt.Market("BTC", [hole])])
    assert {c.name for c in avbt.Cause.__members__.values()} >= {"PartialTakeProfit", "TrailingStop"}
    assert avbt.Side.Long != avbt.Side.Short


def test_sharpe():
    # 32 days of hourly equity: 16 days rising 10% a day, then 16 flat days.
    # The 31 daily returns are 15 of 0.1 and 16 of 0: sqrt(15 * 30 / (31 * 16) * 365).
    days = 100 * 1.1 ** np.minimum(np.arange(32), 15)
    clock = 3600 * np.arange(32 * 24, dtype=np.int64)
    r = avbt.Result(equity=np.repeat(days, 24), timeframe=avbt.Timeframe.Hour1, clock=clock)
    assert avbt.sharpe(r) == pytest.approx(18.197505146266263)


KNOBS = {"average": [50, 20], "reward": [2.0, 3.0]}


def test_optimize_single(setup):
    markets, costs = setup
    s = avbt.optimize("state_trend", {"flip": True}, KNOBS, markets, costs, avbt.PortfolioSettings(), 2)
    assert set(s) == {"best", "sharpe", "runs", "timeframe"}
    assert s["timeframe"] == avbt.Timeframe.Min1
    assert s["best"]["flip"] is True and s["best"]["average"] in (50, 20)
    first = s["runs"][0]
    assert first["round"] == 0 and first["params"] == {"flip": True, "average": 50, "reward": 2.0}
    avbt.run("state_trend", s["best"], markets, costs)


def test_optimize_combined(setup):
    markets, costs = setup
    knobs = {"a.stop": [0.1, 0.05], "b.stop": [0.1, 0.05]}
    s = avbt.optimize("campaign_and_spike", {}, knobs, markets, costs, avbt.PortfolioSettings(), 1)
    assert len(s["runs"]) == 4 and set(s["best"]) == set(knobs)


def test_optimize_bad_knob(setup):
    markets, costs = setup
    with pytest.raises(ValueError, match="knob reward"):
        avbt.optimize("state_trend", {}, {"average": [50], "reward": [2.0, -1.0]}, markets, costs,
                      avbt.PortfolioSettings(), 1)


def test_optimize_progress(setup):
    markets, costs = setup
    calls = []

    def progress(round, rounds, sharpe, best):
        calls.append((round, rounds, best))
        return round < 2

    knobs = {**KNOBS, "breakout": [30, 15]}
    s = avbt.optimize("state_trend", {}, knobs, markets, costs, avbt.PortfolioSettings(), 5, progress)
    assert [c[:2] for c in calls] == [(1, 5), (2, 5)]
    assert calls[-1][2] == s["best"] and s["runs"][-1]["round"] <= 2


def test_optimize_progress_none_goes_on(setup):
    markets, costs = setup
    calls = []
    knobs = {**KNOBS, "breakout": [30, 15]}
    avbt.optimize("state_trend", {}, knobs, markets, costs, avbt.PortfolioSettings(), 3,
                  lambda r, *_: calls.append(r))
    assert calls[:2] == [1, 2]


def test_optimize_bad_rounds(setup):
    markets, costs = setup
    with pytest.raises(ValueError, match="rounds"):
        avbt.optimize("state_trend", {}, KNOBS, markets, costs, avbt.PortfolioSettings(), 0)


def test_optimize_bad_state_delay(setup):
    markets, costs = setup
    bad = avbt.PortfolioSettings(state_delay=30)
    with pytest.raises(ValueError, match="^state_delay"):
        avbt.optimize("state_trend", {}, KNOBS, markets, costs, bad, 1)


def test_optimize_progress_raises(setup):
    markets, costs = setup

    def progress(*_):
        raise KeyError("stop here")

    with pytest.raises(KeyError, match="stop here"):
        avbt.optimize("state_trend", {}, KNOBS, markets, costs, avbt.PortfolioSettings(), 3, progress)


def test_optimize_releases_gil(setup):
    markets, costs = setup
    ticks = []
    done = threading.Event()

    def count():
        while not done.is_set():
            ticks.append(1)

    t = threading.Thread(target=count)
    t.start()
    seen = []
    knobs = {"average": [50, 20, 100, 10], "reward": [2.0, 3.0, 1.0, 4.0]}
    avbt.optimize("state_trend", {}, knobs, markets, costs, avbt.PortfolioSettings(), 1,
                  lambda *_: seen.append(len(ticks)) or True)
    done.set()
    t.join()
    # The thread counted while the 16 backtests ran, before the one progress call.
    assert seen[0] > 1000


def test_summary():
    # Equity 100, 120, 90, 110: return 0.1; the drop from 120 to 90 is 0.25.
    clock = 60 * np.arange(4, dtype=np.int64)
    equity = np.array([100.0, 120.0, 90.0, 110.0])
    r = avbt.Result(equity=equity, timeframe=avbt.Timeframe.Min1, clock=clock)
    s = avbt.summary(r)
    assert set(s) == {"sharpe", "total_return", "max_drawdown", "trades"}
    assert s["total_return"] == pytest.approx(0.1)
    assert s["max_drawdown"] == pytest.approx(0.25)
    assert s["trades"] == 0 and np.isnan(s["sharpe"])


def test_param_type():
    table = {s.name: {p.name: p.type for p in s.params} for s in avbt.strategies()}
    trend, rally = table["state_trend"], table["rally_short"]
    assert (trend["flip"], trend["average"], trend["reward"]) == ("bool", "int", "float")
    assert trend["signal"] == "timeframes"
    assert (rally["instrument"], rally["timeframe"]) == ("str", "timeframe")


def test_old_optimize_gone_and_version():
    assert not hasattr(avbt, "optimize_state_trend")
    assert avbt.version == "0.4.1"


def test_walk_forward(setup):
    markets, costs = setup
    calls = []
    folds = [(markets, costs, markets, costs)] * 2
    rows = avbt.walk_forward("state_trend", {}, KNOBS, folds, avbt.PortfolioSettings(), 2,
                             lambda f, r, n, s, best: calls.append((f, r)))
    assert len(rows) == 2 and {f for f, _ in calls} == {0, 1}
    row = rows[0]
    assert set(row) == {"best", "train", "test", "overfit"}
    assert set(row["train"]) == {"sharpe", "total_return", "max_drawdown", "trades"}
    # Train and test are the same window here, so nothing is overfit.
    tr, te = row["train"]["sharpe"], row["test"]["sharpe"]
    assert row["overfit"] == 0 or (np.isnan(tr) and np.isnan(row["overfit"]))
    with pytest.raises(ValueError, match="fold"):
        avbt.walk_forward("state_trend", {}, KNOBS, [], avbt.PortfolioSettings(), 2)


def test_result_length_mismatch_raises():
    with pytest.raises(ValueError):
        avbt.Result(np.ones(40), avbt.Timeframe.Min1, np.array([], dtype=np.int64))


@pytest.mark.parametrize("bad", [{"starting_balance": 0.0}, {"risk_per_trade": -0.1}, {"hard_stop": 1.5}])
def test_bad_settings_raise(setup, bad):
    markets, costs = setup
    with pytest.raises(ValueError, match=next(iter(bad))):
        avbt.run("state_trend", {}, markets, costs, avbt.PortfolioSettings(**bad))


def test_costs_default_to_no_fees(setup):
    markets, _ = setup
    c = avbt.Costs()
    assert (c.open_fee, c.close_fee) == (0.0, 0.0)
    free = {n: c for n in markets.instruments}
    r = avbt.run("spike_short", {"spike": 0.0}, markets, free)
    assert r.trades and all(t.fees == 0 for t in r.trades)


def test_bars_volume():
    ts, one, m60 = START + 3600 * np.arange(3), np.ones(3), np.full(3, 60, dtype=np.int32)
    v = np.array([1.0, 2.0, 3.0])
    assert np.array_equal(avbt.Bars(avbt.Timeframe.Hour1, ts, one, one, one, one, m60, volume=v).volume, v)
    assert avbt.Bars(avbt.Timeframe.Hour1, ts, one, one, one, one, m60).volume is None
    with pytest.raises(ValueError):
        avbt.Bars(avbt.Timeframe.Hour1, ts, one, one, one, one, m60, volume=np.ones(2))


def test_side_param(setup):
    markets, costs = setup
    table = {s.name: {p.name: p for p in s.params} for s in avbt.strategies()}
    assert table["spike_short"]["side"].type == "str"
    assert table["spike_short"]["side"].default == "short"
    assert table["gold_trend_long"]["side"].default == "long"
    for side, want in (("long", avbt.Side.Long), ("short", avbt.Side.Short)):
        r = avbt.run("spike_short", {"spike": 0.0, "side": side}, markets, costs)
        assert r.trades and {t.side for t in r.trades} == {want}
    with pytest.raises(ValueError, match="side"):
        avbt.run("spike_short", {"side": "up"}, markets, costs)


def test_walk_forward_none_fold_raises(setup):
    markets, costs = setup
    for fold in [(None, costs, markets, costs), (markets, costs, None, costs)]:
        with pytest.raises(ValueError, match="fold"):
            avbt.walk_forward("state_trend", {}, KNOBS, [fold], avbt.PortfolioSettings(), 1)


SPIKE = {"spike": [0.0, 0.002, 0.004], "stop": [0.08, 0.02, 0.04], "take_profit": [0.06, 0.01]}


def test_same_inputs_same_outputs(setup):
    markets, costs = setup
    params = {"spike": 0.0}
    a, b = (avbt.run("spike_short", params, markets, costs) for _ in range(2))
    assert a.trades and np.array_equal(a.equity, b.equity)
    fields = ("instrument", "entry_bar", "exit_bar", "side", "entry_price", "exit_price", "size", "result")
    assert [[getattr(t, f) for f in fields] for t in a.trades] == \
        [[getattr(t, f) for f in fields] for t in b.trades]
    s = avbt.PortfolioSettings()
    np.testing.assert_equal(avbt.optimize("spike_short", {}, SPIKE, markets, costs, s, 2),
                            avbt.optimize("spike_short", {}, SPIKE, markets, costs, s, 2))
    folds = [(markets, costs, markets, costs)] * 2
    np.testing.assert_equal(avbt.walk_forward("spike_short", {}, SPIKE, folds, s, 2),
                            avbt.walk_forward("spike_short", {}, SPIKE, folds, s, 2))


def ticks_during(call):
    # Ticks a second thread makes in the middle half of call(); none if call keeps the GIL.
    ticks = []
    done = threading.Event()

    def count():
        while not done.is_set():
            ticks.append(time.perf_counter())

    t = threading.Thread(target=count)
    t.start()
    start = time.perf_counter()
    call()
    end = time.perf_counter()
    done.set()
    t.join()
    quarter = (end - start) / 4
    return sum(start + quarter < x < end - quarter for x in ticks)


def test_run_releases_gil():
    big = avbt.Markets([market("BTC", 1, 600_000)])
    assert ticks_during(lambda: avbt.run("state_trend", {}, big, {"BTC": avbt.Costs()})) > 1000


def test_walk_forward_releases_gil(setup):
    markets, costs = setup
    ticks = []
    done = threading.Event()

    def count():
        while not done.is_set():
            ticks.append(1)

    t = threading.Thread(target=count)
    t.start()
    seen = []
    knobs = {"average": [50, 20, 100, 10], "reward": [2.0, 3.0, 1.0, 4.0]}
    avbt.walk_forward("state_trend", {}, knobs, [(markets, costs, markets, costs)],
                      avbt.PortfolioSettings(), 1, lambda *_: seen.append(len(ticks)) or True)
    done.set()
    t.join()
    # The thread counted while the 16 backtests ran, before the one progress call.
    assert seen[0] > 1000


def test_walk_forward_progress_false_stops_one_fold(setup):
    markets, costs = setup
    calls = []

    def progress(fold, round, rounds, sharpe, best):
        calls.append((fold, round))
        return fold != 0

    folds = [(markets, costs, markets, costs)] * 2
    rows = avbt.walk_forward("spike_short", {}, SPIKE, folds, avbt.PortfolioSettings(), 3, progress)
    assert len(rows) == 2
    assert calls == [(0, 1), (1, 1), (1, 2), (1, 3)]


def test_walk_forward_progress_raises(setup):
    markets, costs = setup

    def progress(*_):
        raise KeyError("stop here")

    with pytest.raises(KeyError, match="stop here"):
        avbt.walk_forward("state_trend", {}, KNOBS, [(markets, costs, markets, costs)],
                          avbt.PortfolioSettings(), 3, progress)


def two_knobs(strategy):
    # The first two int or float params with a finite default, each with a second value in range.
    knobs = {}
    for p in strategy.params:
        if p.type not in ("int", "float") or np.isnan(p.default):
            continue
        if p.type == "int":
            other = p.default + 1 if p.default + 1 <= p.max else p.default - 1
        else:
            other = p.default / 2
        knobs[p.name] = [p.default, other]
        if len(knobs) == 2:
            return knobs
    raise AssertionError(f"{strategy.name} has fewer than two numeric params")


@pytest.mark.parametrize("strategy", avbt.strategies()[:8], ids=lambda s: s.name)
def test_optimize_every_strategy(setup, strategy):
    markets, costs = setup
    knobs = two_knobs(strategy)
    s = avbt.optimize(strategy.name, {}, knobs, markets, costs, avbt.PortfolioSettings(), 1)
    assert set(knobs) <= set(s["best"]) and len(s["runs"]) == 4
    for k, values in knobs.items():
        assert s["best"][k] in values

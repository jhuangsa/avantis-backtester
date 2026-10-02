"""avbt_cpp.Live: a strategy prepared on history, then fed one bar at a time."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cpp" / "build"))
avbt = pytest.importorskip("avbt_cpp")

START = 1_699_999_200  # a whole hour, UTC
N = 3000


def columns(seed):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.003, N)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return START + 60 * np.arange(N, dtype=np.int64), open_, open_.clip(min=close) * 1.003, \
        open_.clip(max=close) * 0.997, close


def markets(n):
    ts, o, h, l, c = (x[:n] for x in columns(1))
    bars = avbt.Bars(avbt.Timeframe.Min1, ts, o, h, l, c, np.ones(n, dtype=np.int32))
    up = np.full(n, 6, dtype=np.uint8), np.full(n, 1, dtype=np.uint8), np.full(n, 2, dtype=np.uint8)
    return avbt.Markets([avbt.Market("BTC", [bars], avbt.States(START, *up))])


def key(order):
    return (order.kind, order.instrument, order.side, order.stop_distance, order.leverage)


PARAMS = {"signal": {"BTC": avbt.Timeframe.Min1}, "average": 20, "breakout": 10}


def test_live_on_appended_bars_matches_live_on_full_history():
    ts, o, h, l, c = columns(1)
    full = markets(N)
    grown = markets(N - 50)
    live = avbt.Live("state_trend", PARAMS, grown)
    seen = 0
    for i in range(N - 50, N):
        grown.append_states("BTC", int(ts[i]), avbt.State(6, 1, 2))
        grown.append("BTC", avbt.Timeframe.Min1, avbt.Bar(int(ts[i]), o[i], h[i], l[i], c[i], 1))
        orders = live.decide(int(ts[i]) + 60, [])
        assert isinstance(orders, list) and all(isinstance(x, avbt.Order) for x in orders)
        fresh = avbt.Live("state_trend", PARAMS, full).decide(int(ts[i]) + 60, [])
        assert [key(x) for x in orders] == [key(x) for x in fresh]
        seen += len(orders)
    assert seen > 0


def test_append_refuses_out_of_order_ts():
    m = markets(100)
    ts, o, h, l, c = columns(1)
    with pytest.raises(ValueError):
        m.append("BTC", avbt.Timeframe.Min1, avbt.Bar(int(ts[50]), o[50], h[50], l[50], c[50], 1))


def test_position_and_order_kinds():
    p = avbt.Position("BTC", avbt.Side.Short)
    assert p.instrument == "BTC" and p.side == avbt.Side.Short
    assert p.entry_time is None  # unknown, not 0
    assert avbt.Position("BTC", avbt.Side.Short, entry_time=0).entry_time == 0
    assert avbt.Order().kind == avbt.Order.Kind.Open


def test_restart_with_entry_time_makes_the_same_time_exit():
    params = {"instrument": "BTC", "timeframe": avbt.Timeframe.Min1, "lag": 5, "window": 3,
              "rise": 0.001, "time_exit": 4}
    ts = columns(1)[0]
    full = markets(N)
    live = avbt.Live("rally_short", params, full)
    positions, entry, exit_ = [], None, None
    for i in range(N):
        now = int(ts[i]) + 60
        orders = live.decide(now, positions)
        if orders and orders[0].kind == avbt.Order.Kind.Open:
            entry = now  # fills at the next bar's open
            positions = [avbt.Position("BTC", avbt.Side.Short, entry_time=entry)]
        elif orders:
            exit_ = now
            break
    assert entry is not None and exit_ is not None and exit_ > entry + 60

    restarted = avbt.Live("rally_short", params, full)
    held = [avbt.Position("BTC", avbt.Side.Short, entry_time=entry)]
    closes = [now for now in range(entry + 60, exit_ + 60, 60)
              if restarted.decide(now, held)]
    assert closes == [exit_]


def test_state_delay_defaults_to_60_and_must_be_whole_minutes():
    assert avbt.PortfolioSettings().state_delay == 60
    for bad in (30, 90):
        settings = avbt.PortfolioSettings(state_delay=bad)
        with pytest.raises(ValueError):
            avbt.Live("state_trend", PARAMS, markets(100), settings)
        with pytest.raises(ValueError):
            avbt.run("state_trend", PARAMS, markets(100), {"BTC": avbt.Costs(0.0, 0.0)}, settings)

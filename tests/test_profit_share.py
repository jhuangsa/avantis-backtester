"""The tiered profit share on avbt_cpp.Costs (issue #19)."""

import math
import sys
from pathlib import Path

import numpy as np
import pytest

_cpp = Path(__file__).resolve().parents[1] / "cpp"
sys.path.insert(0, str(_cpp / f"build-py{sys.version_info.major}{sys.version_info.minor}"))
sys.path.insert(1, str(_cpp / "build"))
avbt = pytest.importorskip("avbt_cpp")

UPSIDE = [(2.5, 0.25), (5.0, 0.20), (15.0, 0.10), (25.0, 0.05)]
START = 1_699_999_200


def rate(roi, tiers=UPSIDE):
    for max_roi, share in tiers:
        if roi <= max_roi + 1e-9:
            return share
    return tiers[-1][1]


def test_round_trip():
    c = avbt.Costs(profit_share=UPSIDE)
    assert c.profit_share == UPSIDE
    assert all(isinstance(t, tuple) for t in c.profit_share)
    assert (c.open_fee, c.close_fee) == (0.0, 0.0)
    assert avbt.Costs().profit_share == []
    assert avbt.Costs(profit_share=None).profit_share == []


def test_old_positional_calls():
    c = avbt.Costs(0.0005, 0.0005)
    assert (c.open_fee, c.close_fee, c.profit_share) == (0.0005, 0.0005, [])
    h = avbt.Costs(0.0, 0.0, np.zeros(3), np.zeros(3))
    assert len(h.hold_long) == 3 and h.profit_share == []


@pytest.mark.parametrize("kw", [
    {"open_fee": 0.001, "profit_share": UPSIDE},
    {"close_fee": 0.001, "profit_share": UPSIDE},
    {"profit_share": [(0.0, 0.25)]},
    {"profit_share": [(-1.0, 0.25)]},
    {"profit_share": [(2.5, 0.25), (2.5, 0.2)]},
    {"profit_share": [(5.0, 0.25), (2.5, 0.2)]},
    {"profit_share": [(2.5, -0.1)]},
    {"profit_share": [(2.5, 1.1)]},
    {"profit_share": [(math.nan, 0.25)]},
    {"profit_share": [(math.inf, 0.25)]},
    {"profit_share": [(2.5, math.nan)]},
])
def test_bad_tiers_raise(kw):
    with pytest.raises(ValueError):
        avbt.Costs(**kw)


def bars(tf, n, seed):
    step = avbt.timeframe_seconds(tf)
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) * 1.003
    low = np.minimum(open_, close) * 0.997
    ts = START + step * np.arange(n, dtype=np.int64)
    m = np.full(n, step // 60, dtype=np.int32)
    return avbt.Bars(tf, ts, open_, high, low, close, m, volume=np.ones(n))


def test_run_charges_share_per_trade():
    n = 6000
    m1 = bars(avbt.Timeframe.Min1, n, 3)
    h1 = bars(avbt.Timeframe.Hour1, n // 60, 4)
    codes = np.zeros(n, dtype=np.uint8)
    market = avbt.Market("DYM", [m1, h1], avbt.States(START, codes, codes, codes))
    hold = np.full(n, 1e-6)
    costs = {"DYM": avbt.Costs(hold_long=hold, hold_short=hold, profit_share=UPSIDE)}
    r = avbt.run("spike_short", {"spike": 0.0}, avbt.Markets([market]), costs)
    wins = losses = 0
    for t in r.trades:
        gross = t.result + t.fees + t.holding_costs
        sign = 1 if t.side == avbt.Side.Long else -1
        assert gross == pytest.approx(sign * (t.exit_price - t.entry_price) * t.size)
        if gross <= 0:
            losses += 1
            assert t.fees == 0
        else:
            wins += 1
            roi = gross / (t.size * t.entry_price / t.leverage)
            assert t.fees == pytest.approx(rate(roi) * gross)
    assert wins and losses

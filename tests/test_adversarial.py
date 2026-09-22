import math
from collections.abc import Mapping

import pytest

from backtest import (
    Above,
    All,
    Any,
    Bar,
    Below,
    Cross,
    Hypothesis,
    Indicator,
    Price,
    Threshold,
    backtest,
    grid,
)

# Any is imported with the public surface. These cases never build an Any rule.
_ = Any


def _bars(*rows):
    return [Bar(open_, high, low, close, 1) for open_, high, low, close in rows]


def _close(actual, expected):
    assert math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)


def _expect(result, stake, trades):
    assert result.bar_size == "1h"
    assert len(result.trades) == len(trades)
    for trade, spec in zip(result.trades, trades, strict=True):
        entry_bar, exit_bar, side, entry_price, exit_price, cause = spec
        assert trade.entry_bar == entry_bar
        assert trade.exit_bar == exit_bar
        assert trade.side == side
        _close(trade.entry_price, entry_price)
        _close(trade.exit_price, exit_price)
        assert trade.cause == cause
    _close(result.ending_stake, stake)


def _long(name, entry, take_profit, stop, **extra):
    return Hypothesis(
        name=name,
        side="long",
        long_entry=entry,
        distance_kind=extra.pop("distance_kind", "percent"),
        take_profit_size=take_profit,
        stop_loss_size=stop,
        **extra,
    )


def _cross_above(value, name="level"):
    return Cross(Price(), Threshold(value, name), "above")


def _cross_below(value, name="level"):
    return Cross(Price(), Threshold(value, name), "below")


def _no_ranking(value):
    banned = ("winner", "best", "rank")
    for name in banned:
        assert not hasattr(value, name)
    if isinstance(value, Mapping):
        for name in banned:
            assert name not in value
    parameters = getattr(value, "parameters", None)
    if isinstance(parameters, Mapping):
        for name in banned:
            assert name not in parameters


def test_a_open_through_the_target():
    result = backtest(
        _long("a", _cross_above(10), 1 / 5, 1 / 5),
        _bars(
            (10, 10, 10, 10),
            (10, 11, 10, 11),
            (10, 11, 9, 11),
            (13, 13, 7, 10),
        ),
        fee=0.0,
        bar_size="1h",
    )
    _expect(result, 13 / 10, [(2, 3, "long", 10, 13, "take profit")])


def test_b_open_equal_to_the_target():
    result = backtest(
        _long("b", _cross_above(10), 1 / 5, 1 / 5),
        _bars(
            (10, 10, 10, 10),
            (10, 11, 10, 11),
            (10, 11, 9, 11),
            (12, 12, 7, 10),
        ),
        fee=0.0,
        bar_size="1h",
    )
    _expect(result, 12 / 10, [(2, 3, "long", 10, 12, "take profit")])


def test_c_fee_on_both_reverse_fills():
    hypothesis = Hypothesis(
        name="c",
        side="both",
        long_entry=_cross_above(10, "long"),
        short_entry=_cross_below(14, "short"),
        long_rule_exit=Below(Price(), Threshold(14, "exit")),
        distance_kind="percent",
        take_profit_size=1 / 2,
        stop_loss_size=1 / 2,
    )
    result = backtest(
        hypothesis,
        _bars(
            (10, 10, 10, 10),
            (10, 14, 10, 14),
            (10, 14, 10, 14),
            (14, 14, 10, 13),
            (10, 10, 10, 10),
            (10, 10, 4, 5),
        ),
        fee=1 / 10,
        bar_size="1h",
    )
    _expect(
        result,
        19683 / 20000,
        [
            (2, 4, "long", 10, 10, "rule exit"),
            (4, 5, "short", 10, 5, "take profit"),
        ],
    )


def test_d_rule_time_and_other_entry():
    hypothesis = Hypothesis(
        name="d",
        side="both",
        long_entry=_cross_above(10),
        short_entry=_cross_below(10),
        long_rule_exit=Below(Price(), Threshold(10, "exit")),
        time_exit=1,
        distance_kind="percent",
        take_profit_size=1 / 2,
        stop_loss_size=1 / 2,
    )
    result = backtest(
        hypothesis,
        _bars(
            (10, 10, 10, 10),
            (10, 11, 10, 11),
            (10, 11, 9, 9),
            (9, 9, 8, 8),
        ),
        fee=0.0,
        bar_size="1h",
    )
    _expect(
        result,
        1,
        [
            (2, 3, "long", 10, 9, "rule exit"),
            (3, 3, "short", 9, 8, "still open"),
        ],
    )


def test_e_time_exit_on_last_bar_does_not_fill():
    result = backtest(
        _long("e", _cross_above(10), 1 / 5, 1 / 5, time_exit=1),
        _bars(
            (10, 10, 10, 10),
            (10, 11, 10, 11),
            (10, 11, 9, 12),
        ),
        fee=1 / 10,
        bar_size="1h",
    )
    _expect(result, 27 / 25, [(2, 2, "long", 10, 12, "still open")])


def test_f_short_contains_both_levels():
    hypothesis = Hypothesis(
        name="f",
        side="short",
        short_entry=_cross_below(10),
        distance_kind="percent",
        take_profit_size=1 / 5,
        stop_loss_size=1 / 5,
    )
    result = backtest(
        hypothesis,
        _bars(
            (10, 10, 10, 10),
            (10, 10, 9, 9),
            (10, 13, 7, 10),
        ),
        fee=0.0,
        bar_size="1h",
    )
    _expect(result, 4 / 5, [(2, 2, "short", 10, 12, "stop loss")])


def test_g_four_grid_cells_no_winner():
    hypothesis = _long("g", _cross_above(10, "level"), 1 / 5, 1 / 5)
    bars = _bars(
        (8, 8, 8, 8),
        (8, 11, 8, 11),
        (10, 11, 9, 11),
        (10, 13, 9, 12),
    )
    results = grid(
        hypothesis,
        bars,
        fee=0.0,
        bar_size="1h",
        vary={"threshold": {"level": [10, 9]}, "fee": [0, 1 / 10]},
    )
    _no_ranking(results)
    cells = list(results)
    assert len(cells) == 4
    expected = (
        (10, 0, 6 / 5),
        (10, 1 / 10, 243 / 250),
        (9, 0, 6 / 5),
        (9, 1 / 10, 243 / 250),
    )
    for cell, (level, fee, stake) in zip(cells, expected, strict=True):
        _no_ranking(cell)
        _close(cell.parameters["threshold"]["level"], level)
        _close(cell.parameters["fee"], fee)
        _expect(cell, stake, [(2, 3, "long", 10, 12, "take profit")])


def test_h_cross_while_open_is_not_kept():
    result = backtest(
        _long(
            "h",
            _cross_above(10),
            1 / 2,
            1 / 2,
            long_rule_exit=Below(Price(), Threshold(10, "exit")),
        ),
        _bars(
            (10, 10, 10, 10),
            (10, 11, 10, 11),
            (10, 11, 9, 10),
            (10, 12, 10, 12),
            (12, 12, 9, 9),
            (9, 11, 9, 11),
            (11, 11, 10, 11),
        ),
        fee=0.0,
        bar_size="1h",
    )
    _expect(
        result,
        9 / 10,
        [
            (2, 5, "long", 10, 9, "rule exit"),
            (6, 6, "long", 11, 11, "still open"),
        ],
    )


def test_i_sma_warmup():
    average = Indicator("sma", "average", window=2, price="close")
    result = backtest(
        _long("i", Cross(Price(), average, "above"), 1 / 5, 1 / 5),
        _bars(
            (1, 1, 1, 1),
            (3, 3, 3, 3),
            (2, 2, 2, 2),
            (4, 4, 4, 4),
            (4, 6, 4, 5),
            (4, 6, 3, 4),
        ),
        fee=0.0,
        bar_size="1h",
    )
    _expect(result, 6 / 5, [(4, 4, "long", 4, 24 / 5, "take profit")])


def test_j_atr_distance():
    # Window 1, not the default 14. The gate stays true on the signal bar.
    atr = Indicator("atr", "atr", window=1)
    entry = All(
        (
            _cross_above(10),
            Above(atr, Threshold(0, "positive")),
        )
    )
    result = backtest(
        _long("j", entry, 1, 1, distance_kind="average true range"),
        _bars(
            (10, 10, 10, 10),
            (10, 11, 9, 11),
            (10, 14, 10, 12),
        ),
        fee=0.0,
        bar_size="1h",
    )
    _expect(result, 6 / 5, [(2, 2, "long", 10, 12, "take profit")])


def test_k_donchian_excludes_current_bar():
    upper = Indicator("donchian", "donchian", window=2, output="upper")
    result = backtest(
        _long("k", Cross(Price(), upper, "above"), 1 / 2, 1 / 2),
        _bars(
            (5, 5, 4, 5),
            (5, 6, 5, 6),
            (6, 7, 6, 6),
            (6, 9, 6, 8),
            (8, 8, 7, 8),
        ),
        fee=0.0,
        bar_size="1h",
    )
    _expect(result, 1, [(4, 4, "long", 8, 8, "still open")])


def test_l_swing_absent_until_confirmation():
    swing = Indicator(
        "swing",
        "swing",
        output="high",
        params=(("left", 1), ("right", 1)),
    )
    result = backtest(
        _long("l", Cross(Price(), swing, "above"), 1 / 2, 1 / 2),
        _bars(
            (5, 5, 4, 5),
            (5, 8, 5, 6),
            (6, 6, 5, 6),
            (6, 7, 6, 7),
            (7, 9, 7, 8),
            (8, 8, 7, 7),
            (7, 10, 7, 10),
            (10, 10, 9, 10),
        ),
        fee=0.0,
        bar_size="1h",
    )
    _expect(result, 1, [(7, 7, "long", 10, 10, "still open")])


def test_m_refusals():
    bands = Indicator("bbands", "bands", window=None, output="middle")
    held = Hypothesis(
        name="m-bbands",
        side="long",
        long_entry=Cross(Price(), bands, "above"),
        distance_kind="percent",
        take_profit_size=1 / 5,
        stop_loss_size=1 / 5,
    )
    assert held.long_entry is not None

    series = _bars((10, 10, 10, 10), (10, 11, 10, 11))
    plain = _long("m-fee", _cross_above(10), 1 / 5, 1 / 5)
    zero = backtest(plain, series, fee=0.0, bar_size="1h")
    assert len(zero.trades) == 0
    _close(zero.ending_stake, 1)
    assert zero.bar_size == "1h"

    def refused(kind, name, **indicator):
        with pytest.raises(ValueError):
            line = Indicator(kind, name, **indicator)
            hypothesis = _long(name, Cross(Price(), line, "above"), 1 / 5, 1 / 5)
            backtest(hypothesis, series, fee=0.0, bar_size="1h")

    refused("sma", "average", window=None)
    refused("pivot", "floor", params=(), output="pivot")
    refused("swing", "swing", params=(), output="high")
    refused("vwap", "vwap", params=())

    with pytest.raises(ValueError):
        grid(plain, series, fee=0.0, bar_size="1h", vary={"side": ["long", "short"]})
    with pytest.raises(ValueError):
        grid(
            plain,
            series,
            fee=0.0,
            bar_size="1h",
            vary={"distance_kind": ["percent", "average true range"]},
        )
    for fee in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            backtest(plain, series, fee=fee, bar_size="1h")


def test_n_non_finite_close():
    result = backtest(
        _long("n", _cross_above(10), 1 / 5, 1 / 5),
        _bars(
            (10, 10, 10, 10),
            (10, 11, 10, float("nan")),
            (10, 11, 9, 11),
            (10, 13, 9, 12),
        ),
        fee=0.0,
        bar_size="1h",
    )
    _expect(result, 1, [])


def test_o_reverse_does_not_fire_through_the_stop():
    hypothesis = Hypothesis(
        name="gap-reverse",
        side="both",
        long_entry=Cross(Price(), Threshold(10, "lvl"), "above"),
        short_entry=Cross(Price(), Threshold(10, "s"), "below"),
        long_rule_exit=Below(Price(), Threshold(10, "exit")),
        distance_kind="percent",
        take_profit_size=0.5,
        stop_loss_size=0.2,
    )
    result = backtest(
        hypothesis,
        _bars(
            (10, 10, 10, 10),
            (10, 11, 10, 11),
            (10, 11, 9, 9),
            (7, 8, 6, 7),
        ),
        fee=0.0,
        bar_size="1h",
    )
    _expect(result, 7 / 10, [(2, 3, "long", 10, 7, "stop loss")])


def test_p_non_finite_open_blocks_the_entry():
    result = backtest(
        _long("p", _cross_above(10), 1 / 5, 1 / 5),
        _bars(
            (10, 10, 10, 10),
            (10, 11, 10, 11),
            (float("nan"), 11, 9, 11),
        ),
        fee=0.0,
        bar_size="1h",
    )
    _expect(result, 1, [])


def test_q_non_finite_high_does_not_fill():
    result = backtest(
        _long("q", _cross_above(10), 1 / 5, 1 / 5),
        _bars(
            (10, 10, 10, 10),
            (10, 11, 10, 11),
            (10, 11, 9, 11),
            (10, float("nan"), 9, 11),
            (10, 13, 9, 12),
        ),
        fee=0.0,
        bar_size="1h",
    )
    _expect(result, 6 / 5, [(2, 4, "long", 10, 12, "take profit")])

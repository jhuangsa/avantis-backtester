"""A caller-built line can enter and exit through the same fill rules."""

import math

from backtest import Above, Bar, Cross, Hypothesis, Line, Threshold, backtest


def near(actual, expected):
    assert math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9), (
        actual,
        expected,
    )


def series(*rows):
    return tuple(
        Bar(open=row[0], high=row[1], low=row[2], close=row[3], volume=1)
        for row in rows
    )


def test_line_cross_fills_the_next_open_and_a_later_line_exits():
    bars = series(
        (10, 10, 10, 10),
        (10, 11, 10, 11),
        (10, 11, 9, 10),
        (10, 11, 9, 10),
        (13, 13, 12, 13),
    )
    hypothesis = Hypothesis(
        name="line",
        side="long",
        long_entry=Cross(Line("go", (0, 1, 0, 0, 0)), Threshold(0.5, "on"), "above"),
        long_rule_exit=Above(Line("flat", (0, 0, 0, 1, 0)), Threshold(0.5, "off")),
        distance_kind="percent",
        take_profit_size=1 / 2,
        stop_loss_size=1 / 2,
    )
    result = backtest(hypothesis, bars, fee=0.0, bar_size="15m")
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_bar == 2
    assert trade.exit_bar == 4
    assert trade.side == "long"
    assert trade.cause == "rule exit"
    near(trade.entry_price, 10)
    near(trade.exit_price, 13)
    near(result.ending_stake, 13 / 10)


def test_line_length_must_match_the_bars():
    bars = series((10, 10, 10, 10), (10, 11, 10, 11))
    hypothesis = Hypothesis(
        name="short-line",
        side="long",
        long_entry=Cross(Line("go", (0, 1)), Threshold(0.5, "on"), "above"),
        distance_kind="percent",
        take_profit_size=1 / 5,
        stop_loss_size=1 / 5,
    )
    try:
        backtest(hypothesis, bars + bars, fee=0.0, bar_size="15m")
    except ValueError as error:
        assert str(error) == "indicator length"
    else:
        raise AssertionError("expected a length error")

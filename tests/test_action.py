"""A hypothesis that names long, short, or flat from closed bars."""

import math

import pytest

from backtest import Bar, Hypothesis, backtest, grid


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


def follow(history):
    """Long when the last close rose, short when it fell, otherwise flat."""
    if len(history) < 2:
        return "flat"
    last = history[-1].close
    previous = history[-2].close
    if last > previous:
        return "long"
    if last < previous:
        return "short"
    return "flat"


def test_action_fills_at_the_next_open_and_flips():
    bars = series(
        (10, 10, 10, 10),
        (10, 10, 10, 11),
        (11, 12, 11, 12),
        (12, 13, 12, 13),
        (13, 13, 9, 9),
        (9, 9, 8, 8),
        (8, 8, 8, 8),
        (8, 10, 8, 10),
    )
    result = backtest(Hypothesis("follow", action=follow), bars, fee=0.0, bar_size="1h")
    assert result.bar_size == "1h"
    assert len(result.trades) == 2
    long, short = result.trades
    assert (long.entry_bar, long.exit_bar, long.side, long.cause) == (2, 5, "long", "action")
    near(long.entry_price, 11)
    near(long.exit_price, 9)
    assert (short.entry_bar, short.exit_bar, short.side, short.cause) == (
        5,
        7,
        "short",
        "action",
    )
    near(short.entry_price, 9)
    near(short.exit_price, 8)
    near(result.ending_stake, 10 / 11)
    assert "take_profit_size" not in result.parameters


def test_action_still_open_is_marked_at_the_last_close():
    bars = series(
        (10, 10, 10, 10),
        (10, 10, 10, 11),
        (11, 11, 11, 14),
    )
    result = backtest(Hypothesis("follow", action=follow), bars, fee=0.0, bar_size="1h")
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert (trade.entry_bar, trade.exit_bar, trade.side, trade.cause) == (
        2,
        2,
        "long",
        "still open",
    )
    near(trade.entry_price, 11)
    near(trade.exit_price, 14)
    near(result.ending_stake, 14 / 11)


def test_action_charges_the_fee_on_both_fills():
    bars = series(
        (10, 10, 10, 10),
        (10, 10, 10, 12),
        (11, 11, 11, 12),
        (12, 12, 12, 12),
    )
    result = backtest(Hypothesis("follow", action=follow), bars, fee=0.01, bar_size="1h")
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert (trade.entry_bar, trade.exit_bar, trade.cause) == (2, 3, "action")
    near(trade.entry_price, 11)
    near(trade.exit_price, 12)
    near(result.ending_stake, 29403 / 27500)


def test_action_sees_only_bars_that_have_closed():
    bars = series(
        (10, 10, 10, 10),
        (10, 10, 10, 11),
        (11, 11, 11, 12),
        (12, 12, 12, 9),
    )
    seen = []

    def spy(history):
        seen.append(tuple(bar.close for bar in history))
        return "flat"

    result = backtest(Hypothesis("spy", action=spy), bars, fee=0.0, bar_size="1h")
    assert seen == [(10,), (10, 11), (10, 11, 12)]
    assert result.trades == ()
    near(result.ending_stake, 1)


def test_action_does_not_trade_on_a_non_finite_bar():
    """A non-finite bar is not read, and it does not close the open trade."""
    bars = series(
        (10, 10, 10, 10),
        (10, 10, 10, 12),
        (11, 11, 11, math.nan),
        (9, 9, 9, 8),
    )
    seen = []

    def spy(history):
        seen.append(tuple(bar.close for bar in history))
        return "long"

    result = backtest(Hypothesis("spy", action=spy), bars, fee=0.0, bar_size="1h")
    assert seen == [(10,)]
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert (trade.entry_bar, trade.exit_bar, trade.side, trade.cause) == (
        1,
        3,
        "long",
        "still open",
    )
    near(trade.entry_price, 10)
    near(trade.exit_price, 8)
    near(result.ending_stake, 8 / 10)


def test_rejected_action():
    bars = series((10, 10, 10, 10), (10, 10, 10, 11))
    hypothesis = Hypothesis("nope", action=lambda history: "buy")
    with pytest.raises(ValueError, match="action"):
        backtest(hypothesis, bars, fee=0.0, bar_size="1h")


def test_action_hypothesis_rejects_a_target():
    with pytest.raises(ValueError):
        Hypothesis(
            "mixed",
            side="long",
            action=follow,
            distance_kind="percent",
            take_profit_size=0.1,
            stop_loss_size=0.1,
        )


def test_grid_rejects_an_action_hypothesis():
    bars = series((10, 10, 10, 10), (10, 10, 10, 11))
    with pytest.raises(ValueError, match="hypothesis"):
        grid(Hypothesis("follow", action=follow), bars, bar_size="1h")

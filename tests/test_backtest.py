"""Hand oracles for one hypothesis on one bar series.

Expected stakes and prices are literals from the locked oracles, not values
computed by the engine.
"""

import math

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
    Result,
    Threshold,
    Trade,
    backtest,
    grid,
)


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


def run(hypothesis, bars, *, fee=0.0):
    result = backtest(hypothesis, bars, fee=fee, bar_size="1h")
    assert result.bar_size == "1h"
    near(result.parameters["fee"], fee)
    return result


def assert_trade(trade, entry_bar, exit_bar, side, entry_price, exit_price, cause):
    assert trade.entry_bar == entry_bar
    assert trade.exit_bar == exit_bar
    assert trade.side == side
    assert trade.cause == cause
    near(trade.entry_price, entry_price)
    near(trade.exit_price, exit_price)


def long_cross(
    level=10,
    *,
    tp=1 / 5,
    sl=1 / 5,
    time_exit=None,
    rule_exit=None,
    kind="percent",
    entry=None,
    atr_window=None,
):
    if entry is None:
        entry = Cross(Price(), Threshold(level, "level"), "above")
    return Hypothesis(
        name="long",
        side="long",
        long_entry=entry,
        long_rule_exit=rule_exit,
        time_exit=time_exit,
        distance_kind=kind,
        take_profit_size=tp,
        stop_loss_size=sl,
        atr_window=atr_window,
    )


def test_single_cross_fills_next_open_and_later_target():
    """Case 1. The crossing close is not the fill; a later wick takes the target."""
    bars = series(
        (10, 10, 10, 10),
        (10, 11, 10, 11),
        (10, 11, 9, 11),
        (10, 13, 9, 12),
    )
    result = run(long_cross(), bars)
    assert len(result.trades) == 1
    assert_trade(result.trades[0], 2, 3, "long", 10, 12, "take profit")
    near(result.ending_stake, 6 / 5)


def test_staying_above_does_not_open_another_trade():
    """Case 2. A close that remains above the threshold is not a new cross."""
    bars = series(
        (9, 10, 9, 10),
        (10, 12, 10, 12),
        (10, 11, 9, 12),
        (12, 13, 11, 13),
        (13, 14, 12, 14),
    )
    result = run(long_cross(), bars)
    assert len(result.trades) == 1
    assert_trade(result.trades[0], 2, 3, "long", 10, 12, "take profit")
    near(result.ending_stake, 6 / 5)


def test_equal_previous_crosses_and_equal_current_does_not():
    """Case 3. Equality on the previous bar has not crossed; equality now is not through."""
    bars = series(
        (9, 9, 9, 9),
        (9, 10, 9, 10),
        (9, 11, 9, 11),
        (10, 11, 9, 11),
        (10, 13, 9, 12),
    )
    result = run(long_cross(), bars)
    assert len(result.trades) == 1
    assert_trade(result.trades[0], 3, 4, "long", 10, 12, "take profit")
    near(result.ending_stake, 6 / 5)


def test_missing_previous_bar_does_not_trade():
    """Case 4. Warmup, including a missing previous close, is not a trade."""
    bars = series(
        (11, 12, 10, 11),
        (12, 13, 11, 12),
    )
    result = run(long_cross(), bars)
    assert result.trades == ()
    near(result.ending_stake, 1)


def test_above_gate_blocks_one_cross_and_allows_the_next():
    """Case 5. A cross while the above-gate is false does not get in."""
    bars = series(
        (10, 10, 10, 10),
        (10, 11, 10, 11),
        (11, 11, 10, 10),
        (10, 14, 10, 13),
        (10, 11, 9, 11),
        (10, 13, 9, 12),
    )
    entry = All(
        parts=(
            Cross(Price(), Threshold(10, "level"), "above"),
            Above(Price(), Threshold(12, "gate")),
        )
    )
    result = run(long_cross(entry=entry), bars)
    assert len(result.trades) == 1
    assert_trade(result.trades[0], 4, 5, "long", 10, 12, "take profit")
    near(result.ending_stake, 6 / 5)


def test_tie_while_flat_opens_nothing_and_discards_the_crosses():
    """Case 6. Both entries true on one flat close do not invent a side."""
    bars = series(
        (4, 4, 4, 4),
        (4, 12, 4, 11),
        (11, 12, 6, 10),
        (10, 10, 6, 6),
        (6, 13, 6, 12),
        (10, 13, 9, 12),
    )
    hypothesis = Hypothesis(
        name="both",
        side="both",
        long_entry=Cross(Price(), Threshold(10, "long_level"), "above"),
        short_entry=Cross(Price(), Threshold(5, "short_level"), "above"),
        distance_kind="percent",
        take_profit_size=1 / 5,
        stop_loss_size=1 / 5,
    )
    result = run(hypothesis, bars)
    assert len(result.trades) == 1
    assert_trade(result.trades[0], 5, 5, "long", 10, 12, "take profit")
    near(result.ending_stake, 6 / 5)


def test_bar_containing_both_levels_exits_at_the_stop():
    """Case 7. A bar that contains both levels pays the stop, not the target."""
    bars = series(
        (10, 10, 10, 10),
        (10, 11, 10, 11),
        (10, 11, 9, 11),
        (10, 13, 7, 10),
    )
    result = run(long_cross(), bars)
    assert len(result.trades) == 1
    assert_trade(result.trades[0], 2, 3, "long", 10, 8, "stop loss")
    near(result.ending_stake, 4 / 5)


def test_open_already_through_the_stop_fills_at_the_open():
    """Case 8. A gap through the stop fills at the open, not back at the stop."""
    bars = series(
        (10, 10, 10, 10),
        (10, 11, 10, 11),
        (10, 11, 9, 10),
        (7, 9, 6, 8),
    )
    result = run(long_cross(), bars)
    assert len(result.trades) == 1
    assert_trade(result.trades[0], 2, 3, "long", 10, 7, "stop loss")
    near(result.ending_stake, 7 / 10)


def test_stop_inside_the_entry_bar_exits_on_that_bar():
    """Case 9. The stop is live from the entry open, including on the fill bar."""
    bars = series(
        (10, 10, 10, 10),
        (10, 11, 10, 11),
        (10, 11, 7, 9),
    )
    result = run(long_cross(), bars)
    assert len(result.trades) == 1
    assert_trade(result.trades[0], 2, 2, "long", 10, 8, "stop loss")
    near(result.ending_stake, 4 / 5)


def test_time_exit_counts_the_fill_bar_as_bar_one():
    """Case 10a. A count of 1 is known at the entry bar's close and paid next open."""
    bars = series(
        (10, 10, 10, 10),
        (10, 11, 10, 11),
        (10, 11, 9, 11),
        (12, 12, 11, 12),
    )
    result = run(long_cross(tp=1 / 2, sl=1 / 2, time_exit=1), bars)
    assert len(result.trades) == 1
    assert_trade(result.trades[0], 2, 3, "long", 10, 12, "time exit")
    near(result.ending_stake, 6 / 5)


def test_level_on_the_time_exit_bar_wins():
    """Case 10b. A stop on the bar whose close would know the time exit already closed."""
    bars = series(
        (10, 10, 10, 10),
        (10, 11, 10, 11),
        (10, 11, 4, 11),
    )
    result = run(long_cross(tp=1 / 2, sl=1 / 2, time_exit=1), bars)
    assert len(result.trades) == 1
    assert_trade(result.trades[0], 2, 2, "long", 10, 5, "stop loss")
    near(result.ending_stake, 1 / 2)


def test_reverse_rebuilds_distances_from_the_new_open():
    """Case 11. A rule exit and the other entry flip at the next open."""
    bars = series(
        (10, 10, 10, 10),
        (10, 14, 10, 14),
        (10, 14, 10, 14),
        (14, 14, 10, 13),
        (10, 10, 10, 10),
        (10, 10, 4, 5),
    )
    hypothesis = Hypothesis(
        name="reverse",
        side="both",
        long_entry=Cross(Price(), Threshold(10, "long_level"), "above"),
        short_entry=Cross(Price(), Threshold(14, "short_level"), "below"),
        long_rule_exit=Below(Price(), Threshold(14, "long_exit")),
        distance_kind="percent",
        take_profit_size=1 / 2,
        stop_loss_size=1 / 2,
    )
    result = run(hypothesis, bars)
    assert len(result.trades) == 2
    assert_trade(result.trades[0], 2, 4, "long", 10, 10, "rule exit")
    assert_trade(result.trades[1], 4, 5, "short", 10, 5, "take profit")
    near(result.ending_stake, 3 / 2)


def test_rule_exit_alone_closes_and_ignores_the_same_side_entry():
    """Case 12. A rule exit without the other side only closes."""
    bars = series(
        (10, 10, 10, 10),
        (10, 11, 9, 11),
        (10, 11, 9, 11),
        (11, 11, 9, 10),
        (10, 11, 9, 11),
        (11, 11, 10, 11),
    )
    hypothesis = Hypothesis(
        name="both",
        side="both",
        long_entry=Cross(Price(), Threshold(10, "long_level"), "above"),
        short_entry=Cross(Price(), Threshold(4, "short_level"), "below"),
        long_rule_exit=Cross(Price(), Threshold(10, "long_exit"), "above"),
        distance_kind="percent",
        take_profit_size=1 / 5,
        stop_loss_size=1 / 5,
    )
    result = run(hypothesis, bars)
    assert len(result.trades) == 1
    assert_trade(result.trades[0], 2, 5, "long", 10, 11, "rule exit")
    near(result.ending_stake, 11 / 10)


def test_rule_exit_and_time_exit_share_one_open():
    """Case 13. When the clock and the rule are both known, the cause is the rule exit."""
    bars = series(
        (10, 10, 10, 10),
        (10, 11, 10, 11),
        (10, 11, 9, 11),
        (11, 11, 10, 11),
    )
    result = run(
        long_cross(
            rule_exit=Below(Price(), Threshold(12, "exit_level")),
            time_exit=1,
        ),
        bars,
    )
    assert len(result.trades) == 1
    assert_trade(result.trades[0], 2, 3, "long", 10, 11, "rule exit")
    near(result.ending_stake, 11 / 10)


def test_still_open_is_marked_at_the_last_close_with_no_exit_fee():
    """Case 14. A trade that survives the last bar is marked, and the mark pays no fee."""
    bars = series(
        (10, 10, 10, 10),
        (10, 11, 10, 11),
        (10, 11, 9, 12),
    )
    result = run(long_cross(), bars, fee=1 / 10)
    assert len(result.trades) == 1
    assert_trade(result.trades[0], 2, 2, "long", 10, 12, "still open")
    near(result.ending_stake, 27 / 25)


def test_fee_zero_and_fee_above_zero_on_one_target():
    """Case 15. A fee is charged on the entry and on the target; zero leaves the ratio."""
    bars = series(
        (10, 10, 10, 10),
        (10, 11, 10, 11),
        (10, 11, 9, 11),
        (10, 13, 9, 12),
    )
    free = run(long_cross(), bars, fee=0)
    paid = run(long_cross(), bars, fee=1 / 10)
    assert_trade(free.trades[0], 2, 3, "long", 10, 12, "take profit")
    assert_trade(paid.trades[0], 2, 3, "long", 10, 12, "take profit")
    near(free.ending_stake, 6 / 5)
    near(paid.ending_stake, 243 / 250)


def test_true_entry_on_the_last_bar_schedules_nothing():
    """Case 16. The last close can be a real cross and still must not fill."""
    bars = series(
        (10, 10, 10, 10),
        (10, 11, 10, 11),
    )
    result = run(long_cross(), bars)
    assert result.trades == ()
    near(result.ending_stake, 1)


def test_level_fill_reads_that_close_flat_and_fills_the_next_open():
    """Case 17. After a target, that close may arm the next open."""
    bars = series(
        (10, 10, 10, 10),
        (10, 12, 10, 12),
        (10, 11, 9, 10),
        (10, 13, 9, 11),
        (10, 13, 9, 12),
    )
    result = run(long_cross(), bars)
    assert len(result.trades) == 2
    assert_trade(result.trades[0], 2, 3, "long", 10, 12, "take profit")
    assert_trade(result.trades[1], 4, 4, "long", 10, 12, "take profit")
    near(result.ending_stake, 36 / 25)


def test_short_target_and_stop_mirror_around_the_entry():
    """Case 18. A short target sits below the entry and the score stays in price percent."""
    bars = series(
        (10, 10, 10, 10),
        (10, 10, 9, 9),
        (10, 11, 7, 8),
    )
    hypothesis = Hypothesis(
        name="short",
        side="short",
        short_entry=Cross(Price(), Threshold(10, "level"), "below"),
        distance_kind="percent",
        take_profit_size=1 / 5,
        stop_loss_size=1 / 5,
    )
    result = run(hypothesis, bars)
    assert len(result.trades) == 1
    assert_trade(result.trades[0], 2, 2, "short", 10, 8, "take profit")
    near(result.ending_stake, 6 / 5)


def test_percent_distance_is_a_fraction_of_the_fill():
    """Case 19. A percent target is a fraction of the fill, not of the wick."""
    bars = series(
        (10, 10, 10, 10),
        (10, 11, 10, 11),
        (10, 10, 10, 10),
        (10, 12, 10, 10),
    )
    result = run(long_cross(tp=1 / 10, sl=1 / 10), bars)
    assert len(result.trades) == 1
    assert_trade(result.trades[0], 2, 3, "long", 10, 11, "take profit")
    near(result.ending_stake, 11 / 10)


def test_two_fee_cells_are_both_returned_and_neither_is_a_winner():
    """Case 20. A grid keeps every cell and names no winner."""
    bars = series(
        (10, 10, 10, 10),
        (10, 11, 10, 11),
        (10, 11, 9, 11),
        (10, 13, 9, 12),
    )
    results = grid(long_cross(), bars, bar_size="1h", vary={"fee": [0, 1 / 10]})
    assert isinstance(results, tuple)
    assert len(results) == 2
    assert set(Result.__dataclass_fields__) == {
        "trades",
        "ending_stake",
        "parameters",
        "bar_size",
    }
    assert set(Trade.__dataclass_fields__) == {
        "entry_bar",
        "exit_bar",
        "side",
        "entry_price",
        "exit_price",
        "cause",
    }
    assert not hasattr(results, "winner")
    for result, fee, stake in (
        (results[0], 0, 6 / 5),
        (results[1], 1 / 10, 243 / 250),
    ):
        assert result.bar_size == "1h"
        near(result.parameters["fee"], fee)
        near(result.parameters["threshold"]["level"], 10)
        assert len(result.trades) == 1
        assert_trade(result.trades[0], 2, 3, "long", 10, 12, "take profit")
        near(result.ending_stake, stake)
        assert not hasattr(result, "winner")
        assert not hasattr(result, "rank")


def test_rejected_missing_stop_is_not_a_hypothesis():
    """Rejected 1. Both distances are required, so a missing stop is not a hypothesis."""
    with pytest.raises(ValueError, match="size"):
        Hypothesis(
            name="missing stop",
            side="long",
            long_entry=Cross(Price(), Threshold(10, "level"), "above"),
            distance_kind="percent",
            take_profit_size=1 / 5,
        )


def test_rejected_above_only_entry():
    """Rejected 2. An above by itself is not an entry."""
    with pytest.raises(ValueError):
        long_cross(entry=Above(Price(), Threshold(10, "level")))


def test_rejected_long_that_also_carries_a_short_entry():
    """Rejected 3. A long names only the long entry."""
    with pytest.raises(ValueError):
        Hypothesis(
            name="long",
            side="long",
            long_entry=Cross(Price(), Threshold(10, "level"), "above"),
            short_entry=Cross(Price(), Threshold(10, "other"), "below"),
            distance_kind="percent",
            take_profit_size=1 / 5,
            stop_loss_size=1 / 5,
        )


def test_rejected_both_missing_one_entry():
    """Rejected 4. A both hypothesis must name each entry. The missing side is not mirrored."""
    long_entry = Cross(Price(), Threshold(10, "level"), "above")
    short_entry = Cross(Price(), Threshold(10, "level"), "below")
    with pytest.raises(ValueError):
        Hypothesis(
            name="both",
            side="both",
            long_entry=long_entry,
            distance_kind="percent",
            take_profit_size=1 / 5,
            stop_loss_size=1 / 5,
        )
    with pytest.raises(ValueError):
        Hypothesis(
            name="both",
            side="both",
            short_entry=short_entry,
            distance_kind="percent",
            take_profit_size=1 / 5,
            stop_loss_size=1 / 5,
        )


def test_rejected_non_positive_size():
    """Rejected 5. Both sizes must be strictly positive."""
    with pytest.raises(ValueError):
        long_cross(tp=0)
    with pytest.raises(ValueError):
        long_cross(sl=-1 / 5)


def test_rejected_time_exit_of_zero():
    """Rejected 6. A time exit of 0 is below 1 and is not a hypothesis."""
    with pytest.raises(ValueError):
        long_cross(time_exit=0)


def test_rejected_any_branch_without_a_cross():
    """Rejected 7. A branch that can open a trade must contain a cross."""
    entry = Any(
        parts=(
            Cross(Price(), Threshold(10, "level"), "above"),
            Above(Price(), Threshold(12, "gate")),
        )
    )
    with pytest.raises(ValueError):
        long_cross(entry=entry)


def test_sma_cross_waits_until_both_bars_are_defined():
    """Adversarial I. An SMA cross cannot use a bar whose average is still undefined."""
    bars = series(
        (1, 1, 1, 1),
        (3, 3, 3, 3),
        (2, 2, 2, 2),
        (4, 4, 4, 4),
        (4, 6, 4, 5),
        (4, 6, 3, 4),
    )
    entry = Cross(Price(), Indicator("sma", "average", window=2), "above")
    result = run(long_cross(entry=entry), bars)
    assert len(result.trades) == 1
    assert_trade(result.trades[0], 4, 4, "long", 4, 4.8, "take profit")
    near(result.ending_stake, 6 / 5)
    assert result.parameters["window"]["average"] == 2


def test_atr_distance_uses_the_named_window_on_the_signal_bar():
    """Adversarial J. ATR window 1 on the bar before the fill sets the target."""
    bars = series(
        (10, 10, 10, 10),
        (10, 11, 9, 11),
        (10, 14, 10, 12),
    )
    result = run(
        long_cross(tp=1, sl=1, kind="average true range", atr_window=1),
        bars,
    )
    assert len(result.trades) == 1
    assert_trade(result.trades[0], 2, 2, "long", 10, 12, "take profit")
    near(result.ending_stake, 6 / 5)
    assert result.parameters["window"]["atr"] == 1

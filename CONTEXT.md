# Hypothesis backtests

A hypothesis is scored as trades on one bar series. The result is a price return. It does not say whether the account took those trades.

## Language

### The hypothesis

**Hypothesis**:
A named position scored by the price return of its trades. It names a side, the conditions for getting in, a take profit, and a stop loss, or it names long, short, or flat from bars that have already closed.
_Avoid_: strategy, day trade, setup

**Action**:
The position a hypothesis names at one bar from bars that have already closed: long, short, or flat. Flat is no position.
_Avoid_: buy, sell, hold

**Side**:
The direction of the trade: long, short, or both. It belongs to the hypothesis and applies to whatever lines that hypothesis uses. It is fixed for the grid. The cross that enters or exits may point either way. A long or a short hypothesis names that side's entry only. A both hypothesis names the long entry and the short entry, and each of those sides may name its own rule exit.
_Avoid_: a property of one indicator, a mirrored side, the direction of the cross

**Backtest**:
The scoring of one hypothesis on one series. The series is whatever stretch is supplied. It yields the trades, each with its cause, and the compounded return, together with the parameters and the bar size.
_Avoid_: fit, reconstruction, a train period, a test period

**Trade**:
One entry and the way out that closes it. The first way out closes the whole trade. A hypothesis has at most one trade open. A new cross on the same side, while that trade is open, does not add a second trade.

**Return**:
The price change of a trade as a percent of its entry price, after any fee on its fills. A long is (exit − entry) / entry. A short is the mirror. Each trade uses the entire stake, trades compound, and time with no open trade earns nothing.
_Avoid_: inverse bitcoin pnl, the wallet

**Fee**:
A flat charge on every fill, as a fraction of price, including a take profit and a stop loss. It is zero unless the backtest names a rate. The rate is a parameter of the backtest and may sit on the grid.
_Avoid_: a maker and taker schedule, funding

### Getting in and out

**Entry**:
A cross on a bar where every gate on that rule is also true. A gate is an above or a below. An above or a below by itself is not an entry. A both hypothesis has one entry for the long and one entry for the short.

**Tie**:
A close, while the hypothesis is flat, on which the long entry and the short entry are both true. No trade opens. The crosses on that bar are not kept.

**Rule exit**:
An optional way out caused by a cross, an above, or a below, evaluated only while a trade is open. The cross may point either way. It does not depend on the entry price, and it cannot close a trade on the same open that entered it. A side may omit it.

**Time exit**:
An optional way out a fixed number of bars after entry. The count is a positive whole number. The bar of the fill is bar 1. For a cross on bar t and a count of N, it is known at the close of bar t+N, and the price is the open of the following bar. If that same close is also a rule exit, the cause is the rule exit. A level fill on that bar has already closed the trade. A time exit does not open the other side.

**Distance**:
How a take profit and a stop loss sit away from the entry price. The kind is a percent of that price, or a multiple of the average true range of the last closed bar before the fill. A hypothesis uses one kind for both sides. The kind is fixed for the grid. The two sizes are positive parameters. The average-true-range window is 14 unless the hypothesis names another, and that window is a parameter. The same two sizes apply to a long and to a short.

**Take profit**:
The gain-side distance from the entry price. On a short it sits below the entry.
_Avoid_: trailing stop, a fixed price chosen before the entry

**Stop loss**:
The loss-side distance from the entry price, using the same kind of distance as the take profit. On a short it sits above the entry. When a bar's high and low contain both this level and the take profit, the stop loss is the way out.
_Avoid_: trailing stop, a fixed price chosen before the entry

**Open fill**:
The next bar's open. This is the price of an entry, a rule exit, or a time exit, which are known only after a bar closes. The last bar of a series has no entry, because that open does not exist.

**Level fill**:
The price of a take profit or a stop loss. It is the level itself. If the bar opens already through the level, it is the open. The level is live from the entry onward, including on the bar of the entry. Once a level fill has closed the trade, that bar's close is read while flat, and an entry that is true fills at the next open.

**Reverse**:
A trade's rule exit and the other side's entry both true at the same close, with no level fill on that bar. The open trade closes and the other side opens, both at the next open. Each of those is a fill, so each carries the fee. The new distances are measured from that open, using the average true range of the bar that just closed. A rule exit with no entry on the other side only closes the trade.

**Mark**:
The last close, used as the exit price of a trade still open after the last bar's levels have been tested. It is in the compounded return. It is not a fill and pays no fee. The cause is still open.

**Cause**:
Why a trade ended: take profit, stop loss, rule exit, time exit, action, or still open. An action means the hypothesis named a different position.

### Comparisons

**Operand**:
One side of a comparison.
_Avoid_: resistance

**Line**:
An operand that is a series: an indicator output, a price, or a level.
_Avoid_: resistance

**Price**:
The close, when a rule uses a price and the hypothesis names no other. The hypothesis may name the open, the high, or the low instead. That choice is fixed for the grid.

**Threshold**:
An operand that is a single number. It is a parameter when it is placed on the grid.
_Avoid_: level

**Cross**:
The one bar on which an operand moves from at or below another operand to strictly above it, or the mirror move to strictly below. Both operands are defined on this bar and the previous bar.
_Avoid_: staying above, staying below

**Above**:
The condition that one operand is strictly greater than the other on this bar.
_Avoid_: cross

**Below**:
The condition that one operand is strictly less than the other on this bar.
_Avoid_: cross

**Rule**:
A cross, an above, a below, or a combination that requires all of its parts or any of them.

### Levels

**Level**:
A line that is a calculated price: a floor pivot, a confirmed swing, or a channel edge.
_Avoid_: resistance

**Floor pivot**:
A level fixed for one session from the prior session's high, low, and close. The hypothesis names the session.
_Avoid_: resistance

**Session**:
The block of bars a floor pivot is taken from. The pivot is known on the next session and does not move during it.

**Confirmed swing**:
A high strictly beyond the bars on each side of it, or the mirror for a low. The hypothesis names how many bars lie on each side. It is a line only from the bar on which those later bars have closed.
_Avoid_: resistance, the extreme on the bar it prints

**Channel**:
The highest high and the lowest low over a finished window of bars, read as a line on the following bar.
_Avoid_: resistance

### Parameters

**Parameter**:
A number a hypothesis exposes for the grid.
_Avoid_: a searched optimum

**Window**:
The number of bars a line looks back over. A window is a parameter. It is not a threshold.

**Grid**:
The numeric values the user writes down: windows, thresholds, the two distance sizes, the time-exit count, and the fee rate. Each cell is a backtest, and every cell is reported. Side, distance kind, and which price is used stay fixed across the cells.
_Avoid_: search, the winner, a sweep over side

### The series

**Series**:
The open, high, low, close, and volume of one instrument at one bar size.
_Avoid_: a book of several instruments, ticks

**Instrument**:
The single market a series covers.

**Bar size**:
The spacing of the series a backtest is scored on. It is part of the result.

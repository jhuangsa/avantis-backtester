# Backtests

A strategy runs on one or more markets in one account. The result is every closed trade, the equity at every bar, and the ending balance, in account money.

## Language

### The backtest

**Side**:
The direction of a trade: long or short. It belongs to the order, not to an indicator.
_Avoid_: a property of one indicator, the direction of the cross

**Backtest**:
One run of a strategy on the markets the caller supplies. The caller chooses the stretch. It yields the trades, each with its cause, the equity, and the timeframe.
_Avoid_: fit, reconstruction

**Trade**:
One entry and the way out that closes it. It records its instrument, entry bar, exit bar, cause, and result in account money.

**Fee**:
A flat charge on every fill, as a fraction of notional, including a take profit and a stop loss. The order names the rate.
_Avoid_: a maker and taker schedule, funding

### Getting in and out

**Entry**:
An open order, known at a bar's close and filled at the next open.

**Take profit**:
The gain-side distance from the entry price. On a short it sits below the entry. An order may omit it, and then that level is not placed.
_Avoid_: a fixed price chosen before the entry

**Stop loss**:
The loss-side distance from the entry price, using the same kind of distance as the take profit. On a short it sits above the entry. When a bar's high and low contain both this level and the take profit, the stop loss is the way out. When the take profit is omitted, the stop is the only level.
_Avoid_: a fixed price chosen before the entry

**Open fill**:
The next bar's open. This is the price of every order, because an order is known only after a bar closes. The last bar of a series has no entry, because that open does not exist.

**Level fill**:
The price of a take profit or a stop loss. It is the level itself. If the bar opens already through the level, it is the open. The level is live from the entry onward, including on the bar of the entry. Once a level fill has closed the trade, that bar's close is read while flat, and an entry that is true fills at the next open.

**Cause**:
Why a trade ended: take profit, stop, order, liquidation, or hard stop. An order means the strategy closed it.

### Comparisons

**Operand**:
One side of a comparison.
_Avoid_: resistance

**Line**:
An operand that is a series: an indicator output, a price, or a level.
_Avoid_: resistance

**Cross**:
The one bar on which an operand moves from at or below another operand to strictly above it, or the mirror move to strictly below. Both operands are defined on this bar and the previous bar.
_Avoid_: staying above, staying below

**Above**:
The condition that one operand is strictly greater than the other on this bar.
_Avoid_: cross

**Below**:
The condition that one operand is strictly less than the other on this bar.
_Avoid_: cross

### Levels

**Floor pivot**:
A level fixed for one session from the prior session's high, low, and close. The strategy names the session.
_Avoid_: resistance

**Session**:
The block of bars a floor pivot is taken from. The pivot is known on the next session and does not move during it.

**Confirmed swing**:
A high strictly beyond the bars on each side of it, or the mirror for a low. The strategy names how many bars lie on each side. It is a line only from the bar on which those later bars have closed.
_Avoid_: resistance, the extreme on the bar it prints

**Channel**:
The highest high and the lowest low over a finished window of bars, read as a line on the following bar.
_Avoid_: resistance

### Parameters

**Window**:
The number of bars a line looks back over. A window is a parameter. It is not a threshold.

### The series

**Series**:
The open, high, low, close, and volume of one instrument at one bar size.
_Avoid_: a book of several instruments, ticks

**Instrument**:
The single market a series covers.

**Bar size**:
The spacing of the series a backtest is scored on. It is part of the result. In the C++ engine it is a timeframe.

**Timeframe**:
The length of one bar, one of twelve fixed values from 1 minute to 1 month (`Timeframe`). A C++ market can carry several. See ADR 0011.
_Avoid_: interval, resolution

**Base**:
The finest timeframe of a market, the first in its list. Orders fill at its opens, and stops and take profits are checked on its highs and lows. Every market in one run has the same base.

### The portfolio

**Portfolio**:
The account the trades are paid from: a balance and the positions it pays for. Its results are in account money, not price percent. It holds at most one position per instrument, in several instruments at once. See ADR 0008.
_Avoid_: the wallet, a book

**Position**:
One open trade in a portfolio: its instrument, side, size, collateral, stop, and liquidation price.

**Collateral**:
The balance a position locks: its notional over its leverage. A trade whose collateral exceeds the free cash is skipped.

**Liquidation**:
The forced close once a position's loss reaches 85% of its collateral, the Veranta rule.

**Strategy**:
The C++ form of a trading idea: a struct that computes its lines once and, at the close of each bar, returns orders. It never opens or closes a position itself. See ADR 0009.
_Avoid_: hypothesis, a rule tree

**Order**:
What a strategy returns at the close of a bar: open or close, the instrument, the side, the stop and take-profit distances, the leverage, and the fee rate. It fills at the next open. Distances are fractions of the fill price, because the strategy does not know that price yet.
_Avoid_: signal, a fill

**Market**:
In the C++ engine, one instrument's name and its bars on one or more timeframes, finest first.
_Avoid_: asset, symbol, feed

**Markets**:
The markets one C++ backtest runs on, all on one clock. A strategy sees every market and reads the ones it needs. See ADR 0010, 0011.
_Avoid_: universe, basket

**State label**:
One of three names for a market's condition in one minute: market, trend, or volatility. It is computed outside the engine and stored as a code, where 0 is Unknown. A strategy at `now` sees the label stamped one minute earlier. Unknown never opens a trade. See ADR 0012.
_Avoid_: regime, signal

**Clock**:
The sorted list of every base bar open of every market in a backtest, with no repeats. Step t is the same moment in each market. A market joins at its first bar and leaves after its last, so markets may start and end at different times. The C++ engine refuses markets with different base timeframes.
_Avoid_: calendar, index

**Hard stop**:
The fall below the starting balance, counting unrealized results, at which every position closes and the portfolio stops trading for good. 30% by default.

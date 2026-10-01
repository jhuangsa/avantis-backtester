# Indicators update one bar at a time

Until now `prepare` computed every indicator once, over the whole series, before the backtest loop began. A live caller gets one closed bar a minute. Recomputing the whole series each minute repeats work, and a second code path for live could give different numbers than the backtest.

## Decision

Each indicator is a small object with one formula: `update(new bar)` returns the value for that bar, or NaN while it is undefined. The full-series functions (`sma`, `atr`, `chandelier`, and the rest) are loops of `update` over history. There is no second version of any formula.

- Live and backtest give bit-identical numbers. The floating-point operations run in the same order as before: `Sma` keeps the running sum, never `average × n`.
- `test_indicators.cpp` passes unchanged, which shows no number moved. `test_rolling.cpp` checks each object against its function, bit for bit.
- A strategy has `prepare(markets)`, which resets its indicators and calls `update`, and `update(markets)`, which feeds each indicator only the bars added since the last call. `decide` is unchanged.
- `StateTrend` builds a market's lines the first time it sees the market and only extends them after, so its stored bar numbers survive.
- `Markets` only grows. `append` adds one closed bar and `append_states` adds one minute of labels. Bars are never dropped, so bar numbers never shift and references to a market's `Bars` stay valid.
- The caller appends a coarser bar only once it has closed, so a coarser indicator updates once per bar and never on a half-finished one.
- `backtest` takes an optional `on_step` hook. `test_live.cpp` records a backtest, replays it through `append`, `update`, and `decide`, and requires equal orders at every step.

## Consequences

- A backtest gives the same results as before.
- A live caller calls `decide` once per closed bar at a cost that does not grow with the history.
- Memory grows with the history: about 25 MB per market for a year of 1-minute bars.
- After a restart, `prepare` restores every indicator. A strategy's `signal_bar` (the bar of its last entry, which times a time exit) is not in the bars, so `Position` carries `entry_time`, the UTC second of the entry fill; `backtest` sets it. A strategy with no `signal_bar` for an open position takes the bar closed at `entry_time`. `test_live.py` checks that a restarted `Live` makes the same time exit.
- State labels arrive about 7 minutes late. The live caller appends each label when it arrives, and `decide` reads only labels already appended; there is no live setting for the delay. The label-delay setting (a separate issue) makes a backtest see labels as late as live.
- `report` is optional in `Live.decide`.
- Cost: one append of a 1-minute bar, one `append_states`, `update`, and `decide` for `StateTrend` on BTC after a month (43,200 minutes) of history takes a median of 0.12 µs (99th percentile 0.2–0.3 µs; the rare maximum, about 0.1–0.2 ms, is a vector growing) on an Apple M1 Pro, `-O2`, over 2,000 steps. The cost does not grow with the history.
- `decide` returns opens and rule exits only. In a backtest the portfolio handles stops, take profits, trailing stops, partial take profits, and liquidation. Live, the caller's system must do this on the exchange. See [Stops live](../../cpp/README.md#stops-live).

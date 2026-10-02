# Markets may carry state labels

This adds to [ADR 0011](0011-markets-carry-several-timeframes-and-their-own-history.md). A `Market` may now hold labels for each minute, besides its bars. The clock, the fills, and the base timeframe do not change.

A state label is a name for the condition of the market in one minute, computed outside the engine and stored in the `market_context_1m` table. A market may carry three: `market_state`, `trend_state`, and `volatility_state`. Each is a `uint8` code in `states.hpp`, and 0 is Unknown. The codes are fixed, because Python writes them. `raw_market_state` is left out: it changes about twice as often as `market_state`, so a strategy on it would trade on noise.

Python cleans the labels. C++ never sees a hole. The cleaning in `clean_states` (`examples/clickhouse_data.py`) is:

- Query the table with `FINAL`, so each minute has its settled row.
- Treat the text `unknown` as missing.
- Start at the first row that has all three labels.
- Keep one row per minute, on a 60-second grid.
- Carry a label forward at most 60 minutes. A longer gap becomes Unknown.
- Refuse a value that is not in the code lists. A new label in the table is an error, not a silent Unknown.

C++ receives a start time (a whole minute, in UTC seconds) and three arrays of equal length. `Markets::make` refuses a market whose arrays differ in length or whose start is not a whole minute.

At `now`, a strategy sees the label stamped one minute earlier: `state_at` reads the row for `now - seconds(Min1)`. A label stamped T may use the candle that opens at T, and that candle closes at T+60. The label stamped T-60 is complete at T whether or not a label uses its own minute. Reading the label at `now` could use a candle that has not closed. Do not change this.

The delay is a setting, `PortfolioSettings::state_delay`, for labels that arrive late. It is in seconds, a whole number of minutes, and 60 by default. `state_at` reads the row for `now - state_delay`; a backtest and a `Live` both refuse a delay under 60 or off the minute grid. A longer delay reads an older label, so it never reads ahead; 60 s stays the minimum for the reason above.

Before the first row and after the last, every label is Unknown. Unknown never opens a trade, because a trade needs a defined operand. An open position is not closed by Unknown either, because Unknown is no evidence that the trend has ended.

Consequences:

- The labels are relative to one pair, so a code on BTC and the same code on ETH are not the same size of move.
- The history was backfilled in September 2026. Labels for older minutes were computed later, with the method of that date. Treat a backtest on them as a test of the code, not as a record of what a live system would have shown.
- The labels change every few minutes. A strategy that trades on them opens and closes often, so fees weigh more.

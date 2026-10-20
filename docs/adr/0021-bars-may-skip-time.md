# Bars may skip time

This replaces the gap rules of [ADR 0011](0011-markets-carry-several-timeframes-and-their-own-history.md): "a series has no gaps", "an order for a market with no bar at that step is dropped", and "`last_closed` uses the next bar's open as the close of a bar". The rest of ADR 0011 stands.

Some markets close: stocks, forex, metals, Korean equities. They have no bars while closed. Filling the time with flat bars would let orders fill and levels trigger at a price nobody could trade. So a market may now skip time.

## Decision

- **Markets accept gaps.** `Markets::make` and `append` no longer refuse two base bars more than one bar apart. A base bar must still open on its timeframe grid. The clock is still every base bar open of every market, so a market in a gap has no bar at some steps (`bar_at` is -1).
- **A bar closes at its end.** `bar_end(tf, ts)` is `ts + seconds(tf)`, or for `Month1` the first second of the next UTC month. `last_closed(bars, now)` is the last bar with `bar_end <= now`. Friday's last bar closes on Friday, not when Monday opens. With no gaps this is the same as before.
- **`now` is `bar_end` of the step.** The backtest, Sharpe and the live warm-up all use it.
- **Nothing happens in a gap.** A market in a gap gets no fill, no level check and no holding cost. Its positions keep their last mark. At the reopen, an open past a stop, take profit or liquidation fills at the open, as before. A gap is not the end of data: that is only a market's last bar.
- **Orders wait.** An order for a market in a gap (bars before and after the step) waits and fills at that market's next open. An order for a market that has not started, or has ended, is dropped. Closes still fill first. When a step sends new orders for a market, a new open replaces its waiting open, and a new close replaces its waiting close and drops its waiting open. A waiting close survives a later open. So at most one close and one open wait per market, and at the reopen the close fills first, then the open. With no gaps nothing waits, so results are unchanged. A second open on a held market is refused by the portfolio.
- **Time exits count bars.** A time exit of N bars uses `bars_held(bars, entry_time, now) >= N`: the bars closed since the fill, the bar opened at or after the fill being the first. A weekend counts 0. With no `entry_time`, there is no time exit. With no gaps this is the same as the old wall-time rule.
- **Baskets act per market.** `dip_basket` tracks a new bar per market, so a closed market does not hold back the others. Breadth still counts every market.
- **`buy_hold`** counts a position already on its market as opened.
- **`Combined`** keeps an open's claim until the open's fill chance has passed: the market has a base bar opened at or after the decide time that has closed. A waiting order keeps its owner through a gap.
- **Live warm-up.** `decide_once` warms up at the previous clock step's close, which may be before a gap. It falls back to one base bar earlier when `now` is the first step's close or not a step's close.
- **Python.** `btc_bars.resample(..., keep_empty=False)` drops buckets with no minutes; `True`, the default, fills them flat as before. `timeframes.all_timeframes` passes it through.

## Caller's part

- **Append in time order.** Live, append every market's bars in time order. A base bar earlier than the clock's end must already be a clock step.
- **Holding costs.** A market charges its holding cost per bar it has. The caller folds the cost of a gap (a weekend's funding) into the bar before it.
- **States.** A market's states still need a row for every minute. While it is closed, those rows are Unknown, which never opens a trade (ADR 0012).

## Consequences

- **Sharpe** counts calendar days, ×√365. A closed day is a 0% day, so a market that closes often shows a lower Sharpe than one that trades every day.
- **The hard stop** checks the whole account, so it can close a gapped market's position at its last mark.
- **Strategy effects we accept:**
  - the weekend strategies' "Monday bar at hour H" test reads the bar's end, so a market closed then has no such bar;
  - bar-count lookbacks and indicators span the closed time;
  - the true range of the first bar after a gap includes the reopen jump.
- **Live `decide` across a gap.** `Combined` built fresh in `decide_once` does not know a claim made before the last step; a caller that keeps a `Live` keeps its claims.

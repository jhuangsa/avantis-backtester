# Costs belong to the market, not the strategy

Until now each C++ strategy carried a `fee_rate` (default 0.0001) and copied it onto every `Order`. The portfolio charged that one rate on the notional at the open and again at the close. This had three faults:

- A fee belongs to the market. StateTrend on BTC and ETH charged both the same rate.
- The fee was a strategy param, so a search such as `optimize` could tune it.
- The open and the close shared one rate, and there were no holding costs (funding, rollover).

A backtest with costs left out looks better than reality, so costs must be impossible to forget.

## Decision

Costs are an input of the run, one `Costs` per market. They are no longer part of a strategy. `fee_rate` is removed from `Order` and from every strategy's `Params`, so no strategy and no search can set a fee.

`Costs` has four fields:

| Field | Meaning |
|---|---|
| `open_fee` | Fraction of notional charged at the open fill. |
| `close_fee` | Fraction of notional charged at the close fill. |
| `hold_long` | One value per base bar: the cost of holding a long through that bar, as a fraction of notional. |
| `hold_short` | The same for a short. |

The rules:

- The fee formula is unchanged: rate × size × price at each fill. Only the source of the rate moves. A partial close charges the close fee on the part closed.
- Holding costs are signed. Positive means the position pays. Negative means it receives, as funding often does for one side.
- On each base bar, an open position adds holding cost × size × price to its running total, priced at the bar's close. Equity includes the total at every bar. The balance pays it at the close, as it pays fees. A partial close pays its share.
- A NaN holding value counts as zero. A holding cost never blocks a trade.
- `hold_long` and `hold_short` are either empty (no holding cost) or have exactly one value per base bar of the market. Any other length is an error naming the market.
- A market with no `Costs` is an error, not zero cost. So is a `Costs` that names no market. Zero cost must be written out: `Costs()`.
- `backtest`, `run`, and `optimize` all take the costs and run `check_costs` before the first step.

Costs are not a strategy param, so the strategy table (`run.hpp`) has no fee entry, and `optimize` cannot search it.

## Consequences

- A fee can differ by market, and the open and the close can differ.
- `Trade` gains `entry_time`, `exit_time`, `size`, `leverage`, `fees` (open plus close), and `holding_costs`. `result` is after fees and holding costs. `fees + holding_costs` explains the gap between the price result and `result`.
- Every caller must pass costs. Code that used the default `fee_rate = 0.0001` now passes `Costs(open_fee=0.0001, close_fee=0.0001)` for each market and gets the same trades as before.
- Holding costs add to the open position's loss, so they count toward the hard stop through equity. The liquidation price does not move.
- The costs are the caller's data, like the bars: the library never reads them from a file or a network.

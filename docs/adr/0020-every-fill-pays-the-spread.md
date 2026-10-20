# Every fill pays the spread

Veranta fills an order away from the oracle price: a long above it, a short below it, and a close the same way (docs.veranta.xyz/trading/execution-and-pricing). Until now the engine filled at the oracle price, so every backtest was a little too good.

Terms:

- **Oracle price**: the price in the bars.
- **Half-spread** (`s`): the distance from the oracle price to the fill, as a fraction of price.
- **Fill price**: the oracle price moved against the trader by `s`.

## Decision

The spread is part of a market's costs (ADR 0016). `Costs` gains two last fields:

- `spread`: `nullopt`, or the caller's half-spread per base bar. Empty means no spread, one value means that value on every bar, and one value per base bar lets the spread widen on weekends or in thin hours.
- `max_spread`: a cap on the half-spread. NaN means no cap.

`nullopt` uses Veranta's default for the market, from the table in `cpp/include/avbt/spreads.hpp`. The table has one row per market: the half-spread, the cap, and the date read. The lookup tries the instrument name, then the name plus `/USD`. A market with no row is an error, as a market with no `Costs` is. A caller writes `.spread = avbt::no_spread` in C++, or `spread=0` in Python, to run with none. In C++, `.spread = {}` is `nullopt`, the table, not "no spread".

The rows come from Veranta's risk API, read with a small order (about $1,000), long and short, open and close. The row's spread is the smallest of the four: the market's floor; the rest is the live order book. The API shows no cap, so the rows have none. A market closed at the time has no row. `examples/veranta_spreads.py` reads the API and rewrites the rows.

On base bar `i` the half-spread is `min(value_i, cap)`. With `dir` +1 for a long and −1 for a short:

- An open fills at `E = P × (1 + dir × s)`. A close fills at `X = P × (1 − dir × s)`. `P` is the oracle price the fill used before: the next open, the stop level or the open past it, the take-profit level or the open past it, the last close at end of data, or the last mark for the hard stop.
- Every fill pays: next-open fills, stops, trailing stops, take profits, partial take profits, the hard stop and end-of-data closes.
- Stops, take profits, trails and the liquidation trigger stay on oracle prices. They count from the oracle open and trigger on the oracle high and low. Only the fill moves.
- Size is `risk / (dir × (E − stop × (1 − dir × s_e)))`, with `s_e` the entry bar's spread. A stop that fills at its level on a bar with the same spread loses exactly the risk, before fees.
- The collateral, the 80% stop cap, free cash and the liquidation price use `E` (ADR 0008). Liquidation is decided on the oracle fill and fills at its level with no spread.
- The hard stop closes each position at its last mark with the last spread seen for its market.
- Equity marks at the oracle close with no spread.

## Consequences

- `Trade.entry_price` and `exit_price` are the fill prices. Fees are on the fill prices. The price result already holds the spread, so gross = `result` + `fees` + `holding_costs` still holds (ADR 0019).
- `Trade` gains `spread_cost`: size × `P` × `s` at each fill, 0 for a liquidation exit. A partial close takes its fraction of the entry part, as with holding costs. It explains part of the price result; it is not taken off again.
- The profit share's ROI counts the fill prices, so the spread can move a close into a lower tier.
- Every caller that built `Costs` without a spread now uses the table. A market with no row raises an error until the caller passes a spread.
- Live `decide` never fills, so it does not change.
- `walk_forward` takes each fold's costs from the caller. A caller that cuts folds cuts a per-bar spread column the same way as the holding columns; `examples/walk_forward.py` does.
- Size impact (a spread that grows with order size) is left for later.

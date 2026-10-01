# A stop may trail and a take profit may close part

A C++ position can carry two more settings for its levels, both set when it opens and both run by the portfolio, not the strategy:

- **Trail.** A price gap. On every bar that leaves the position open, the stop moves to the bar's high minus the gap (low plus the gap for a short), if that is better than the stop. It never moves back.
- **Take profit fraction.** The share of the position the take profit closes. Below 1, the take profit fires once; the rest has no take profit and runs on its stop.

`Order` carries them as `trail_distance`, a fraction of the fill like `stop_distance`, and `take_profit_fraction`. The defaults, 0 and 1, are the old behaviour.

Why in the portfolio: a trailed stop is checked inside the bar like any stop, so it fills at its level. A strategy that sent a close order when the price crossed its line would fill at the next open instead.

The rules:

- The stop moves only after the bar's stop and take-profit checks, from that bar's high or low. The new stop counts from the next bar, so the bar that sets a stop cannot also fill it.
- A bar that reaches both levels still exits at the stop (ADR 0003), the whole position.
- The trail gap is fixed at the open. A gap that follows a changing ATR would need an order that moves a stop.
- One partial target only. Several targets would need a list.
- The backtest keeps a trade's entry step until no position is left, so each part of a partly closed trade is a `Trade` with the same entry step.

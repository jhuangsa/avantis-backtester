# An open below the min position is refused

Veranta will not open a position smaller than a set notional (position size, margin times leverage): $100 on most markets, $300 on forex, gold and silver, and $10 on a few others. Veranta Copy lists each market's minimum as `min_position_usdc` at `verantacopy.com/api/pairs`.

Until now the engine had no minimum. A trade is sized as balance × risk × leverage / stop distance (ADR 0015). With a wide stop, low leverage or a small account, that comes out below the minimum, so the backtest made trades Veranta would refuse. A strategy could look fine on a $10,000 account but need a much larger budget to make every trade.

## Decision

`Costs` gains a last field, `min_position`: the smallest notional, in account money, the market lets a position open with. 0, the default, means none, so existing results do not change.

- `Portfolio::open` refuses an open whose notional at the fill (size × fill price, spread included) is below `min_position`. A refused open records nothing, as one with no free cash does.
- `check_costs` and the Python `Costs` require `min_position` to be finite and at least 0.
- Closes, partial take profits included, have no minimum.

## Consequences

- A small account trades less: its smaller trades are skipped, not raised to the minimum. Raising them would risk more than `risk_per_trade`.
- `examples/mimic.py` reads each market's minimum from Veranta Copy (`market_costs`). It reports each run's **budget needed** (`min_budget`): the smallest account at which every open in the run clears its minimum. Sizes scale with the account, so it is the account × the largest minimum / notional over the run's opens.
- `mimic_frontier.py` and `mimic_drawdown.py` take `--budget`, the account to search at.
- Live `decide` does not size orders, so it does not change.

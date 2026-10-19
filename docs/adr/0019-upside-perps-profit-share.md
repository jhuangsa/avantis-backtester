# Upside Perps charge a share of profit, not a fee on size

ADR 0016 charges every trade a fee on its size: a fraction of notional at the open and at the close, paid on a win or a loss. Veranta's Upside Perps charge no open, close or borrow fee. A losing close pays nothing. A winning close pays a share of its profit, and the share falls as the close's ROI rises. Sources: docs.veranta.xyz/trading/fees and docs.veranta.xyz/trading/upside-perps/profit-sharing.

Terms:

- **ROI** (return on investment): the profit of a close over the collateral of the part closed, as a fraction. 2.5 is 250%.
- **Tier**: a pair (`max_roi`, `share`). A close whose ROI is at most `max_roi` pays `share` of its profit.
- **Profit share**: the fee a winning close pays under the tiers.

## Decision

`Costs` gains a last field, `profit_share`, a list of tiers. Empty means no profit share, and the size fees of ADR 0016 work as before, bit for bit. `Fees`, which a position carries, gains the same list, and `backtest` copies it from the market's `Costs`.

The default Upside Perps tiers:

| `max_roi` | `share` |
|---|---|
| 2.5 (250%) | 0.25 |
| 5.0 (500%) | 0.20 |
| 15.0 (1,500%) | 0.10 |
| 25.0 (2,500%) | 0.05 |

The examples define them once, as `UPSIDE` in `examples/timeframes.py`.

The rules:

- The profit share is charged only in `Portfolio::close`, on the part closed. Gross is the price result of that part: direction × (exit − entry) × size closed. ROI is gross over the position's collateral times the fraction closed, taken before the collateral shrinks.
- Gross of 0 or less pays 0.
- Otherwise the rate is the share of the first tier with ROI ≤ `max_roi` + 1e-9. The small tolerance keeps a close exactly on a boundary, such as 500%, in that tier despite float error. A ROI above every tier pays the last tier's share.
- The fee is rate × gross: one rate on the whole profit, not a rate per slice.
- ROI is measured before holding costs (funding).
- `Closed.fees` and `Trade.fees` include the profit share, so gross = `result` + `fees` + `holding_costs` still holds for every trade.
- Unrealized profit, which equity and the hard stop use, subtracts the profit share the whole position would owe if it closed at its mark now. With no tiers, unrealized profit is unchanged.
- `check_costs`, and the Python `Costs` constructor, raise an error when tiers sit beside a non-zero `open_fee` or `close_fee`, when a `max_roi` is not positive or not strictly increasing, when a share is outside [0, 1], or when a value is not finite.

## Open questions and how they were decided

1. **Whole profit or slices?** One rate on the whole profit. The docs' example (+2% at 250x is 500% ROI, and the trader keeps 80%) reads that way.
2. **Tier boundaries.** A boundary is inclusive: exactly 500% pays 20%. From 1,500% up to 2,500% the last tier, 5%, applies. Above 2,500% the last tier's share applies too.
3. **ROI and funding.** ROI is measured before funding. Funding passes between traders and stays in the holding-cost columns.
4. **Partial closes.** Each part is priced on its own ROI, on its own share of the collateral.
5. **Pending fee.** Unrealized profit subtracts the profit share owed at the mark, so equity does not count money the venue would take.
6. **Scope.** An option in `Costs`, beside the size fees. A market uses one model or the other, never both.
7. **Staking.** No knob. A staker passes each share × 0.75.

## Consequences

- Liquidation is always a loss, so it pays no profit share. A hard stop that closes at a profit pays it.
- The engine does not cap ROI at 2,500%, the venue's maximum take profit. A strategy that wants that cap sets its take profit.
- The liquidation price does not move: it depends on loss, and a loss pays no profit share.
- Examples keep their size fees, since they model other venues. A caller on Upside Perps passes `UPSIDE` or its own tiers.

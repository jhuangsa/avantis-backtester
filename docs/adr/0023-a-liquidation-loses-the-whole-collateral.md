# A liquidation loses the whole collateral

ADR 0008 liquidates a position once its loss reaches 85% of its collateral, and charged the usual close fee. The trader kept the other 15%. Veranta does not return it: a liquidation fee takes what the loss leaves. Veranta's trade history shows this on every liquidation we have: the payout is $0, and the fee is the rest of the margin (15% of it in the v2 history; 15% or 30% in the old one, where the loss had passed 85% first).

## Decision

On a liquidation, `Portfolio::close` charges a liquidation fee in place of the close fee: the collateral plus the gross result, less holding costs, and never below 0. The trader gets nothing back.

- The trade's `result` is minus the collateral, minus the open fee paid at the open.
- `fees` holds the open fee, the liquidation fee and any profit share (0: a liquidation is a loss, ADR 0019).
- The liquidation price, the 80% stop cap and the hard stop do not change.

## Consequences

- A liquidation costs about 18% more than before (1 / 0.85). Results with no liquidation do not change.
- A stop always fills before liquidation (ADR 0008), so only a gap past both, or a close past the liquidation price (ADR 0020), pays the fee.

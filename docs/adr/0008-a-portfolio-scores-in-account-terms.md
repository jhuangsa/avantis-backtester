# A portfolio scores in account terms and may hold several instruments

The C++ engine adds a portfolio: a balance and the positions it pays for. Sizing from risk, leverage, and liquidation have no meaning in price percent, so a portfolio's results are in account money. The price-percent score of a hypothesis stays as it was.

A portfolio holds at most one position per instrument, and positions in several instruments at once. This relaxes "one series" for the portfolio only; each hypothesis still reads one series.

Sizing risks a fixed percent of the current balance at the stop. A trade is skipped when its collateral exceeds the free cash. A position is liquidated once its loss reaches 85% of its collateral, the Veranta rule (docs.veranta.xyz/trading/liquidations and limitations-and-safeguards; the health-ratio wording on the first page reads as 85% health, but the stop cap below only fits an 85% loss). The stop fills before liquidation on a bar that reaches both, since Veranta caps a stop at an 80% loss of collateral; a trade whose stop would lose more is skipped. A bar that gaps past both fills at the liquidation price. When the balance plus unrealized profit and loss falls a set percent below the starting balance (30% by default), every position closes and the portfolio stops trading for good.

Fees, leverage, leverage caps, and partial exits belong to the strategy, not the portfolio.

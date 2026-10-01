# Risk may scale with leverage

By default a C++ trade is sized from `risk_per_trade` and the stop, so leverage changes only the margin a trade locks, never its size or its result (ADR 0008). Day traders expect 5x leverage to make 5 times as much.

`PortfolioSettings::scale_risk_with_leverage`, false by default, makes a trade risk `risk_per_trade × leverage` of the balance. At 1% and 5x, a stop fill loses 5%.

Consequences:

- With the setting on, results scale with leverage, and so do drawdowns. The hard stop and the liquidation rules are unchanged.
- The 80% stop cap still applies, so a high leverage refuses trades whose stop sits past liquidation.
- Off, every earlier result is unchanged.

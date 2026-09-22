# avantis-backtester

The glossary is CONTEXT.md. The decisions are docs/adr/. The formulas are docs/research/indicators.md.

Line: an operand that is a series — an indicator output, a price, or a level.
Operand: one side of a comparison.
Rule: a cross, an above, a below, or an all or any of those.
Hypothesis: a named side, its entries, a take profit, a stop loss, and any rule exit or time exit.
Backtest: one hypothesis scored on one caller-supplied series, yielding trades, causes, ending stake, parameters, and bar size.
Grid: that backtest repeated over the listed numeric parameters; every cell is returned and none is a winner.

Guards: next-open fill; no trade on an undefined operand; a cross is two bars; a swing publishes at confirmation; every result names the bar size; the caller chooses the slice; the score is price percent.

The engine does not read the fill files.

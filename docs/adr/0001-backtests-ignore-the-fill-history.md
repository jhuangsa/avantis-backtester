# Backtests ignore the fill history

The account's BitMEX executions sit in this repo, and the orders in them finish in about a second. A backtest does not read them. It scores a hypothesis on one bar series the caller supplies, and the result is a price return, not a claim that the account took the trade. A bar cannot see the burst, so using the fills as the price path would answer a different question.

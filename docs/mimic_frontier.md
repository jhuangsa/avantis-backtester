# Mimic frontier

`examples/mimic_frontier.py` finds three versions of a wallet's mimic strategy: **careful**, **balanced** and **bold**. It keeps the 100 strategies whose PnL is closest to the wallet's, draws their efficient frontier, and picks the three off that frontier by risk.

Drawdown limits pick nothing here. `examples/mimic_drawdown.py` picks by drawdown limits; this page does not.

## Terms

- **Mimic**: the library strategy `mimic`, tuned by `examples/mimic.py` to follow one wallet. Its knobs are in `KNOBS` in `mimic.py`.
- **Fit**: the mimic settings `mimic.py` saved for a wallet, in `data/candles/mimic_<address>.json`.
- **Realized PnL**: the profit or loss of closed trades, after fees and funding.
- **Total PnL**: realized PnL plus the unrealized PnL of positions still open.
- **Return**: total PnL over the peak of the trader's own money in positions (`wallet_returns.ledger`). See `docs/wallet_returns.md`.
- **Risk**: the standard deviation of daily returns, read at each UTC midnight, times √365.
- **Gap**: how far a strategy's realized PnL is from the wallet's. The strategy's PnL is first scaled by the best factor k ≥ 0, since the mimic trades a $10,000 account and the wallet does not. The gap is the RMS of (k × mimic − wallet) over the hourly grid, divided by the RMS of the wallet's. 0 is the same shape; 1 is no closer than never trading.
- **Efficient frontier**: the strategies that no other kept strategy beats. A strategy is beaten when another has lower or equal risk and a higher return.

## The method

1. **Search.** Start from the fit. Change two knobs at a time, every pair, and keep any setting with a smaller gap. Do up to 4 passes, and stop early when a pass gains 1e-6 or less. Do the same from 3 random starts, drawn with seed 0. Every backtest goes into one pool.
2. **Rules on the pool.** A strategy with fewer than 20 trades is never kept. Strategies that make the same trades (same return, risk and trade count) are kept once.
3. **Keep 100.** The 100 strategies with the smallest gap.
4. **Frontier.** Sort the 100 by risk; a strategy is on the frontier when its return is higher than every strategy with lower or equal risk.
5. **Pick.** Take the 10th, 50th and 90th percentiles of the 100 strategies' risk. At each, the pick is the frontier strategy with the highest return at or under that risk:
   - **careful**: the 10th percentile,
   - **balanced**: the 50th,
   - **bold**: the 90th.

When few strategies are on the frontier, two picks can be the same strategy.

## Default settings

The defaults are the search settings of AI Copy v5 (search configuration 5):

| Setting | Default | Option |
|---|---|---|
| Strategies kept | 100 | `--count` |
| Passes per start | 4 | `--passes` |
| Random starts | 3, seed 0 | `--restarts` |
| Fewest trades | 20 | `--min-trades` |
| Leverage cap | the lowest maximum leverage of the fit's markets on Veranta, and 100, rounded down to 0.5× | `--bound` / `--no-bound` |
| Account | $10,000; risk per trade 0.2% × leverage; hard stop 1.0 | as in `mimic.py` |
| Fees | 0.025% of notional at each open and close, plus the market's Veranta spread | as in `mimic.py` |

With the leverage cap, leverage knob values above the cap are dropped before the random starts are drawn, and the fit's leverage starts at the cap when it is higher.

AI Copy v5 picks its three styles by drawdown limits (10%, 15%, 20%) and leverage lifts. This method keeps the v5 search settings above and picks off the frontier instead. It does not take from v5: its fit window (at most 365 days, past outages plus 48 days), its per-market costs, the catalog backtest, the gates and the live-money checks.

`--by rmse` is the older method: every setting whose RMSE to the wallet's return is at most `--rmse` (default twice the fit's), from climbs for return minus λ × risk (`--lambdas`), then the highest return in each of 100 risk bins.

## The wallet's PnL

The gap compares against the wallet's realized PnL, and the page shows its total PnL and return. For Avantis and Veranta wallets these come from `examples/wallet_returns.py`.

Avantis is now Veranta. Trades since its v2 contracts (mid-August 2026) are only in the v2 trade history. `examples/veranta_history.py` downloads both histories into the cache `mimic.py` reads, and keeps the v2 rows. The ledger then rebuilds each book (one market and side) from every open, increase and close: its size in coins and its cost. Unrealized PnL is each book at the hour's price. This puts each increase at its own time and price.

Veranta's history leaves out some costs. With `--pages`, `veranta_history.py` reads the wallet's USDC transfers from saved BaseScan pages and charges them:

- **Fees on opens and increases**: the USDC paid in each trade's transaction, less its margin. The history has no fee for an increase.
- **Fees before v2**: the USDC paid in before the v2 history, less the margin of the old closes, spread over them by margin. Payouts beyond the old closes' (margin taken back) are credited.
- **Margin used up**: margin the history leaves open, less the margin open now, charged evenly from the oldest open position to now.

It then checks the rebuilt total PnL against the chain: the USDC received from Veranta less the USDC paid to it, plus the margin open now and its price PnL. The check passes within 1%.

## How to run

From the repository root, for a Veranta wallet:

```bash
mkdir -p data/candles/basescan_<address>
for p in $(seq 1 <pages>); do curl -s "https://basescan.org/tokentxns?a=<address>&ps=100&p=$p" -o data/candles/basescan_<address>/p$p.html; sleep 0.5; done
python3 examples/veranta_history.py --pages data/candles/basescan_<address> <address>
python3 examples/mimic.py <address>
python3 examples/mimic_frontier.py data/candles/mimic_<address>.json
```

`<pages>` is the page count BaseScan shows for the wallet, at 100 transfers per page. For an Avantis wallet from before v2, skip the first three lines.

It writes `data/candles/mimic_<address>_frontier_pnl.json`: the 100 strategies, the frontier and the three picks. And `examples/mimic_frontier_<first 6 hex>_pnl.html`: the frontier, each pick's PnL beside the wallet's, and each pick's return. `--replay` redraws the page from the saved file without searching.

Each run takes a few minutes.

## Limits

- The margin of open positions is estimated: each book's cost over its average leverage. The history leaves out margin moved in and out of open positions.
- A wallet with little own capital has a return curve that swings hard, so no strategy follows its return closely. The gap uses PnL in dollars, so it is not affected.
- The search covers the fit's whole window. There is no test period.

## Tests

`tests/test_mimic_frontier.py` (the gap, the frontier and the picks), `tests/test_veranta_history.py` (the download, the costs and the chain check) and `tests/test_wallet_returns.py` (the book rebuild).

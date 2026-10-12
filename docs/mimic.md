# Mimic: copy a wallet's results with a strategy

`examples/mimic.py` takes a wallet address and finds a strategy whose return on peak own capital looks like the wallet's. It gives you the strategy's settings and its Sharpe ratio, maximum drawdown and return. It works on Avantis and on Hyperliquid.

## Run it

From the repository root, for an Avantis wallet:

```bash
python3 examples/mimic.py 0xFFB7eF358cEe48DaFE15B63A625DFF9eA4E2268F
```

For a Hyperliquid wallet:

```bash
python3 examples/mimic.py --venue hyperliquid <address>
```

It takes about 3 minutes on an 8-core laptop. To check a saved result:

```bash
python3 examples/mimic.py --replay data/candles/mimic_<address>.json
```

To draw one result as a web page:

```bash
python3 examples/mimic_chart.py data/candles/mimic_<address>.json
```

To put every saved Hyperliquid result on one page, `examples/mimic_hl_all.html`:

```bash
python3 examples/mimic_hl_all.py
```

Options: `--top` sets how many markets to use (default 8), `--restarts` the random starting points (default 4), `--passes` the most passes per start (default 6), `--workers` the backtest threads (default one per core). For an Avantis wallet's account scores, `--fees real` (default) charges its real closing fees and `--fees flat` a fixed 0.025% of notional at each open and close; `--funding` (default) charges its real funding and `--no-funding` leaves it out. The return the fit uses always has the real fees and funding.

You need the C++ module built (see `README.md`) and the `pycryptodome` package.

## How it works

### 1. Get the wallet's trades

The tool first writes the address in its standard mixed-case form (the trade services only answer to that form).

On Avantis it downloads every closed trade from the public trade history. The history covers the wallet's whole life.

On Hyperliquid it downloads the wallet's perp fills. A fill is one execution: a price, a size and a side. Spot fills are dropped. The curve covers the last 200 days, because the candle service serves about that much. Fills refresh once an hour. The tool also downloads the wallet's ledger (deposits, withdrawals and transfers) and its account snapshots (the account's value at points in time, as Hyperliquid reports it). Section 3 says what they are for.

All of it is saved in `data/candles/`, so it is downloaded only once.

### 2. Build the wallet's profit

The first step is the wallet's dollar profit at the end of each hour: its PnL. It is marked to market: at each hour, every open position is valued at that hour's closing price, so a loss shows while it is open, not only when it closes.

On Avantis, each close adds its realized profit. Each open order adds its remaining size times its price change since the open, times its leverage. A market with no candles counts realized profit only.

On Hyperliquid, each fill pays its price in cash and each open position is worth its size times the hour's price. Positions held before the window starts do not count.

Fills in one millisecond come back in no set order. The tool puts them in position order, so each fill starts where the last one ended. A few fills never come back from the service. The position they moved is still known from the next fill, so the tool trades the gap at the next fill's price, with no gain or loss. `gap_share` is the gaps' notional over all traded notional (notional is a position's size times its price). Above 1% the tool prints a warning: the curve may be unreliable.

The mimic pays a fee of 0.025% of notional at each open and each close, 0.05% per round trip, with no funding. In the return the fit uses, a wallet pays its real costs: on Avantis each close's closing fee and funding, from the trade history (the history has no opening fee, so none is charged); on Hyperliquid each fill's fee and the funding payments. In the account scores, a Hyperliquid wallet pays the mimic's fixed fee, and an Avantis wallet pays what `--fees` and `--funding` choose. The choice is saved under `wallet_costs`.

### 3. Measure the profit two ways

A dollar PnL is not a return until you divide it by something. The tool divides it two ways and scores both. The first is the one the fit uses.

#### Return on peak own capital

This is the measure from `examples/wallet_returns.py`; `docs/wallet_returns.md` explains it in full. At each hour:

- **P** is the total PnL so far, realized plus unrealized, after fees and funding.
- **Own money** is the margin in open positions less the PnL realized so far: the trader's own money in positions.
- **Capital K** is the most own money there has ever been, up to that hour.
- **The return ρ** is P / K.

Nothing is compounded. Deposits, withdrawals and idle cash do not count. Before the wallet's first open, ρ is empty.

The mimic is measured the same way. Each trade of its backtest becomes one Avantis-style close: its margin is size times entry price over leverage, its gross profit is its result plus its fees and holding costs. A position still open at the end is marked at each hour's close and never realized. The wallet and the mimic share one hourly grid. The 1-hour closes value both.

Account size does not change ρ: twice every margin gives the same ρ. Leverage does: at 10 times leverage the same price moves give 10 times the return of 1 times. So leverage is one of the settings the fit searches (section 5).

Two limits are accepted:

- **Hyperliquid inflation.** Hyperliquid keeps no margin history, so the margin is estimated from the wallet's current leverage setting. When that estimate is too low, K is too low and ρ too high. On 4 of the 5 wallets tested, the open loss reached 1.25 to 3.86 times the estimated margin. The fit uses ρ as it is. The run prints the check "U >= -margin open" so you can see it.
- **Early jumpiness.** ρ moves a lot while K is small, in the first hours of a history. Every hour where the wallet's ρ is defined still has the same weight.

#### Account return

The second measure divides each hour's PnL by the account's value at the hour before, then compounds from $10,000. What the account is depends on the venue:

- **Hyperliquid**: the wallet's whole account, perp plus spot. It starts at the account snapshot nearest the window start, then grows by the PnL and by the money that enters or leaves from outside, from the ledger: deposits, withdrawals, transfers to or from other addresses, vault moves. Moves between the wallet's own perp and spot accounts are internal and count for nothing. A flow counts from the hour it lands in. The account is never less than the open notional divided by 50, the most an account can hold. This is a time-weighted return: a deposit or a withdrawal moves the account, not the return.
- **Avantis**: $10,000 at the wallet's busiest moment, when the margin in open orders peaks. There is no account history on Avantis.
- **Mimic**: its own $10,000 backtest account.

On Hyperliquid, `account_drift` checks the computed account against Hyperliquid's own snapshots: it is the largest gap between a snapshot inside the window and the account computed at that time, as a share of the account. The two can differ because of spot token prices, funding, and flows the ledger does not report. The tool warns above 10%, and `mimic_hl_all.py` marks a wallet above 25%. When the ledger cannot be fetched, the account restarts from each snapshot instead and `account_drift` is empty.

The account return exists because of what the return on own capital leaves out: how much of the whole account was at risk. The tool once scored a fixed account only, the perp value at the start. A wallet that withdraws its profits or tops up after a loss then looked blown: one wallet made about $750,000 yet showed −100%, because its PnL dipped below a starting value it had long since withdrawn.

### 4. Get price bars

The tool picks the markets the wallet traded most, `--top` of them (default 8). On Avantis it loads 15-minute, 1-hour and 4-hour bars from the wallet's first trade to its last. On Hyperliquid it loads 1-hour and 4-hour bars for the 200 days. A market with no candles is dropped. The tool prints the covered share: how much of the wallet's trading is in the kept markets, so how much the mimic can copy at best. The bars are saved in `data/candles/`, so they are loaded only once.

### 5. The strategy being tuned: `mimic`

`mimic` is a C++ strategy (`cpp/include/avbt/library/mimic.hpp`). It runs on all kept markets in one account. It has five filters, and each one can be switched on or off:

| Filter | Lets a trade through when |
|---|---|
| RSI | RSI is low (for a long) or high (for a short) |
| Trend | the price is above or below its average, following or fading the trend |
| Move | the price has just moved by at least a set amount, following or fading the move |
| Breakout | the price breaks its recent high or low, following or fading the break |
| Hours | the bar starts within set hours of the day (UTC); the range may wrap past midnight, so 18 to 12 means 18:00 to 12:00 |

A trade opens only when every filter that is on agrees. Each trade has a stop and a take profit, both measured in ATRs (the average size of a bar's move) and both above 0. A trade that has hit neither closes after a set number of bars.

Other settings: the bar length, longs or shorts or both, and the leverage, from 1 to 100. The leverage search starts at the wallet's median leverage, which is also added to the values tried. The median comes from the wallet's closes on Avantis, and from its leverage settings on Hyperliquid (1 when it has none).

Risk is fixed: each trade risks 0.2% times its leverage of the $10,000 account. It is not searched, because account size does not change the return on own capital. It still shapes the mimic's account scores.

All the rules of the backtester still hold. For example, a trade fills at the next bar's open, and a filter never trades on a value it can't compute yet.

### 6. Score a try

For each set of settings, the C++ engine runs a backtest. The tool builds the mimic's return on peak own capital from the backtest's trades, the same way as the wallet's, and compares the two once an hour. The score is the **MSE** (mean squared error): the average of the squared gaps between the two returns, over the hours where the wallet's return is defined. Before the mimic's first open its return counts as 0: it has made nothing yet. Lower is closer. A score of 0 would be a perfect copy.

### 7. Search, two settings at a time

There are 23 settings, far too many combinations to try them all. So the tool tunes them two at a time:

1. Start from one set of values.
2. Pick two settings. Try every pair of their values while the others stay fixed. Keep the best pair.
3. Do this for every pair of settings. That is one pass.
4. Repeat until a pass improves the MSE by less than 1%, or after 6 passes.

This kind of search can get stuck on a good answer that isn't the best one. So the tool runs it again from 4 random starting points and keeps the best result. The first start uses the wallet's usual side, long or short.

All the starting points run at the same time and share one set of backtest threads, one per CPU core, so every core stays busy until the end. The tool never repeats a backtest it has already run. A setting that belongs to a filter that is off changes nothing, so those tries are skipped.

The fit uses the whole history on purpose. There is no separate test period, so the result describes the past and does not predict the future.

### 8. Score wallet and mimic by one rule

On peak own capital, each side gets its **final** return, its **peak** and its **lowest**, and the mimic its **RMSE**: the square root of the MSE, in return points.

The account curve of each side gets three numbers from the C++ `summary`, plus a trade count per side:

- **Sharpe ratio**: return per unit of risk. The curve is read at each UTC midnight. The daily returns' mean is divided by their sample standard deviation and multiplied by the square root of 365. It is minus infinity if the account ever hits 0, and empty under 30 days.
- **Maximum drawdown**: the largest fall from a peak, as a fraction of that peak.
- **Return**: the last value over the first, minus 1.
- **Trades**: positions opened and fully closed. A partial close is not a trade. A flip from long to short closes one trade and opens the next, which counts once it closes.

### 9. Save and report

The tool prints both score sets for both sides, own capital first, the account second, and the winning settings. Then it prints the wallet's return at the end, its capital, and the checks from `docs/wallet_returns.md` that fail. On Hyperliquid it always prints "U >= -margin open" and "12 return >= -100%", pass or fail.

It saves everything needed to rerun the result in `data/candles/mimic_<address>.json`, or `mimic_hl_<address>.json` on Hyperliquid: `"fit": "own"`, the venue, the strategy name, its settings (leverage among them), the markets, the covered share, the dates, the fees, the account settings, the engine version and the scores. The scores are under `wallet` and `mimic`, and each holds an `own` and an `account` set, with `trades` and the Hyperliquid checks (`gap_share`, `account_drift`, `net_flows`, `start_account`) beside them. The wallet's return curve is saved beside it, in `mimic_<address>_target.npz`, so a replay can score the RMSE.

`--replay` reruns this file with its saved fees and settings and should give the same numbers exactly. It warns when the engine version differs from the saved one. A file without `fit` was fitted on the old return on deployed capital: the replay warns, then reruns it and scores it on own capital. Its saved own scores show as empty, and its old account scores still show.

The same strategy name and settings also work with `avbt_cpp.Live` and `decide` for live trading.

## Example result

The Avantis wallet `0xFFB7…268F` opened 596 positions from November 2025 to June 2026, nearly all shorts. Its saved fit was made on the old return on deployed capital, before the fit moved to the return on peak own capital. Its numbers come once it is fitted again.

On the account:

| | Sharpe | Max drawdown | Return |
|---|---|---|---|
| Wallet ($10,000 at its busiest margin) | 1.84 | 34.6% | 60.3% |

The wallet's account drawdown is real. On 1 February 2026 it held 21 ZK shorts that were about 28% under water for a few hours. They closed in profit. An earlier version of this tool counted realized profit only, so that fall never showed. The marked-to-market curve shows it, but the MSE averages over every hour of seven months, so a few bad hours barely move the score.

## Know the limits

- **The same curve doesn't mean the same trading.** MSE scores only the curve. A few dozen trades can match a wallet that made hundreds.
- **The return on own capital ignores idle cash.** It shows what the trader's own money in positions made, not what an investor in the whole account earned. A wallet that trades $100 at a time with $1,000,000 idle can show a fine return and a flat account.
- **Hyperliquid margin is estimated.** It comes from the current leverage setting, so ρ can read too high. Check "U >= -margin open".
- **Early hours are jumpy.** While K is small, ρ swings, and those hours weigh as much as any other.
- **The mimic's opening fee shows at the close.** The engine reports a trade's open and close fees together, so the mimic's curve takes both at the close. The error is one fee for the length of each hold.
- **The account return rests on the ledger.** On Hyperliquid, `account_drift` says how far the computed account strays from Hyperliquid's snapshots. Spot token prices, funding and unreported flows all add to it. Treat the account scores of a wallet above 25% as unreliable.
- **MSE ignores short spikes.** A deep fall that lasts hours weighs almost nothing against months of curve. The wallet's drawdown can be far worse than the mimic's with a good score.
- **Overfit by design.** The settings are chosen to fit this history.
- **Only the top markets.** Trades in other markets count toward the wallet's curve but can't be copied. The covered share says how much.
- **Missing fills.** The Hyperliquid service drops a few fills. Their gaps trade at the next fill's price. Check `gap_share`.
- **The mimic pays a fixed 0.05% per round trip and no funding.** An Avantis wallet pays its real closing fees and funding by default, so a wallet that holds for days carries a cost the mimic does not. Funding shows at the close, not as it builds up.

## Files

| File | What it does |
|---|---|
| `cpp/include/avbt/library/mimic.hpp` | The `mimic` strategy |
| `cpp/tests/test_mimic.cpp` | Its C++ test |
| `examples/mimic.py` | Downloads, builds the curves, fits, saves and replays |
| `examples/wallet_returns.py` | `ledger()`: the return on peak own capital, for wallet and mimic |
| `examples/mimic_chart.py` | Draws a saved fit as a web page |
| `examples/mimic_hl_all.py` | Puts all saved Hyperliquid fits on one page |
| `tests/test_mimic.py` | Python tests: curves, fees, scores, the fit on own capital, fill order, caches |

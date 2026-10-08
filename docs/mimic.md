# Mimic: copy a wallet's results with a strategy

`examples/mimic.py` takes a wallet address and finds a strategy whose profit curve looks like the wallet's. It gives you the strategy's settings and its Sharpe ratio, maximum drawdown and return.

## Run it

From the repository root:

```bash
python3 examples/mimic.py 0xFFB7eF358cEe48DaFE15B63A625DFF9eA4E2268F
```

It takes about 5 minutes on a laptop. To check a saved result:

```bash
python3 examples/mimic.py --replay data/candles/mimic_<address>.json
```

To draw the result as a web page:

```bash
python3 examples/mimic_chart.py data/candles/mimic_<address>.json
```

You need the C++ module built (see `README.md`) and the `pycryptodome` package.

## How it works

### 1. Get the wallet's trades

The tool first writes the address in its standard mixed-case form (the trade service only answers to that form). It then downloads every closed trade from the public Avantis trade history and saves them in `data/candles/`, so it downloads them only once.

### 2. Build the wallet's profit curve

The wallet's trades are resized as if the account held $10,000 at its busiest moment. This is the same method `veranta_top5.py` uses. Each closed trade adds its profit, after a small fee, to the account. The result is a curve: the account's value over time.

### 3. Get price bars

The tool picks the 8 markets the wallet traded most. It loads 15-minute, 1-hour and 4-hour price bars for those markets, covering the wallet's first trade to its last. The bars are saved in `data/candles/`, so they are loaded only once.

### 4. The strategy being tuned: `mimic`

`mimic` is a C++ strategy (`cpp/include/avbt/library/mimic.hpp`). It runs on all 8 markets in one account. It has five filters, and each one can be switched on or off:

| Filter | Lets a trade through when |
|---|---|
| RSI | RSI is low (for a long) or high (for a short) |
| Trend | the price is above or below its average, following or fading the trend |
| Move | the price has just moved by at least a set amount, following or fading the move |
| Breakout | the price breaks its recent high or low, following or fading the break |
| Hours | the bar starts within set hours of the day (UTC) |

A trade opens only when every filter that is on agrees. Each trade has a stop and a take profit, both measured in ATRs (the average size of a bar's move). A trade that has hit neither closes after a set number of bars.

Other settings: the bar length, longs or shorts or both, leverage, and how much of the account each trade risks.

All the rules of the backtester still hold. For example, a trade fills at the next bar's open, and a filter never trades on a value it can't compute yet.

### 5. Score a try

For each set of settings, the C++ engine runs a backtest. The tool then compares the backtest's curve with the wallet's curve once an hour and computes the **MSE** (mean squared error): the average of the squared gaps between the two curves. Lower is closer. A score of 0 would be a perfect copy.

### 6. Search, two settings at a time

There are 24 settings, far too many combinations to try them all. So the tool tunes them two at a time:

1. Start from one set of values.
2. Pick two settings. Try every pair of their values while the others stay fixed. Keep the best pair.
3. Do this for every pair of settings. That is one pass.
4. Repeat until a pass improves the MSE by less than 1%.

This kind of search can get stuck on a good answer that isn't the best one. So the tool runs it again from 4 random starting points (8 for the example below) and keeps the best result. The first start uses the wallet's usual side, long or short.

Backtests run in parallel, and the tool never repeats a backtest it has already run. A setting that belongs to a filter that is off changes nothing, so those tries are skipped.

The fit uses the whole history on purpose. There is no separate test period, so the result describes the past and does not predict the future.

### 7. Save and report

The tool prints the wallet's and the strategy's Sharpe ratio, maximum drawdown, return and trade count, and the winning settings. It saves everything needed to rerun the result in `data/candles/mimic_<address>.json`: the strategy name, its settings, the markets, the dates, the fees, the account settings, the engine version and the scores. `--replay` reruns this file and should give the same numbers exactly.

The same strategy name and settings also work with `avbt_cpp.Live` and `decide` for live trading.

## Example result

The wallet `0xFFB7…268F` made 602 closed trades from November 2025 to June 2026.

| | Sharpe | Max drawdown | Return | Trades |
|---|---|---|---|---|
| Wallet | 3.10 | 11.3% | 60.7% | 602 |
| Mimic | 2.70 | 12.4% | 58.0% | 22 |

The curves are on average about 5% of the account apart (MSE 0.00274). The chart is in `examples/mimic_ffb7ef.html`.

The best match uses 15-minute bars and two filters. It fades 2-bar moves of 1% or more when the 21-bar RSI is below 10 or above 90. The stop is 2 ATRs and the take profit 8 ATRs. A trade closes after 48 bars at most. Leverage is 2, and each trade risks 2% of the account.

## Know the limits

- **The same curve doesn't mean the same trading.** MSE scores only the curve. Here, 22 large trades matched a wallet that made 602 smaller ones. A start that found 440 trades matched less well (MSE 0.0084).
- **Overfit by design.** The settings are chosen to fit this history.
- **Only 8 markets.** Trades in other markets count toward the wallet's curve but can't be copied.
- **Fees are a fixed 0.05% at open and at close**, the same as in `sharpe_hunt`.

## Files

| File | What it does |
|---|---|
| `cpp/include/avbt/library/mimic.hpp` | The `mimic` strategy |
| `cpp/tests/test_mimic.cpp` | Its C++ test |
| `examples/mimic.py` | Downloads, fits, saves and replays |
| `examples/mimic_chart.py` | Draws a saved fit as a web page |
| `tests/test_mimic.py` | Python tests: address case, skipping repeat backtests, curve alignment |

# Explain a mimic fit to a new trader

Give this prompt to an agent working in this repository, together with the path to a JSON file that `examples/mimic.py` saved.

---

You are a friendly trading coach. Your student is a new day trader. They know what a long, a short, a stop loss and a take profit are, and not much more. Explain the strategy in the JSON file below in plain English, as a story they can follow and trade by hand.

**File:** `<path to data/candles/mimic_<address>.json>`

## What to read

1. **The JSON file.** `params` holds the strategy's settings. `markets`, `start` and `end` say what it traded and when. `wallet` and `mimic` hold the scores of the real wallet and of the copy. `mse` is how far apart their profit curves are.
2. **`cpp/include/avbt/library/mimic.hpp`.** It has the rules. Use it if this prompt and the code disagree. The code is right.

Do not read any other file. Do not run anything.

## How to turn params into rules

First drop every filter whose switch (`rsi_on`, `trend_on`, `move_on`, `break_on`, `hours_on`) is `false`. Its numbers are still in the file, but they do nothing. Do not mention them, except to say which filters are off.

Then translate each filter that is on. All of them must be true at once before the strategy opens a trade.

| Param values | Long when | Short when |
|---|---|---|
| `rsi_level: L` | RSI(`rsi_period`) < L | RSI > 100 − L |
| `trend_dir: 1` | close > SMA(`trend_period`) | close < SMA |
| `trend_dir: -1` | close < SMA | close > SMA |
| `move_dir: 1` | price rose at least `move_size` over the last `move_lag` bars | price fell at least `move_size` |
| `move_dir: -1` | price fell at least `move_size` | price rose at least `move_size` |
| `break_dir: 1` | close > highest high of the last `break_period` bars | close < lowest low |
| `break_dir: -1` | close < lowest low | close > highest high |
| `hours_on` | the bar opens at a UTC hour from `hour_from` up to, not including, `hour_to` | same |

`side` is `1` for longs only, `-1` for shorts only, and `0` for both.

### Other rules

- **Timing.** The strategy checks the rules when a bar on `timeframe` closes. It enters at the next bar's open.
- **Stop loss.** It sits `stop_atrs` ATRs from the entry. ATR(`atr_period`) is the average size of one bar.
- **Take profit.** It sits `tp_atrs` ATRs from the entry.
- **Time exit.** The strategy closes the trade after `hold` bars if neither the stop nor the take profit was hit. Convert this to hours or days.
- **Size.** If the stop is hit, the trade loses `risk` of the account. For example, 0.01 is 1%. Leverage is `leverage`.
- **One trade at a time.** It holds at most one trade per market, and it opens no new trade there while one is open.

## How to explain

Use this order and these headings:

1. **The idea in one sentence.** For example: "It waits for a sharp drop in an uptrend, then buys the bounce."
2. **Where and when.** The markets, the bar size, and the dates.
3. **When to get in.** The long and short rules as a checklist. Explain each indicator the first time you use it, in one sentence, with no formulas. Use the real numbers from the file.
4. **When to get out.** The stop, the take profit and the time limit. Put them in words a trader can picture, such as "about 3 average candles below your entry".
5. **How much to bet.** The risk per trade, shown in dollars on a $10,000 account, and the leverage.
6. **A made-up example trade.** Walk through one trade from setup to exit, with simple round prices.
7. **Did it match the wallet?** Put the `wallet` and `mimic` scores side by side: return, max drawdown, Sharpe and trades. Define each one in a few words. Give `mse` as an RMSE in plain words: "on a typical hour, the copy's profit was about X% of the account away from the wallet's". Say plainly if the match is poor.
8. **Before you trade it.** Three short warnings:
   - The fit was tuned on the whole history, so it has not been tested on new data.
   - It is a guess at the wallet's rules, not their real rules.
   - Fees, slippage and your own reaction time will make results worse.

## Style

- Talk to the student as "you". Write short sentences and use everyday words.
- Define every trading term the first time you use it.
- Use the file's numbers. Round them sensibly.
- Do not invent rules that are not in the file or the code.
- Do not give investment advice. Describe the strategy and do not recommend trading it.
- Keep it under one page.

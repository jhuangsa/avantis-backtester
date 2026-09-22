# When an indicator backtest lies

The engine will test rules of the form "buy when an indicator crosses a line or a threshold" on OHLCV bars. It will not ingest this trader's fills. His XBTUSD orders finish in a median 1.3 seconds, 89% finish within a minute, and the position those bursts leave is what persists across sessions (`docs/findings.md`). A bar test that ignores the failures below can print a profit the rule never had.

## 1. Lookahead

**Failure.** The signal uses the current bar's close, and the fill is booked at that same close. Or a later bar confirms a level, and the trade is dated as if the level had existed earlier.

**Mechanism.** On a historical bar the close is the last price. A decision that needs that close cannot also be filled at it. TradingView's broker emulator waits one tick for that reason: with the default `process_orders_on_close = false`, "the earliest point at which the emulator can fill orders created on a bar's closing tick is on the next tick, at the open of the following bar," because "creating and filling an order on the same tick is typically unrealistic." Backtrader's cheat-on-close does the opposite on purpose. `set_coc(True)` "enables matching a Market order to the closing price of the bar in which the order was issued. This is actually cheating, because the bar is closed and any order should first be matched against the prices in the next bar."

The same leak appears one timeframe up. TradingView: `request.security()` with `lookahead = barmerge.lookahead_on`, "to fetch prices without offsetting the series by `[1]`, ... will return data from the future on historical bars, which is dangerously misleading." The non-repainting form is `expression[1]` together with `lookahead_on`. Neither piece works alone.

**Guard.** Build the signal only from bars that have already closed. Fill at the next bar's open, never at the signal bar's close. Do not implement cheat-on-close or fill-on-close. A higher-timeframe value may enter the signal only after that higher-timeframe bar has closed.

Sources: [TradingView, Strategies](https://www.tradingview.com/pine-script-docs/concepts/strategies/) (`process_orders_on_close`); [TradingView, Repainting](https://www.tradingview.com/pine-script-docs/concepts/repainting/) (future leak); [Backtrader, Broker](https://www.backtrader.com/docu/broker/) (`coc`).

## 2. Indicator warmup

**Failure.** Bars before EMA, MACD, or RSI is defined are traded, often because a missing value was filled with zero and then "crossed."

**Mechanism.** TA-Lib writes the lookback, "a required number of observations before an output is generated," as `NaN`. The lookback can exceed the period argument. `RSI(timeperiod=14)` "needs 15 price observations, because the first RSI value is based on 14 consecutive price changes," so the first 14 outputs are `NaN`. `TA_EMA_Lookback` is `period - 1` plus the unstable-period setting, which defaults to 0, so an EMA of length `n` is `NaN` for `n - 1` bars and the first number is the SMA of the first `n` prices (ta-lib.org: "Seed: EMA = SMA of first `period` bars"). `TA_MACD_Lookback` is `TA_EMA_Lookback(slow) + TA_EMA_Lookback(signal)`. For 12/26/9 that is 33, and the Python wrapper leaves `NaN` in the MACD line, the signal, and the histogram until then. EMA is also marked with an initial unstable period: the same bar can change if a longer history is fed in, because the recursion has not forgotten its seed.

pandas-ta's native path (the default, `talib=False`) does not invent those early values either. Its EMA calculation sets `close[:length - 1] = NaN` and plants the SMA seed at index `length - 1`. Its RSI is an RMA of one-bar price changes, so the first `length` bars are `NaN` and the first reading is at index `length`. Its native MACD line starts at index `slow - 1` (25 with the default 26), but the signal and the histogram stay `NaN` until index `slow + signal - 2` (33). A rule that reads the histogram is still undefined on bars where the MACD line has already printed. A hole later in the input is not something to fill forward: the TA-Lib Python docs show an SMA with one interior `NaN` propagating `NaN` through the rest of the output.

**Guard.** A bar is ineligible until every series the rule reads is finite. Do not fill indicator `NaN` with zero, and do not forward-fill a hole in price before the indicator runs. For a MACD rule, "finite" includes the signal line when the rule uses it, not only the MACD line.

Sources: [ta-lib-python docs](https://github.com/TA-Lib/ta-lib-python/blob/master/docs/index.md) (lookback as `NaN`; RSI's 15 observations; interior `NaN`); [TA-Lib EMA](https://ta-lib.org/functions/ema.html) and [`TA_EMA_Lookback`](https://github.com/TA-Lib/ta-lib/blob/main/src/ta_func/ta_EMA.c); [`TA_MACD_Lookback`](https://github.com/TA-Lib/ta-lib/blob/main/src/ta_func/ta_MACD.c); [TA-Lib core API](https://ta-lib.org/api/) (recursive values change as history accumulates); pandas-ta native EMA, RMA, and MACD calculations in [pandas-ta-classic](https://github.com/xgboosted/pandas-ta-classic) (`ema.py`, `rma.py`, `macd.py`), which carry the library's documented warmup.

## 3. Crossover off-by-one

**Failure.** "Crosses above" is coded as `value > threshold`. That stays true on every later bar, so one cross becomes a signal per bar.

**Mechanism.** TradingView defines a cross as a two-bar event. `ta.crossover(source1, source2)` is true only when, "on the current bar, the value of `source1` is greater than the value of `source2`, and on the previous bar, the value of `source1` was less than or equal to the value of `source2`." Otherwise it is false. Equality on the previous bar still counts as not-yet-above. The signal is the bar that first becomes strictly greater, and it does not repeat while the condition remains true.

**Guard.** Fire only when the previous bar is finite and less than or equal to the line, and this bar is finite and strictly greater. The mirror, `ta.crossunder`, is previous greater-or-equal and current strictly less. One cross, one bar. A bar that is merely above the line is a state, not a signal.

Source: [Pine Script v6 reference, `ta.crossover()`](https://www.tradingview.com/pine-script-reference/v6/#fun_ta.crossover).

## 4. Support and resistance that repaints

**Failure.** A swing high is used as resistance on the bar it prints, or on an earlier bar. It was not a swing yet. Trading that touch is lookahead.

**Mechanism.** A confirmed swing high of strength N is the high at bar `t` that is strictly higher than the N highs before it and the N highs after it. The right-hand bars do not exist at `t`, so the level is not knowable then. TradingView's `ta.pivothigh(leftbars, rightbars)` "returns price of the pivot high point" and "`NaN`, if there was no pivot high point." The arguments are documented only as "Left strength" and "Right strength." The repainting page says a script that detects pivots with `ta.pivothigh(5, 5)` does so "after 5 bars have elapsed," then often draws the price back at `bar_index[5]`. That plot "will often cause unsuspecting traders looking at plots on historical bars to infer that when the pivot happens in realtime, the same plots will appear on the pivot when it occurs, as opposed to when it is detected."

**Guard.** The level may be read only from bar `t+N` onward, the first bar on which the N right-hand highs have closed and are all lower. Until that bar, the value is `NaN` and the level does not exist. Do not shift the signal back onto bar `t`. A touch of an unconfirmed high is not a signal.

Sources: [Pine Script v6 reference, `ta.pivothigh()`](https://www.tradingview.com/pine-script-reference/v6/#fun_ta.pivothigh); [TradingView, Repainting, "Plotting in the past"](https://www.tradingview.com/pine-script-docs/concepts/repainting/).

## 5. Bar size against this trader

**Failure.** A 1-minute or 1-hour cross is reported as evidence that he traded that print.

**Mechanism.** The median XBTUSD order is finished in 1.3 seconds, and 89% are finished within a minute. The position is what lasts: in 2021 it swings to about +80 million and -85 million USD, and about half of Bitcoin trading days end short (`docs/findings.md`). The engine has bars, not his fills, so it cannot ask whether the indicator was true at the second an order started. No extra citation is claimed for a "standard" day-trading bar.

A 1-minute test asks a different question: after a 1-minute close crossed, did the next minute's price move with the rule? Most of his orders are already over before that minute closes. The minute contains the burst and every other print in the minute. A 1-hour test asks the same question one hour at a time. Because the position persists across sessions, an hourly cross can be compared with the direction he was holding, but only as a coarse alignment of the book with the next hour. The hour of a fill is close to the hour he traded, which is as far as the clock in the findings goes. Neither bar says he traded the cross.

**Guard.** Stamp every result with the bar size and with the question that size can answer. Do not score a 1-minute or 1-hour run as a reconstruction of the 1.3-second orders.

Source for the 1.3 seconds, the one-minute share, and the position: `docs/findings.md`.

## 6. Overfit grids

**Failure.** EMA windows and MACD thresholds are swept on the whole 2018-2021 sample, and the best cell is reported as the rule.

**Mechanism.** Bailey, Borwein, Lopez de Prado, and Zhu: "high simulated performance is easily achievable after backtesting a relatively small number of alternative strategy configurations, a practice we denote 'backtest overfitting'. The higher the number of configurations tried, the greater is the probability that the backtest is overfit." Under memory effects, "backtest overfitting leads to negative expected returns out-of-sample, rather than zero performance." A later paper of theirs adds that "standard statistical techniques designed to prevent regression over-fitting, such as hold-out, tend to be unreliable and inaccurate in the context of investment backtests," so a split is not a license to keep searching on the held-out block.

A random row split makes the leak worse. scikit-learn provides time-ordered splits because other cross-validation methods "would lead to training on future data and evaluating on past data." `KFold` and `ShuffleSplit` assume independent, identically distributed rows and, on a series, produce "unreasonable correlation between training and testing instances." Neighboring bars of the same day would fall on both sides of a shuffled cut.

**Guard.** One time-ordered split of this single series, chosen before any cell is scored, not a random row split and not a grid scored on the whole sample. Fit windows and thresholds only on an earlier contiguous block. For this history, 5 March 2018 through 31 December 2020 is the natural train, because 2021 is when the book becomes large. Drop a gap of at least the label horizon between the blocks. scikit-learn's `gap` is "the number of samples to exclude from the end of each train set before the test set," so the last training label does not use a 2021 price. Freeze the rule. Score it once on 1 January 2021 through 31 December 2021. Print the number of configurations tried beside the figure. Do not pick a new winner on the hold-out. The hold-out figure is the result; the in-sample best is not.

Sources: Bailey, Borwein, Lopez de Prado, and Zhu, ["Pseudo-Mathematics and Financial Charlatanism"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659) (SSRN 2308659, abstract); the same authors, ["The Probability of Backtest Overfitting"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253) (SSRN 2326253, abstract, on hold-out); [scikit-learn, `TimeSeriesSplit`](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html) and the [time-series cross-validation note](https://scikit-learn.org/stable/modules/cross_validation.html#cross-validation-of-time-series-data).

## 7. Inverse accounting, and why not to use it yet

**Failure.** A price move is converted into Bitcoin and then compared with the wallet.

**Mechanism.** BitMEX's perpetual guide puts XBTUSD on the inverse row, not the quanto row. Quanto (their example is ETHUSD) pays `(ExitPrice - EntryPrice) * Multiplier` in XBT. For the inverse row, margin and PnL are in XBT, each contract is worth 1 USD of bitcoin, and "XBT PNL of 1 Contract" is `(1/EntryPrice - 1/ExitPrice) * $1`. The inverse-perpetual guide writes the same identity as number of contracts times the 1 USD multiplier times `(1/Entry Price - 1/Exit Price)`. The sign for a long is positive when the exit is above the entry. Their worked example: long 50,000 contracts from 10,000 to 11,000 is `50,000 * 1 * (1/10,000 - 1/11,000) = 0.4545 XBT`; the same size from 10,000 to 9,000 is `-0.5556 XBT`. A symmetric price move is not a symmetric Bitcoin move, and the formula is not defined without a contract count.

**Guard.** The first engine stays in price-space percent, `(exit - entry) / entry`, signed by the side of the cross. It does not model inverse PnL. It has no contract count, no partial fills, and no funding, so the BitMEX formula cannot be evaluated. A percent of price is the question the indicator test is actually asking. The spec is why that percent is not the Bitcoin in the findings.

Sources: [BitMEX, Perpetual Contracts Guide](https://www.bitmex.com/app/perpetualContractsGuide) (inverse versus quanto; XBT PnL of one XBTUSD contract); [BitMEX, Inverse Perpetual Contracts](https://www.bitmex.com/app/inversePerpetualsGuide) (formula and the 10,000 / 11,000 / 9,000 example).

## Checklist

On every run the engine enforces all six:

1. The signal uses only bars already closed, and the fill is the next bar's open. No same-bar close fill, no cheat-on-close, and no higher-timeframe value from a bar that has not closed.
2. No order on a bar where any series the rule reads is `NaN`, including EMA, RSI, and MACD warmup. Those `NaN`s are not filled with zero.
3. A cross is the two-bar event: the previous bar is on the other side or equal, and this bar is strictly through. A bar that is merely above the line is not a signal.
4. A swing of strength N is tradable only at bar `t+N`, after N lower bars have closed on each side. The level is not shifted back onto the extreme.
5. The report names the bar size and the question it can answer. A 1-minute or 1-hour result is not a test of the 1.3-second fills.
6. Parameters are chosen on one earlier contiguous slice, a gap is dropped, the later slice is scored once, and the trial count is printed. The score stays in price-space percent, not in XBT inverse PnL.

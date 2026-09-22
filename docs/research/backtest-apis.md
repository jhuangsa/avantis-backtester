# How a small backtester names a rule and a grid

This note is the API research for a later hypothesis tester. It is not a design to implement yet. The repo today is analysis only: `analysis/make_charts.py`, `analysis/requirements.txt` (`duckdb`, `matplotlib`), and `docs/findings.md`. There is no package, no tests, and no price bars. The only bytecode in the tree is `analysis/__pycache__/make_charts.cpython-313.pyc`, so the charts were produced on Python 3.13.

The BitMEX fill CSVs stay where they are. This stage does not ingest them. The engine, when it exists, must take an OHLCV bar series from the outside.

A colleague will hand over hypotheses in the form "buy when MACD crosses a line," then change the EMA window, then run that same rule on a grid of windows and thresholds. The stated requirements are object oriented code, an easy threshold, indicator hyperparameters that are easy to tune, and strategies that are easy to swap. A future `CLAUDE.md` router has to stay under 40 lines. That is a documentation constraint only. This note is the long form that router should point at. The router is not written here.

Primary pages used below: Backtrader's [concepts](https://www.backtrader.com/docu/concepts/), [strategy](https://www.backtrader.com/docu/strategy/), [indicator reference](https://www.backtrader.com/docu/indautoref/), [operating](https://www.backtrader.com/docu/operating/), [broker](https://www.backtrader.com/docu/broker/), [cerebro](https://www.backtrader.com/docu/cerebro/), [quickstart](https://www.backtrader.com/docu/quickstart/quickstart/), [order creation](https://www.backtrader.com/docu/order-creation-execution/order-creation-execution/), and the built-in [SMA crossover strategy](https://www.backtrader.com/docu/automated-bt-run/automated-bt-run/); vectorbt's [getting started](https://vectorbt.dev/), [indicator factory](https://vectorbt.dev/api/indicators/factory/), [built-in indicators](https://vectorbt.dev/api/indicators/basic/), [generic accessors](https://vectorbt.dev/api/generic/accessors/), and [portfolio](https://vectorbt.dev/api/portfolio/base/); NautilusTrader's [strategies](https://nautilustrader.io/docs/latest/concepts/strategies) page only. Where an older generated vectorbt page disagrees with the current reference, the current reference wins. That case is called out once. The Backtrader blog posts on orders (2015) and optimization (2016) match the doc pages on the two shapes that matter, so nothing from those posts was kept over a doc.

## What the account forces on any bar test

Account `aoa`, BitMEX, 5 March 2018 through 31 December 2021. Realised PnL was 3,537 BTC. Almost all of the size is XBTUSD, the inverse perpetual, with ETHUSD second. Orders are bursts that finish in seconds: the median XBTUSD order is done in 1.3 seconds, and 89% are done within a minute. They are often maker (65% of XBTUSD volume in 2018, 77% in 2021). The clips are large, a median of 350,000 USD and common prints at 5 million and 10 million. The position those bursts leave behind is what persists. About half of Bitcoin trading days end short, about a quarter end long, and about a quarter end flat. Funding received was 150 BTC. Trade fees paid were 78 BTC. Both sit inside the 3,537, and neither is the business. The 23 liquidation days are crowded into early 2018, which the findings treat as a different account.

A next-bar market fill is a hypothesis test about when a rule said to be in the market. It is not a reconstruction of his execution. A daily or hourly bar cannot see a maker burst that finished in a second. Scoring an entry with no exit is also empty: the Bitcoin came from positions that outlived the burst, not from the burst itself.

## Backtrader

### Naming a parameter, and how a sweep walks it

A parameter is a class attribute on the strategy, or on the indicator, with a default. The concepts page shows both spellings, a dict and a tuple of tuples. The instance reads it as `self.params` or `self.p`. Cerebro passes overrides as keywords when it constructs the strategy:

```text
class MyStrategy(bt.Strategy):
    params = dict(period=20)

    def __init__(self):
        sma = btind.SimpleMovingAverage(self.data, period=self.p.period)

cerebro.addstrategy(MyStrategy, period=30)
```

That is from [Platform Concepts](https://www.backtrader.com/docu/concepts/). The same page says keyword arguments are matched to declared parameters and removed from `**kwargs`.

The EMA window is the indicator's own `period`, not a global config. The indicator reference gives `ExponentialMovingAverage` (aliases `EMA`, `MovingAverageExponential`) one parameter, `period`, default 30, and one line, `ema`. The smoothing factor is `2 / (1 + period)`. `MACD` is the same idea with the windows on the indicator: `period_me1` default 12, `period_me2` default 26, `period_signal` default 9, `movav` default `ExponentialMovingAverage`. Lines are `macd` and `signal`. Formula on that page: `macd = ema(data, me1_period) - ema(data, me2_period)`, then `signal = ema(macd, signal_period)`. Source: [indicator reference](https://www.backtrader.com/docu/indautoref/).

The built-in sample strategy puts the windows on the strategy and hands them to the indicators. From [automated running](https://www.backtrader.com/docu/automated-bt-run/automated-bt-run/):

```text
class SMA_CrossOver(bt.Strategy):
    params = (('fast', 10), ('slow', 30))

    def __init__(self):
        sma_fast = btind.SMA(period=self.p.fast)
        sma_slow = btind.SMA(period=self.p.slow)
        self.buysig = btind.CrossOver(sma_fast, sma_slow)
```

A sweep does not touch the indicator. It re-instantiates the strategy once per value. [Cerebro](https://www.backtrader.com/docu/cerebro/) says the arguments to `optstrategy` must be iterables, and instantiation happens at `run` time:

```text
cerebro.optstrategy(MyStrategy, myparam1=range(10, 20))
cerebro.optstrategy(MyStrategy, period=range(15, 25))
```

`range(15, 25)` is 15 through 24. A parameter that should stay fixed is still an iterable, of one element: `period=(15,)`. The [quickstart](https://www.backtrader.com/docu/quickstart/quickstart/) uses the same shape, `cerebro.optstrategy(TestStrategy, maperiod=range(10, 31))`, and says this replaces `addstrategy` by passing a range instead of a value. The strategy page calls optimization "reproduction": the system instantiates the strategy several times with different parameters. With `optreturn` left on, each result is not the full strategy. It is an object with `params` (or `p`) and `analyzers`. That thinner return is the part worth remembering. The process pool around it is not.

### "A crosses above B" versus "A crosses above a constant"

A cross of two series is a built-in indicator, not a comparison the user writes. [CrossOver](https://www.backtrader.com/docu/indautoref/) "gives a signal if the provided datas (2) cross up or down." The line `crossover` is `1.0` if the first data crosses the second upwards and `-1.0` if it crosses downwards. The formula looks at the last non-zero difference and at index 0 of both datas. The order-creation page uses it as `CrossOver(self.data.close, sma)`. The sample strategy uses `CrossOver(sma_fast, sma_slow)`. The MACD page says the signal line "should provide a 'signal' upon being crossed by the macd," but it does not show the call. The call that is actually documented is still `CrossOver` of two lines, so a MACD cross is `CrossOver` of the `macd` line and the `signal` line.

The same reference page has an internal contradiction on the one-sided siblings. `CrossUp`'s formula is an upward cross (`data0(0) > data1(0)`). `CrossDown`'s prose says "upwards," but its formula is the downward cross (`data0(0) < data1(0)`). Trust the formula, not the `CrossDown` blurb.

A constant is not a second data in that reference. What the concepts page documents is a comparison operator, and that operator is "greater than" or "less than," not "crosses." In `next`, `if self.sma > 30.0` compares the current SMA value to the number. At init, `sma_dist_to_high < 3.5` builds a line object rather than a boolean. The page says a bare `30` "is transformed internally into a pseudo-iterable which always returns 30," and shows that inside `bt.If`, not inside `CrossOver`. Python will not let the library override `and` and `or`, so composition is `bt.And` and `bt.Or`.

So: series against series is the built-in `CrossOver`. Series against a constant, as documented, is a level test written with an operator. A true cross of a constant (below on the previous bar, above on this one) is hand-written. That split is the gap a later operand type has to close. Do not paper over it by assuming `CrossOver(macd, 0)` just works. The reference never says it.

### What the strategy object is responsible for

The strategy is not a signal function. The [strategy page](https://www.backtrader.com/docu/strategy/) says indicators are created in `__init__`. `next` is where the strategy buys, sells, closes, and cancels. `buy` and `sell` default `size` to whatever the sizer returns. A market order, `exectype` of `Order.Market` or `None`, "will be executed with the next available price. In backtesting it will be the opening price of the next bar." The strategy is also told about the accounting after the fact: `notify_order` on every status change, `notify_trade` when a trade opens, updates, or closes, `notify_cashvalue` with cash and portfolio value. The [operating page](https://www.backtrader.com/docu/operating/) shows the same split in miniature: `next` calls `self.buy()` and `self.sell()`, and `notify_order` hears the result.

The broker, not the strategy, keeps cash, positions, commission, and fills. The strategy decides that an order should exist, and at what size if it does not want the sizer. It does not price the fill.

### Where costs, slippage, and warmup are applied

Costs are on the broker, applied when an order fills, and reported back on the order. A fresh cerebro has "no commission for the operations" ([operating](https://www.backtrader.com/docu/operating/)). The quickstart then sets one with `cerebro.broker.setcommission(commission=0.001)` and calls that 0.1% per operation. The broker reference documents `setcommission(commission=0.0, margin=None, mult=1.0, ...)`. The filled commission shows up as `order.executed.comm` inside `notify_order`, in the samples on the [commission](https://www.backtrader.com/docu/commission-schemes/commission-schemes/) and [order creation](https://www.backtrader.com/docu/order-creation-execution/order-creation-execution/) pages. Nothing in the indicator sees the fee.

Slippage is also a broker parameter, default zero. `slip_perc` is an absolute fraction (`0.01` is 1%). `set_slippage_perc(perc, slip_open=True, ...)` and `set_slippage_fixed` are the setters. `slip_open` defaults to false, and the page defines it as whether to slip fills that use the next bar's open. `slip_match` defaults to true, which caps slippage at the bar's high and low. Cheat-on-close (`coc`, default false) matches a market order to the close of the bar that issued it. The broker page says this "is actually cheating, because the bar is closed and any order should first be matched against the prices in the next bar." Source: [broker](https://www.backtrader.com/docu/broker/).

Warmup is not a NaN. The [operating page](https://www.backtrader.com/docu/operating/) and the [strategy page](https://www.backtrader.com/docu/strategy/) describe a minimum period. An SMA with `period=25` gets `prenext` 24 times, `nextstart` once, and `next` after that. A second SMA stacked on the first lengthens the wait automatically. `next` on the strategy does not run until every indicator's minimum period is met. There is no documented NaN to drop. Copying vectorbt's NaN warmup into a description of Backtrader would be wrong.

### What not to copy

The broker simulator. Order types beyond a next-bar market fill, margin and cash checks, volume fillers, slippage capped at the bar high and low, cheat-on-close, fund-mode share accounting, and the sizer hierarchy are a trading simulator. This project is a test of when. Also leave the live stores (Interactive Brokers, Oanda, Visual Chart), multi-data resampling, observers, and analyzers. `optstrategy` is the right idea, a grid of declared values, but not the machinery: one process per parameter combination, and a full strategy object graph unless `optreturn` strips it down. The strategy class owning both the rule and the order calls is the coupling to avoid. Swapping a hypothesis should not mean rewriting `buy` and `notify_order`.

## vectorbt

### Naming a parameter, and how a sweep walks it

There is no strategy class and no `self.p`. A parameter is a name on an indicator built by `IndicatorFactory`, and a sweep is passing several values to `run` so the output grows a column per value. The factory page's own example:

```text
MyInd = vbt.IndicatorFactory(
    input_names=['price'],
    param_names=['window'],
    output_names=['ma'],
).from_apply_func(...)

myind = MyInd.run(price, [2, 3])
```

`window` is the parameter name. `[2, 3]` is the sweep. The result's columns are labeled with that name. Source: [factory](https://vectorbt.dev/api/indicators/factory/).

The built-in moving average is the same shape. [basic indicators](https://vectorbt.dev/api/indicators/basic/) define `MA.run(close, window, ewm=False)`. `vbt.MA.run(price, [2, 3]).ma` has columns `ma_window` 2 and 3. `MACD.run` names the windows `fast_window` (default 12), `slow_window` (default 26), and `signal_window` (default 9). Hyperparameters live on the indicator call, which is what we want. They do not live on a strategy, because there is no strategy.

A Cartesian product is explicit. `run_pipeline` takes `param_product`, "whether to build a Cartesian product out of all parameters." The factory page runs `MyInd.run(price, window=[2, 3], lower=[3, 4], upper=5, param_product=True)` and the columns become the product of those three. A single number stays a single number: in that example `upper=5` is repeated across the product rather than zipped.

The [getting started](https://vectorbt.dev/) guide does this without a custom class. One pair of windows is `MA.run(btc_price, 10)` and `MA.run(btc_price, 20)`. The sweep is `MA.run(btc_price, [10, 20], short_name='fast')` against `MA.run(btc_price, [30, 30], short_name='slow')`. `Portfolio.from_signals` then returns `total_return` indexed by `fast_window` and `slow_window`. The signal line does not change between the one-shot and the grid. That is the property to keep.

An older generated page in the vectorbt git history (the `docs/indicators/factory.html` snapshot around commit `1caba86`) builds the indicator with `from_params` and writes a cross as `price_sm_above(..., crossed=True)`. The current reference does not. It uses `run` and `price_crossed_above`. The current page wins. Do not copy the old spelling.

### "A crosses above B" versus "A crosses above a constant"

The cross is a generated method, one per input and per output. The factory example is `myind.price_crossed_above(myind.ma)` and `myind.price_crossed_below(myind.ma)`. Getting started is `fast_ma.ma_crossed_above(slow_ma)` and `fast_ma.ma_crossed_below(slow_ma)`. The accessor underneath is `GenericAccessor.crossed_above(other)`, "generate crossover above another array," with `crossed_below` "in reversed order." The printed examples pass another series. Source: [factory](https://vectorbt.dev/api/indicators/factory/), [getting started](https://vectorbt.dev/), [accessors](https://vectorbt.dev/api/generic/accessors/).

The same method is how a constant would enter, because `other` is not a second indicator. It is whatever `combine_objs` can broadcast. That function "combines/compares `obj` to `other`, for example, to generate signals. Both will broadcast together. Pass `other` as a tuple or a list to compare with multiple arguments. In this case, a new column level will be created." So a list of thresholds is a documented sweep, and it uses the same call as a cross of two lines. The RSI reference has `rsi_crossed_above(other)` and `rsi_above(other)` with that same `other`. What the pages do not show is a worked example of `crossed_above(0)` or `rsi_crossed_above(70)`. The series-versus-series call is demonstrated. The constant is the broadcast rule, not a separate operator. Greater-than is a different generated method (`rsi_above`), not a cross.

The factory also shows the hand-written version it is trying to retire: compare with `>`, then `vbt.signals.first(after_false=True)`. Both exist. The built-in is the one-liner. The hand-written form is there so the reader can see that a cross is "became true," not "is true."

Boolean composition is array logic. The factory describes the generated helpers as a way "to easily combine boolean arrays using logical rules and to compare numeric arrays," strictly with NumPy, and the two sides broadcast. There is no `bt.And`, because `and` on a whole array is meaningless and the library never pretends otherwise.

### What the strategy object is responsible for

Nothing, because it does not exist. The user builds boolean arrays. `Portfolio.from_signals` is the thing that turns them into positions. The [portfolio page](https://vectorbt.dev/api/portfolio/base/) says it "adds an abstraction layer on top of `Portfolio.from_orders` to automate some signaling processes. For example, by default, it won't let us execute another entry signal if we are already in the position. It also implements stop loss and take profit orders for exiting positions."

Direction is an argument, not a class. With `entries` and `exits`, direction comes from `direction` (`longonly`, `shortonly`, or `both` in the page's hint). With `short_entries` and `short_exits` as well, direction is already in the arrays. Size, price, fees, and slippage are arguments to the same call. Stops (`sl_stop`, `tp_stop`, trailing) are arguments too. So the portfolio owns sizing, the fill, the costs, the stops, and the accounting. The arrays own the signal and nothing else. That split is cleaner than Backtrader's strategy, and then the portfolio immediately fills the signal side back up with stop logic.

### Where costs, slippage, and warmup are applied

Costs are arguments on the portfolio call, not on the indicator. `from_signals` takes `price`, `fees`, `fixed_fees`, and `slippage`, and points each of them at `from_orders`. There, `fees` is "fees in percentage of the order value" and `slippage` is "slippage in percentage of price." `price` "defaults to `np.inf`." The getting-started DMAC call passes the close series and no `price`, so that example fills on the same timestamp as the signal, against the series it was given. The portfolio guide's own "don't look into the future" example does the other thing: `result.vbt.fshift(1)`, then `Portfolio.from_orders(..., price=ohlcv['Open'], fees=0.001, slippage=0.001)`. The features page uses `fees=0.005` on `from_signals` for a golden-cross illustration. Sources: [portfolio](https://vectorbt.dev/api/portfolio/base/), [getting started](https://vectorbt.dev/), [features](https://vectorbt.dev/getting-started/features/).

The same portfolio page warns, on `from_order_func`, that "each bar is effectively a black box. We don't know how the price moves inside. Since trades must come in an order that replicates that of the real world, the only reliable pieces of information are the opening and the closing price." It also says the event-driven order function has "less risk of exposure to the look-ahead bias" than the vectorized calls. Read that next to the getting-started example, which does not shift. The doc page is not contradicting a blog here. It is showing two different choices, and only one of them refuses the signal bar's close.

Warmup NaNs belong to the indicator output. `MA.run` on `[1, 2, 3]` with windows 2 and 3 prints `NaN` until the window is full, and the factory's rolling-mean table does the same. The portfolio does not drop those bars. `fillna_close` is a different knob: forward and backward fill of NaNs in `close`, "applied after the simulation to avoid NaNs in asset value." The hand-built cross on that factory page is `False` on the rows where the average is `NaN`. The pages do not state a special NaN rule inside `crossed_above` beyond what those tables show.

### What not to copy

The portfolio engine. Stops, conflict modes (`upon_long_conflict`, `upon_opposite_entry`), cash sharing across columns, call sequences that presume several assets fill at the same price in the same tick, partial fills, rejection probabilities, and `from_order_func`'s Numba callbacks are a simulator. So is `YFData.download`, multi-symbol column stacking, grouping, and the in-memory caching of every metric. The column-per-parameter trick is the right model for a grid. The rest of `Portfolio` is too heavy for a test of when.

Also do not copy the missing strategy object. A boolean array is easy to sweep and hard to name, hand to a colleague, or swap. The hypothesis needs a name.

## NautilusTrader, for the one seam the other two lack

One page, [Strategies](https://nautilustrader.io/docs/latest/concepts/strategies). It is included because it separates the parameter object from the strategy behavior. Backtrader hangs parameters on the class. vectorbt hangs them on columns. Nautilus hangs them on a config the strategy is constructed with.

A strategy "inherits the `Strategy` class." "`Strategy` builds on `DataActor` and adds order management." The page says there are two parts: the strategy, and an optional configuration inheriting `StrategyConfig`. The documented example is a config whose fields include `fast_ema_period: int = 10`, `slow_ema_period: int = 20`, and `trade_size`. The strategy is `MyStrategy(config)`, and after `super().__init__(config)` the values are `self.config`. The page's own words on the split: configuration is "initial settings that define how the strategy works," and other attributes are state. The same class is constructed again with a different config. That is the seam to steal.

This page does not document a cross operator, and it does not document a parameter sweep. Indicators are registered in `on_start` with `register_indicator_for_bars`, and the page says to check `indicators_initialized()` before acting on them. That is warmup as a flag, not as a NaN and not as Backtrader's `prenext`. Costs and slippage are not on this page, so they are not claimed here.

What not to copy is everything else that page is about. The strategy submits, cancels, and modifies orders. It handles position events, quotes, the order book, timers, a cache, and a portfolio. The same source "can run in backtest and live environments," and the config exists so it can be serialized "over the wire, enabling distributed backtesting and remote live trading." Live reconciliation and external-order claims are on the same page. None of that belongs in a hypothesis tester. Take the config object. Leave the trader.

## One seam for this repo

Bars arrive from outside as one OHLCV series. This repo does not load them and does not know the symbol. The six objects below are the whole engine. No order type appears in any of them.

An indicator is a stateless function of those bars plus a small parameter object. The EMA window lives on that object, not in a global config and not as a column of a portfolio. Changing the window means building another parameter object and calling the same function. MACD is one indicator, with its own parameter object holding the fast, slow, and signal windows, because those three numbers belong to the indicator. This is Backtrader's `period` on `ExponentialMovingAverage`, and vectorbt's `param_names=['window']`, without either library's container. It is also Nautilus's config object, narrowed so the MACD windows are not mixed with the threshold and the trade size in one flat bag.

An operand is either an indicator output or a literal threshold. The MACD line, the signal line, and the number 0 are the same kind of thing to a rule. This is the piece neither library quite says. Backtrader's `CrossOver` takes two datas, and a constant is a separate operator that does not cross. vectorbt uses one method and broadcasts `other`, and a list of thresholds grows a column level, but the documented examples only ever pass another series. Making the operand explicit is what lets "crosses 0" and "crosses the signal line" be one rule. A sweep of thresholds is a sweep of those literals, the same way a sweep of windows is a sweep of indicator parameter objects.

A rule is a comparison of two operands. The comparisons are crosses above, crosses below, greater than, and less than. A cross is one of those comparisons. It is not an indicator class, which is where Backtrader puts it, and not a method generated onto every output, which is where vectorbt puts it. Rules compose with AND and OR. That composition is what Backtrader had to spell `bt.And` and `bt.Or`, and what vectorbt does with boolean arrays. Keep the composition. Do not keep the line objects or the array engine.

A strategy is a named bundle of an entry rule, an exit rule, and a direction. Direction is long only, short only, or both. There are no order types, no sizer, and no `notify_order`. Swapping a hypothesis means swapping this bundle. The class does not change when a window changes. Backtrader cannot say that: the strategy class owns the parameters and the `buy` call, and a sweep rebuilds the strategy. vectorbt cannot say it either: there is nothing to name or to hand to a colleague. The direction field is required by the account, not by taste. About half of Bitcoin days end short. A long-only bundle is a different hypothesis, and it has to be labeled as one.

A run is one strategy, one bar series, and one fill assumption. The signal is read on the bar close. The fill is the next bar's open. That is Backtrader's documented market-order rule, and it is the vectorbt pattern only when the user shifts the signal and passes the open as `price`. It is not cheat-on-close, which Backtrader's own broker page calls cheating. The run returns a result object: the trades, the equity, and the parameters that produced them. Backtrader's `optreturn` already shrinks an optimization result to params plus analyzers. Keep a result that small. Do not keep the strategy, the indicators, and the broker attached to it.

A sweep is a grid over the parameter objects of the indicators and the literal thresholds inside the rules. The same strategy class, many parameter tuples. The user writes the grid. That is `optstrategy(MyStrategy, period=range(...))`, and it is `param_product=True`. It is not a search. Nothing in the sweep tries to discover the windows he used.

A future `CLAUDE.md` should name these six seams and point here. It has to stay under 40 lines, so it does not restate the libraries and it does not grow examples.

## Two decisions still open

The seam above has slots for both of these. It does not choose them. Scoring is meaningless until they are chosen, and the findings cut against the convenient default in each case.

### Exits

He does not flatten every day. About half of Bitcoin days end short, a quarter long, a quarter flat, and the position is what is left after a burst that finished in a second or two. An entry-only rule cannot be scored. There is no trade, only a signal, and the signal is not what produced the 3,537 BTC.

Opposite signal means the exit rule is the other cross. It is the same kind of object as the entry, so it fits the seam with no extra field. It also assumes he leaves when the indicator flips. A both-directions bundle of opposite crosses is in the market almost all the time, and a quarter of his days end flat, so "always in" is already false. A long-only opposite-cross bundle ignores the side he held on about half the days.

Fixed hold of N bars is easy to grid, and N looks like a literal. It is not a comparison of two operands, so it does not fit the exit-rule slot without a new field on the strategy. It also does not match the account. The position is not a timer. A grid over N will find a horizon that fits this sample. That is a statement about the sample, not about how he held.

An ATR stop is allowed by the seam: another indicator, another parameter object, a threshold operand. Nothing in the findings says he traded one. The 23 liquidation days are the exchange, crowded into early 2018, not a volatility rule repeated across the four years. A stop also cuts the few large days that dominate the result. Thirty-six days of +50 BTC or more add up to +3,140 BTC, 89% of the net. A stop hypothesis is a risk hypothesis. It should not be smuggled in as the default exit.

Hold until the rule is false means the exit is the entry comparison failing, which is not the same as waiting for the opposite cross. A cross is one bar. "Greater than" can stay true for many bars, and both can be false, which is a flat book. This is the only option that can sit out a day without a timer, and a quarter of his Bitcoin days end flat. It is still not his execution. It is the exit that the seam can express with no new concept.

The tradeoff is between an exit the seam already is (opposite signal, or hold until false) and an exit that needs new machinery (a bar count, or a stop indicator) and that the findings do not support. Pick before any run is scored. Do not ship an entry-only rule and call the equity a result.

### Fill and costs

His fills are maker bursts inside a second. Median XBTUSD order 1.3 seconds, 89% finished within a minute, maker share rising from 65% to 77%, size from hundreds of thousands of USD to clips of 5 and 10 million. The hour-of-day chart is only informative because the burst is so short that the hour of the fill is the hour he traded. A daily or hourly bar cannot see any of that. The next-bar open is a clock for the rule. It is not his price.

Ignoring costs is the honest first cut if the question is which rule was in the market on the right days. Fees paid were 78 BTC and funding received was 150 BTC, against 3,537 BTC realised. The findings say both sit inside the 3,537 and neither is the business. A first grid that ignores them will not be wrong about the large months because of fees. The cost of the choice is turnover and holding time. A rule that crosses every bar pays the 78 BTC many times over in a way his maker bursts did not. A rule that holds a short for months collects or pays funding he did pay, and funding was small in total without being zero on a single long hold. "Small next to 3,537" is not the same claim at every grid point.

A flat fee in basis points on the next-bar open is what both libraries already do. Backtrader's quickstart uses `setcommission(commission=0.001)`. vectorbt's `fees` is a percentage of order value and `slippage` is a percentage of price. It punishes a chatty rule relative to a rule that holds, which is the comparison worth having once two grids look alike with costs at zero. It is not his fee. He was mostly maker. A taker charge on the next open is a worse price than he paid, at a time he did not trade. Use it as a sensitivity on the result object. Do not use it as an estimate of the 78 BTC, and do not add a slippage model that walks the high and low of the bar. Backtrader's `slip_match` does that. The bar does not contain the burst.

The seam already fixes the fill: signal on the close, fill on the next open, no costs inside the indicator. The open decision is only whether that fill pays zero or a flat fee. Either way the result is a test of when, not a reconstruction of the account.

## Do not build yet

No loader for the BitMEX fill CSVs. Bars come from outside.

No optimizer that searches for his parameters. He specifies the grid. A search that hunts the windows which would have matched 2018–2021 is a different project, and it is the one the quickstart warns against when it says not to over-optimize.

No UI. A named strategy, a grid, and a result object are the interface until a hypothesis has actually been run.

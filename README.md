# avantis-backtester

This repository tests trading strategies on past prices for Avantis perpetual futures, then runs the same strategies live.

A **backtest** replays history bar by bar. At each bar's close, the **strategy** (C++ code) returns **orders**: open a long or a short, or close a position. Orders fill at the next bar's open. One account holds every position, with its balance, fees, holding costs, stops, take profits, trailing stops, liquidation, and a hard stop. The result is every closed trade, the equity at every bar, and the ending balance.

The engine is C++, for speed. Python calls it through the module `avbt_cpp`. You give it the **markets** (named price series, on one shared clock); it never reads files or the network.

Live, the same strategy code runs one bar at a time. A backtest and a live run on the same bars give exactly the same orders.

The words used here are defined in [CONTEXT.md](CONTEXT.md). Decisions that are easy to undo by accident are in [docs/adr](docs/adr). Indicator formulas are in [docs/research/indicators.md](docs/research/indicators.md).

Requires Python 3.13.

## Install

From the repository root:

```bash
python3 -m pip install -e '.[test]'
```

The `[test]` extra adds pytest. The examples also use numpy, pandas, and plotly.

## Run the tests

```bash
python3 -m pytest
```

The Python tests do not read anything in `data/`.

## The C++ backtester

The full guide, covering every file, type, function, and test, is [cpp/README.md](cpp/README.md).

Build and test, from `cpp/`:

```bash
cmake -S . -B build
```

```bash
cmake --build build
```

```bash
ctest --test-dir build --output-on-failure
```

To call the C++ code from Python, build the `avbt_cpp` module with pybind11, from the repository root:

```bash
cmake -S cpp -B cpp/build -Dpybind11_DIR=$(python3 -m pybind11 --cmakedir)
```

```bash
cmake --build cpp/build
```

Then run the five Veranta strategies and chart them:

```bash
python3 examples/veranta_cpp_chart.py
```

It writes `examples/veranta_cpp_chart.html`.

## The Python API

```python
import avbt_cpp as avbt
```

### 1. Describe the markets

| Name | What it is |
|---|---|
| `Timeframe` | Bar length: `Min1`, `Min3`, `Min5`, `Min15`, `Min30`, `Hour1`, `Hour4`, `Hour8`, `Hour12`, `Day1`, `Week1`, `Month1`. |
| `Bars(timeframe, ts, open, high, low, close, minutes_with_data, volume=None)` | One price series. `ts` is each bar's open time in UTC seconds. The columns are numpy arrays of equal length. |
| `States(start, market, trend, volatility)` | Market state labels, one row per minute from `start`. Only `state_trend` uses them. |
| `Market(instrument, timeframes, states=None)` | One instrument, such as `"BTC"`, with its `Bars` on one or more timeframes. |
| `Markets(markets)` | Every market, on one clock. Read `.clock`, `.timeframe`, `.instruments`. |
| `Costs(open_fee, close_fee, hold_long=None, hold_short=None)` | Fees as fractions of the position's value, charged at the open and at the close. `hold_long` and `hold_short` give the cost of holding through each base bar, also as a fraction (positive pays, negative receives). Every market needs one. |
| `PortfolioSettings(starting_balance=10000, risk_per_trade=0.01, hard_stop=0.30, scale_risk_with_leverage=False, state_delay=60)` | The account. `risk_per_trade` is the fraction of the balance lost if a stop is hit. `hard_stop`: once equity falls this fraction below the starting balance, every position closes and trading stops for good. `state_delay` is how many seconds back a strategy reads state labels (a whole number of minutes, at least 60); it counts back to the label's timestamp (the start of its minute), so for labels that arrive 7 minutes after their minute closes, set 480. |

Prices must have no NaN. The engine does not check, and one NaN gives wrong levels, a dead ATR, or a NaN position. Pass minute candles through `clean_candles` in `examples/candles.py` before you resample them.

### 2. Run a backtest

```python
result = avbt.run("state_trend", params, markets, costs, settings)
```

- `strategies()` lists every strategy, with its `params` (name, default, `min`, `max`) and the `timeframes` it needs.
- The strategy names are: `late_day_short`, `rally_short`, `campaign_short`, `spike_short`, `gold_trend_long`, `state_trend`, and two combined in one account, `campaign_and_spike` and `late_day_and_rally`.
- `params` is a dict. A missing key keeps its default.
- `costs` is a dict from instrument to `Costs`.

A `Result` has:

| Field | Meaning |
|---|---|
| `trades` | Every closed `Trade`: `instrument`, `side`, `entry_time`, `exit_time`, `entry_price`, `exit_price`, `size`, `leverage`, `fees`, `holding_costs`, `result` (profit or loss in account money), and `cause` (`Stop`, `TakeProfit`, `TrailingStop`, `PartialTakeProfit`, `Liquidation`, `HardStop`, `Order`, `EndOfData`). |
| `equity`, `clock` | Equity at every bar, and that bar's time. |
| `ending_balance` | Balance after the last bar. |
| `timeframe` | The timeframe of the clock. |
| `version` | The engine version that made the result. |

`optimize(name, start, knobs, markets, costs, settings, rounds, progress=None)` tunes any strategy by a greedy search over pairs of params; `summary(result)` gives its Sharpe, total return, max drawdown, and trade count.

`walk_forward(name, start, knobs, folds, settings, rounds, progress=None)` runs a walk-forward. You cut the windows: each fold is `(train_markets, train_costs, test_markets, test_costs)`. Per fold it tunes on train, then runs the winner on train and on test. It returns one dict per fold: `best`, `train` and `test` (each a `summary`), and `overfit`, train Sharpe minus test Sharpe. `progress(fold, round, rounds, sharpe, best)` is called after each round. See `examples/walk_forward.py`.

The indicators are also callable on their own: `sma`, `pct_change`, `prior_max`, `prior_min`, `true_range`, `atr`, `hour_of_day`, `bar_change`, `chandelier`.

### 3. Trade live

```python
live = avbt.Live("state_trend", params, markets, settings)   # warms up on the history, once
```

Each time a bar closes:

1. Add the closed bar with `markets.append(instrument, timeframe, avbt.Bar(ts, open, high, low, close, minutes_with_data))`. Add a coarser bar, such as 1 hour, only once it has closed. Add each state label when it arrives with `markets.append_states(instrument, ts, avbt.State(market, trend, volatility))`.
2. Call `orders = live.decide(now, positions)`. It does not take the history: indicators update one bar at a time, so passing every bar each call would slow down as the history grows. `now` is the close time in UTC seconds. `positions` is a list of `Position(instrument, side, entry_price=0, size=0, entry_time=0)`.
3. Send the orders to the exchange.

`append` refuses a bar that is not later than the last one, an unknown market or timeframe, and a gap in the state labels.

Each `Order` has `kind` (`Order.Kind.Open` or `Close`), `instrument`, `side` (`Side.Long` or `Short`), `leverage`, and distances as fractions of the fill price: `stop_distance`, `take_profit_distance`, `trail_distance`, `take_profit_fraction`.

`decide` returns only opens and rule exits. In a backtest the account handles stops, take profits, trailing stops, and partial take profits. Live, your system must place them on the exchange from each order's distances, move the trailing stop, and close the partial take profit.

After a restart, build `Live` again on the history, and give each open position its `entry_time`, so time exits still close at the right bar.

`decide(name, params, markets, positions, now, settings=None)` is the same in one call, with no object kept: it warms up on `markets` (the bars so far) every time, then returns the orders at `now`. Call it at every base bar close; it gives the orders `run` gives at that step. It is slower than `Live`, since it reads the whole history each call.

More detail: [cpp/README.md](cpp/README.md) and [ADR 0017](docs/adr/0017-indicators-update-one-bar-at-a-time.md).

## Versions

The version has three numbers, such as 0.4.0. The engine and every result carry it (`version`).

- Renaming or removing anything, or changing what a result means, raises the middle number: 0.2 to 0.3.
- Adding something new raises the last number: 0.2.0 to 0.2.1.
- Names stay stable within a version.

Version 0.4.0:

- Added `decide` and `walk_forward`.
- `PortfolioSettings` is checked: `starting_balance` above 0, `risk_per_trade` and `hard_stop` in (0, 1]. A bad value raises `ValueError`.
- `sharpe` and `Result(...)` raise `ValueError` when `equity` and `clock` differ in length.

Version 0.3.0:

- Removed `optimize_state_trend`. Call `optimize` with the strategy name.
- Added `state_delay` in `PortfolioSettings`, `summary`, `Param.type`, and the `settings` argument of `Live`.

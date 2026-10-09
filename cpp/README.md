# The C++ backtester

This guide explains every part of the C++ code in `cpp/`: each file, type, function, and test. It assumes no C++ or trading background. Terms are defined where they first appear, and the project's full word list is in [CONTEXT.md](../CONTEXT.md).

## Contents

1. [What the code does](#what-the-code-does)
2. [Words used in this guide](#words-used-in-this-guide)
3. [Files](#files)
4. [Build and test](#build-and-test)
5. [How one backtest runs](#how-one-backtest-runs)
6. [indicators.hpp: bars and indicators](#indicatorshpp-bars-and-indicators)
7. [markets.hpp: several markets on one clock](#marketshpp-several-markets-on-one-clock)
8. [states.hpp: state labels](#stateshpp-state-labels)
9. [costs.hpp: what a market charges](#costshpp-what-a-market-charges)
10. [portfolio.hpp: the account](#portfoliohpp-the-account)
11. [backtest.hpp: orders, trades, and the loop](#backtesthpp-orders-trades-and-the-loop)
12. [strategies.hpp: the strategies and Combined](#strategieshpp-the-strategies-and-combined)
13. [run.hpp and version.hpp: run a strategy by name](#runhpp-and-versionhpp-run-a-strategy-by-name)
14. [optimize.hpp: Sharpe and the greedy search](#optimizehpp-sharpe-and-the-greedy-search)
15. [avbt_py.cpp: the Python module](#avbt_pycpp-the-python-module)
16. [Live: one bar at a time](#live-one-bar-at-a-time)
17. [Tests](#tests)
18. [Write a new strategy](#write-a-new-strategy)
19. [Rules that must stay true](#rules-that-must-stay-true)

---

## What the code does

A **backtest** replays a trading idea on past prices and records the trades the idea would have made. The C++ backtester takes:

- one or more **markets**: past prices for each instrument, such as ZORA or gold;
- one **strategy**: C++ code that decides when to open and close trades;
- the **costs** of each market: fees and holding costs;
- the **portfolio settings**: the starting balance, how much to risk per trade, and when to stop trading.

It returns every closed **trade**, the account's **equity** at every bar, the ending balance, and the engine version.

Python loads and cleans the price data. C++ does every calculation. The Python engine in `backtest/` is separate, and this guide does not cover it.

## Words used in this guide

**Trading words**

| Word | Meaning |
|---|---|
| Instrument | One thing you can trade, such as ZORA or gold. |
| Bar | Prices for one period of time: the **open** (first price), **high** (highest), **low** (lowest), and **close** (last price). |
| Bar size | The length of one bar, in seconds. 3600 is one hour. |
| Long | A trade that gains when the price rises. |
| Short | A trade that gains when the price falls. |
| Fill | The moment an order becomes a trade, and the price it happens at. |
| Stop (stop loss) | A price that closes a losing trade. |
| Take profit | A price that closes a winning trade. |
| Level | A stop or a take profit: a price set when the trade opens and watched on every bar. |
| Gap | A bar that opens past a level, because the price jumped between bars. |
| Notional | The full value of a position: size × price. |
| Leverage | Notional ÷ collateral. At leverage 1 you lock the full notional; at leverage 5, one fifth of it. |
| Collateral | The balance a position locks while it is open. |
| Free cash | Balance minus all collateral: what a new trade can use. |
| Fee | A charge at each fill, as a fraction of notional. 0.0001 is 1 basis point (one hundredth of one percent). |
| Holding cost | A charge for keeping a position open through a bar, as a fraction of notional. It may be negative: the position then receives money. Funding and rollover are holding costs. |
| Liquidation | The forced close of a position once its loss reaches 85% of its collateral. |
| Balance | Realized money: it changes only when a fill happens. |
| Unrealized result | The profit or loss of an open position, valued at the last close. |
| Mark | Valuing an open position at the latest close. A mark is not a fill and has no fee. |
| Equity | Balance plus the unrealized result of every open position. |
| Hard stop | A fall in equity (30% below the starting balance, by default) at which every position closes and trading stops for good. |
| Lookahead | A backtest error: a decision uses a price that was not known yet. |

**C++ words**

| Word | Meaning |
|---|---|
| Header (`.hpp`) | A file that declares names so other files can use them. |
| Source (`.cpp`) | A file that holds the code of functions declared in a header. |
| Type | What kind of value a variable holds. `double` is a decimal number, `int` a whole number, `std::string` text, `bool` true or false. |
| `std::vector<T>` | A list of values of type `T` that can grow. `std::vector<double>` is a list of numbers. |
| `struct` | A type you define that groups named values, called **fields**. |
| `class` | Like a struct, but its fields are usually **private**: only its own functions can change them. |
| Method | A function that belongs to a struct or class, called as `value.method()`. |
| Constructor | The function that creates a value of a type. |
| `static` method | A method called on the type itself (`Markets::make(...)`), not on a value. |
| `const` | "Will not be changed." A `const` method does not change its object. |
| Reference (`&`) | Another name for an existing value, so the value is not copied. `const Bars&` reads bars without copying them. |
| `enum class` | A type with a fixed list of named values, such as `Side::Long` and `Side::Short`. |
| Template | Code written once for any type. The compiler makes one copy per type used. |
| Concept | A named list of requirements a type must meet to be used with a template. |
| NaN | "Not a number", a special `double`. Here it means "no value yet". Every comparison with NaN is false. |
| `std::optional<T>` | Either a value of type `T` or nothing. |
| Exception | An error that stops the current function. `throw std::invalid_argument(...)` raises one with a message. |
| Namespace | A prefix that groups names. All code here is in `avbt`, so `Bars` is fully `avbt::Bars`. |

## Files

```
cpp/
├── CMakeLists.txt            build instructions
├── include/avbt/
│   ├── indicators.hpp        Timeframe, Bars, last_closed, Side, Field, and the indicators
│   ├── states.hpp            state labels and state_at
│   ├── markets.hpp           Market and Markets
│   ├── costs.hpp             Costs, MarketCosts, check_costs
│   ├── portfolio.hpp         the account: positions, levels, fees, holding costs, liquidation, hard stop
│   ├── backtest.hpp          Order, Trade, Result, the Strategy concept, the backtest loop
│   ├── strategies.hpp        the five Veranta strategies, StateTrend, and Combined
│   ├── run.hpp               the strategy table, strategies(), and run
│   ├── version.hpp           the engine version
│   └── optimize.hpp          sharpe, summary, Knob, optimize (the greedy search), walk_forward
├── src/
│   ├── indicators.cpp        indicator code
│   ├── markets.cpp           Markets::make and its lookups
│   └── portfolio.cpp         Portfolio methods
├── python/avbt_py.cpp        the Python module avbt_cpp
└── tests/test_*.cpp          one test program per header
```

Each header includes the ones below it:

```
optimize.hpp ───► backtest.hpp
run.hpp ────────► strategies.hpp ─► backtest.hpp
backtest.hpp ───► costs.hpp ─────► markets.hpp
              └─► version.hpp
strategies.hpp ─► backtest.hpp ─► portfolio.hpp ─► indicators.hpp
                              └─► markets.hpp ───► indicators.hpp
                                              └─► states.hpp ───► indicators.hpp
```

`backtest.hpp`, `costs.hpp`, `strategies.hpp`, `run.hpp`, and `optimize.hpp` have no `.cpp` file because they hold templates, and a template's code must be visible wherever it is used.

## Build and test

From `cpp/`:

```bash
cmake -S . -B build
```

```bash
cmake --build build
```

```bash
ctest --test-dir build --output-on-failure
```

- `cmake -S . -B build` reads `CMakeLists.txt` and writes build files into `build/`.
- `cmake --build build` compiles. It makes the library `libavbt.a` (the three `.cpp` files in `src/`), the six test programs, and the Python module when pybind11 is installed.
- `ctest` runs the test programs. A test program passes when it exits with code 0.

The build uses C++20, compiles in Release mode (optimized) unless you choose another, and compiles with `-Wall -Wextra` (extra warnings). `_LIBCPP_ENABLE_ASSERTIONS=1` makes the standard library check, for example, that list indexes are in range.

To build the Python module, tell CMake where pybind11 is. From the repository root:

```bash
cmake -S cpp -B cpp/build -Dpybind11_DIR=$(python3 -m pybind11 --cmakedir)
```

```bash
cmake --build cpp/build
```

The module file (`avbt_cpp.cpython-*.so`) lands in `cpp/build/`. It works only with the Python version that built it.

## How one backtest runs

Everything happens in clock order. The **clock** is every base bar open of every market, sorted, with no repeats. A market's **base** is its finest timeframe. One step is one base bar. A decision at step t can only use bars that have closed by the end of step t:

```
for each step t of the clock, from the first to the last:

  1. FILL      Orders the strategy returned at step t-1 fill at the open of
               their own market's base bar. Closes fill before opens. An order
               for a market with no bar at step t is dropped.
  2. CHECK     The portfolio checks every open position against its own
               market's base bar: stop, take profit, liquidation, then the
               hard stop. This includes the bar a position opened on.
  3. END       A market whose data ends at step t, before the run's last step,
               has its position closed at its last close. The cause is
               EndOfData.
  4. RECORD    Equity at the close of step t is saved.
  5. DECIDE    The strategy gets `now`, the UTC second at the close of step t,
               and returns orders. They wait for step 1 of the next step.
```

Orders returned on the last step never fill, because there is no next step. A position still open at the end is not a trade; it only counts in the last equity value.

A market joins the run at its first bar. Before that it has no bar, so an order for it is dropped.

An example with one short trade on ZORA, with a take profit 2% below and a stop 5% above the fill. ZORA is the only market, on 1-hour bars, so a step is a bar:

| Step | What happens |
|---|---|
| 10 | At the close, the strategy's entry test is true. It returns an Open order. |
| 11 | The order fills at step 11's open, 1.000. Stop = 1.050, take profit = 0.980. Step 11's low is 0.990: neither level is reached. |
| 12 | Step 12's low is 0.975, past the take profit. The trade closes at 0.980. The cause is `TakeProfit`. |

The recorded trade has `entry_bar = 11`, `exit_bar = 12`. These are clock steps. With one market they equal its bar numbers.

---

## indicators.hpp: bars and indicators

Files: `include/avbt/indicators.hpp`, `src/indicators.cpp`.

An **indicator** turns prices into one number per bar, such as a moving average. Every indicator here returns a `std::vector<double>` as long as its input, and puts NaN at bars that do not have enough history yet. Because every comparison with NaN is false, a rule such as `close[t] > average[t]` is false there, so no trade happens on an undefined value.

### `enum class Timeframe`

The length of one bar. Twelve values, finest first: `Min1`, `Min3`, `Min5`, `Min15`, `Min30`, `Hour1`, `Hour4`, `Hour8`, `Hour12`, `Day1`, `Week1`, `Month1`. No other length is allowed. A finer timeframe compares as smaller (`Min1 < Hour1`).

| Function | What it does |
|---|---|
| `int64_t seconds(Timeframe tf)` | The length of one bar in seconds. `Month1` returns 31 days, the longest month, because months differ. `last_closed` uses it only for a series' last bar, so a month can make that bar close late but never early. |
| `const char* name(Timeframe tf)` | Plain text for results and messages: `"1 minute"`, `"4 hours"`, `"1 month"`. |

### `struct Bars`

One instrument's price history on one timeframe. Index `i` in every list is the same bar. Python builds every timeframe from the same 1-minute candles and fills empty bars flat, so a series has no gaps: bar `i` ends when bar `i+1` opens. Bars start on UTC boundaries, weeks start on Monday, and months are calendar months.

| Field | Type | Meaning |
|---|---|---|
| `timeframe` | `Timeframe` | The length of every bar in the series. |
| `ts` | `std::vector<int64_t>` | Timestamp of each bar's start: seconds since 1970-01-01 UTC. |
| `open`, `high`, `low`, `close` | `std::vector<double>` | The four prices of each bar. |
| `minutes_with_data` | `std::vector<int>` | For each bar, how many 1-minute candles with real prices went into it, from 0 to `seconds(timeframe) / 60`. 0 means the bar had no data and was filled flat at the previous close. Nothing in C++ reads it yet. |
| `volume` | `std::vector<double>` | Optional: empty, or one value per bar. No strategy reads it yet. |

`Bars` has no methods. It only holds data.

### `int last_closed(const Bars& bars, int64_t now)`

The index of the last bar that has fully closed at UTC second `now`, or -1 when none has. Bar `i` has closed once bar `i+1` has opened. The last bar has closed once `seconds(timeframe)` have passed since it opened. A strategy reads every bar through this function, so it never sees a bar still open. At 10:30 on hourly bars it sees the 09:00 bar, not the 10:00 bar. This is what prevents lookahead when a strategy reads a coarser timeframe. It uses a binary search (`std::upper_bound`).

### `enum class Side { Long, Short }`

The direction of a trade. Used by the portfolio, orders, and `chandelier`.

### `enum class Field { Open, High, Low, Close }`

Names one of the four price lists. Used by `bar_change`.

### Indicator functions

In the table, `n`, `period`, and `lag` must be at least 1, or the function throws `std::invalid_argument`.

| Function | Result at bar i | NaN when |
|---|---|---|
| `sma(series, period)` | **Simple moving average**: the mean of `series[i - period + 1]` to `series[i]`, the last `period` values including bar i. | `i < period - 1` |
| `prior_max(series, n)` | The highest of `series[i - n]` to `series[i - 1]`: the last `n` values **before** bar i. | `i < n` |
| `prior_min(series, n)` | The lowest of the same `n` values before bar i. | `i < n` |
| `pct_change(series, lag)` | `(series[i] - series[i - lag]) / series[i - lag]`: the change over `lag` bars as a fraction. 0.10 is a 10% rise. | `i < lag`, or `series[i - lag]` is 0 |
| `true_range(bars)` | **True range**: the largest of `high - low`, `abs(high - previous close)`, and `abs(low - previous close)`. Bar 0 has no previous close, so it is `high - low`. | a high, low, or previous close is missing |
| `atr(bars, n)` | **Average true range**: the typical size of a bar's move. Bar n is the mean of true range at bars 1 to n. After that, Wilder smoothing: `(atr[i-1] × (n-1) + tr[i]) / n`. | `i < n`; a missing true range stays NaN from then on |
| `hour_of_day(bars)` | The UTC hour, 0 to 23, at which bar i opens. Throws if the timeframe is coarser than `Hour1`, because an opening hour means nothing on longer bars. | never |
| `bar_change(bars, now_field, then_field, lag)` | `now_field[i] / then_field[i - lag] - 1`. `bar_change(bars, High, Close, 1)` is "this bar's high over the previous close, minus 1". | `i < lag`, a value is missing, or `then_field[i - lag]` is 0 |
| `chandelier(bars, n, k, side)` | **Chandelier line**, a trailing-stop line. Long: `prior_max(high, n) - k × atr(n)`. Short: `prior_min(low, n) + k × atr(n)`. Throws if `k` is not above 0. | either part is NaN |

How `prior_max` and `prior_min` work: they keep a **deque** (a list you can add to or remove from at both ends) of bar indexes whose values are in falling order for `prior_max`, or rising order for `prior_min`. The front of the deque is always the answer. Each index enters and leaves once, so the whole series takes time proportional to its length, whatever `n` is.

### Rolling indicators

Each indicator is a class with `update`, which takes one bar and returns that bar's value (NaN while undefined). The functions above are loops of `update`, so a live caller and a backtest get bit-identical numbers. Constructors check their arguments and throw `std::invalid_argument`. [ADR 0017](../docs/adr/0017-indicators-update-one-bar-at-a-time.md).

| Class | `update` takes | Keeps |
|---|---|---|
| `Sma(n)` | a value | the running sum and the last `n` values |
| `PriorMax(n)`, `PriorMin(n)` | a value | a deque of (bar count, value), plus the bar count |
| `PctChange(lag)` | a value | the last `lag` values |
| `BarChange(lag)` | `now`, `then` | the last `lag` values of `then` |
| `TrueRange` | high, low, close | the previous close |
| `Atr(n)` | high, low, close | a `TrueRange`, the count, the sum, the previous ATR |
| `HourOfDay(tf)` | `ts` | nothing; throws on bars coarser than `Hour1` |
| `Chandelier(n, k, side)` | high, low, close | an `Atr` and a `PriorMax` and `PriorMin` |

`field_of` is a private helper in `indicators.cpp`: it returns the list named by a `Field`.

---

## markets.hpp: several markets on one clock

Files: `include/avbt/markets.hpp`, `src/markets.cpp`. Decisions: [ADR 0010](../docs/adr/0010-a-cpp-backtest-runs-on-markets-on-one-clock.md), [ADR 0011](../docs/adr/0011-markets-carry-several-timeframes-and-their-own-history.md).

### `struct Market`

| Field | Type | Meaning |
|---|---|---|
| `instrument` | `std::string` | The instrument's name, such as `"ZORA"`. Orders and positions use this name. |
| `timeframes` | `std::vector<Bars>` | Its price history on one or more timeframes, finest first. |
| `states` | `std::optional<States>` | Its state labels, or none. See [states.hpp](#stateshpp-state-labels). |

`timeframes[0]` is the **base**. Orders fill at its opens, and stops and take profits are checked on its highs and lows. The coarser timeframes are for strategies to read. Usually the base is 1 minute, so exits are exact without looking inside a coarse bar.

### `class Markets`

All the markets one backtest uses, on one **clock**: every base bar open of every market, sorted, with no repeats. Markets may start and end at different times. A market joins the run at its first bar. All markets must have the same base timeframe, so one step is one base bar everywhere.

The constructor is private, so the only way to get a `Markets` is `Markets::make`, and `make` always runs the check. Code that receives a `Markets` never has to check again.

| Method | What it does |
|---|---|
| `static Markets make(std::vector<Market> markets)` | Builds a `Markets`. Throws `std::invalid_argument`, naming the market, when: the list is empty; two markets share a name; a market has no timeframes, repeats one, or does not list them finest first; a market's base timeframe differs from the first market's; a `Bars` is empty, has lists of different lengths, or has timestamps that do not rise; a market's base bars skip a bar (two base bars more than one bar apart; Month1 is not checked); a base bar opens off its timeframe grid (`ts` not a multiple of the bar length, or for Month1 not the first second of a UTC month), which would let one market's step run ahead of another's; or `states` has columns of different lengths or a start that is not a whole minute. |
| `const Bars& at(const std::string& instrument, Timeframe tf) const` | Returns the market's bars on that timeframe. Throws if there is no such market or timeframe. |
| `const Bars& base(const std::string& instrument) const` | Returns the market's base bars. |
| `const States* states(const std::string& instrument) const` | The market's states, or `nullptr` when it has none. |
| `const std::vector<Market>& all() const` | Every market, in the order given to `make`. |
| `const std::vector<int64_t>& clock() const` | The clock: the UTC second at which each step opens. |
| `Timeframe timeframe() const` | The base timeframe, shared by every market. |
| `int bar_at(const std::string& instrument, int t) const` | The index of the market's base bar that opens at step `t`, or -1 when it has none then (its data has not started, or has ended). |
| `void append(const std::string& instrument, Timeframe tf, const Bar& bar)` | Adds one closed bar to the market on that timeframe. A base bar past the clock's end adds one clock step. Throws, naming the market, when the market or timeframe does not exist, `ts` is not later than the last bar, the volume column does not match, a base `ts` is off its timeframe grid (as in `make`), or a base `ts` would shift existing clock steps. |
| `void append_states(const std::string& instrument, int64_t ts, const State& state)` | Adds one minute of labels. With no states yet, they start at `ts`. Throws, naming the market, when the market does not exist, `ts` is not a whole minute, or `ts` is not exactly one minute after the last row. |

`Bar` holds `ts`, `open`, `high`, `low`, `close`, `minutes_with_data`, and an optional `volume`. Bars are only appended, never dropped, so bar numbers never shift and a reference from `at` or `base` stays valid. Append a coarser bar only after it has closed. [ADR 0017](../docs/adr/0017-indicators-update-one-bar-at-a-time.md).
---

## states.hpp: state labels

File: `include/avbt/states.hpp`. Decision: [ADR 0012](../docs/adr/0012-markets-may-carry-state-labels.md). It has no `.cpp` file.

A **state label** names the condition of a market in one minute. It is computed outside the engine. A market may carry three: market, trend, and volatility. Each is an enum stored as a `uint8_t`, and `Unknown = 0` in all three. The codes are fixed because Python writes them.

| Type | What it is |
|---|---|
| `MarketState` | 13 labels and `Unknown`, such as `TrendingUp`, `Breakout`, and `ShockStress`. |
| `TrendState` | `Uptrend`, `Downtrend`, `NonTrending`, `MixedConflicted`, and `Unknown`. |
| `VolatilityState` | `Low`, `Normal`, `High`, `Extreme`, `Compression`, `Expansion`, `Shock`, and `Unknown`. |
| `States` | `start` (UTC seconds, a whole minute) and the columns `market`, `trend`, `volatility`: one row per minute, no holes. Python has filled short gaps and set the rest to `Unknown`. |
| `State` | The three labels of one minute. |

`State state_at(const States& s, int64_t now)` returns the labels a strategy may read at `now`: the row stamped one minute earlier. At 10:00 it returns the 9:59 row, whose minute closed at 10:00, so there is no lookahead. It returns all `Unknown` before the first row or past the last.

---

## costs.hpp: what a market charges

File: `include/avbt/costs.hpp`. Decision: [ADR 0016](../docs/adr/0016-costs-belong-to-the-market.md). It has no `.cpp` file.

Costs belong to the market, not the strategy. The caller gives one `Costs` for each market.

### `struct Costs`

| Field | Meaning |
|---|---|
| `open_fee` | Fraction of notional charged at the open fill. Default 0. |
| `close_fee` | Fraction of notional charged at the close fill. Default 0. |
| `hold_long` | Holding cost of a long, one value per base bar: the cost of holding through that bar, as a fraction of notional. Empty means none. |
| `hold_short` | The same for a short. |

Holding costs are signed: positive means the position pays, negative means it receives. NaN means zero, so a holding cost never blocks a trade. Example: `hold_long` of 0.0001 on a bar charges a long 0.01% of its notional for that bar.

`MarketCosts` is `std::map<std::string, Costs>`, from instrument to costs.

### `void check_costs(const Markets& markets, const MarketCosts& costs)`

Throws `std::invalid_argument`, naming the market, when:

- a market has no `Costs`;
- a `Costs` names an instrument that is not a market;
- a holding list is neither empty nor one value per base bar of its market.

A missing `Costs` is an error and not zero cost, because a backtest with no costs looks better than reality. `backtest` calls this before the first step.

---

## portfolio.hpp: the account

Files: `include/avbt/portfolio.hpp`, `src/portfolio.cpp`. Decisions: [ADR 0008](../docs/adr/0008-a-portfolio-scores-in-account-terms.md), [ADR 0014](../docs/adr/0014-a-stop-may-trail-and-a-take-profit-may-close-part.md), [ADR 0016](../docs/adr/0016-costs-belong-to-the-market.md).

The portfolio is the trading account. It holds the balance and the open positions, and it applies the rules of Veranta, the exchange whose rules this backtester copies. Strategies never call it. Only the backtest loop does.

### Constants

| Name | Value | Meaning |
|---|---|---|
| `liquidation_loss` | 0.85 | A position is liquidated when its loss reaches 85% of its collateral. |
| `max_stop_loss` | 0.80 | A position is refused if a fill at its stop would lose more than 80% of its collateral. So the stop is always reached before liquidation. |

### `struct PortfolioSettings`

Chosen once, fixed for the run.

| Field | Default | Meaning |
|---|---|---|
| `starting_balance` | 10000 | Money at the start. |
| `risk_per_trade` | 0.01 | Fraction of the current balance lost if a trade's stop fills. |
| `hard_stop` | 0.30 | When equity falls to 70% of the starting balance, everything closes and trading stops for good. |
| `scale_risk_with_leverage` | false | When true, a trade risks `risk_per_trade × leverage`: at 5x, a stop loses 5% instead of 1%, so returns scale with leverage. |
| `state_delay` | 60 | Seconds back at which a strategy reads state labels; whole minutes, at least 60. ADR 0012. |

`use_settings` in `backtest.hpp` checks them when a run, `Live`, or `decide` starts: `starting_balance` above 0, `risk_per_trade` and `hard_stop` in (0, 1], `state_delay` as above. Anything else throws `std::invalid_argument`.

### `struct Fees`

The two fees of one position, as fractions of notional: `open` and `close`. Both default to 0. The backtest fills them from the market's `Costs`.

### `struct Position`

One open trade. The portfolio holds at most one per instrument.

| Field | Meaning |
|---|---|
| `instrument` | Which market. |
| `side` | Long or short. |
| `entry_price` | The fill price. |
| `size` | Units of the instrument. Notional = `size × entry_price`. |
| `collateral` | Balance locked: `notional / leverage`. |
| `stop_price` | The stop level. |
| `take_profit_price` | The take-profit level, or NaN for none. |
| `fees` | The `Fees` charged at the open and at the close. |
| `leverage` | The leverage the position opened with. |
| `holding` | Holding costs so far, in account money. Positive means the position has paid. |
| `liquidation_price` | Where the loss reaches 85% of the collateral. |
| `mark_price` | The last close seen; it values the unrealized result. |
| `trail` | The price gap the stop keeps behind the best high (low for a short). 0: the stop never moves. |
| `trailed` | True once the trail has moved the stop. Only then is a stop-out a `TrailingStop`; otherwise it is a `Stop`. |
| `take_profit_fraction` | The fraction of the position the take profit closes. Below 1, the take profit fires once and the rest runs on its stop. |
| `entry_time` | UTC second of the entry fill, or `unknown_time` (the lowest `int64_t`) when it is not known. 0 is a real time. |

### `enum class Cause`

Why a position closed.

| Value | Meaning |
|---|---|
| `Stop` | The price reached the stop (stop loss), and it had not moved. |
| `TakeProfit` | The price reached the take profit. |
| `Liquidation` | The loss reached 85% of the collateral. |
| `HardStop` | Equity fell to the hard-stop floor. |
| `EndOfData` | The market's data ended while the position was open. The backtest closes it at the last close. |
| `TrailingStop` | The stop had trailed, and the price reached it. |
| `PartialTakeProfit` | The take profit closed part of the position. The rest stays open. |
| `Order` | The strategy sent a close order: a **time exit** (the trade was open for its maximum number of bars) or a **rule exit** (a condition said to leave). |

### `struct Closed`

What `close` and `check` return for each closed position.

| Field | Meaning |
|---|---|
| `instrument`, `side` | As in `Position`. |
| `entry_price`, `exit_price` | The two fill prices. |
| `size` | Units closed. |
| `leverage` | The position's leverage. |
| `fees` | The open fee and the close fee on this size. |
| `holding` | This size's share of the holding costs. |
| `result` | Price result minus `fees` and `holding`, in account money. |
| `cause` | Why it closed. |

### `struct Quote`

One instrument's bar, handed to `check`: `instrument`, `open`, `high`, `low`, `close`.

### `struct Report`

A snapshot of the account.

| Field | Meaning |
|---|---|
| `balance` | Realized money. |
| `equity` | Balance plus every open position's unrealized result, minus its holding costs so far. |
| `free_cash` | Balance minus all collateral. |
| `open_positions` | Number of open positions. |
| `halted` | True once the hard stop has fired. |

### `class Portfolio`

Its state is private: `settings_`, `balance_`, `positions_`, and `halted_`. Only its methods change them.

**`Portfolio(PortfolioSettings settings)`**: the constructor. The balance starts at `starting_balance`.

**`bool open(instrument, side, entry_price, stop_price, leverage, take_profit_price = NaN, fees = {}, trail = 0, take_profit_fraction = 1)`**

Opens a position and returns true, or opens nothing and returns false.

It sizes the position so that a fill at the stop loses `risk_per_trade` of the balance, times `leverage` when `scale_risk_with_leverage` is on:

```
risk       = risk_per_trade × balance
distance   = the gap between entry and stop, on the losing side
size       = risk / distance
collateral = size × entry_price / leverage
```

Example: balance 10,000, risk 1%, a short at 1.00 with a stop at 1.05. Risk = 100, distance = 0.05, size = 2,000 units, notional = 2,000, collateral at leverage 1 = 2,000. The fee is not part of the sizing.

It refuses the open (returns false) when:

- the portfolio has halted;
- leverage or the entry price is not above 0;
- the instrument already has a position;
- the stop is not on the losing side of the entry;
- the take profit is not on the winning side (a NaN take profit passes, because it means "none");
- the stop would lose more than 80% of the collateral;
- the collateral is more than the free cash;
- `trail` is negative, or `take_profit_fraction` is not above 0 and at most 1.

On success, the open fee (`fees.open × notional`) leaves the balance, and the liquidation price is set 85% of the collateral's worth of price away from the entry. At leverage 1 that is an 85% price move.

**`void hold(instrument, rate, price)`**

Adds `rate × size × price` to the instrument's holding costs. A NaN rate adds nothing, and so does an instrument with no position. The backtest calls it once per base bar for each open position, with the bar's close as the price.

**`std::optional<Closed> close(instrument, price, fraction = 1.0, cause = Cause::Order)`**

Closes `fraction` (from 0 to 1) of the instrument's position at `price`. The price result minus the close fee and that fraction of the holding costs goes into the balance. It returns what closed, or nothing if there is no position. A partial close shrinks the size and the collateral by the same fraction, so the liquidation price does not move. A partial take profit uses it.

**`std::vector<Closed> check(const std::vector<Quote>& quotes)`**

Applies one bar to every position that has a quote. For each position:

1. **Stop.** Has the bar's worst price (low for a long, high for a short) reached the stop?
2. **Take profit.** Has the bar's best price reached the take profit? If yes and the stop was not reached, close `take_profit_fraction` of the position at the take profit, or at the open if the bar opened past it. If part of the position is left, its take profit becomes NaN and it goes on to step 3.
3. **Neither.** Mark the position at the close. With a `trail`, move the stop to the bar's high minus the trail (low plus the trail for a short), if that is better than the stop. The stop never moves back, and the new stop counts from the next bar.
4. **Stop reached.** Close at the stop, or at the open if the bar opened past it (a gap). The cause is `TrailingStop` when the trail had moved the stop, otherwise `Stop`. If that price is at or past the liquidation price, close at the liquidation price instead, with cause `Liquidation`.

If a bar reaches both the stop and the take profit, the stop fills. A bar does not show which price came first, so the backtest assumes the worse one ([ADR 0003](../docs/adr/0003-a-bar-with-both-levels-exits-at-the-stop.md)).

After every position, it checks the **hard stop**: if equity is at or below `starting_balance × (1 - hard_stop)`, it closes every position at its mark with cause `HardStop` and sets `halted_`. A position without a quote keeps its last mark.

It returns every position it closed.

**`Report report() const`**: builds a `Report` from the current state.

**`const std::vector<Position>& positions() const`**: the open positions, read-only.

**`double unrealized(const Position& p) const`** (private): `(mark_price - entry_price) × size` for a long; the opposite sign for a short. Minus the position's `holding`.

---

## backtest.hpp: orders, trades, and the loop

File: `include/avbt/backtest.hpp`. Decisions: [ADR 0009](../docs/adr/0009-a-cpp-strategy-is-code-checked-by-a-concept.md), [ADR 0010](../docs/adr/0010-a-cpp-backtest-runs-on-markets-on-one-clock.md), [ADR 0011](../docs/adr/0011-markets-carry-several-timeframes-and-their-own-history.md), [ADR 0016](../docs/adr/0016-costs-belong-to-the-market.md).

### `bool defined(double x)`

True unless `x` is NaN. Use it when a rule must check that an indicator has a value.

### `struct Order`

What a strategy returns at the close of a clock step. It fills at the open of the next step.

| Field | Default | Meaning |
|---|---|---|
| `kind` | `Open` | `Order::Kind::Open` or `Order::Kind::Close`. |
| `instrument` | | Which market. |
| `side` | `Long` | Long or short. Used by opens only. |
| `stop_distance` | 0 | Stop distance as a fraction of the fill price. 0.05 puts the stop 5% away, on the losing side. |
| `take_profit_distance` | NaN | Take-profit distance as a fraction. NaN for none. |
| `leverage` | 1 | Leverage for the position. |
| `trail_distance` | 0 | Trailing-stop gap as a fraction of the fill price. 0 for a stop that never moves. |
| `take_profit_fraction` | 1 | Fraction of the position the take profit closes. 0.5 sells half and lets the rest run. |

Distances are fractions, not prices, because the strategy decides before it knows the next open. The loop turns them into prices at the fill: for a short filled at 1.00 with `stop_distance = 0.05`, the stop is 1.05.

A close order uses only `kind` and `instrument`.

### `struct Trade`

One closed trade.

| Field | Meaning |
|---|---|
| `instrument` | Which market. |
| `entry_bar`, `exit_bar` | Clock steps of the two fills: indexes into `Result::clock`. |
| `entry_time`, `exit_time` | The UTC seconds of those two steps: `clock[entry_bar]` and `clock[exit_bar]`. |
| `side` | Long or short. |
| `entry_price`, `exit_price` | The fill prices. |
| `size` | Units closed in this trade. |
| `leverage` | The position's leverage. |
| `fees` | The open fee plus the close fee, in account money. |
| `holding_costs` | The holding costs paid, in account money. Negative when the position received. |
| `result` | Account money gained or lost, after fees and holding costs. |
| `cause` | Why it closed (see `Cause`). |

### `struct Result`

What `backtest` returns.

| Field | Meaning |
|---|---|
| `trades` | Every closed trade, in the order they closed. |
| `equity` | Equity at the close of every clock step: one value per step. |
| `ending_balance` | Balance after the last step. Positions still open are not in it. |
| `timeframe` | The base timeframe the run stepped on, so every result names its timeframe. |
| `clock` | The UTC second at which each step opens. `entry_bar` and `exit_bar` index it. |
| `version` | The engine version that made the result (`avbt::version`). |

### `concept Strategy`

The requirements a type must meet to be a strategy. A type `S` is a strategy when:

- `s.prepare(markets)` compiles, with `markets` a `const Markets&`;
- `s.update(markets)` compiles;
- `s.decide(now, report, positions)` compiles and returns `std::vector<Order>`, with `now` an `int64_t`, `report` a `const Report&`, and `positions` a `const std::vector<Position>&`.

There is no base class and no `virtual` function (a function looked up while the program runs). The compiler checks the concept when you call `backtest`, and it calls `decide` directly.

- `prepare` runs once, before the first step. It resets the strategy's indicators and calls `update`. A strategy chooses its timeframes.
- `update` feeds each rolling indicator only the bars added since the last call, and pushes the outputs onto the strategy's vectors. It never clears state that `decide` relies on, such as `seen_bar` and `signal_bar`. `StateTrend` builds a market's lines once and only extends them. `Combined` forwards `update` to both strategies.
- `decide` runs at the close of every clock step. `now` is the UTC second of that close. It must read bars only through `last_closed(bars, now)`, which never returns a bar still open.

### `template <Strategy S, class OnStep> Result backtest(S& strategy, const Markets& markets, const MarketCosts& costs, PortfolioSettings settings, OnStep on_step = {})`

The loop described in [How one backtest runs](#how-one-backtest-runs). Details:

- It runs `check_costs(markets, costs)` first, so a market with no `Costs` fails before any step.
- It makes a fresh `Portfolio` from `settings`.
- It keeps a map from instrument to entry step, because the portfolio does not know clock steps. When a position closes, the loop turns the `Closed` into a `Trade` with both steps. It forgets the entry step only when no position is left, so the rest of a partly closed trade keeps it.
- **Fill step.** It moves close orders ahead of open orders, keeping their order otherwise (`std::stable_partition`). A close frees collateral, so a new open on the same bar can use it. It looks up the order's market bar with `bar_at`; an order for a market with no bar at this step is dropped. For each open order it computes the stop and take-profit prices from that base open and calls `portfolio.open` with the market's `Fees`. A refused open is skipped, and nothing is recorded.
- **Check step.** It builds one `Quote` per open position from that position's own base bar and calls `portfolio.check`. A position whose market has no bar at this step is not checked.
- **End-of-data step.** Except on the run's last step, a market whose last base bar is this step has its position closed at that bar's close, with `Cause::EndOfData`.
- **Holding step.** After the checks and the end-of-data close, each open position whose market has a bar at this step is charged `hold_long[i]` or `hold_short[i]` through `portfolio.hold`, at that bar's close. The step's equity then includes it.
- **Decide step.** It passes `now`, the step's open plus `seconds(timeframe)`, which is the close of the step.
- **`on_step` hook.** After `decide`, the loop calls `on_step(t, positions, orders)` with the step, the open positions, and the orders just returned. The default does nothing. `test_live.cpp` uses it.
- The caller chooses the bars. The loop never trims them.

The compiler makes one copy of `backtest` for each strategy type it is used with.

---

## strategies.hpp: the strategies and Combined

File: `include/avbt/strategies.hpp`. The rules are the five Veranta wallet ideas; `examples/veranta_rules_cpp.py` runs them.

### Shape of a strategy

Each strategy is a struct with:

- `Params`: a nested struct of settings, with defaults. You can change them before a run, for example `s.params.lag = 1`.
- line fields: the indicators it computes in `prepare`.
- `signal_bar`: the bar of the last entry signal, for the time exit.
- `seen_bar`: the last bar `decide` acted on.
- `bars`: a pointer to the bars it reads.
- `prepare(const Markets& m)`: looks up its own bars with `m.at(params.instrument, params.timeframe)` and computes its lines. It keeps a pointer to them in `bars`.
- `entry(int t)`: true when the entry rule holds at bar t's close.
- `rule_exit(int t)` (gold only): true when the exit rule holds.
- `decide(...)`: calls `decide_entry_and_exits`.

Every strategy's `Params` has `instrument`, `side`, `take_profit`, `stop`, `time_exit`, `leverage = 1`, and `timeframe = Timeframe::Hour1`. None has a fee: fees come from `Costs`. The `Params` bar counts (such as `time_exit = 3`) count bars of that timeframe.

### `decide_entry_and_exits(s, now, positions)`

The shared decision, a template used by all five strategies. It first sets `t = last_closed(*s.bars, now)`. It returns nothing when `t` is -1 or equals `seen_bar`, so a strategy acts once per new bar on its timeframe, however fine the clock is. Then it sets `seen_bar = t` and:

- **While a position is open:** return a close order if the time exit is due (`t - signal_bar >= time_exit`) or the rule exit holds. Otherwise return nothing.
- **While flat:** if `entry(t)` is true, save `signal_bar = t` and return an open order with the strategy's side, stop, take profit, and leverage.

**Time exit:** the bar of the fill is bar 1. With a signal at bar t and a time exit of N, the close order is returned at bar t+N and fills at the open of bar t+N+1. A time exit of 0 means none.

It uses `if constexpr (requires { s.rule_exit(t); })`, which asks the compiler whether the strategy has a `rule_exit` method, so strategies without one skip that test.

### The five strategies

All entries are known at a bar's close and fill at the next open. Distances are fractions of the fill price.

| Struct | Market | Side | Entry at bar t's close | Take profit | Stop | Time exit | Rule exit |
|---|---|---|---|---|---|---|---|
| `LateDayShort` | ZORA | short | bar opens at 16:00 UTC **and** `pct_change(close, 72) >= 0.10` | 2% | 5% | 3 bars | none |
| `RallyShort` | ZORA | short | `pct_change(close, 24) >= 0.10` **and** `close > sma(close, 21)` | 2% | 5% | 4 bars | none |
| `CampaignShort` | AVNT | short | `pct_change(close, 24) >= 0.09` | 20% | 30% | 120 bars | none |
| `SpikeShort` | DYM | short | `bar_change(High, Close, 1) > 0.05` (the high is more than 5% over the previous close) | 6% | 8% | 16 bars | none |
| `GoldTrendLong` | GOLD | long | `close > sma(close, 55)` **and** `pct_change(close, 72) > 0` | none | 1.5% | none | `close < sma(close, 55)` |

### `StateTrend`

Trades every market that has `states`, each with its own position. It reads each market's signal bars and its base (1-minute) bars, so each such market needs both. `params.signal` maps an instrument to its signal timeframe, such as `{"BTC": Min15}`; an instrument not listed reads `Hour1`. A market that lacks its signal timeframe throws when the backtest starts.

| Part | Rule |
|---|---|
| Bias | Up when the last closed signal close is above its `average` (50) bars' average and the trend label is `Uptrend`. Down is the mirror. |
| Entry | With the bias, when the market label is the same trend or `Breakout`, the minute close breaks the prior `breakout` (30) minute high (low for a short), and volatility is known and not `Extreme` or `Shock`. |
| Stop and take profit | `atr_stops` (2) signal-bar ATRs, and `reward` (2) times the stop. The take profit closes `take_fraction` (1) of the trade; the rest runs on its stop. |
| Trailing stop | `trail_atrs` (0) signal-bar ATRs, taken at entry, behind the best price. 0 keeps the stop fixed. |
| Exit | The bias no longer matches the position, or the market label turns to the opposite trend. An `Unknown` trend label, or an average with no value yet (fewer signal bars than `average`), is no evidence and keeps the position. |

`Unknown` never opens a trade. It does not close one either. Default `leverage = 1`. It acts once per new minute bar.

### `template <Strategy A, Strategy B> struct Combined`

Runs two strategies as one strategy, in one account. The two strategies do not know about each other.

| Member | Meaning |
|---|---|
| `a`, `b` | The two strategies. Change their settings through `a.params` and `b.params`. |
| `owner` | A map from instrument to the strategy that owns its position: 0 for `a`, 1 for `b`. |
| `claimed` | A map from instrument to the `now` of the `decide` whose open order claimed it. |
| `prepare(m)` | Clears `owner` and `claimed`, then calls `a.prepare(m)` and `b.prepare(m)`. |
| `trades(instrument)` | True when `a` or `b` can trade the instrument. |
| `decide(now, report, positions)` | See below. |
| `owned(positions, who)` (private) | The positions that `who` owns. |
| `take(orders, mine, who, now)` (private) | Adds one strategy's orders to the list. It drops an open order on an instrument someone already owns. Otherwise an open order makes its sender the owner and claims the instrument at `now`. |

`decide` works in four steps:

1. Forget the owner of any instrument that has no position (a level closed it, or the open was refused), unless an open claimed it at this same `now`: a live caller may ask again before the open fills.
2. Give each position with no owner to `a` if `a` trades its instrument, else to `b` if `b` does. A position does not name the strategy that opened it, so this is how a restarted `Combined` finds its owners.
3. Ask `a`, passing only the positions `a` owns. Then ask `b`, passing only the positions `b` owns.
4. Return all the orders, `a`'s first.

The strategy that opens a position owns it and decides its exits. When both strategies trade the same instrument (such as `LateDayShort` and `RallyShort` on ZORA), they take turns: while one owns the ZORA position, the other's entries are dropped. When both open on the same bar, `a` wins. When they trade different instruments (such as `CampaignShort` on AVNT and `SpikeShort` on DYM), each makes the same trades it would make alone; only the sizes differ, because they share one balance.

A `static_assert` at the end of the file checks, at compile time, that all six strategies and both `Combined` pairs meet the `Strategy` concept.

---

## run.hpp and version.hpp: run a strategy by name

Files: `include/avbt/run.hpp`, `include/avbt/version.hpp`. Decision: [ADR 0016](../docs/adr/0016-costs-belong-to-the-market.md). Neither has a `.cpp` file.

`run.hpp` lets a caller, such as the Python module, run any strategy from a name and a map of params. It holds the logic, so the Python bridge needs none.

### Types

| Type | What it is |
|---|---|
| `Value` | One param value: a `std::variant` of `bool`, `int`, `double`, `std::string`, `Timeframe`, or a map from instrument to `Timeframe` (the `signal` param of `StateTrend`). |
| `Params` | `std::map<std::string, Value>`: a variant of a strategy's settings, by name. |
| `Param` | One row of a strategy's param list: `name`, default `value`, and the `min` and `max` a number must lie in. |
| `StrategyInfo` | One row of the strategy table: `name`, its `params`, the `timeframes` it reads by default, and a `run` function that builds the strategy and backtests it. |

`signal` and `instrument` are in the table like any other param. Each Veranta strategy lists `instrument`, `take_profit`, `stop`, `time_exit`, `leverage`, and `timeframe`, plus its own settings.

### `const std::vector<StrategyInfo>& strategies()`

The table. It has eight entries:

| Name | Strategy |
|---|---|
| `late_day_short`, `rally_short`, `campaign_short`, `spike_short`, `gold_trend_long` | The five Veranta strategies. Each has a `side` param, a string: `"long"` or `"short"`. Any other value throws. |
| `state_trend` | `StateTrend`. |
| `campaign_and_spike`, `late_day_and_rally` | `Combined` pairs. Their params start with `a.` or `b.`: `a.lag` is the first strategy's `lag`. |

### `Result run(name, params, markets, costs, settings)`

1. Finds `name` in the table.
2. Builds the strategy. A param not given keeps its default.
3. Calls `backtest` with `markets`, `costs`, and `settings`.

It throws `std::invalid_argument` for an unknown name (the message lists the known names), an unknown param (the message lists the known params), a value of the wrong type or outside its range, and bad costs. A typo in a param name cannot fall back to the default.

An `int` is accepted where a `double` is expected.

### `decide(name, params, markets, positions, now, settings = {})` and `decide_once(live, markets, positions, now)`

`decide` gives the orders of the strategy `name` at `now`, keeping nothing between calls. `decide_once` does the work: it prepares a fresh `Live` on `markets` (the bars so far), decides one base step before `now` so the strategy knows which bars it has already acted on, then decides at `now`. So it returns orders only at the step where `run` would: an hourly strategy on minute bars gives its open once, not every minute until the next hourly close. It throws as `run` does. Each call reads the whole history; `Live` is the fast path. `test_live.cpp` checks it gives `run`'s orders.

### `avbt::version`

`version.hpp` holds one constant, `avbt::version`. Every `Result` copies it into `Result::version`, and the Python module reads the same constant, so both report one number.

---

## optimize.hpp: Sharpe and the greedy search

File: `include/avbt/optimize.hpp`. ADR 0013.

### `double sharpe(const Result& r)`

Daily Sharpe from clock time, the same on bars of every length. Step `i` closes at `clock[i] + seconds(timeframe)`.

1. It uses full UTC days only: from the first midnight at or after the first close to the last midnight at or before the last close. The part-days at either end are dropped.
2. At each midnight it takes the equity of the last step that closed at or before it. A day with no bars keeps the last value, so it is a 0% return.
3. Daily return: `day[k] / day[k-1] - 1`.
4. −∞ when any equity value, at any step, is at or below 0. NaN with fewer than 30 daily returns, or when they never vary. Otherwise the mean over the sample standard deviation, times √365; crypto trades every day.

### `template <class P> struct Knob`

One parameter the search may change. `choices[i]` is a function that writes one value into a `P`, and `labels[i]` is that value as text for printing, since a function cannot be read back. A parameter with no knob keeps its value in `start`.

### `struct Knob`, `struct Run`, `struct Search`, `Progress`

A `Knob` is a param name and the `Value`s it may take, as `run` takes them; a combined strategy's names start with `a.` or `b.`. A `Run` is one backtest: its `round`, its `params`, its `Summary`, and its `score`. `Search` holds the `best` params, their `score` and `summary`, the base `timeframe`, and every `Run`. `Progress` is called after each round with (round, rounds, best score, best params); returning false stops the search after that round.

### `struct Goal`, `score(summary, goal)`

What the search maximizes. With `max_drawdown` NaN (the default), Sharpe. With a limit (a positive fraction: 0.1 is a 10% fall), total return over runs whose max drawdown is at most the limit; a run over it scores NaN and never wins. `min_gain`: a full pass whose gains are all at or below it stops the search. When no run meets the limit, `score` is NaN and `best` is the start. ADR 0018.

### `Search optimize(name, start, knobs, markets, costs, settings, rounds, progress = {}, goal = {})`

Tunes the table strategy `name`. It first checks every knob value (name, type, range) and throws `std::invalid_argument` naming the bad knob. It then calls `search`, which scores each run's `summary` under `goal`:

1. Every knob starts at its first value. That is round 0.
2. The pairs of knobs are listed in a fixed order: (0,1), (0,2), …, (1,2), ….
3. Round `r` takes the next pair and runs every combination of its two knobs, the other knobs at the best so far. Any higher score becomes the best. NaN never does.
4. A combination already run is not run again.
5. The search stops after `rounds` rounds, or sooner, when a full pass over the pairs brings no gain above `goal.min_gain`, or when `progress` returns false.

### `Summary summary(result)`

Sharpe, total return (ending equity / starting equity − 1), max drawdown (the largest fall from an equity peak, a positive fraction), and trade count.

Each run builds a fresh strategy, so `prepare` starts clean.

### `walk_forward(name, start, knobs, folds, settings, rounds, progress = {}, goal = {})`

A `Fold` holds a train `Markets` and its `MarketCosts`, and a test `Markets` and its `MarketCosts`; the caller cuts them. Per fold, `walk_forward` calls `optimize` on train with `goal`, then runs the winner on train and on test. It returns one `FoldResult` per fold: `best`, the `train` and `test` `Summary`, and `overfit`, train Sharpe minus test Sharpe. `FoldProgress` is called with the fold's index and then `Progress`'s arguments; returning false stops that fold's search only. No folds throws, and so does a fold whose train or test `Markets` is null (Python: `None`).

`sharpe` throws `std::invalid_argument` when `equity` and `clock` differ in length.

## avbt_py.cpp: the Python module

File: `python/avbt_py.cpp`. It uses **pybind11**, a library that makes C++ functions callable from Python. The module is named `avbt_cpp`.

The bridge holds no rules of its own. It converts types and releases the Python GIL while `run` works; every check and error message lives in C++ (`check_costs`, `Markets::make`, `run`). A `std::invalid_argument` becomes a Python `ValueError`.

**Helpers** (in an unnamed namespace, so only this file sees them):

| Helper | What it does |
|---|---|
| `to_vector(a)` | Copies a 1-dimensional numpy array into a `std::vector`. Throws for other shapes. |
| `to_numpy(v)` | Hands a `std::vector<double>` or `std::vector<int64_t>` to numpy without copying it. |
| `make_bars(...)` | Builds `Bars` from numpy arrays and checks that every list is the same length. |
| `field_named`, `side_named` | Turn `"high"` into `Field::High`, `"short"` into `Side::Short`, and so on. |

**What Python sees:**

| Python name | What it is |
|---|---|
| `Timeframe` | The enum, with the same twelve values (`Timeframe.Hour1`). |
| `timeframe_name(tf)`, `timeframe_seconds(tf)` | `name` and `seconds` for a `Timeframe`. |
| `Bars(timeframe, ts, open, high, low, close, minutes_with_data, volume=None)` | Builds bars. `len(bars)` is the number of bars; `bars.timeframe` reads the timeframe back; `bars.ts`, `open`, `high`, `low`, `close` return copies of the columns as numpy arrays. `bars.volume` does the same, or is `None` when no volume was given. A column of the wrong length, `volume` included, raises `ValueError`. |
| `PortfolioSettings(starting_balance=10000, risk_per_trade=0.01, hard_stop=0.30, scale_risk_with_leverage=False, state_delay=60)` | The settings. |
| `States(start, market, trend, volatility)` | Builds state labels from a start second and three `uint8` arrays. Throws when a code is past the last label of its enum. `len(states)` is the number of minutes; `states.start` reads the start back. |
| `Market(instrument, timeframes, states=None)` | One market: its `Bars` on one or more timeframes, finest first, and optional `States`. |
| `Markets([market, ...])` | Calls `Markets::make`, so the checks run once, when the object is created. Pass the same object to many runs. Reads `clock`, `timeframe`, and `instruments`. |
| `Costs(open_fee=0.0, close_fee=0.0, hold_long=None, hold_short=None)` | One market's costs. `Costs()` is zero cost. The two holding lists are numpy arrays with one value per base bar, or empty. |
| `strategies()` | The strategy table: each strategy's name, its params (name, default, range), and its timeframes. |
| `run(name, params, markets, costs, settings)` | Runs one strategy by name. `params` is a dict of the params to change; numpy numbers and booleans work as values, and an integer too big for a C++ `int` is taken as a float; `costs` is a dict from instrument to `Costs`. Returns a `Result`. |
| `Result`, `Trade` | The result and its trades, with the fields in the tables above, as attributes: `result.trades`, `trade.entry_time`. |
| `Side`, `Cause` | The two enums, now Python enums. `Cause` has `Stop`, `TrailingStop`, `PartialTakeProfit`, `TakeProfit`, `Liquidation`, `HardStop`, `Order`, and `EndOfData`. |
| `version` | The engine version, the same constant C++ stamps on every `Result`. |
| `sharpe` | `(result)`: `avbt::sharpe`. `Result(equity, timeframe, clock)` builds a result to score. |
| `optimize` | `(name, start, knobs, markets, costs, settings, rounds, progress=None, max_drawdown=None, min_gain=0.0)`: `avbt::optimize`. `start` is a params dict; `knobs` is `{name: [values]}`; `max_drawdown` and `min_gain` make the `Goal` (None: maximize Sharpe). The search runs without the GIL; `progress(round, rounds, score, best)` takes it, and an exception it raises stops the search and reaches the caller. Returns `{"best", "score", "sharpe", "total_return", "max_drawdown", "trades", "runs", "timeframe"}`; `best` passes to `run`, and each run is `{"round", "params", "score", "sharpe", "total_return", "max_drawdown", "trades"}`. |
| `summary` | `(result)`: `avbt::summary` as a dict `{"sharpe", "total_return", "max_drawdown", "trades"}`. |
| `walk_forward` | `(name, start, knobs, folds, settings, rounds, progress=None, max_drawdown=None, min_gain=0.0)`: `avbt::walk_forward`, with the goal as in `optimize`. Each fold is `(train_markets, train_costs, test_markets, test_costs)`. Runs without the GIL; `progress(fold, round, rounds, score, best)` takes it. Returns one dict per fold: `{"best", "train", "test", "overfit"}`, `train` and `test` as `summary` gives them. |
| `Live`, `decide` | `Live(name, params, markets, settings)` then `live.decide(now, positions)`; or `decide(name, params, markets, positions, now, settings)` in one call, `avbt::decide`. Both keep the GIL. See Live below. |
| `sma`, `pct_change`, `prior_max`, `prior_min` | Take a numpy array and a number; return a numpy array. |
| `true_range`, `atr`, `hour_of_day`, `bar_change`, `chandelier` | Take `Bars`; return a numpy array. Fields and sides are strings. |

The old per-strategy functions (`late_day_short`, `rally_short`, `campaign_short`, `spike_short`, `gold_trend_long`, `campaign_and_spike`, `late_day_and_rally`, `run_state_trend`) are gone. Call `run` with the strategy's name.

Costs are required. To run without costs, pass `Costs()` for each market.

```python
markets = avbt_cpp.Markets([avbt_cpp.Market("ZORA", [bars_1h])])
costs = {"ZORA": avbt_cpp.Costs(open_fee=0.0001, close_fee=0.0001)}
result = avbt_cpp.run("late_day_short", {"stop": 0.05}, markets, costs, avbt_cpp.PortfolioSettings())
```

A new strategy needs a row in the table in `run.hpp`, and no change to the bridge.

These example scripts use the module:

- `examples/veranta_rules_cpp.py` compares C++ trades with the Python engine's, trade by trade.
- `examples/state_trend.py` runs `StateTrend` on BTC and ETH with states from `examples/clickhouse_data.py`.
- `examples/state_trend_optimize.py` tunes `StateTrend` with `optimize` on June and July 2026, then scores the winner on August.
- `examples/walk_forward.py` cuts folds and runs `walk_forward` on `StateTrend`.
- `examples/veranta_cpp_chart.py` writes `examples/veranta_cpp_chart.html`: a summary table (trades, win rate, return, maximum drawdown, Sharpe ratio, exit causes) and a chart for each strategy.

---

## Live: one bar at a time

A live caller runs the same strategy code as a backtest. In order:

1. Build `Markets` from the history, and build the strategy once: `Live(name, params, markets, settings)` in Python, or `prepare` in C++. This warms up every indicator on the history.
2. Each time a bar closes, append the closed bars with `markets.append(...)` and the new state labels with `markets.append_states(...)`.
3. Call `live.decide(now, positions)`, which runs `update` on the new bars and then `decide`. `now` is the UTC second of the close.
4. Send the orders it returns to the exchange.

Or skip the object: `decide(name, params, markets, positions, now, settings)` warms up on `markets` every call and gives the same orders (see run.hpp above). Positions need `entry_time` for time exits, as after a restart. A `Combined` strategy on one instrument for both halves (`late_day_and_rally`) cannot tell from a position which half opened it, so it gives it to `a`; see Combined.

`decide` does not take the full history on each call. Indicators update one bar at a time ([ADR 0017](../docs/adr/0017-indicators-update-one-bar-at-a-time.md)), so passing all bars every call would recompute everything and slow down as the history grows. Stops and take profits stay the caller's (see Stops live below).

`positions` are the caller's open positions, with at least `instrument` and `side`. `report` is optional: Python's `decide(now, positions, report=None)` passes an empty `Report` when it is left out, and no strategy reads it today.

**State labels arrive late.** A label is stamped with the start of its minute and reaches the caller some minutes after that minute closes. Append each label when it arrives. `decide` reads only labels already appended, so it acts on the labels it has, as a trader would. Set `settings.state_delay` (seconds, a whole number of minutes, at least 60) to match how late live labels arrive. `state_delay` counts back from now to the label's timestamp: at 10:00 with 420, the engine reads the label stamped 9:53. A label stamped 9:53 closes at 9:54, so if labels arrive 7 minutes after their minute closes, use 480. Then a backtest reads each label as late as live does. The default is 60.

**Restart.** A fresh `Live` rebuilds every indicator from the history, but not the bar on which a strategy opened a position it now holds. Give each open position its `entry_time` (UTC second of the entry fill), as in `Position("ZORA", Side.Short, entry_time=...)`. A strategy with a time exit counts the bars from there, so its time exit lands on the same bar as without the restart. Without `entry_time` (it is `None`), the strategy cannot count the bars, so the position gets no time exit; rule exits, stops, and take profits still apply. A strategy reads only positions on its own instrument, so a caller may pass all of the account's positions.

### Stops live

`decide` returns opens and rule exits only. In a backtest the portfolio handles stops, take profits, trailing stops, partial take profits, and liquidation. Live, none of that runs. The caller's system must place them on the exchange from each `Order`, once the open fills:

- `stop_distance`, `take_profit_distance`, and `trail_distance` are fractions of the fill price, not prices. The caller computes the prices from the actual fill, as `backtest` does.
- The caller's system moves the trailing stop as the price improves, and never moves it back.
- The caller's system closes the partial take profit: when the take profit is hit, it closes `take_profit_fraction` of the position and leaves the rest open with no take profit.
- The caller's system watches liquidation and the hard stop. A position that the exchange closes is no longer in `positions` at the next `decide`.

## Tests

Each test program builds its own small price series by hand, runs the code, and compares the answers with values worked out by hand. It prints `FAIL ...` for each wrong value, and exits with 1 if anything failed or 0 if everything passed. The tests never read `data/`.

**`test_indicators.cpp`**: for each indicator: a basic case, the smallest window (1), a window longer than the series, an empty series, missing values, and bad arguments being refused. It also checks `prior_min` excludes the current bar, an old maximum leaves the window, `hour_of_day` at day edges and before 1970, and refused on 4-hour bars, and `chandelier` for both sides.

**`test_rolling.cpp`**: each rolling indicator, updated bar by bar, equals its full-series function bit for bit (NaN equals NaN), across NaN gaps and windows longer than the series.

**`test_append.cpp`**: `Markets::append` and `append_states` grow the bars, a reference taken before an append stays valid, an out-of-order `ts`, a missing market or timeframe, a missing base bar, and a states gap are refused, and a base bar past the clock's end extends the clock. A volume column must match the bars: `make` refuses a wrong length, `append` refuses a bar with no volume when the market has one, and an appended volume is stored.

**`test_live.cpp`**: records a backtest with `on_step`, then replays it as a live caller would: start from the first bar, `prepare`, and for each step append the bars and states, `update`, and `decide` with the recorded positions. The orders must be equal at every step, with no tolerance. It runs the five Veranta strategies, `StateTrend` on two markets with 1-minute and 1-hour bars, and a `Combined`. A strategy that looks ahead gives different orders and fails. It also checks that `avbt::decide` and `avbt::run`, called by table name, give the same orders and result.

**`test_run.cpp`**: the strategy table, called by name.

| Test | What it checks |
|---|---|
| `test_every_strategy_tunes` | every table strategy tunes by name, combined ones with `a.` and `b.` knobs; a bad or unknown knob throws |
| `test_by_name_matches_direct` | `run` by name gives the same result as building the strategy directly |
| `test_bad_input_throws` | an unknown strategy, an unknown param, a value out of range, a wrong type, and an unknown combined param throw; a valid param runs |
| `test_side_param` | each Veranta strategy has a `side` param (a combined pair has `a.side` and `b.side`); `"long"` makes long trades; the default stays short for `rally_short`; a bad value or a non-string throws |
| `test_state_delay` | `state_delay` of 30, 90, 0, or 61 throws; 60 and 420 run |
| `test_deterministic` | the same inputs give the same trades, equity, and `optimize` search, bit for bit |

**`test_portfolio.cpp`**:

| Test | Checks that |
|---|---|
| `test_open_sizes_from_risk` | the size makes a stop fill lose `risk_per_trade` of the balance |
| `test_open_short_liquidates_above` | a short's liquidation price is above its entry |
| `test_open_skips` | an open is refused for a second position in one instrument, a stop on the wrong side, a stop past the 80% cap, and collateral over the free cash; a second instrument still opens |
| `test_partial_close` | a partial close shrinks size and collateral together |
| `test_stop_fills_at_stop`, `test_gap_past_stop_fills_at_open` | stops fill at the level, or at the open on a gap |
| `test_gap_past_liquidation` | a gap past liquidation fills at the liquidation price |
| `test_mark_moves_equity` | equity follows the close |
| `test_hard_stop_counts_unrealized`, `test_hard_stop_holds_above_floor` | the hard stop uses equity and fires only at the floor |
| `test_take_profit_fills_at_level`, `test_take_profit_gap_fills_at_open` | take profits fill at the level, or at the open on a gap |
| `test_stop_wins_over_take_profit` | the stop fills when a bar reaches both |
| `test_take_profit_wrong_side` | a take profit on the losing side is refused |
| `test_fees` | the fee is charged at the open and at the close |
| `test_trailing_stop`, `test_trailing_stop_short` | a trailing stop follows the best price, never moves back, and fills at its level |
| `test_unmoved_trail_is_a_stop` | a stop-out with a trail that never moved the stop is a `Stop`, not a `TrailingStop` |
| `test_partial_take_profit` | a partial take profit closes its fraction, and the rest has no take profit and stays open |
| `test_open_refuses_bad_trail_or_fraction` | an open is refused for a negative trail or a fraction outside (0, 1] |
| `test_scale_risk_with_leverage` | with the setting on, 5x leverage gives 5 times the size and 5 times the loss at the stop |

**`test_backtest.cpp`**, with small test-only strategies (`UpDown`, `Script`, `FollowY`, `HalfAtTarget`):

| Test | Checks that |
|---|---|
| `test_signal_fills_at_next_open` | an order from bar t fills at bar t+1's open |
| `test_last_bar_order_never_fills` | an order from the last bar never fills |
| `test_fee_at_open_and_close` | the trade result includes both fees |
| `test_no_lookahead` | changing every bar after t changes no order, trade, or equity up to t |
| `test_timeframe_carried` | the result names the input's base timeframe |
| `test_markets_refuse_misaligned` | `Markets::make` refuses an empty list, a repeated name, a market with no timeframes, timeframes not finest first or repeated, different base timeframes, empty bars, uneven lists, timestamps that do not rise, and a missing base bar; markets of different lengths, and a missing coarser bar, are accepted |
| `test_each_market_uses_its_own_bars` | a position's stop uses its own market's low |
| `test_closes_fill_before_opens` | a close on a bar frees the cash for an open on the same bar |
| `test_no_lookahead_two_markets` | the lookahead test, with a strategy that reads one market to trade another |
| `test_last_closed` | `last_closed` returns -1 before a bar closes, the right bar mid-bar, and counts the last bar closed only after its full length |
| `test_market_starts_late` | the clock is the union of both markets; an order for a market before its first bar is dropped; the market trades from its first bar |
| `test_market_ends_early` | a position in a market whose data ends is closed at its last close with `EndOfData` |
| `test_coarser_timeframe` | a strategy on 4-hour bars acts once per 4-hour bar and fills at the open after the bar closes, never earlier |
| `test_partial_close_keeps_entry_bar` | both parts of a partly closed trade keep the trade's entry step |

**`test_states.cpp`**:

| Test | Checks that |
|---|---|
| `test_state_at_lag` | `state_at` reads the row one minute back, and returns `Unknown` before the start and past the end |
| `test_make_checks_states` | `Markets::make` refuses unequal columns and a start inside a minute; `states()` is `nullptr` without states |
| `test_state_trend_unknown_no_trade` | all-`Unknown` labels open no trade |
| `test_state_trend_opens_long` | a long opens at the next open, closes when the market label turns, and a `Shock` blocks it |
| `test_state_trend_signal` | `signal` set to `Hour1` matches the default, `Min1` changes the result, and a missing timeframe throws |

**`test_optimize.cpp`**: `sharpe` counts days with no bars as 0% returns, is −∞ after equity touches 0 during a day, NaN with fewer than 30 daily returns or on flat equity, and is the same on 1-minute and 1-hour bars; on a toy strategy whose best is known, `search` finds it, runs each combination once, stops after a pass with no gain, runs one pair in one round, gives the same runs for the same inputs, and stops when `progress` returns false. `walk_forward` is checked for its rows, the folds seen by `progress`, no folds throwing, and a fold with a null train or test `Markets` throwing (`test_walk_forward`).

**`test_strategies.cpp`**: one hand-built series per strategy, so that the entry fires on a known bar and the exit happens for a known cause. It also checks that a strategy entering on the bar a level closed its trade fills at the next open, that `Combined` on two markets makes the same trades as each strategy alone, and that `Combined` on one market takes turns. Restart checks: a strategy ignores a position on another instrument, a position with no entry time gets no time exit while an entry time of 0 does, a fresh `Combined` gives a held position to the strategy that trades it, and `StateTrend` keeps a position while its average has no value.

---

## Write a new strategy

1. In `strategies.hpp`, add a struct with a `Params` struct (including `instrument`, `side`, `take_profit`, `stop`, `time_exit`, `leverage`), a `signal_bar = -1` field, `prepare`, `update`, `entry`, and a `decide` that calls `decide_entry_and_exits`. Add `rule_exit` if it has one.
2. In `update`, feed rolling indicators the new bars; `prepare` resets them and calls `update`. In `entry` and `rule_exit`, read index `t` or earlier only. Choose a `timeframe` in `Params`, look the bars up with `m.at(instrument, params.timeframe)`, and let `decide_entry_and_exits` map `now` to `t`.
3. Add it to the `static_assert` at the end of the file.
4. Add a test in `test_strategies.cpp` with a hand-built series.
5. To call it from Python, add a field list and a row in `strategies()` in `run.hpp`. `run` then finds it by name. Costs come from the caller's `MarketCosts`, so the strategy has no fee.

A strategy that does not fit `decide_entry_and_exits` can write its own `decide`, as long as it returns orders and reads bars only through `last_closed(bars, now)`.

## Rules that must stay true

These come from [CLAUDE.md](../CLAUDE.md) and the ADRs, and each has a test:

- **Next-open fill.** An order from bar t fills at bar t+1's open. Strategies never call the portfolio.
- **No trade on an undefined value.** Indicators are NaN until they have enough bars, and a comparison with NaN is false.
- **No lookahead.** `decide(now)` reads only bars that `last_closed` returns, so never a bar still open. The lookahead tests guard this.
- **Levels are live from the entry bar.** The bar a position opens on is checked too.
- **The stop wins** when one bar reaches both levels.
- **Every result names its timeframe.**
- **The caller chooses the bars.** C++ checks the markets but never trims or pads them.
- **Closes fill before opens** on the same bar.
- **Live equals backtest.** Updating bar by bar gives the same numbers and orders as a full run; `test_rolling.cpp` and `test_live.cpp` guard this.
- **Costs belong to the market.** Every market needs a `Costs`, and no strategy sets a fee.

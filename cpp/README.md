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
8. [portfolio.hpp: the account](#portfoliohpp-the-account)
9. [backtest.hpp: orders, trades, and the loop](#backtesthpp-orders-trades-and-the-loop)
10. [strategies.hpp: the five strategies and Combined](#strategieshpp-the-five-strategies-and-combined)
11. [avbt_py.cpp: the Python module](#avbt_pycpp-the-python-module)
12. [Tests](#tests)
13. [Write a new strategy](#write-a-new-strategy)
14. [Rules that must stay true](#rules-that-must-stay-true)

---

## What the code does

A **backtest** replays a trading idea on past prices and records the trades the idea would have made. The C++ backtester takes:

- one or more **markets**: past prices for each instrument, such as ZORA or gold;
- one **strategy**: C++ code that decides when to open and close trades;
- the **portfolio settings**: the starting balance, how much to risk per trade, and when to stop trading.

It returns every closed **trade**, the account's **equity** at every bar, and the ending balance.

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
│   ├── markets.hpp           Market and Markets
│   ├── portfolio.hpp         the account: positions, levels, fees, liquidation, hard stop
│   ├── backtest.hpp          Order, Trade, Result, the Strategy concept, the backtest loop
│   └── strategies.hpp        the five Veranta strategies and Combined
├── src/
│   ├── indicators.cpp        indicator code
│   ├── markets.cpp           Markets::make and its lookups
│   └── portfolio.cpp         Portfolio methods
├── python/avbt_py.cpp        the Python module avbt_cpp
└── tests/test_*.cpp          one test program per header
```

Each header includes the ones below it:

```
strategies.hpp ─► backtest.hpp ─► portfolio.hpp ─► indicators.hpp
                              └─► markets.hpp ───► indicators.hpp
```

`backtest.hpp` and `strategies.hpp` have no `.cpp` file because they hold templates, and a template's code must be visible wherever it is used.

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
- `cmake --build build` compiles. It makes the library `libavbt.a` (the three `.cpp` files in `src/`), the four test programs, and the Python module when pybind11 is installed.
- `ctest` runs the four test programs. A test program passes when it exits with code 0.

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
| `pct_change(series, lag)` | `(series[i] - series[i - lag]) / series[i - lag]`: the change over `lag` bars as a fraction. 0.10 is a 10% rise. | `i < lag` |
| `true_range(bars)` | **True range**: the largest of `high - low`, `abs(high - previous close)`, and `abs(low - previous close)`. Bar 0 has no previous close, so it is `high - low`. | a high, low, or previous close is missing |
| `atr(bars, n)` | **Average true range**: the typical size of a bar's move. Bar n is the mean of true range at bars 1 to n. After that, Wilder smoothing: `(atr[i-1] × (n-1) + tr[i]) / n`. | `i < n`; a missing true range stays NaN from then on |
| `hour_of_day(bars)` | The UTC hour, 0 to 23, at which bar i opens. Throws if the timeframe is coarser than `Hour1`, because an opening hour means nothing on longer bars. | never |
| `bar_change(bars, now_field, then_field, lag)` | `now_field[i] / then_field[i - lag] - 1`. `bar_change(bars, High, Close, 1)` is "this bar's high over the previous close, minus 1". | `i < lag`, or a value is missing |
| `chandelier(bars, n, k, side)` | **Chandelier line**, a trailing-stop line. Long: `prior_max(high, n) - k × atr(n)`. Short: `prior_min(low, n) + k × atr(n)`. Throws if `k` is not above 0. | either part is NaN |

How `prior_max` and `prior_min` work: they keep a **deque** (a list you can add to or remove from at both ends) of bar indexes whose values are in falling order for `prior_max`, or rising order for `prior_min`. The front of the deque is always the answer. Each index enters and leaves once, so the whole series takes time proportional to its length, whatever `n` is.

`field_of` is a private helper in `indicators.cpp`: it returns the list named by a `Field`.

---

## markets.hpp: several markets on one clock

Files: `include/avbt/markets.hpp`, `src/markets.cpp`. Decisions: [ADR 0010](../docs/adr/0010-a-cpp-backtest-runs-on-markets-on-one-clock.md), [ADR 0011](../docs/adr/0011-markets-carry-several-timeframes-and-their-own-history.md).

### `struct Market`

| Field | Type | Meaning |
|---|---|---|
| `instrument` | `std::string` | The instrument's name, such as `"ZORA"`. Orders and positions use this name. |
| `timeframes` | `std::vector<Bars>` | Its price history on one or more timeframes, finest first. |

`timeframes[0]` is the **base**. Orders fill at its opens, and stops and take profits are checked on its highs and lows. The coarser timeframes are for strategies to read. Usually the base is 1 minute, so exits are exact without looking inside a coarse bar.

### `class Markets`

All the markets one backtest uses, on one **clock**: every base bar open of every market, sorted, with no repeats. Markets may start and end at different times. A market joins the run at its first bar. All markets must have the same base timeframe, so one step is one base bar everywhere.

The constructor is private, so the only way to get a `Markets` is `Markets::make`, and `make` always runs the check. Code that receives a `Markets` never has to check again.

| Method | What it does |
|---|---|
| `static Markets make(std::vector<Market> markets)` | Builds a `Markets`. Throws `std::invalid_argument`, naming the market, when: the list is empty; two markets share a name; a market has no timeframes, repeats one, or does not list them finest first; a market's base timeframe differs from the first market's; or a `Bars` is empty, has lists of different lengths, or has timestamps that do not rise. |
| `const Bars& at(const std::string& instrument, Timeframe tf) const` | Returns the market's bars on that timeframe. Throws if there is no such market or timeframe. |
| `const Bars& base(const std::string& instrument) const` | Returns the market's base bars. |
| `const std::vector<Market>& all() const` | Every market, in the order given to `make`. |
| `const std::vector<int64_t>& clock() const` | The clock: the UTC second at which each step opens. |
| `Timeframe timeframe() const` | The base timeframe, shared by every market. |
| `int bar_at(const std::string& instrument, int t) const` | The index of the market's base bar that opens at step `t`, or -1 when it has none then (its data has not started, or has ended). |

---

## portfolio.hpp: the account

Files: `include/avbt/portfolio.hpp`, `src/portfolio.cpp`. Decision: [ADR 0008](../docs/adr/0008-a-portfolio-scores-in-account-terms.md).

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
| `fee_rate` | Fraction of notional charged at every fill. |
| `liquidation_price` | Where the loss reaches 85% of the collateral. |
| `mark_price` | The last close seen; it values the unrealized result. |

### `enum class Cause`

Why a position closed.

| Value | Meaning |
|---|---|
| `Stop` | The price reached the stop (stop loss). |
| `TakeProfit` | The price reached the take profit. |
| `Liquidation` | The loss reached 85% of the collateral. |
| `HardStop` | Equity fell to the hard-stop floor. |
| `EndOfData` | The market's data ended while the position was open. The backtest closes it at the last close. |
| `Order` | The strategy sent a close order: a **time exit** (the trade was open for its maximum number of bars) or a **rule exit** (a condition said to leave). |

### `struct Closed`

What `close` and `check` return for each closed position.

| Field | Meaning |
|---|---|
| `instrument`, `side` | As in `Position`. |
| `entry_price`, `exit_price` | The two fill prices. |
| `size` | Units closed. |
| `result` | Price result minus the open fee and the close fee on this size, in account money. |
| `cause` | Why it closed. |

### `struct Quote`

One instrument's bar, handed to `check`: `instrument`, `open`, `high`, `low`, `close`.

### `struct Report`

A snapshot of the account.

| Field | Meaning |
|---|---|
| `balance` | Realized money. |
| `equity` | Balance plus every open position's unrealized result. |
| `free_cash` | Balance minus all collateral. |
| `open_positions` | Number of open positions. |
| `halted` | True once the hard stop has fired. |

### `class Portfolio`

Its state is private: `settings_`, `balance_`, `positions_`, and `halted_`. Only its methods change them.

**`Portfolio(PortfolioSettings settings)`**: the constructor. The balance starts at `starting_balance`.

**`bool open(instrument, side, entry_price, stop_price, leverage, take_profit_price = NaN, fee_rate = 0)`**

Opens a position and returns true, or opens nothing and returns false.

It sizes the position so that a fill at the stop loses `risk_per_trade` of the balance:

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
- the collateral is more than the free cash.

On success, the open fee (`fee_rate × notional`) leaves the balance, and the liquidation price is set 85% of the collateral's worth of price away from the entry. At leverage 1 that is an 85% price move.

**`std::optional<Closed> close(instrument, price, fraction = 1.0, cause = Cause::Order)`**

Closes `fraction` (from 0 to 1) of the instrument's position at `price`. The price result minus the close fee goes into the balance. It returns what closed, or nothing if there is no position. A partial close shrinks the size and the collateral by the same fraction, so the liquidation price does not move. No strategy uses partial closes yet.

**`std::vector<Closed> check(const std::vector<Quote>& quotes)`**

Applies one bar to every position that has a quote. For each position:

1. **Stop.** Has the bar's worst price (low for a long, high for a short) reached the stop?
2. **Take profit.** Has the bar's best price reached the take profit? If yes and the stop was not reached, close at the take profit, or at the open if the bar opened past it.
3. **Neither.** Mark the position at the close.
4. **Stop reached.** Close at the stop, or at the open if the bar opened past it (a gap). If that price is at or past the liquidation price, close at the liquidation price instead, with cause `Liquidation`.

If a bar reaches both the stop and the take profit, the stop fills. A bar does not show which price came first, so the backtest assumes the worse one ([ADR 0003](../docs/adr/0003-a-bar-with-both-levels-exits-at-the-stop.md)).

After every position, it checks the **hard stop**: if equity is at or below `starting_balance × (1 - hard_stop)`, it closes every position at its mark with cause `HardStop` and sets `halted_`. A position without a quote keeps its last mark.

It returns every position it closed.

**`Report report() const`**: builds a `Report` from the current state.

**`const std::vector<Position>& positions() const`**: the open positions, read-only.

**`double unrealized(const Position& p) const`** (private): `(mark_price - entry_price) × size` for a long; the opposite sign for a short.

---

## backtest.hpp: orders, trades, and the loop

File: `include/avbt/backtest.hpp`. Decisions: [ADR 0009](../docs/adr/0009-a-cpp-strategy-is-code-checked-by-a-concept.md), [ADR 0010](../docs/adr/0010-a-cpp-backtest-runs-on-markets-on-one-clock.md), [ADR 0011](../docs/adr/0011-markets-carry-several-timeframes-and-their-own-history.md).

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
| `fee_rate` | 0 | Fraction of notional charged at every fill of this position. |

Distances are fractions, not prices, because the strategy decides before it knows the next open. The loop turns them into prices at the fill: for a short filled at 1.00 with `stop_distance = 0.05`, the stop is 1.05.

A close order uses only `kind` and `instrument`.

### `struct Trade`

One closed trade.

| Field | Meaning |
|---|---|
| `instrument` | Which market. |
| `entry_bar`, `exit_bar` | Clock steps of the two fills: indexes into `Result::clock`. |
| `side` | Long or short. |
| `entry_price`, `exit_price` | The fill prices. |
| `result` | Account money gained or lost, after both fees. |
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

### `concept Strategy`

The requirements a type must meet to be a strategy. A type `S` is a strategy when:

- `s.prepare(markets)` compiles, with `markets` a `const Markets&`;
- `s.decide(now, report, positions)` compiles and returns `std::vector<Order>`, with `now` an `int64_t`, `report` a `const Report&`, and `positions` a `const std::vector<Position>&`.

There is no base class and no `virtual` function (a function looked up while the program runs). The compiler checks the concept when you call `backtest`, and it calls `decide` directly.

- `prepare` runs once, before the first step. A strategy computes its indicators there, on the timeframes it chooses.
- `decide` runs at the close of every clock step. `now` is the UTC second of that close. It must read bars only through `last_closed(bars, now)`, which never returns a bar still open.

### `template <Strategy S> Result backtest(S& strategy, const Markets& markets, PortfolioSettings settings)`

The loop described in [How one backtest runs](#how-one-backtest-runs). Details:

- It makes a fresh `Portfolio` from `settings`.
- It keeps a map from instrument to entry step, because the portfolio does not know clock steps. When a position closes, the loop turns the `Closed` into a `Trade` with both steps.
- **Fill step.** It moves close orders ahead of open orders, keeping their order otherwise (`std::stable_partition`). A close frees collateral, so a new open on the same bar can use it. It looks up the order's market bar with `bar_at`; an order for a market with no bar at this step is dropped. For each open order it computes the stop and take-profit prices from that base open and calls `portfolio.open`. A refused open is skipped, and nothing is recorded.
- **Check step.** It builds one `Quote` per open position from that position's own base bar and calls `portfolio.check`. A position whose market has no bar at this step is not checked.
- **End-of-data step.** Except on the run's last step, a market whose last base bar is this step has its position closed at that bar's close, with `Cause::EndOfData`.
- **Decide step.** It passes `now`, the step's open plus `seconds(timeframe)`, which is the close of the step.
- The caller chooses the bars. The loop never trims them.

The compiler makes one copy of `backtest` for each strategy type it is used with.

---

## strategies.hpp: the five strategies and Combined

File: `include/avbt/strategies.hpp`. The rules copy `examples/veranta_rules.py`.

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

Every strategy's `Params` has `instrument`, `side`, `take_profit`, `stop`, `time_exit`, `leverage = 1`, `fee_rate = 0.0001`, and `timeframe = Timeframe::Hour1`. The `Params` bar counts (such as `time_exit = 3`) count bars of that timeframe.

### `decide_entry_and_exits(s, now, positions)`

The shared decision, a template used by all five strategies. It first sets `t = last_closed(*s.bars, now)`. It returns nothing when `t` is -1 or equals `seen_bar`, so a strategy acts once per new bar on its timeframe, however fine the clock is. Then it sets `seen_bar = t` and:

- **While a position is open:** return a close order if the time exit is due (`t - signal_bar >= time_exit`) or the rule exit holds. Otherwise return nothing.
- **While flat:** if `entry(t)` is true, save `signal_bar = t` and return an open order with the strategy's side, stop, take profit, leverage, and fee.

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

### `template <Strategy A, Strategy B> struct Combined`

Runs two strategies as one strategy, in one account. The two strategies do not know about each other.

| Member | Meaning |
|---|---|
| `a`, `b` | The two strategies. Change their settings through `a.params` and `b.params`. |
| `owner` | A map from instrument to the strategy that owns its position: 0 for `a`, 1 for `b`. |
| `prepare(m)` | Calls `a.prepare(m)` and `b.prepare(m)`. |
| `decide(now, report, positions)` | See below. |
| `owned(positions, who)` (private) | The positions that `who` owns. |
| `take(orders, mine, who)` (private) | Adds one strategy's orders to the list. It drops an open order on an instrument someone already owns. Otherwise an open order makes its sender the owner. |

`decide` works in three steps:

1. Forget the owner of any instrument that no longer has a position (a level closed it, or the open was refused).
2. Ask `a`, passing only the positions `a` owns. Then ask `b`, passing only the positions `b` owns.
3. Return all the orders, `a`'s first.

The strategy that opens a position owns it and decides its exits. When both strategies trade the same instrument (such as `LateDayShort` and `RallyShort` on ZORA), they take turns: while one owns the ZORA position, the other's entries are dropped. When both open on the same bar, `a` wins. When they trade different instruments (such as `CampaignShort` on AVNT and `SpikeShort` on DYM), each makes the same trades it would make alone; only the sizes differ, because they share one balance.

A `static_assert` at the end of the file checks, at compile time, that all five strategies and both `Combined` pairs meet the `Strategy` concept.

---

## avbt_py.cpp: the Python module

File: `python/avbt_py.cpp`. It uses **pybind11**, a library that makes C++ functions callable from Python. The module is named `avbt_cpp`.

**Helpers** (in an unnamed namespace, so only this file sees them):

| Helper | What it does |
|---|---|
| `to_vector(a)` | Copies a 1-dimensional numpy array into a `std::vector`. Throws for other shapes. |
| `to_numpy(v)` | Hands a `std::vector<double>` or `std::vector<int64_t>` to numpy without copying it. |
| `make_bars(...)` | Builds `Bars` from numpy arrays and checks that every list is the same length. |
| `field_named`, `side_named` | Turn `"high"` into `Field::High`, `"short"` into `Side::Short`, and so on. |
| `cause_name(c)` | Turns a `Cause` into `"stop"`, `"take_profit"`, `"liquidation"`, `"hard_stop"`, `"order"`, or `"end_of_data"`. |
| `run_markets(strategy, markets, settings)` | Runs `backtest` and turns the `Result` into a Python dict. |
| `run<S>(bars, settings)` | Makes strategy `S` with default settings and runs it on one market named after its instrument. `bars` is a list of `Bars`, finest first. |
| `run_many<S>(bars_by_name, settings)` | Makes strategy `S` and runs it on `{name: [Bars, ...]}`, all checked by `Markets::make`. |

**What Python sees:**

| Python name | What it is |
|---|---|
| `Timeframe` | The enum, with the same twelve values (`Timeframe.Hour1`). |
| `timeframe_name(tf)`, `timeframe_seconds(tf)` | `name` and `seconds` for a `Timeframe`. |
| `Bars(timeframe, ts, open, high, low, close, minutes_with_data)` | Builds bars. `len(bars)` is the number of bars; `bars.timeframe` reads the timeframe back; `bars.ts`, `open`, `high`, `low`, `close` return copies of the columns as numpy arrays. |
| `PortfolioSettings(starting_balance=10000, risk_per_trade=0.01, hard_stop=0.30)` | The settings. |
| `late_day_short`, `rally_short`, `campaign_short`, `spike_short`, `gold_trend_long` | `([bars, ...], settings)`: one strategy on one market, given as its list of `Bars`, finest first. |
| `campaign_and_spike` | `({"AVNT": [bars, ...], "DYM": [bars, ...]}, settings, a_timeframe=Hour1, b_timeframe=Hour1)`: `Combined<CampaignShort, SpikeShort>`. `a_timeframe` and `b_timeframe` are the timeframes the two strategies read. |
| `late_day_and_rally` | `({"ZORA": [bars, ...]}, settings, a_timeframe=Hour1, b_timeframe=Hour1)`: `Combined<LateDayShort, RallyShort>`, with the same two timeframe arguments. |
| `sma`, `pct_change`, `prior_max`, `prior_min` | Take a numpy array and a number; return a numpy array. |
| `true_range`, `atr`, `hour_of_day`, `bar_change`, `chandelier` | Take `Bars`; return a numpy array. Fields and sides are strings. |

Each strategy function returns a dict:

```python
{
    "trades": [ {"instrument", "entry_bar", "exit_bar", "side",
                 "entry_price", "exit_price", "result", "cause"}, ... ],  # cause may be "end_of_data"
    "equity": numpy array, one value per clock step,
    "ending_balance": float,
    "timeframe": str,          # the base timeframe, such as "1 hour"
    "clock": numpy array,      # UTC second of each step; entry_bar and exit_bar index it
}
```

Each template copy is a separate compiled function, so a new strategy, or a new `Combined` pair, needs its own `m.def(...)` line.

Two example scripts use the module:

- `examples/veranta_rules_cpp.py` compares C++ trades with the Python engine's, trade by trade.
- `examples/veranta_cpp_chart.py` writes `examples/veranta_cpp_chart.html`: a summary table (trades, win rate, return, maximum drawdown, Sharpe ratio, exit causes) and a chart for each strategy.

---

## Tests

Each test program builds its own small price series by hand, runs the code, and compares the answers with values worked out by hand. It prints `FAIL ...` for each wrong value, and exits with 1 if anything failed or 0 if everything passed. The tests never read `data/`.

**`test_indicators.cpp`**: for each indicator: a basic case, the smallest window (1), a window longer than the series, an empty series, missing values, and bad arguments being refused. It also checks `prior_min` excludes the current bar, an old maximum leaves the window, `hour_of_day` at day edges and before 1970, and refused on 4-hour bars, and `chandelier` for both sides.

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

**`test_backtest.cpp`**, with small test-only strategies (`UpDown`, `Script`, `FollowY`):

| Test | Checks that |
|---|---|
| `test_signal_fills_at_next_open` | an order from bar t fills at bar t+1's open |
| `test_last_bar_order_never_fills` | an order from the last bar never fills |
| `test_fee_at_open_and_close` | the trade result includes both fees |
| `test_no_lookahead` | changing every bar after t changes no order, trade, or equity up to t |
| `test_timeframe_carried` | the result names the input's base timeframe |
| `test_markets_refuse_misaligned` | `Markets::make` refuses an empty list, a repeated name, a market with no timeframes, timeframes not finest first or repeated, different base timeframes, empty bars, uneven lists, and timestamps that do not rise; markets of different lengths are accepted |
| `test_each_market_uses_its_own_bars` | a position's stop uses its own market's low |
| `test_closes_fill_before_opens` | a close on a bar frees the cash for an open on the same bar |
| `test_no_lookahead_two_markets` | the lookahead test, with a strategy that reads one market to trade another |
| `test_last_closed` | `last_closed` returns -1 before a bar closes, the right bar mid-bar, and counts the last bar closed only after its full length |
| `test_market_starts_late` | the clock is the union of both markets; an order for a market before its first bar is dropped; the market trades from its first bar |
| `test_market_ends_early` | a position in a market whose data ends is closed at its last close with `EndOfData` |
| `test_coarser_timeframe` | a strategy on 4-hour bars acts once per 4-hour bar and fills at the open after the bar closes, never earlier |

**`test_strategies.cpp`**: one hand-built series per strategy, so that the entry fires on a known bar and the exit happens for a known cause. It also checks that a strategy entering on the bar a level closed its trade fills at the next open, that `Combined` on two markets makes the same trades as each strategy alone, and that `Combined` on one market takes turns.

---

## Write a new strategy

1. In `strategies.hpp`, add a struct with a `Params` struct (including `instrument`, `side`, `take_profit`, `stop`, `time_exit`, `leverage`, `fee_rate`), a `signal_bar = -1` field, `prepare`, `entry`, and a `decide` that calls `decide_entry_and_exits`. Add `rule_exit` if it has one.
2. In `prepare`, compute every line once. In `entry` and `rule_exit`, read index `t` or earlier only. Choose a `timeframe` in `Params`, look the bars up with `m.at(instrument, params.timeframe)`, and let `decide_entry_and_exits` map `now` to `t`.
3. Add it to the `static_assert` at the end of the file.
4. Add a test in `test_strategies.cpp` with a hand-built series.
5. To call it from Python, add an `m.def(...)` line in `avbt_py.cpp` with `run<YourStrategy>`, or `run_many<...>` for several markets.

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

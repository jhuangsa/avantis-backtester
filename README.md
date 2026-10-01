# avantis-backtester

A C++ backtester with a Python wrapper. It runs one **strategy** (C++ code that returns orders at each bar's close) on one or more **markets** (named price series on one shared clock), in one account with a balance, fees, stops, take profits, liquidation, and a hard stop. Orders fill at the next bar's open. The result is every closed trade, the equity at every bar, and the ending balance, in account money.

The language is in [CONTEXT.md](CONTEXT.md). Decisions that are easy to undo by accident are in [docs/adr](docs/adr). Indicator formulas are in [docs/research/indicators.md](docs/research/indicators.md).

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

## Trade live with the same code

`Live(name, params, markets)` runs a strategy one bar at a time, with the same code and the same numbers as a backtest. Each time a bar closes:

1. Add the closed bars with `markets.append(...)`, and each state label when it arrives with `markets.append_states(...)`.
2. Call `live.decide(now, positions)`. It returns the orders.
3. Send the orders to the exchange.

`decide` returns only openings and rule exits. Your system places the stops, take profits, trailing stops, and partial take profits on the exchange, from each order's distances. After a restart, give each open position its `entry_time` so time exits still work. Details are in [cpp/README.md](cpp/README.md) and [ADR 0017](docs/adr/0017-indicators-update-one-bar-at-a-time.md).

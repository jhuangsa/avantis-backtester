# avantis-backtester

Score one hypothesis on a bar series you pass in. The result is the price change of each trade, compounded, with a cause on every trade. The engine does not read the account's fill files and does not resample.

A hypothesis names entries and a stop. It may name a target. Or it names long, short, or flat from bars that have already closed.

The language is in [CONTEXT.md](CONTEXT.md). Decisions that are easy to undo by accident are in [docs/adr](docs/adr). Indicator formulas are in [docs/research/indicators.md](docs/research/indicators.md).

Requires Python 3.13.

## Install

From the repository root:

```bash
python3 -m pip install -e '.[test]'
```

The install has no runtime dependencies. The `[test]` extra adds pytest.

## Run the tests

```bash
python3 -m pytest
```

A test builds a small series and a hypothesis, then checks the trades: entry bar, exit bar, side, fill prices, cause, and ending stake. The suite does not read anything in `data/`.

## Quick start

No install is required for this program. From the repository root:

```bash
python3 examples/mean_reversion.py
```

It buys when the close crosses below a 4-bar average, and sells when the close crosses back above. The target and the stop are 8 percent of the entry. The series is ten made-up hourly bars: four quiet bars at 100, a dip to 96, then a climb back through the average.

You should see one trade. The dip crosses below the average on bar 4, so the fill is bar 5's open, 97. The close crosses back above the average on bar 6, so the exit is bar 7's open, 98. The cause is a rule exit. The ending stake is 98/97.

The same command then repeats the hypothesis for a 4-bar average and a 5-bar average. The 5-bar average never crosses, so that cell has no trade. Both cells are printed. The program checks these results and exits with an error if they change.

## The C++ backtester

`cpp/` holds a second engine, written in C++ for speed. It runs one **strategy** (C++ code that returns orders at each bar's close) on one or more **markets** (named price series on one shared clock), in one account with a balance, fees, stops, take profits, liquidation, and a hard stop. Orders fill at the next bar's open. The result is every closed trade, the equity at every bar, and the ending balance, in account money.

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

# avantis-backtester

A C++ backtester (`cpp/`) with a Python wrapper, the pybind11 module `avbt_cpp`. It runs strategies on caller-supplied markets in one account.

## Guards

Every change keeps these true: next-open fill; no trade on an undefined operand; a cross is two bars; a swing publishes at confirmation; every result names its timeframe; the caller chooses the slice. The engine does not read the fill files.

## Where to look

**Terms** (strategy, order, market, line, operand, backtest): `CONTEXT.md`. Use its words and its _Avoid_ lists.

**Decisions** that are easy to undo by accident: `docs/adr/`, one file per decision, numbered. Read the matching ADR before changing entries, stops, targets, or fills.

**Indicator formulas and defaults**: `docs/research/indicators.md`. Lookahead and other ways a backtest lies: `docs/research/backtest-pitfalls.md`. API naming research: `docs/research/backtest-apis.md`.

**Python tests**: `tests/`, run with `python3 -m pytest` from the root. Tests never read `data/`.

**C++** (`cpp/`, built with `cpp/CMakeLists.txt`):
- `include/avbt/indicators.hpp`, `src/indicators.cpp`: the `Bars` struct and the indicators, each a rolling object with `update`, one bar at a time; ADR 0017.
- `include/avbt/portfolio.hpp`, `src/portfolio.cpp`: the portfolio (balance, positions, take profit, partial take profit, trailing stop, fees, holding costs, liquidation, hard stop); ADR 0008, 0014, 0016.
- `include/avbt/costs.hpp`: `Costs` per market (open and close fee, signed holding costs), `MarketCosts`, `check_costs`; a market with no `Costs` is an error; ADR 0016.
- `include/avbt/markets.hpp`, `src/markets.cpp`: `Market` and `Markets`, several markets, each on several timeframes, on one clock; `append` and `append_states` grow it; ADR 0011, 0017.
- `include/avbt/backtest.hpp`: `Order`, `Trade`, the `Strategy` concept (`prepare`, `update`, `decide`), and the `backtest` template loop over `Markets`, with an `on_step` hook; ADR 0009, 0010, 0011, 0017.
- `include/avbt/states.hpp`: the state labels of a market and `state_at`; ADR 0012.
- `include/avbt/strategies.hpp`: the five Veranta strategies, `StateTrend` (its signal timeframe set per instrument), and `Combined`, which runs two as one.
- `include/avbt/run.hpp`: the strategy table, `strategies()`, `run(name, params, markets, costs, settings)`, and `Live`, one strategy kept alive for live calls; a `Position` carries `entry_time` so time exits survive a restart.
- `include/avbt/version.hpp`: `avbt::version`, read by C++ and Python.
- `include/avbt/optimize.hpp`: `sharpe`, `summary`, and `optimize(name, ...)`, a greedy search over pairs of knobs for any strategy, with a progress callback; ADR 0013.
- `python/avbt_py.cpp`: the pybind11 module `avbt_cpp`: `Market`, `Markets`, `Costs`, `run`, `strategies`, `Result`, `Trade`, and `Live` (`decide` for a live caller; stops are the caller's, see `cpp/README.md`); no logic of its own.
- `tests/test_*.cpp`: plain test programs; exit code 0 is a pass. `test_live.cpp` replays a backtest through `append`, `update`, `decide` and needs equal orders.
- `README.md`: the guide to every C++ file, type, function, and test.
- Build from `cpp/`: `cmake -S . -B build`, `cmake --build build`, `ctest --test-dir build --output-on-failure`. `build/` is generated.

**Examples** (`examples/`): runnable scripts, each with its run command in its docstring. The five-wallet page (`veranta_top5.py`, trades in `veranta_trades/`), its rules run in C++, plus strategies 3 and 4 combined in one account (`veranta_rules_cpp.py`), and the C++ results charted with strategies 1 and 2 combined (`veranta_cpp_chart.py`). `timeframes.py` builds the 12 C++ timeframes from 1-minute candles; `mixed_timeframes.py` runs strategy 3 on AVNT 4-hour bars and strategy 4 on DYM 15-minute bars in one account. `clickhouse_data.py` loads Avantis minute candles and market states from ClickHouse, cached in `data/candles/`; `state_trend.py` runs `StateTrend` on BTC and ETH, on 1-minute and 1-hour bars; `state_trend_optimize.py` tunes it with `optimize` and checks the winner on a later month; `walk_forward.py` tunes it on each training window and scores the winner on the next test window, the folds built by the caller. The `.html` files are their outputs.

**Presentation**: `docs/presentation/state_trend_tour.html`, a page for day traders on what the backtester does, with StateTrend on BTC and ETH.

**Trader history analysis**: `analysis/make_charts.py` reads `data/` and writes `docs/figures/`. Results are in `docs/findings.md`.

**Data**: `data/*.csv` and `Wonyotti Trading History/` are local-only fill exports, ignored by git. `data/candles/` holds downloaded market candles that the examples reuse; it is ignored by git and never committed. Private database notes are in `docs/database.md`, ignored by git; never commit or push it.

**Setup**: `README.md` (Python 3.13, `pip install -e '.[test]'`).

## Agent skills

### Issue tracker

GitHub Issues on jhuangsa/avantis-backtester, via `gh`. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default labels. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the root. See `docs/agents/domain.md`.

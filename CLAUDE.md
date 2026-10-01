# avantis-backtester

Scores one hypothesis on one caller-supplied bar series. Python engine, with a C++ indicator library in progress.

## Guards

Every change keeps these true: next-open fill; no trade on an undefined operand; a cross is two bars; a swing publishes at confirmation; every result names its timeframe; the caller chooses the slice; the score is price percent. The engine does not read the fill files.

## Where to look

**Terms** (hypothesis, rule, line, operand, backtest, grid): `CONTEXT.md`. Use its words and its _Avoid_ lists.

**Decisions** that are easy to undo by accident: `docs/adr/`, one file per decision, numbered. Read the matching ADR before changing entries, stops, targets, or fills.

**Indicator formulas and defaults**: `docs/research/indicators.md`. Lookahead and other ways a backtest lies: `docs/research/backtest-pitfalls.md`. API naming research: `docs/research/backtest-apis.md`.

**Python engine** (`backtest/`):
- `engine.py`: hypotheses, rules, the backtest, and the grid.
- `lines.py`: SMA and average true range.
- `catalog.py`: every other indicator kind, aligned to the caller's bars.
- `__init__.py`: the public names.

**Python tests**: `tests/`, run with `python3 -m pytest` from the root. Tests build their own series and never read `data/`.

**C++** (`cpp/`, built with `cpp/CMakeLists.txt`):
- `include/avbt/indicators.hpp`, `src/indicators.cpp`: the `Bars` struct and the indicators.
- `include/avbt/portfolio.hpp`, `src/portfolio.cpp`: the portfolio (balance, positions, take profit, fees, liquidation, hard stop); ADR 0008.
- `include/avbt/markets.hpp`, `src/markets.cpp`: `Market` and `Markets`, several markets, each on several timeframes, on one clock; ADR 0011.
- `include/avbt/backtest.hpp`: `Order`, `Trade`, the `Strategy` concept, and the `backtest` template loop over `Markets`; ADR 0009, 0010, 0011.
- `include/avbt/states.hpp`: the state labels of a market and `state_at`; ADR 0012.
- `include/avbt/strategies.hpp`: the five Veranta strategies, `StateTrend` (its signal timeframe set per instrument), and `Combined`, which runs two as one.
- `include/avbt/optimize.hpp`: `sharpe` and `optimize`, a greedy search over pairs of knobs; ADR 0013.
- `python/avbt_py.cpp`: the pybind11 module `avbt_cpp`.
- `tests/test_*.cpp`: plain test programs; exit code 0 is a pass.
- `README.md`: the guide to every C++ file, type, function, and test.
- Build from `cpp/`: `cmake -S . -B build`, `cmake --build build`, `ctest --test-dir build --output-on-failure`. `build/` is generated.

**Examples** (`examples/`): runnable scripts, each with its run command in its docstring. Breakout-30 (`breakout30*.py`), snapback (`btc_snapback.py`), mean reversion (`mean_reversion.py`), the 2 bp Avantis cells (`hf_2bps.py`, `build_hf_folio.py`), Lighter (`lighter_ensemble.py`), the five-wallet page (`veranta_top5.py`, trades in `veranta_trades/`), its rules scored in Python (`veranta_rules.py`) and in C++ against Python, plus strategies 3 and 4 combined in one account (`veranta_rules_cpp.py`), and the C++ results charted with strategies 1 and 2 combined (`veranta_cpp_chart.py`). `timeframes.py` builds the 12 C++ timeframes from 1-minute candles; `mixed_timeframes.py` runs strategy 3 on AVNT 4-hour bars and strategy 4 on DYM 15-minute bars in one account. `clickhouse_data.py` loads Avantis minute candles and market states from ClickHouse, cached in `data/candles/`; `state_trend.py` runs `StateTrend` on BTC and ETH, on 1-minute and 1-hour bars; `state_trend_optimize.py` tunes it with `optimize` and checks the winner on a later month. The `.html` files are their outputs.

**Trader history analysis**: `analysis/make_charts.py` reads `data/` and writes `docs/figures/`. Results are in `docs/findings.md`.

**Data**: `data/*.csv` and `Wonyotti Trading History/` are local-only fill exports, ignored by git. `data/candles/` holds downloaded market candles that the examples reuse; it is ignored by git and never committed. Private database notes are in `docs/database.md`, ignored by git; never commit or push it.

**Setup**: `README.md` (Python 3.13, `pip install -e '.[test]'`).

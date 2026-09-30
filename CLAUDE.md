# avantis-backtester

Scores one hypothesis on one caller-supplied bar series. Python engine, with a C++ indicator library in progress.

## Guards

Every change keeps these true: next-open fill; no trade on an undefined operand; a cross is two bars; a swing publishes at confirmation; every result names the bar size; the caller chooses the slice; the score is price percent. The engine does not read the fill files.

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
- `include/avbt/portfolio.hpp`, `src/portfolio.cpp`: the portfolio (balance, positions, liquidation, hard stop); ADR 0008.
- `tests/test_*.cpp`: plain test programs; exit code 0 is a pass.
- Build from `cpp/`: `cmake -S . -B build`, `cmake --build build`, `ctest --test-dir build --output-on-failure`. `build/` is generated.

**Examples** (`examples/`): runnable scripts, each with its run command in its docstring. Breakout-30 (`breakout30*.py`), snapback (`btc_snapback.py`), mean reversion (`mean_reversion.py`), the 2 bp Avantis cells (`hf_2bps.py`, `build_hf_folio.py`), Lighter (`lighter_ensemble.py`), the five-wallet page (`veranta_top5.py`, trades in `veranta_trades/`). The `.html` files are their outputs.

**Trader history analysis**: `analysis/make_charts.py` reads `data/` and writes `docs/figures/`. Results are in `docs/findings.md`.

**Data**: `data/*.csv` and `Wonyotti Trading History/` are local-only fill exports, ignored by git. Private database notes are in `docs/database.md`, ignored by git; never commit or push it.

**Setup**: `README.md` (Python 3.13, `pip install -e '.[test]'`).

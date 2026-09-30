# A C++ strategy is code checked by a concept, and it returns orders

The C++ engine does not port the Python hypothesis or its rule trees (`Cross`, `Above`, `Below`, `All`, `Any`). Each trading idea is its own C++ struct. A C++20 concept named `Strategy` lists what every strategy must have: `prepare(bars)`, which computes its lines once, and `decide(t, report, positions)`, which returns orders. There is no base class and no virtual call.

The backtest is a function template over that concept. The compiler makes one copy per strategy and calls `decide` directly, so it can be inlined. The template lives in a header.

A strategy returns orders and never opens or closes a position itself. The backtest owns the clock: an order returned at the close of bar t fills at the open of bar t+1. Stops and take profits are levels on the position, and the portfolio fills them.

This keeps the C++ simple and fast. There is no interpreter to port: about 800 of the roughly 1,070 lines in `engine.py` validate and interpret rule trees. The cost is that a strategy stores whole lines and could read a later bar. A lookahead test guards that: change every bar after t, run again, and the orders and trades up to t must be the same.

The Python hypothesis and its price-percent score are unchanged.

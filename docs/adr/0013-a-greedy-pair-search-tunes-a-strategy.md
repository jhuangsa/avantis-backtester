# A greedy pair search tunes a strategy

`optimize` in `cpp/include/avbt/optimize.hpp` searches any strategy in the table, by name, for the params with the highest Sharpe. ADR 0018 adds a second goal: the highest return under a drawdown limit. A knob is one param name and the values it may take, as `run` takes them. The search changes two knobs at a time, because a full grid over every knob grows too fast to run.

The rules:

- Every knob starts at its first value.
- One round tries every combination of one pair of knobs, with the other knobs at the best so far. The pairs come in a fixed order, so the same inputs give the same answer.
- Any higher Sharpe wins, however small the gain. NaN, from equity that never moves, never wins.
- Every run counts, however few trades it has.
- Only the signal timeframe is a knob. The base timeframe, and with it the clock, the fills, and the stops, stays fixed.
- Sharpe samples equity at each UTC midnight and annualizes over 365 days, so bars of every length give the same scale. Equity at or below 0 at any step scores −∞, so a wiped-out run ranks worst.

Consequences:

- The answer depends on the starting values and the pair order. A greedy search can stop at a local best.
- A high Sharpe from a few trades can win by chance, and nothing guards against it. Check the winner on bars the search did not see; the caller chooses both slices. `walk_forward` does this per fold, on folds the caller cuts, and reports train Sharpe minus test Sharpe as `overfit`.

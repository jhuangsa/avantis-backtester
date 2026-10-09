# A search may cap drawdown and maximize return

`optimize` and `walk_forward` take a `Goal`. With no `max_drawdown`, the search maximizes Sharpe, as in ADR 0013. With one, it maximizes total return over the runs whose max drawdown is within the limit.

The rules:

- `max_drawdown` is a positive fraction: 0.1 is a 10% fall from an equity peak.
- A run over the limit scores NaN, so it never becomes the best. It still counts as run, and stays in `runs`.
- The search is the same pair-by-pair search. Only the score changes.
- `min_gain` sets when a gain is too small to count. A smaller rise still becomes the best, but does not keep the search going: a full pass with no gain above `min_gain` stops it.
- When no run meets the limit, the score is NaN and `best` is the start.

Consequences:

- The winner tends to sit just under the limit: return grows with size and leverage until the limit stops it. On bars the search did not see, its drawdown is often over the limit. Check it with `walk_forward`.
- The search changes two knobs at a time, so it cannot cross a region over the limit to reach a better one beyond it.

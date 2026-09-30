_Superseded in part by [ADR 0011](0011-markets-carry-several-timeframes-and-their-own-history.md): markets may have several timeframes and their own history. The clock rules below no longer hold._

# A C++ backtest runs one strategy on several markets on one clock

The C++ backtest takes `Markets`: several markets, each an instrument name and its bars. The strategy's `prepare` receives all of them, so one strategy can trade several instruments or read one market to trade another. A strategy that trades one market looks it up by name.

Every market is on one clock: the same bar size, the same number of bars, and the same timestamps, so bar t is the same moment everywhere. The caller aligns the markets, which keeps "the caller chooses the slice"; the C++ engine only checks. `Markets::make` is the only way to build a `Markets`, and it refuses markets that do not line up, so the loop and the strategies never check again. A market that starts later is not padded with empty bars: the caller trims every market to the hours they share. Padding would put undefined prices into the lines.

Each order fills at the next open of its own instrument, and each position is checked against its own instrument's bar. When one bar has several orders, closes fill before opens, so collateral freed on that bar can pay for a new trade on it. Opens then fill in the order the strategy returned them, and an open with too little free cash is refused, as before.

Several single-market strategies run in one account by combining them into one strategy (`Combined<A, B>`), not by a second kind of backtest.

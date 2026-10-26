# Wallet returns

`examples/wallet_returns.py` measures how well a wallet traded. It works on Avantis and on Hyperliquid. Its question: how much did the trader make on the most of their own money they ever had in positions at once?

## Terms

- **Margin** (collateral): the trader's own money put into a position. Notional = margin × leverage.
- **Close**: one event that closes all or part of a position. On Avantis, each row of the trade history is one close.
- **Fill**: one trade on Hyperliquid. It opens, grows, shrinks or closes a position.
- **Realized PnL** (R): the profit or loss of closes so far, after fees and funding.
- **Unrealized PnL** (U): the profit or loss of positions still open, at the hour's price. It does not deduct the fee to close them.
- **Total PnL** (P): R + U.
- **Margin open** (M): the margin still in open positions, at cost.
- **Own money in positions** (O): M − R. Taking out R keeps reinvested profit from counting as new money.
- **Capital** (K): the highest O so far. It never looks ahead.
- **Return** (ρ): P / K. Nothing is compounded.
- **Grid**: the end of each UTC hour, from the first open to the last event.

Deposits, withdrawals and idle cash change nothing. Leverage raises the return, since the capital is margin, not notional.

## The math

**Avantis.** Each close carries its margin, price PnL, closing fee and funding. R adds up price PnL less fee and funding at each close. U prices the margin still open at the hour's 1-hour close: side × margin × leverage × (price / open price − 1). The history reports funding only at the close, so U spreads it evenly over the hold, and R takes it exactly at the close. The history has no opening fee, so none is charged.

**Veranta.** Avantis is now Veranta. For a wallet saved by `examples/veranta_history.py`, the old closes are as above, and the v2 history rows rebuild each book (one market and side): an open or increase adds its coins and notional, a close takes out its coins at its own open price. U is each book at the hour's price. Fees on opens and increases, and margin used up in open positions, come from the wallet's USDC transfers on Base. Margin is each book's cost over its average leverage, an estimate. See `docs/mimic_frontier.md`.

**Hyperliquid.** Each coin's position is rebuilt from its fills: its size and its average entry price. A fill that shrinks the position realizes its price PnL. R adds that, less each fill's fee, plus funding as it is paid. U = size × (price − entry). Fills carry no margin, so margin is estimated as |size| × entry / the coin's current leverage setting. When the setting is unknown, the wallet's median setting is used, else 1. A position open at the window start enters at the first price; the page lists such coins as "carried in". The window is the last 200 days, the reach of the 1-hour candles.

The full specification is GitHub issue #15.

## The checks

Each wallet's page section has a table of checks. "ok" passes, "fail" fails, "note" only reports.

- **8.** P = R + U at every hour.
- **9.** With no margin open, U = 0.
- **10.** Capital never falls. On Avantis it rises only in an hour with an open, unless a close lost more than its margin.
- **11.** The return has the sign of P.
- **12.** The return is at least −100%. On Hyperliquid a breach means the margin estimate is too low.
- **15.** Avantis: R at the last hour equals the sum of (money sent to the trader − margin) over all closes.
- **16.** Hyperliquid: R at the last hour equals the fills' realized PnL less fees plus funding. A position carried in, or fills missing from the API, make a gap expected: then it is a note.
- **U ≥ −M.** Unrealized loss never exceeds the margin open, the argument behind check 12. A note when it holds; a fail, with the hours that break it and the worst U / M, means the margin estimate is too low.
- **17.** Avantis leaderboard (a local export, when it lists the wallet): R against the leaderboard's realized PnL; the leaderboard's return reproduced from the history, Σ (money sent − margin) / Σ margin, within 0.05 percentage points; and coverage, the history's margin over the leaderboard's. PnL and return are judged only at 99% coverage or more. Coverage under 90% fails: the history is missing trades.
- **18.** Hyperliquid: the change in P over the window against the change in the portfolio's PnL history. A gap of over 10% means spot trades or other flows the perp fills miss.
- **19.** Hyperliquid: the API serves only the newest 10,000 fills. A wallet at the limit whose oldest fill is after the window start fails: its older fills in the window are gone.
- **20.** Hyperliquid: missing fills (gap share) over 1% of the traded notional fails.

## How to run

From the repository root:

    python3 examples/wallet_returns.py ADDRESS...
    python3 examples/wallet_returns.py --venue hyperliquid ADDRESS...

Each wallet's table goes to `data/candles/wallet_returns_<venue>_<address>.csv`, the git-ignored cache, one row per grid hour. The page is `examples/wallet_returns_<venue>.html`, one per venue. Downloads reuse the caches of `examples/mimic.py`.

The table's columns: `time`, `realized`, `unrealized_price`, `unrealized_funding`, `unrealized`, `fees`, `funding`, `pnl`, `margin_open`, `own`, `capital`, `return`, `margin_estimated`.

Tests: `tests/test_wallet_returns.py` (the math, checks 1–16) and `tests/test_wallet_returns_cli.py` (the checks table and the page).

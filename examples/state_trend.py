"""StateTrend on BTC/USD and ETH/USD, in one account.

Each market carries 1-minute and 1-hour bars, built from the Avantis
1-minute candles, and the minute market states from ClickHouse. The hourly
close against its average and the trend label give the bias; the market
label, a 30-minute breakout and the volatility label time the entry on the
minute bars. See StateTrend in cpp/include/avbt/strategies.hpp.

It runs three times: as written, with the default parameters; flipped,
taking the other side of every trade; and flipped with parameters tuned on
these same three months, so its result is in sample and overfitted.

It reads the files examples/clickhouse_data.py caches in data/candles/, and
downloads them when they are missing. Build the module first (cpp/README.md),
then run from the repository root:

    python3 examples/state_trend.py
"""

import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))

import clickhouse_data as ch  # noqa: E402
from btc_bars import resample  # noqa: E402
from timeframes import avbt_cpp, to_bars  # noqa: E402

TF = avbt_cpp.Timeframe
START, END = "2026-06-01", "2026-09-01"
PAIRS = {"BTC": 1, "ETH": 0}
# Found by a search on June to August 2026. Trades only when 2 hourly ATRs
# are at least 2% of price, so the flipped strategy acts in volatile hours.
TUNED = {"flip": True, "average": 20, "breakout": 30, "atr_stops": 2.0, "min_stop": 0.02}


def market(pair_id: int):
    """One market's [1-minute, 1-hour] Bars and its States."""
    minutes = ch.minutes(pair_id, START, END)
    bars = [to_bars(TF.Min1, resample(minutes, "1min")), to_bars(TF.Hour1, resample(minutes, "1h"))]
    start, *codes = ch.clean_states(ch.states(pair_id, START, END))
    return bars, avbt_cpp.States(start, *codes)


def report(r, label: str) -> None:
    """Print one run's balance, Sharpe, and trades per instrument."""
    trades = pd.DataFrame(r["trades"])
    # Sharpe from hourly equity returns, 24 * 365 hours a year.
    returns = pd.Series(r["equity"][::60]).pct_change().dropna()
    sharpe = returns.mean() / returns.std() * (24 * 365) ** 0.5 if returns.std() > 0 else float("nan")
    print(f"StateTrend {label}, {START} to {END}, base timeframe {r['timeframe']}")
    print(f"ending balance {r['ending_balance']:.2f} from 10000.00, Sharpe {sharpe:.2f}")
    if trades.empty:
        print("no trades")
        return
    for name, t in trades.groupby("instrument"):
        print(f"{name}: {len(t)} trades, {(t.result > 0).mean():.0%} won, result {t.result.sum():.2f}, "
              f"long {(t.side == 'long').sum()}, short {(t.side == 'short').sum()}")
    print("exits:", dict(Counter(trades.cause)))


if __name__ == "__main__":
    markets = {name: market(pid) for name, pid in PAIRS.items()}
    for label, values in [("as written", {}), ("flipped", {"flip": True}), ("flipped, tuned", TUNED)]:
        params = avbt_cpp.StateTrendParams()
        for key, value in values.items():
            setattr(params, key, value)
        report(avbt_cpp.run_state_trend(markets, avbt_cpp.PortfolioSettings(), params), label)
        print()

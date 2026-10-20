"""Buy and hold BTC for one month under the Upside Perps profit share.

For an up month (2026-08), a down month (2026-06) and a month that went
both ways (2025-04), at 1x, 10x and 50x, it runs the library strategy
buy_hold on 1-minute bars: one long at the open of the second minute, held
to the month's last close. The stop sits 0.75 / leverage from the open
(buy_hold's default) and risk_per_trade is 0.075, so every run puts up
about 1,000 of collateral out of 10,000 (a little less: BTC's table spread
adds to the stop's loss). No holding costs (no funding data).

A one-bar market one minute after the month makes BTC's data end before the
run does, so the backtest closes the long with cause end_of_data.

Each row checks the engine's fee against the tier rule computed here.

It reads data/candles/btc_1m.csv. Run from the repository root:

    python3 examples/buy_hold_fees.py
"""

from __future__ import annotations

import pandas as pd

from timeframes import ROOT, TF, UPSIDE, avbt_cpp, to_bars
from btc_bars import resample

MONTHS = {"up": "2026-08", "down": "2026-06", "both": "2025-04"}
LEVERAGES = [1, 10, 50]
TIERS = [(2.5, 0.25), (5.0, 0.20), (15.0, 0.10), (25.0, 0.05)]


def month_bars(minutes: pd.DataFrame, month: str) -> pd.DataFrame:
    """One UTC month of 1-minute bars, gaps filled flat."""
    start = pd.Timestamp(month, tz="UTC")
    lo, hi = start.timestamp(), (start + pd.offsets.MonthBegin()).timestamp()
    return resample(minutes[(minutes.ts >= lo) & (minutes.ts < hi)], "1min")


def markets(bars: pd.DataFrame) -> avbt_cpp.Markets:
    """BTC, and a one-bar market after it so BTC ends before the run."""
    last = bars.iloc[-1:].assign(ts=bars.ts.iloc[-1] + 60, minutes_with_data=1)
    return avbt_cpp.Markets([avbt_cpp.Market("BTC", [to_bars(TF.Min1, bars)]),
                             avbt_cpp.Market("PAD", [to_bars(TF.Min1, last)])])


def expected_fee(gross: float, roi: float) -> float:
    """The tier rule: first tier with roi <= max_roi, else the last."""
    if gross <= 0:
        return 0.0
    share = next((s for m, s in TIERS if roi <= m + 1e-9), TIERS[-1][1])
    return share * gross


def main() -> None:
    minutes = pd.read_csv(ROOT / "data" / "candles" / "btc_1m.csv")
    minutes = minutes.drop_duplicates("ts").sort_values("ts")
    costs = {"BTC": UPSIDE, "PAD": avbt_cpp.Costs(spread=0)}
    head = (f"{'month':>12} {'lev':>4} {'entry':>10} {'exit':>10} {'move%':>7} "
            f"{'collat':>8} {'gross':>9} {'roi%':>8} {'fee':>8} {'fee/gr%':>7} "
            f"{'result':>9} {'cause':>12} match")
    print(head)
    for label, month in MONTHS.items():
        m = markets(month_bars(minutes, month))
        for lev in LEVERAGES:
            settings = avbt_cpp.PortfolioSettings(
                starting_balance=10000, risk_per_trade=0.075, scale_risk_with_leverage=False)
            params = {"leverage": float(lev)}
            r = avbt_cpp.run("buy_hold", params, m, costs, settings)
            t = r.trades[0]
            collateral = t.size * t.entry_price / lev
            gross = t.result + t.fees + t.holding_costs
            roi = gross / collateral
            move = t.exit_price / t.entry_price - 1
            want = expected_fee(gross, roi)
            share = f"{t.fees / gross:7.1%}" if gross > 0 else f"{'-':>7}"
            ok = "yes" if abs(t.fees - want) < 1e-6 else "NO"
            cause = t.cause.name
            print(f"{label + ' ' + month:>12} {lev:>4} {t.entry_price:>10.2f} "
                  f"{t.exit_price:>10.2f} {move:>7.2%} {collateral:>8.2f} "
                  f"{gross:>9.2f} {roi:>8.1%} {t.fees:>8.2f} {share} "
                  f"{t.result:>9.2f} {cause:>12} {ok}")


if __name__ == "__main__":
    main()

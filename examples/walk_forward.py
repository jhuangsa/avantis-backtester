"""Walk-forward check of StateTrend on BTC and ETH with the C++ optimizer.

The history is cut into folds: a training window, then the test window
right after it. Each fold tunes StateTrend on its training window only,
then runs the winner on its test window, which the search never saw. The
next fold moves both windows forward by the step. Each window is its own
Markets, so indicators warm up again at the start of every window. It reads
the candles and states that examples/state_trend.py cached under
data/candles/. Build the module first (cpp/README.md), then run from the
repository root:

    python3 examples/walk_forward.py
    python3 examples/walk_forward.py --train 45 --test 31 --step 16 --rounds 4

Each window needs at least 31 days, for 30 daily returns in the Sharpe.
With three cached months the test windows of the default folds overlap.
It prints one line per search round and one table row per fold.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))

import clickhouse_data as ch  # noqa: E402
from btc_bars import resample  # noqa: E402
from timeframes import FEES, avbt_cpp, to_bars  # noqa: E402

TF = avbt_cpp.Timeframe
START, END = "2026-06-01", "2026-09-01"  # the cached span
PAIRS = {"BTC": 1, "ETH": 0}
SIGNALS = {TF.Min15: "15min", TF.Hour1: "1h"}
# Few knobs, few values: each fold searches in seconds. The first value
# of each knob is where the search starts.
KNOBS = {
    "average": [50, 20],
    "breakout": [30, 60],
    "atr_stops": [2.0, 3.0],
    "flip": [False, True],
}
DAY = 86400


def ts(day: str) -> int:
    return int(pd.Timestamp(day, tz="UTC").timestamp())


def load():
    """Per pair: raw minutes, 1-minute bars, and raw states, cached span."""
    out = {}
    for name, pid in PAIRS.items():
        raw = ch.minutes(pid, START, END)
        base = resample(raw, "1min")
        out[name] = raw, base, ch.states(pid, START, END)
    return out


def cut(costs, keep):
    """The Costs for a window: same fees, holding costs for its bars only."""
    hl, hs = costs.hold_long, costs.hold_short
    return avbt_cpp.Costs(costs.open_fee, costs.close_fee,
                          hl[keep] if len(hl) else None,
                          hs[keep] if len(hs) else None)


def window(data, costs, lo: int, hi: int):
    """Markets and Costs holding only the bars in [lo, hi)."""
    markets, out = [], {}
    for name, (raw, base, states) in data.items():
        keep = ((base.ts >= lo) & (base.ts < hi)).to_numpy()
        m = raw.query("@lo <= ts < @hi")
        bars = [to_bars(TF.Min1, base[keep])]
        bars += [to_bars(tf, resample(m, r)) for tf, r in SIGNALS.items()]
        st = states.query("@lo <= ts < @hi")
        first, *codes = ch.clean_states(st)
        st = avbt_cpp.States(first, *codes)
        markets.append(avbt_cpp.Market(name, bars, st))
        out[name] = cut(costs[name], keep)
    return avbt_cpp.Markets(markets), out


def progress(r, total, sharpe, best):
    print(f"  round {r}/{total}: best Sharpe {sharpe:.2f}")
    return True  # False would stop the search


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--train", type=int, default=31, help="training days")
    p.add_argument("--test", type=int, default=31, help="test days")
    p.add_argument("--step", type=int, default=15, help="days between folds")
    p.add_argument("--rounds", type=int, default=6, help="search rounds")
    a = p.parse_args()
    if min(a.train, a.test, a.step, a.rounds) < 1:
        p.error("every value must be at least 1")
    # avbt_cpp.sharpe needs 30 daily returns, so 31 days; below that it is
    # NaN and the search cannot rank anything.
    if min(a.train, a.test) < 31:
        p.error("--train and --test must be at least 31 days")

    settings = avbt_cpp.PortfolioSettings()
    data = load()
    # Full-span Costs; window() slices their holding costs per window.
    costs = {k: FEES for k in data}
    rows = []
    lo, end = ts(START), ts(END)
    while lo + (a.train + a.test) * DAY <= end:
        mid, hi = lo + a.train * DAY, lo + (a.train + a.test) * DAY
        day = lambda t: pd.Timestamp(t, unit="s").strftime("%m-%d")
        fold = f"{day(lo)}..{day(mid)} | {day(mid)}..{day(hi)}"
        print(f"fold {len(rows) + 1}: train | test = {fold}")
        train_m, train_c = window(data, costs, lo, mid)
        test_m, test_c = window(data, costs, mid, hi)
        s = avbt_cpp.optimize("state_trend", {}, KNOBS, train_m, train_c,
                              settings, a.rounds, progress=progress)
        train = avbt_cpp.summary(
            avbt_cpp.run("state_trend", s["best"], train_m, train_c, settings))
        test = avbt_cpp.summary(
            avbt_cpp.run("state_trend", s["best"], test_m, test_c, settings))
        rows.append({"fold": fold, "train": train, "test": test,
                     "best": s["best"]})
        lo += a.step * DAY

    if not rows:
        sys.exit("the cached span is shorter than one train plus test window")
    print()
    print(f"{'train | test':26} {'tr Sh':>6} {'te Sh':>6} {'te/tr':>6}"
          f" {'te ret':>7} {'te DD':>6} {'trades':>6}")
    for r in rows:
        tr, te = r["train"], r["test"]
        # Test Sharpe over train Sharpe. Near 1: the winner did as well on
        # unseen days as on the days it was tuned on. Far below 1, or
        # negative: the search fit noise in the training window, so the
        # train Sharpe overstates what the strategy can do. With a train
        # Sharpe of 0 or less the ratio means nothing, so it shows nan.
        ok = tr["sharpe"] > 0
        ratio = te["sharpe"] / tr["sharpe"] if ok else float("nan")
        print(f"{r['fold']:26} {tr['sharpe']:6.2f} {te['sharpe']:6.2f}"
              f" {ratio:6.2f} {te['total_return']:7.2%}"
              f" {te['max_drawdown']:6.2%} {te['trades']:6d}")
    for i, r in enumerate(rows, 1):
        print(f"fold {i} best:", r["best"])


if __name__ == "__main__":
    main()

"""Fit the C++ mimic strategy to a wallet's PnL curve.

Downloads the wallet's closed trades, builds its $10,000 equity curve (as
veranta_top5.py does), loads bars for its busiest markets, then tunes the
mimic strategy two knobs at a time to minimize the MSE between the two
curves, hour by hour. It fits the whole history on purpose: no test period.

Run from the repository root:

    python3 examples/mimic.py 0xFFB7eF358cEe48DaFE15B63A625DFF9eA4E2268F

Writes data/candles/mimic_<address>.json: the strategy, its params, the
markets, dates, costs and settings, and the scores. To rerun it:

    python3 examples/mimic.py --replay data/candles/mimic_<address>.json

Needs pycryptodome for the address checksum.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "sharpe_hunt"))
import data as hd  # noqa: E402
import veranta_top5 as vt  # noqa: E402

avbt_cpp, TF = hd.avbt_cpp, hd.TF
CACHE = hd.ch.CACHE
FEE = 0.0005  # 0.05% at open and at close, as in sharpe_hunt
TFS = (TF.Min15, TF.Hour1, TF.Hour4)
TF_NAMES = {avbt_cpp.timeframe_name(t): t for t in TFS}

# Each knob and the values a pass tries. risk goes to the settings, the rest
# to the strategy. A knob in a group is ignored while its switch is off.
KNOBS = {
    "timeframe": list(TF_NAMES), "side": [-1, 0, 1],
    "rsi_on": [False, True], "rsi_period": [2, 3, 5, 7, 14, 21], "rsi_level": [5, 10, 15, 20, 25, 30, 35, 40],
    "trend_on": [False, True], "trend_period": [10, 20, 50, 100, 200], "trend_dir": [-1, 1],
    "move_on": [False, True], "move_lag": [1, 2, 4, 8, 12, 24, 48],
    "move_size": [0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.2], "move_dir": [-1, 1],
    "break_on": [False, True], "break_period": [6, 12, 24, 48, 96, 168], "break_dir": [-1, 1],
    "hours_on": [False, True], "hour_from": [0, 3, 6, 9, 12, 15, 18, 21], "hour_to": [3, 6, 9, 12, 15, 18, 21, 24],
    "atr_period": [7, 14, 28], "hold": [1, 2, 4, 8, 12, 24, 48, 96, 168],
    "stop_atrs": [0.5, 1, 1.5, 2, 3, 4, 6, 8], "tp_atrs": [0.5, 1, 2, 3, 4, 6, 8, 12],
    "leverage": [1, 2, 3, 5, 10], "risk": [0.002, 0.005, 0.01, 0.02, 0.03, 0.05, 0.08],
}
GROUPS = {"rsi_": "rsi_on", "trend_": "trend_on", "move_": "move_on", "break_": "break_on", "hour_": "hours_on"}
START = {"timeframe": "1 hour", "side": 0, "rsi_on": False, "rsi_period": 14, "rsi_level": 30,
         "trend_on": False, "trend_period": 50, "trend_dir": 1, "move_on": False, "move_lag": 4,
         "move_size": 0.03, "move_dir": -1, "break_on": False, "break_period": 24, "break_dir": 1,
         "hours_on": False, "hour_from": 0, "hour_to": 24, "atr_period": 14, "hold": 24,
         "stop_atrs": 3, "tp_atrs": 3, "leverage": 2, "risk": 0.01}


def checksum(address):
    """EIP-55 case: the trade API only answers to it."""
    from Crypto.Hash import keccak

    a = address.lower().removeprefix("0x")
    h = keccak.new(digest_bits=256, data=a.encode()).hexdigest()
    return "0x" + "".join(c.upper() if int(h[i], 16) >= 8 else c for i, c in enumerate(a))


def download(address):
    """The wallet's closed trades, cached in data/candles/wallet_<address>.json."""
    path = CACHE / f"wallet_{address}.json"
    if path.exists():
        return path
    symbols = vt._symbols()
    first = vt._get(vt.HISTORY.format(address=address, page=1))
    rows = list(first.get("portfolio") or [])
    for page in range(2, (first.get("pageCount") or 1) + 1):
        time.sleep(0.12)
        rows.extend(vt._get(vt.HISTORY.format(address=address, page=page)).get("portfolio") or [])
    if not rows:
        raise SystemExit(f"{address} has no closed trades")
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"address": address, "count": len(rows),
                                "trades": [vt._record(r, symbols) for r in rows]}))
    return path


def bars(pid, start, end):
    """15-minute, 1-hour and 4-hour bars of one pair, cached as npz."""
    path = CACHE / f"mimic_pair{pid}_{start}_{end}.npz"
    if not path.exists():
        edges = list(pd.date_range(start, end, freq="MS").strftime("%Y-%m-%d"))
        edges = sorted({start, end, *edges})
        mins = pd.concat([hd._minutes(pid, a, b) for a, b in zip(edges, edges[1:])], ignore_index=True)
        if mins.empty:
            return None
        mins = hd.clean_candles(mins.drop_duplicates("ts"))[0]
        out = {}
        for tf in TFS:
            rule = hd.RULES[tf]
            df = hd.resample(mins, rule)
            out.update({f"{rule}_{c}": df[c].to_numpy() for c in hd.BAR_COLS})
        np.savez_compressed(path, **out)
    z = np.load(path)
    return [hd.to_bars(tf, pd.DataFrame({c: z[f"{hd.RULES[tf]}_{c}"] for c in hd.BAR_COLS})) for tf in TFS]


def wallet_curve(path):
    """The wallet's $10,000 equity steps and its own scores, from veranta_top5."""
    rows = vt._closes(path)
    book = vt._book({"id": "", "address": "", "blurb": "", "color": ""}, rows)
    eq = pd.Series([v for _, v in book["equity"]], index=[t for t, _ in book["equity"]])
    eq = eq.groupby(level=0).last()
    return rows, eq, {"sharpe": book["sharpe"], "max_drawdown": abs(book["max_dd"]),
                      "return": book["end"] / vt.ACCOUNT - 1, "trades": len(rows),
                      "short_share": float(np.mean([r["side"] == "short" for r in rows]))}


def load_markets(symbols, start, end):
    """Markets for the symbols with candles; drops the rest from symbols."""
    ids = {v: k for k, v in vt._symbols().items()}
    markets = []
    for s in symbols:
        b = bars(ids[s], start, end)
        if b is None or not len(b[0].ts):
            print(f"  {s}: no candles, left out")
            continue
        markets.append(avbt_cpp.Market(s, b))
    symbols[:] = [m.instrument for m in markets]
    return avbt_cpp.Markets(markets)


def settings(risk):
    s = avbt_cpp.PortfolioSettings()
    s.starting_balance, s.risk_per_trade, s.hard_stop = vt.ACCOUNT, risk, 1.0
    return s


def backtest(p, markets, costs):
    params = {k: v for k, v in p.items() if k != "risk"}
    params["timeframe"] = TF_NAMES[params["timeframe"]]
    return avbt_cpp.run("mimic", params, markets, costs, settings(p["risk"]))


def on_grid(series, grid):
    """Equity as a return on the hourly grid, carried forward."""
    return series.reindex(series.index.union(grid)).ffill().bfill().loc[grid].to_numpy() / vt.ACCOUNT - 1


def scores(r):
    s = avbt_cpp.summary(r)
    return {"sharpe": s["sharpe"], "max_drawdown": s["max_drawdown"], "return": s["total_return"],
            "trades": s["trades"]}


def effective(p):
    """p without knobs whose switch is off, so equal runs share one cache key."""
    out = dict(p)
    for prefix, switch in GROUPS.items():
        if not p[switch]:
            for k in list(out):
                if k.startswith(prefix) and k != switch:
                    del out[k]
    return tuple(sorted(out.items()))


def fit(markets, costs, target, grid, start, restarts=4, passes=6, tol=0.01, workers=8, seed=0):
    """Two knobs at a time, every pair per pass, until a pass gains less than tol.

    Runs from `start`, then from `restarts` random starts; keeps the best.
    """
    seen = {}

    def mse(p):
        key = effective(p)
        if key not in seen:
            r = backtest(p, markets, costs)
            eq = pd.Series(r.equity, index=r.clock)
            seen[key] = float(np.mean((on_grid(eq, grid) - target) ** 2))
        return seen[key]

    rng = np.random.default_rng(seed)
    starts = [start] + [{k: v[rng.integers(len(v))] for k, v in KNOBS.items()} for _ in range(restarts)]
    pairs = list(itertools.combinations(KNOBS, 2))
    winner, winner_mse = None, np.inf
    with ThreadPoolExecutor(workers) as pool:
        for i, best in enumerate(starts):
            best = {k: v.item() if hasattr(v, "item") else v for k, v in best.items()}
            best_mse = mse(best)
            for n in range(1, passes + 1):
                before = best_mse
                for a, b in pairs:
                    tries = [{**best, a: x, b: y} for x in KNOBS[a] for y in KNOBS[b]]
                    for p, m in zip(tries, pool.map(mse, tries)):
                        if m < best_mse - 1e-12:
                            best, best_mse = p, m
                print(f"start {i}, pass {n}: mse {best_mse:.5f}, {len(seen)} backtests")
                if before - best_mse < tol * before:
                    break
            if best_mse < winner_mse:
                winner, winner_mse = best, best_mse
    return winner, winner_mse


def report(out):
    print(f"\nwallet {out['address']}  markets {', '.join(out['markets'])}  {out['start']} to {out['end']}")
    print(f"mse {out['mse']:.5f}  (rmse {out['mse'] ** 0.5:.1%} of the account)")
    print(f"{'':10}{'sharpe':>8}{'max dd':>9}{'return':>9}{'trades':>8}")
    for name in ("wallet", "mimic"):
        s = out[name]
        print(f"{name:10}{s['sharpe']:8.2f}{s['max_drawdown']:9.1%}{s['return']:9.1%}{s['trades']:8d}")
    print("strategy mimic, params:")
    for k, v in out["params"].items():
        print(f"  {k} = {v}")


def setup(address, top):
    path = download(address)
    rows, eq, wallet = wallet_curve(path)
    counts = pd.Series([r["symbol"] for r in rows]).value_counts()
    symbols = list(counts.index[:top])
    start = pd.Timestamp(eq.index[0], unit="s").strftime("%Y-%m-%d")
    end = (pd.Timestamp(eq.index[-1], unit="s") + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"{len(rows)} closes, {start} to {end}; {counts.iloc[:top].sum()} of them in {', '.join(symbols)}")
    return eq, wallet, symbols, start, end


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("address", nargs="?")
    ap.add_argument("--replay", help="a saved mimic_<address>.json to rerun")
    ap.add_argument("--top", type=int, default=8, help="markets to use, busiest first")
    ap.add_argument("--passes", type=int, default=6, help="most passes per start")
    ap.add_argument("--restarts", type=int, default=4, help="random starts after the first")
    args = ap.parse_args()

    if args.replay:
        saved = json.loads(Path(args.replay).read_text())
        markets = load_markets(saved["markets"], saved["start"], saved["end"])
        costs = {s: avbt_cpp.Costs(FEE, FEE) for s in saved["markets"]}
        now = scores(backtest(saved["params"], markets, costs))
        print(f"{'':10}{'sharpe':>8}{'max dd':>9}{'return':>9}{'trades':>8}")
        for name, s in (("saved", saved["mimic"]), ("rerun", now)):
            print(f"{name:10}{s['sharpe']:8.2f}{s['max_drawdown']:9.1%}{s['return']:9.1%}{s['trades']:8d}")
        return

    if not args.address:
        ap.error("give a wallet address or --replay")
    address = checksum(args.address)
    eq, wallet, symbols, start, end = setup(address, args.top)
    markets = load_markets(symbols, start, end)
    costs = {s: avbt_cpp.Costs(FEE, FEE) for s in symbols}
    lo, hi = pd.Timestamp(start, tz="UTC").timestamp(), pd.Timestamp(end, tz="UTC").timestamp()
    grid = pd.Index(np.arange(lo, hi, 3600, dtype="int64"))
    target = on_grid(eq, grid)

    t0 = time.time()
    # Start on the wallet's usual side: with no filter on, both sides pass and it never opens.
    first = {**START, "side": -1 if wallet["short_share"] > 0.5 else 1}
    best, best_mse = fit(markets, costs, target, grid, first, args.restarts, args.passes)
    out = {"address": address, "avbt_version": avbt_cpp.version, "strategy": "mimic", "params": best,
           "markets": symbols, "start": start, "end": end, "costs": {"open_fee": FEE, "close_fee": FEE},
           "settings": {"starting_balance": vt.ACCOUNT, "risk_per_trade": best["risk"], "hard_stop": 1.0},
           "mse": best_mse, "wallet": wallet, "mimic": scores(backtest(best, markets, costs))}
    path = CACHE / f"mimic_{address}.json"
    path.write_text(json.dumps(out, indent=1))
    report(out)
    print(f"\n{time.time() - t0:.0f} s; saved {path}")

if __name__ == "__main__":
    main()

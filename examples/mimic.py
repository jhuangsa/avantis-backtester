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

A Hyperliquid wallet (1-hour and 4-hour bars, the last 200 days). Its curve
is total PnL, realized plus unrealized, from its fills and hourly closes:

    python3 examples/mimic.py --venue hyperliquid 0x5e13d5cedbddd7237f659947ce64bec1294e2607

Needs pycryptodome for the address checksum.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
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


HL = "https://api.hyperliquid.xyz/info"
HL_DAYS = 200  # Hyperliquid serves the last 5000 candles: about 208 days of 1-hour bars
HL_MAX_LEVERAGE = 50  # an account holds at most 50 times its value in notional


def hl_post(body):
    """One info call, waiting and retrying when rate limited."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(HL, json.dumps(body).encode(), {"content-type": "application/json"})
    for wait in (5, 10, 20, 40, 80, 0):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code != 429 or not wait:
                raise
            time.sleep(wait)


def hl_fills(address):
    """A Hyperliquid wallet's perp fills, oldest first, cached. The API keeps about the last 10,000."""
    path = CACHE / f"wallet_hl_fills_{address.lower()}.json"
    if path.exists():
        return json.loads(path.read_text())
    fills, since = [], 0
    while True:
        page = hl_post({"type": "userFillsByTime", "user": address.lower(), "startTime": since})
        fills += page
        if len(page) < 2000:
            break
        since = page[-1]["time"] + 1
        time.sleep(1)
    keep = ("coin", "px", "sz", "side", "time", "startPosition", "fee")
    perp = [{k: f[k] for k in keep} for f in {f["tid"]: f for f in fills}.values()
            if not f["coin"].startswith("@") and ("Long" in f["dir"] or "Short" in f["dir"])]
    if not perp:
        raise SystemExit(f"{address} has no Hyperliquid perp fills")
    perp.sort(key=lambda f: f["time"])
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(perp))
    return perp


def hl_curve(address, since, end=None):
    """The wallet's hourly $10,000 equity on total PnL, its scores, its fills per coin, start and end.

    Total PnL is realized plus unrealized: each fill pays its price and fee in
    cash, and each open position is worth its size times the hour's close,
    less the positions held at the start. A few fills are missing from the
    API; the position they move is still right, from each fill's
    startPosition, and is bought or sold at the next fill's price. Funding
    is left out. The account is
    the wallet's perp account value at the start, at least the peak open
    notional over HL_MAX_LEVERAGE, scaled to $10,000. The curve starts at
    `since`, or later when the fills start later.
    """
    fills = hl_fills(address)
    first = pd.Timestamp(fills[0]["time"], unit="ms").floor("D").strftime("%Y-%m-%d")
    start = max(since, first)
    end = end or pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    lo, hi = (int(pd.Timestamp(x, tz="UTC").timestamp()) for x in (start, end))
    grid = np.arange(lo, hi, 3600, dtype="int64")
    by_coin = {}
    for f in fills:
        by_coin.setdefault(f["coin"], []).append(f)
    cash, value, gross = (np.zeros(len(grid)) for _ in range(3))
    counts, closes = {}, []
    for coin, fs in by_coin.items():
        t = np.array([f["time"] // 1000 for f in fs])
        size = np.array([float(f["sz"]) * (1 if f["side"] == "B" else -1) for f in fs])
        px, fee = (np.array([float(f[k]) for f in fs]) for k in ("px", "fee"))
        before = np.array([float(f["startPosition"]) for f in fs])
        # A position change no fill explains trades at the next fill's price: no gain, no loss.
        gap = before - np.concatenate([before[:1], (before + size)[:-1]])
        k = np.searchsorted(t, grid, side="right")  # fills up to each hour
        pos = np.where(k > 0, (before + size)[np.maximum(k - 1, 0)], before[0])
        inside = (t > lo) & (t < hi)
        if not inside.any() and not pos.any():
            continue
        counts[coin] = int(inside.sum())
        closes += [b < 0 for b, s in zip(before[inside], size[inside]) if b * s < 0]
        price = pd.Series(px, index=t).groupby(level=0).last()
        price = price.reindex(price.index.union(grid)).ffill().bfill().loc[grid]
        z = hl_npz(coin, start, end)
        if z is not None:  # an hour's price is the close of the bar that ends on it
            bar = pd.Series(z["1h_close"], index=z["1h_ts"] + 3600).reindex(grid)
            price = bar.fillna(price)
        price = price.to_numpy()
        cash += np.concatenate([[0], np.cumsum(np.where(inside, -(size + gap) * px - fee, 0))])[k]
        value += pos * price - pos[0] * price[0]
        gross += np.abs(pos) * price
    account = max(hl_account(address, lo), gross.max() / HL_MAX_LEVERAGE)
    if account <= 0:
        raise SystemExit(f"{address} holds nothing from {start} to {end}")
    eq = pd.Series(vt.ACCOUNT + (cash + value) * vt.ACCOUNT / account, index=grid)
    dead = (eq <= 0).cummax()  # a blown account stays at zero
    eq[dead] = 0.0
    day = eq.iloc[::24].to_numpy()
    r = day[1:] / np.where(day[:-1] > 0, day[:-1], np.nan) - 1
    r = r[~np.isnan(r)]
    stats = {"sharpe": float(r.mean() / r.std(ddof=1) * np.sqrt(365)),
             "max_drawdown": float((1 - eq / eq.cummax()).max()), "return": float(eq.iloc[-1] / vt.ACCOUNT - 1),
             "trades": len(closes), "short_share": float(np.mean(closes)) if closes else 0.0,
             "pnl": float(cash[-1] + value[-1]), "account": float(account)}
    return eq, stats, pd.Series(counts).sort_values(ascending=False), start, end


def hl_account(address, at):
    """The wallet's perp account value at a unix time, from its history, cached.

    The history has a point every few days; this takes the last one at or
    before `at`, or the first one after it.
    """
    path = CACHE / f"wallet_hl_portfolio_{address.lower()}.json"
    if not path.exists():
        path.write_text(json.dumps(hl_post({"type": "portfolio", "user": address.lower()})))
    history = dict(json.loads(path.read_text()))["perpAllTime"]["accountValueHistory"]
    before = [float(v) for t, v in history if t // 1000 <= at]
    return before[-1] if before else float(history[0][1])


def hl_npz(coin, start, end):
    """The cached npz of hl_bars, or None when the coin has no candles."""
    return np.load(CACHE / f"mimic_hl_{coin.replace(':', '_')}_{start}_{end}.npz") if hl_bars(coin, start, end) else None


def hl_bars(coin, start, end):
    """1-hour and 4-hour Hyperliquid bars of one coin, cached as npz."""
    path = CACHE / f"mimic_hl_{coin.replace(':', '_')}_{start}_{end}.npz"
    if not path.exists():
        lo, hi = (int(pd.Timestamp(x, tz="UTC").timestamp()) for x in (start, end))
        out = {}
        for tf in TFS:
            rule, step = hd.RULES[tf], {"1h": 3600, "4h": 14400}[hd.RULES[tf]]
            got = hl_post({"type": "candleSnapshot", "req": {"coin": coin, "interval": rule,
                           "startTime": lo * 1000, "endTime": hi * 1000}})
            if not got:
                return None
            df = pd.DataFrame({"ts": [g["t"] // 1000 for g in got], "open": [float(g["o"]) for g in got],
                               "high": [float(g["h"]) for g in got], "low": [float(g["l"]) for g in got],
                               "close": [float(g["c"]) for g in got]}).drop_duplicates("ts").set_index("ts")
            full = np.arange(df.index[0], hi, step, dtype="int64")
            df = df.reindex(full)
            df["minutes_with_data"] = np.where(df.close.isna(), 0, step // 60).astype("int32")
            df["close"] = df.close.ffill()
            for k in ("open", "high", "low"):
                df[k] = df[k].fillna(df.close)
            df = df.reset_index(names="ts")
            out.update({f"{rule}_{c}": df[c].to_numpy() for c in hd.BAR_COLS})
            time.sleep(1)
        np.savez_compressed(path, **out)
    z = np.load(path)
    return [hd.to_bars(tf, pd.DataFrame({c: z[f"{hd.RULES[tf]}_{c}"] for c in hd.BAR_COLS})) for tf in TFS]


VENUE = "avantis"


def use_venue(venue):
    """Hyperliquid has 1-hour and 4-hour bars only, so the timeframe knob shrinks."""
    global VENUE, TFS, TF_NAMES
    VENUE = venue
    if venue == "hyperliquid":
        TFS = (TF.Hour1, TF.Hour4)
        TF_NAMES = {avbt_cpp.timeframe_name(t): t for t in TFS}
        KNOBS["timeframe"] = list(TF_NAMES)


def wallet_curve(path, since=None):
    """The wallet's $10,000 equity steps and its own scores, from veranta_top5.

    since: a date; closes before it are left out.
    """
    rows = vt._closes(path)
    if since:
        lo = pd.Timestamp(since, tz="UTC")
        rows = [r for r in rows if r["close"] >= lo]
    book = vt._book({"id": "", "address": "", "blurb": "", "color": ""}, rows)
    eq = pd.Series([v for _, v in book["equity"]], index=[t for t, _ in book["equity"]])
    eq = eq.groupby(level=0).last()
    dead = (eq <= 0).cummax()  # a blown account stays at zero
    eq[dead] = 0.0
    return rows, eq, {"sharpe": book["sharpe"], "max_drawdown": 1.0 if dead.any() else abs(book["max_dd"]),
                      "return": eq.iloc[-1] / vt.ACCOUNT - 1, "trades": len(rows),
                      "short_share": float(np.mean([r["side"] == "short" for r in rows]))}


def load_markets(symbols, start, end):
    """Markets for the symbols with candles; drops the rest from symbols."""
    ids = {} if VENUE == "hyperliquid" else {v: k for k, v in vt._symbols().items()}
    markets = []
    for s in symbols:
        b = hl_bars(s, start, end) if VENUE == "hyperliquid" else bars(ids[s], start, end)
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


def fit(markets, costs, target, grid, first, restarts=4, passes=6, tol=0.01, workers=None, seed=0):
    """Climbs from `first` and from `restarts` random starts, all at once; keeps the best.

    Each start climbs two knobs at a time, every pair per pass, until a pass
    gains less than tol. All starts share one pool of backtest threads (the
    C++ run frees Python's lock) and one record of the runs done so far.
    """
    seen = {}

    def mse(p):
        key = effective(p)
        if key not in seen:
            r = backtest(p, markets, costs)
            eq = pd.Series(r.equity, index=r.clock)
            seen[key] = float(np.mean((on_grid(eq, grid) - target) ** 2))
        return seen[key]

    def climb(i, best):
        best_mse = mse(best)
        for n in range(1, passes + 1):
            before = best_mse
            for a, b in itertools.combinations(KNOBS, 2):
                tries = [{**best, a: x, b: y} for x in KNOBS[a] for y in KNOBS[b]]
                for p, m in zip(tries, runs.map(mse, tries)):
                    if m < best_mse - 1e-12:
                        best, best_mse = p, m
            print(f"start {i}, pass {n}: mse {best_mse:.5f}, {len(seen)} backtests", flush=True)
            if before - best_mse < tol * before:
                break
        return best, best_mse

    rng = np.random.default_rng(seed)
    starts = [first] + [{k: v[rng.integers(len(v))] for k, v in KNOBS.items()} for _ in range(restarts)]
    starts = [{k: v.item() if hasattr(v, "item") else v for k, v in p.items()} for p in starts]
    with ThreadPoolExecutor(workers or os.cpu_count()) as runs, ThreadPoolExecutor(len(starts)) as climbs:
        done = list(climbs.map(climb, range(len(starts)), starts))
    return min(done, key=lambda x: x[1])


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
    if VENUE == "hyperliquid":
        since = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=HL_DAYS)).strftime("%Y-%m-%d")
        eq, wallet, counts, start, end = hl_curve(address, since)
        print(f"total pnl ${wallet['pnl']:,.0f} on a ${wallet['account']:,.0f} account")
    else:
        rows, eq, wallet = wallet_curve(download(address))
        counts = pd.Series([r["symbol"] for r in rows]).value_counts()
        start = pd.Timestamp(eq.index[0], unit="s").strftime("%Y-%m-%d")
        end = (pd.Timestamp(eq.index[-1], unit="s") + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    symbols = list(counts.index[:top])
    print(f"{counts.sum()} fills or closes, {start} to {end}; {counts.iloc[:top].sum()} of them in {', '.join(symbols)}")
    return eq, wallet, symbols, start, end


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("address", nargs="?")
    ap.add_argument("--replay", help="a saved mimic_<address>.json to rerun")
    ap.add_argument("--top", type=int, default=8, help="markets to use, busiest first")
    ap.add_argument("--passes", type=int, default=6, help="most passes per start")
    ap.add_argument("--restarts", type=int, default=4, help="random starts after the first")
    ap.add_argument("--venue", choices=["avantis", "hyperliquid"], default="avantis")
    ap.add_argument("--workers", type=int, help="backtest threads; default one per core")
    args = ap.parse_args()

    if args.replay:
        saved = json.loads(Path(args.replay).read_text())
        use_venue(saved.get("venue", "avantis"))
        markets = load_markets(saved["markets"], saved["start"], saved["end"])
        costs = {s: avbt_cpp.Costs(FEE, FEE) for s in saved["markets"]}
        now = scores(backtest(saved["params"], markets, costs))
        print(f"{'':10}{'sharpe':>8}{'max dd':>9}{'return':>9}{'trades':>8}")
        for name, s in (("saved", saved["mimic"]), ("rerun", now)):
            print(f"{name:10}{s['sharpe']:8.2f}{s['max_drawdown']:9.1%}{s['return']:9.1%}{s['trades']:8d}")
        return

    if not args.address:
        ap.error("give a wallet address or --replay")
    use_venue(args.venue)
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
    best, best_mse = fit(markets, costs, target, grid, first, args.restarts, args.passes,
                         workers=args.workers)
    out = {"address": address, "venue": VENUE, "avbt_version": avbt_cpp.version, "strategy": "mimic", "params": best,
           "markets": symbols, "start": start, "end": end, "costs": {"open_fee": FEE, "close_fee": FEE},
           "settings": {"starting_balance": vt.ACCOUNT, "risk_per_trade": best["risk"], "hard_stop": 1.0},
           "mse": best_mse, "wallet": wallet, "mimic": scores(backtest(best, markets, costs))}
    path = CACHE / f"mimic_{'hl_' if VENUE == 'hyperliquid' else ''}{address}.json"
    path.write_text(json.dumps(out, indent=1))
    report(out)
    print(f"\n{time.time() - t0:.0f} s; saved {path}")

if __name__ == "__main__":
    main()

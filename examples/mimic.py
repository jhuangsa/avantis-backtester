"""Fit the C++ mimic strategy to a wallet's PnL curve.

Downloads the wallet's trades, builds its hourly PnL, marked to market,
loads bars for its busiest markets, then tunes the mimic strategy two knobs
at a time to minimize the MSE between the two curves. It fits the whole
history on purpose: no test period.

The curve fitted and scored first is the return on deployed capital
(deployed_curve): each hour's PnL over the notional held the hour before,
or opened in the hour, compounded from $10,000. Wallet and mimic are
measured the same way, so the fit does not depend on account size, risk
or leverage. A second score set, "account", is the return on the account:
the wallet's whole Hyperliquid account or its peak Avantis margin, and
the mimic's $10,000.

Every curve pays FEE of notional at each open and each close, 0.05% per
round trip, and leaves out funding. Every curve is scored by the C++ rules
through curve_stats. A trade is one position from open to full close.

Run from the repository root:

    python3 examples/mimic.py 0xFFB7eF358cEe48DaFE15B63A625DFF9eA4E2268F

Writes data/candles/mimic_<address>.json: the strategy, its params, the
markets, dates, costs and settings, and the scores. To rerun it:

    python3 examples/mimic.py --replay data/candles/mimic_<address>.json

A Hyperliquid wallet (1-hour and 4-hour bars, the last 200 days), from its
fills and hourly closes:

    python3 examples/mimic.py --venue hyperliquid 0x5e13d5cedbddd7237f659947ce64bec1294e2607

Needs pycryptodome for the address checksum.
"""

from __future__ import annotations

import argparse
import bisect
import gzip
import itertools
import json
import os
import sys
import time
import urllib.error
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
FEE = 0.00025  # 0.025% of notional at each open and each close: 0.05% per round trip
HOUR, DAY = 3600, 86400
AV_TFS = TFS = (TF.Min15, TF.Hour1, TF.Hour4)
TF_NAMES = {avbt_cpp.timeframe_name(t): t for t in TFS}

# The fit measures the return on deployed capital, which risk and leverage do
# not change, so both are fixed: risk per trade RISK times LEVERAGE of the
# $10,000 account (scale_risk_with_leverage), at LEVERAGE times the margin.
RISK, LEVERAGE = 0.002, 3

# Each knob and the values a pass tries, all strategy params. A knob in a
# group is ignored while its switch is off.
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
}
GROUPS = {"rsi_": "rsi_on", "trend_": "trend_on", "move_": "move_on", "break_": "break_on", "hour_": "hours_on"}
START = {"timeframe": "1 hour", "side": 0, "rsi_on": False, "rsi_period": 14, "rsi_level": 30,
         "trend_on": False, "trend_period": 50, "trend_dir": 1, "move_on": False, "move_lag": 4,
         "move_size": 0.03, "move_dir": -1, "break_on": False, "break_period": 24, "break_dir": 1,
         "hours_on": False, "hour_from": 0, "hour_to": 24, "atr_period": 14, "hold": 24,
         "stop_atrs": 3, "tp_atrs": 3}


def checksum(address):
    """EIP-55 case: the trade API only answers to it."""
    from Crypto.Hash import keccak

    a = address.lower().removeprefix("0x")
    h = keccak.new(digest_bits=256, data=a.encode()).hexdigest()
    return "0x" + "".join(c.upper() if int(h[i], 16) >= 8 else c for i, c in enumerate(a))


def curve_stats(eq):
    """sharpe, max_drawdown and return of hourly equity, by the C++ rules.

    eq: a Series on a regular hourly grid; the index is the unix second the
    value is known, the close of the hour.
    """
    r = avbt_cpp.Result(list(eq), TF.Hour1, list(eq.index - HOUR))
    s = avbt_cpp.summary(r)
    return {"sharpe": s["sharpe"], "max_drawdown": s["max_drawdown"], "return": s["total_return"]}


def mimic_equity(r):
    """A backtest's equity, indexed by the close of each bar."""
    return pd.Series(r.equity, index=np.asarray(r.clock) + avbt_cpp.timeframe_seconds(r.timeframe))


def deployed_curve(P, N, T):
    """Equity on the return on deployed capital, from $10,000, on the hourly grid.

    P: the dollar PnL known at each hour (a Series; its index is the grid);
    N: the gross notional held at each hour; T: the notional opened inside
    each hour. The hour's return is its PnL over the larger of the notional
    held the hour before and the notional opened in it, 0 when both are 0.
    Equity at or below 0 stays 0.
    """
    N, T = np.asarray(N, float), np.asarray(T, float)
    d = np.maximum(N[:-1], T[1:])
    r = np.where(d > 0, np.diff(np.asarray(P, float)) / np.where(d > 0, d, 1.0), 0.0)
    eq = pd.Series(vt.ACCOUNT * np.concatenate([[1.0], np.cumprod(1 + r)]), index=P.index)
    eq[(eq <= 0).cummax()] = 0.0
    return eq


def dropped_pnl(P, N, T):
    """The PnL deployed_curve drops: the sum of |ΔP| on hours with PnL but no deployed capital, and its share of all |ΔP|.

    P, N, T as in deployed_curve. A share above a few percent means the
    fills are placed on the wrong hours.
    """
    N, T = np.asarray(N, float), np.asarray(T, float)
    d = np.maximum(N[:-1], T[1:])
    dp = np.abs(np.diff(np.asarray(P, float)))
    lost, total = float(dp[d <= 0].sum()), float(dp.sum())
    return lost, lost / total if total else 0.0


def warn_dropped(name, stats):
    """Prints a warning when a curve's deployed scores drop over 1% of its PnL moves."""
    if stats.get("dropped_share", 0.0) > 0.01:
        print(f"warning: {name}: ${stats['dropped_pnl']:,.0f} of PnL, {stats['dropped_share']:.0%} of its moves, "
              "fell on hours with no deployed capital and is left out of the deployed scores")


SCORE_KEYS = ("sharpe", "max_drawdown", "return")


def score_sets(s):
    """A side's scores as {"deployed": {...}, "account": {...}, "trades": n}; an old file's scores are its account's."""
    if "deployed" in s:
        return s
    return {**s, "deployed": dict.fromkeys(SCORE_KEYS, float("nan")), "account": {k: s[k] for k in SCORE_KEYS}}


def closed_positions(before, after):
    """One entry per fill that closes a position: True when it was short.

    A fill closes when before != 0 and after is 0 or on the other side.
    """
    before, after = np.asarray(before, float), np.asarray(after, float)
    after = np.where(np.abs(after) <= 1e-9 * np.abs(before), 0.0, after)
    closes = (before != 0) & ((after == 0) | (before * after < 0))
    return before[closes] < 0


def save_json(path, obj, **kw):
    """Writes through a temp file beside `path`, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, **kw))
    os.replace(tmp, path)


def save_json_gz(path, obj):
    """Compact JSON, gzip-compressed, through a temp file beside `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt") as fh:
        json.dump(obj, fh, separators=(",", ":"))
    os.replace(tmp, path)


def load_json_gz(path):
    with gzip.open(path, "rt") as fh:
        return json.load(fh)


def save_npz(path, **arrays):
    """np.savez_compressed through a temp file; a file object keeps numpy from adding .npz."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        np.savez_compressed(fh, **arrays)
    os.replace(tmp, path)


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
    save_json(path, {"address": address, "count": len(rows), "trades": [vt._record(r, symbols) for r in rows]})
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


def day_bounds(first, last):
    """Midnight at or before `first` to midnight after `last`, as dates and unix seconds."""
    lo, hi = first // DAY * DAY, last // DAY * DAY + DAY
    return (pd.Timestamp(lo, unit="s").strftime("%Y-%m-%d"),
            pd.Timestamp(hi, unit="s").strftime("%Y-%m-%d"), lo, hi)


PAIR_IDS = {}  # symbol -> Avantis pair id, from the wallet's rows


def avantis_curve(address):
    """The wallet's hourly deployed-capital curve on Avantis, its scores, its orders per symbol, start, end, and its account curve.

    An order is one position: symbol, side, index, open price and open
    minute; its margin is the sum of its closes' positionSize. Each hour the
    PnL is the realized price PnL of the closes so far, less fees, plus
    each open order's remaining notional times its price change since the
    open. The deployed curve (deployed_curve) is returned first; the
    account curve, the PnL scaled so the peak open margin is the $10,000
    account, last. A symbol with no candles counts realized PnL only.
    """
    rows = json.loads(download(address).read_text())["trades"]
    whole = [r for r in rows if all(r.get(k) is not None for k in ("positionSize", "gross", "leverage"))
             and float(r["leverage"]) > 0]
    if len(whole) < len(rows):
        print(f"  {len(rows) - len(whole)} rows without size, gross or leverage skipped")
    rows = whole
    orders = {}
    for r in rows:
        key = (r["symbol"], bool(r["buy"]), r["index"], round(float(r["openPrice"]), 6), r["openedAt"] // 60)
        orders.setdefault(key, []).append(r)
        PAIR_IDS[r["symbol"]] = r["pairIndex"]
    closed_at = {k: [int(pd.Timestamp(r["time"]).timestamp()) for r in v] for k, v in orders.items()}
    start, end, lo, hi = day_bounds(min(r["openedAt"] for r in rows), max(max(v) for v in closed_at.values()))
    grid = np.arange(lo, hi, HOUR, dtype="int64")
    prices, missing = {}, []
    for symbol in sorted({k[0] for k in orders}):
        b = bars(PAIR_IDS[symbol], start, end)
        if b is None or not len(b[1].ts):
            missing.append(symbol)
            continue
        close = pd.Series(b[1].close, index=b[1].ts + HOUR)
        prices[symbol] = close.reindex(close.index.union(grid)).ffill().loc[grid].to_numpy()
    if missing:
        print(f"  no candles, realized PnL only: {', '.join(missing)}")
    realized, fees, unreal, notional, opening = (np.zeros(len(grid)) for _ in range(5))
    shorts, events = [], []  # events: (time, change in open margin)
    for key, group in orders.items():
        symbol, buy = key[:2]
        t0, lever = group[0]["openedAt"], float(group[0]["leverage"])
        size = np.array([float(r["positionSize"]) for r in group])
        gross = np.array([float(r["gross"]) for r in group])
        t = np.array(closed_at[key])
        order = sorted(range(len(group)), key=lambda i: t[i])
        t, size, gross = t[order], size[order], gross[order]
        opened = (grid >= t0).astype(float)
        k = np.searchsorted(t, grid, side="right")  # closes up to each hour
        done = np.concatenate([[0.0], np.cumsum(size)])[k]
        realized += np.concatenate([[0.0], np.cumsum(gross)])[k]
        fees += FEE * lever * (size.sum() * opened + done)
        events += [(t0, size.sum()), *zip(t, -size)]
        change = np.nan_to_num(prices[symbol] / float(group[0]["openPrice"]) - 1) if symbol in prices else 0.0
        remaining = (size.sum() * opened - done) * lever  # notional still open, at the open price
        unreal += remaining * (1 if buy else -1) * change
        notional += remaining * (1 + change)
        opening[np.searchsorted(grid, t0)] += size.sum() * lever  # the hour the open is marked in
        shorts.append(not buy)
    events.sort(key=lambda e: (e[0], e[1] < 0))  # opens before closes at one time
    scale = vt.ACCOUNT / np.cumsum([m for _, m in events]).max()  # peak open margin
    pnl = pd.Series(realized - fees + unreal, index=grid)
    eq = vt.ACCOUNT + scale * pnl
    eq[(eq <= 0).cummax()] = 0.0  # a blown account stays at zero
    deployed = deployed_curve(pnl, notional, opening)
    lost, lost_share = dropped_pnl(pnl, notional, opening)
    counts = pd.Series([k[0] for k in orders]).value_counts()
    stats = {"deployed": curve_stats(deployed), "account": curve_stats(eq),
             "trades": len(orders), "short_share": float(np.mean(shorts)),
             "dropped_pnl": lost, "dropped_share": lost_share}
    return deployed, stats, counts, start, end, eq


HL = "https://api.hyperliquid.xyz/info"
HL_DAYS = 200  # Hyperliquid serves the last 5000 candles: about 208 days of 1-hour bars
HL_MAX_LEVERAGE = 50  # an account holds at most 50 times its value in notional
HL_KEEP = ("coin", "px", "sz", "side", "time", "startPosition", "fee", "tid", "dir", "hash")
HL_KEEPS = "perps and outcome markets"  # what hl_spot lets through; a fill cache of anything else is refetched


def hl_post(body):
    """One info call; waits and retries when rate limited, retries a server error three times."""
    import urllib.request

    req = urllib.request.Request(HL, json.dumps(body).encode(), {"content-type": "application/json"})
    for i in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if not (e.code == 429 and i < 5 or e.code >= 500 and i < 3):
                raise
            time.sleep(5 * 2 ** i if e.code == 429 else 2)


def hl_key(f):
    """The fill's identity: its tid, or its fields when the tid is 0."""
    return f["tid"] or (f.get("hash"), f["time"], f["coin"], f["px"], f["sz"], f["side"], f["startPosition"])


def hl_signed(f):
    return float(f["sz"]) * (1 if f["side"] == "B" else -1)


def near(a, b):
    """Equal positions, allowing for float noise."""
    return abs(a - b) <= 1e-9 * max(abs(a), abs(b), 1e-12)


def hl_chain(g, prev):
    """One millisecond's fills of a coin, ordered so each starts where the last ended.

    The fills are edges between positions, and the order is a trail over
    them (Hierholzer), found whenever one uses every fill. It starts at the
    one position more fills leave than arrive at; when there is no such
    position or several, at `prev`, the coin's position before this
    millisecond, else at the first of them, else at the first fill. Without
    a complete trail the walk is greedy from the same start: the next fill
    is one starting at the running end, else the one starting nearest it.
    Ties keep buys by start ascending, sells descending.
    """
    g = sorted(g, key=lambda f: (f["side"] != "B", float(f["startPosition"]) * (1 if f["side"] == "B" else -1)))
    n = len(g)
    start = [float(f["startPosition"]) for f in g]
    end = [s + hl_signed(f) for s, f in zip(start, g)]
    vals = []  # the positions, one for each set of near-equal values, ascending
    for v in sorted(start + end):
        if not vals or not near(v, vals[-1]):
            vals.append(v)

    def bucket(v):
        i = bisect.bisect_left(vals, v)
        return next((j for j in (i - 1, i) if 0 <= j < len(vals) and near(vals[j], v)), None)

    sb, eb = [bucket(v) for v in start], [bucket(v) for v in end]
    outs = [[] for _ in vals]  # the fills starting at each position, in g order
    for i in range(n):
        outs[sb[i]].append(i)
    balance = [len(o) for o in outs]
    for b in eb:
        balance[b] -= 1
    heads = [b for b in range(len(vals)) if balance[b] > 0]
    pb = bucket(prev) if prev is not None else None
    first = heads[0] if len(heads) == 1 else pb if pb is not None and outs[pb] else heads[0] if heads else sb[0]
    cursor = [0] * len(vals)

    def take(b):
        """The next unused fill starting at position b, or None."""
        if cursor[b] < len(outs[b]):
            cursor[b] += 1
            return outs[b][cursor[b] - 1]

    def nearest(e):
        """The unused fill starting nearest e, the first in g order on a tie, or None."""
        lo, hi = bisect.bisect_left(vals, e) - 1, bisect.bisect_left(vals, e)
        while lo >= 0 and cursor[lo] >= len(outs[lo]):
            lo -= 1
        while hi < len(vals) and cursor[hi] >= len(outs[hi]):
            hi += 1
        c = [b for b in (lo, hi) if 0 <= b < len(vals)]
        return take(min(c, key=lambda b: (abs(vals[b] - e), outs[b][cursor[b]]))) if c else None

    stack, path = [None], []
    while stack:
        i = take(first if stack[-1] is None else eb[stack[-1]])
        if i is None:
            path.append(stack.pop())
        else:
            stack.append(i)
    out = [i for i in reversed(path) if i is not None]
    if len(out) < n or any(eb[a] != sb[b] for a, b in zip(out, out[1:])):
        cursor[:], out = [0] * len(vals), []
        i = take(first)
        while i is not None:
            out.append(i)
            i = take(eb[i])
            i = nearest(end[out[-1]]) if i is None else i
    return [g[i] for i in out]


def hl_order(fills):
    """Fills oldest first; same-millisecond fills of a coin follow its position chain."""
    out, end = [], {}
    fills = sorted(fills, key=lambda f: (f["time"], f["coin"], f["tid"]))
    for (coin, _), g in itertools.groupby(fills, key=lambda f: (f["coin"], f["time"])):
        g = list(g)
        if len(g) > 1:
            g = hl_chain(g, end.get(coin))
        out += g
        end[coin] = float(g[-1]["startPosition"]) + hl_signed(g[-1])
    return out


def hl_stale(cache):
    """True when a cache is missing or fetched more than an hour ago."""
    return not isinstance(cache, dict) or cache.get("fetched", 0) < time.time() * 1000 - HOUR * 1000


def hl_spot(f):
    """True for a spot fill. An outcome market (a "#" coin) is a perp, its Buy and Sell fills too."""
    coin = f["coin"]
    if coin.startswith("#"):
        return False
    return coin.startswith("@") or "/" in coin or f["dir"] in ("Buy", "Sell") or "Spot" in f["dir"]


def hl_pages(address, since):
    """Perp fills from `since` (ms), 2000 a page, deduped by hl_key."""
    fills = {}
    while True:
        page = hl_post({"type": "userFillsByTime", "user": address.lower(), "startTime": since})
        fills.update((hl_key(f), {k: f.get(k) for k in HL_KEEP}) for f in page if not hl_spot(f))
        if len(page) < 2000:
            return fills
        last = page[-1]["time"]
        since = last if last > since else since + 1
        time.sleep(1)


def hl_fills(address):
    """A Hyperliquid wallet's perp fills, oldest first, cached gzipped and refreshed hourly.

    The cache keeps the fetch time; a refresh refetches from a day before it
    and merges by tid. A cache without tid, or from before outcome markets
    were kept (no "keeps"), is refetched in full.
    """
    path = CACHE / f"wallet_hl_fills_{address.lower()}.json.gz"
    old = path.with_suffix("")  # the uncompressed cache of earlier versions; read once, then replaced
    cache = load_json_gz(path) if path.exists() else json.loads(old.read_text()) if old.exists() else None
    if not isinstance(cache, dict) or cache.get("keeps") != HL_KEEPS or not all("tid" in f for f in cache["fills"][:1]):
        cache = None
    if hl_stale(cache):
        fills = {hl_key(f): f for f in cache["fills"]} if cache else {}
        now = int(time.time() * 1000)
        fills.update(hl_pages(address, cache["fetched"] - DAY * 1000 if cache else 0))
        if not fills:
            raise SystemExit(f"{address} has no Hyperliquid perp fills")
        cache = {"fetched": now, "keeps": HL_KEEPS, "fills": hl_order(fills.values())}
        save_json_gz(path, cache)
    elif not path.exists():
        save_json_gz(path, cache)
    old.unlink(missing_ok=True)
    return cache["fills"]


def hl_curve(address, since, end=None):
    """The wallet's hourly deployed-capital curve, its scores, its fills per coin, start, end, and its account curve.

    The deployed curve (deployed_curve) is returned first: each hour's PnL
    over the notional held the hour before or opened in the hour. The
    account curve, a time-weighted return on the whole account, is last.
    Total PnL is realized plus unrealized: each fill pays its price and FEE
    of its notional in cash, and each open position is worth its size times
    the hour's price, less the positions held at the start. A few fills are
    missing from the API; the position they move is still right, from each
    fill's startPosition, and is bought or sold at the next fill's price.
    gap_share is their notional over the notional of all fills in the
    window, gaps included: above a few percent the curve is unreliable.
    The hour's price is the close of the bar that ends on it, else the open
    of the bar that starts on it, else the last fill price.

    The account is the whole Hyperliquid account, perp plus spot: its value
    at the start (the portfolio snapshot), plus the perp PnL since, plus the
    net USDC that entered it from outside since (the ledger: HL_FLOWS;
    moves between perp and spot are internal), at least the open notional
    over HL_MAX_LEVERAGE. A flow counts from the hour it lands in. Each
    hour's return is its PnL over the account at the hour before (0 when
    that is not positive); the equity compounds them from $10,000.
    `start_account` is the account at the start, `net_flows` the net inflow in
    the window, and `account_drift` the largest gap between a snapshot
    inside the window and the account computed at it, as a share of the
    account: spot token prices, funding, and anything else not modelled.
    When the ledger cannot be fetched the account re-anchors at each
    snapshot instead, `net_flows` comes from the snapshots, and
    `account_drift` is None.
    The curve starts at `since`, or later when the fills start later.
    """
    fills = hl_fills(address)
    first = pd.Timestamp(fills[0]["time"], unit="ms").floor("D").strftime("%Y-%m-%d")
    start = max(since, first)
    end = end or pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    lo, hi = (int(pd.Timestamp(x, tz="UTC").timestamp()) for x in (start, end))
    grid = np.arange(lo, hi, HOUR, dtype="int64")
    covered = fills[0]["time"] // 1000 < lo  # the history reaches back past the window start
    by_coin = {}
    for f in fills:
        by_coin.setdefault(f["coin"], []).append(f)
    cash, value, gross, opened = (np.zeros(len(grid)) for _ in range(4))
    counts, closes, missing, traded = {}, [], 0.0, 0.0  # notional of the gaps, and of fills plus gaps
    for coin, fs in by_coin.items():
        fs = hl_order(fs)
        t = np.array([f["time"] // 1000 for f in fs])
        size = np.array([hl_signed(f) for f in fs])
        px = np.array([float(f["px"]) for f in fs])
        before = np.array([float(f["startPosition"]) for f in fs])
        after = before + size
        # A position change no fill explains trades at the next fill's price: no gain, no loss.
        # When the history covers the window start, a coin whose first fill is inside the window
        # and starts from a position was opened by fills the API never served: that open is such
        # a change. Otherwise (the history begins inside the window), or when the first fill is
        # at or before the window start, the start position is the position held when the window opens.
        k0 = np.searchsorted(t, lo)  # fills before the window
        opened_inside = covered and k0 == 0 and t[0] > lo
        gap = before - np.concatenate([[0.0 if opened_inside else before[0]], after[:-1]])
        gap[np.abs(gap) <= 1e-9 * np.maximum(np.maximum(np.abs(before), np.abs(size)), 1e-12)] = 0.0
        held = 0.0 if opened_inside else after[k0 - 1] if k0 > 0 else before[0]
        k = np.searchsorted(t, grid, side="right")  # fills up to each hour
        pos = np.where(k > 0, after[np.maximum(k - 1, 0)], held)
        inside = (t >= lo) & (t < hi)
        missing += (np.abs(gap) * px)[inside].sum()
        traded += ((np.abs(size) + np.abs(gap)) * px)[inside].sum()
        if not inside.any() and not pos.any():
            continue
        counts[coin] = int(inside.sum())
        closes += closed_positions(before[inside], after[inside]).tolist()
        price = pd.Series(px, index=t).groupby(level=0).last()
        price = price.reindex(price.index.union(grid)).ffill().bfill().loc[grid]
        b = hl_bars(coin, start, end)
        if b is not None:
            ts, bar = b[0].ts, b[0]
            price = pd.Series(bar.close, index=ts + HOUR).reindex(grid).fillna(
                pd.Series(bar.open, index=ts).reindex(grid)).fillna(price)
        price = price.to_numpy()
        fee = FEE * np.abs(size + gap) * px
        cash += np.concatenate([[0], np.cumsum(np.where(inside, -(size + gap) * px - fee, 0))])[k]
        value += pos * price - held * price[0]
        gross += np.abs(pos) * price
        # Notional that grew the position: the gap trade (from before - gap to before), then the fill.
        grew = np.maximum(np.abs(before) - np.abs(before - gap), 0) + np.maximum(np.abs(after) - np.abs(before), 0)
        opened += np.diff(np.concatenate([[0], np.cumsum(np.where(inside, grew * px, 0))])[k], prepend=0.0)
    pnl = cash + value  # P(h): the dollar PnL known at each hour
    snaps, snap_flows = hl_snapshots(address)
    st, sv = snaps.index.to_numpy(), snaps.to_numpy()
    s = max(int(np.searchsorted(st, lo, side="right")) - 1, 0)  # the last snapshot at or before lo, else the first
    try:
        flows = hl_flows(address)
    except OSError as e:
        print(f"note: the ledger is unavailable ({e}); the account re-anchors at each snapshot")
        flows = None
    if flows is None:
        j = np.maximum(np.searchsorted(st, grid, side="right") - 1, 0)
        at = np.clip(np.searchsorted(grid, st[j], side="right") - 1, 0, len(grid) - 1)  # the hour each snapshot's PnL is known
        account = sv[j] + pnl - pnl[at]
        net, drift = net_flows(snap_flows, lo, hi), None
    else:
        between = flows[(flows.index > min(st[s], lo)) & (flows.index <= max(st[s], lo))].sum()
        at = min(int(np.searchsorted(grid, st[s], side="right")) - 1, len(grid) - 1)  # the hour the snapshot's PnL is known
        # The snapshot carried to lo: forward by the flows between, or back by the PnL and flows between.
        a0 = sv[s] + between if st[s] <= lo else sv[s] - (pnl[at] - pnl[0]) - between
        inflow = flows[(flows.index > lo) & (flows.index < hi)]
        f = np.concatenate([[0.0], np.cumsum(inflow.to_numpy())])[np.searchsorted(inflow.index.to_numpy(), grid, side="right")]
        account = a0 + pnl - pnl[0] + f  # A(h) = A(lo) + P(h) - P(lo) + F(h)
        net = float(f[-1])
    account = np.maximum(account, gross / HL_MAX_LEVERAGE)
    if flows is not None:
        inside = (st > lo) & (st < hi)
        at = np.searchsorted(grid, st[inside], side="right") - 1
        d = (sv[inside] - account[at]) / np.where(account[at] > 0, account[at], np.nan)
        drift = float(np.nanmax(np.abs(d))) if inside.any() and np.isfinite(d).any() else 0.0
    if not (account > 0).any():
        raise SystemExit(f"{address} holds nothing from {start} to {end}")
    held = account[:-1]
    r = np.where(held > 0, np.diff(pnl) / np.where(held > 0, held, 1.0), 0.0)
    eq = pd.Series(vt.ACCOUNT * np.concatenate([[1.0], np.cumprod(1 + r)]), index=grid)
    eq[(eq <= 0).cummax()] = 0.0  # a blown account stays at zero
    deployed = deployed_curve(pd.Series(pnl, index=grid), gross, opened)
    lost, lost_share = dropped_pnl(pnl, gross, opened)
    stats = {"deployed": curve_stats(deployed), "account": curve_stats(eq),
             "trades": len(closes), "short_share": float(np.mean(closes)) if closes else 0.0,
             "pnl": float(pnl[-1]), "start_account": float(account[0]), "net_flows": net, "account_drift": drift,
             "gap_share": float(missing / traded) if traded else 0.0,
             "dropped_pnl": lost, "dropped_share": lost_share}
    return deployed, stats, pd.Series(counts).sort_values(ascending=False), start, end, eq


HL_FLOWS = """\
External flows, money entering or leaving the wallet's whole Hyperliquid account:
deposit (+usdc); withdraw (-usdc - fee);
internalTransfer and subAccountTransfer (+usdc as destination, -usdc - fee as user);
vaultCreate (-usdc - fee), vaultDeposit (-usdc), vaultWithdraw (+netWithdrawnUsd), vaultDistribution (+usdc);
send and spotTransfer of USDC with another address (+amount as destination, -amount - fee as user).
Internal moves are 0: accountClassTransfer (perp and spot) and a send between the wallet's own dexes.
Transfers of other tokens are 0 (hl_flows prints how many); spotGenesis, rewardsClaim and the rest are 0."""


def hl_flow(d, me):
    """The USDC a ledger delta moves into (+) or out of (-) the wallet's whole account; see HL_FLOWS."""
    t, usd, fee = d["type"], float(d.get("usdc") or 0), float(d.get("fee") or 0)
    is_user, is_dest = d.get("user", "").lower() == me, d.get("destination", "").lower() == me
    if t == "deposit":
        return usd
    if t == "withdraw":
        return -usd - fee
    if t in ("internalTransfer", "subAccountTransfer"):
        return usd * is_dest - (usd + fee) * is_user
    if t in ("vaultCreate", "vaultDeposit"):
        return -usd - fee
    if t == "vaultWithdraw":
        return float(d.get("netWithdrawnUsd") or 0)
    if t == "vaultDistribution":
        return usd
    if t in ("send", "spotTransfer") and d.get("token") == "USDC" and is_user != is_dest:
        amount = float(d.get("amount") or 0)
        return amount * is_dest - (amount + fee) * is_user
    return 0.0


def hl_token_transfer(d):
    """True for a send or spotTransfer of a token other than USDC: left out of the flows."""
    return d["type"] in ("send", "spotTransfer") and d.get("token") != "USDC"


def hl_ledger(address):
    """The wallet's non-funding ledger updates (deposits, withdrawals, transfers), cached like its fills."""
    path = CACHE / f"wallet_hl_ledger_{address.lower()}.json.gz"
    cache = load_json_gz(path) if path.exists() else None
    if hl_stale(cache):
        items = {hl_ledger_key(x): x for x in cache["updates"]} if cache else {}
        now = int(time.time() * 1000)
        since = cache["fetched"] - DAY * 1000 if cache else 0
        while True:
            page = hl_post({"type": "userNonFundingLedgerUpdates", "user": address.lower(), "startTime": since})
            items.update((hl_ledger_key(x), x) for x in page)
            if len(page) < 2000:
                break
            last = page[-1]["time"]
            since = last if last > since else since + 1
            time.sleep(1)
        cache = {"fetched": now, "updates": sorted(items.values(), key=lambda x: x["time"])}
        save_json_gz(path, cache)
    return cache["updates"]


def hl_ledger_key(x):
    return (x.get("hash"), x["time"], json.dumps(x["delta"], sort_keys=True))


def hl_flows(address):
    """Net USDC into the wallet's whole account per unix second, from its ledger; see HL_FLOWS."""
    me = address.lower()
    flows, tokens = {}, 0
    for x in hl_ledger(address):
        usd = hl_flow(x["delta"], me)
        tokens += hl_token_transfer(x["delta"])
        if usd:
            flows[x["time"] // 1000] = flows.get(x["time"] // 1000, 0.0) + usd
    if tokens:
        print(f"note: {tokens} transfers of tokens other than USDC are left out of the flows")
    return pd.Series(flows, dtype=float).sort_index()


def net_flows(flows, lo, hi):
    """Net deposits from lo to hi: the change in a wallet's deposits-so-far series between the two."""
    if flows.empty:
        return 0.0
    before, inside = flows[flows.index <= lo], flows[flows.index < hi]
    base = before.iloc[-1] if len(before) else flows.iloc[0]
    return float((inside.iloc[-1] if len(inside) else base) - base)


HL_HISTORIES = ("AllTime", "Month", "Week", "Day")


def hl_portfolio(address):
    """The wallet's portfolio response, by history name, cached and refreshed hourly."""
    path = CACHE / f"wallet_hl_portfolio_{address.lower()}.json"
    cache = json.loads(path.read_text()) if path.exists() else None
    if hl_stale(cache):
        cache = {"fetched": int(time.time() * 1000), "portfolio": hl_post({"type": "portfolio", "user": address.lower()})}
        save_json(path, cache)
    return dict(cache["portfolio"])


def hl_history(portfolio, perp):
    """Account value at every snapshot of the perp (or whole-account) histories, and deposits so far.

    The value is the union of the all-time, month, week and day histories,
    one point per second. Deposits so far are value less pnl along the
    all-time history: a deposit moves the value and not the pnl.
    """
    names = [("perp" + n) if perp else (n[0].lower() + n[1:]) for n in HL_HISTORIES]
    value, flows = {}, {}
    for name in names:
        h = portfolio.get(name)
        if not h:
            continue
        for t, v in h["accountValueHistory"]:
            value.setdefault(t // 1000, float(v))
        if name == names[0]:
            pnl = {t // 1000: float(v) for t, v in h["pnlHistory"]}
            flows = {t: value[t] - p for t, p in pnl.items() if t in value}
    return pd.Series(value, dtype=float).sort_index(), pd.Series(flows, dtype=float).sort_index()


def hl_snapshots(address):
    """The wallet's whole-account value (perp plus spot) at each snapshot (unix seconds), and its deposits so far.

    A wallet with no whole-account history uses the perp one.
    """
    portfolio = hl_portfolio(address)
    value, flows = hl_history(portfolio, False)
    if value.empty:
        value, flows = hl_history(portfolio, True)
    if value.empty:
        raise SystemExit(f"{address} has no Hyperliquid account history")
    return value, flows


def hl_account(address, at):
    """The wallet's whole-account value at a unix time: the last snapshot at or before it, else the first."""
    value, _ = hl_snapshots(address)
    before = value[value.index <= at]
    return float(before.iloc[-1] if len(before) else value.iloc[0])


HL_COLS = ("ts", "open", "high", "low", "close")
HL_TFS = (TF.Hour1, TF.Hour4)


def hl_seed(coin):
    """A per-coin cache from the old per-range files: real candles and the covered range."""
    tag = coin.replace(":", "_")
    files = [p for p in CACHE.glob(f"mimic_hl_{tag}_*_*.npz") if p.stem.rsplit("_", 2)[0] == f"mimic_hl_{tag}"]
    if not files:
        return None
    c, ranges = {}, []
    for p in files:
        z = np.load(p)
        ranges.append(tuple(int(pd.Timestamp(x, tz="UTC").timestamp()) for x in p.stem.rsplit("_", 2)[1:]))
        for tf in HL_TFS:
            rule = hd.RULES[tf]
            real = z[f"{rule}_minutes_with_data"] > 0
            for col in HL_COLS:
                c[f"{rule}_{col}"] = np.concatenate([c.get(f"{rule}_{col}", np.array([])), z[f"{rule}_{col}"][real]])
    # Claim only the span of touching ranges that ends at the latest end.
    lo, hi = max(ranges, key=lambda r: r[1])
    for a, b in sorted(ranges, reverse=True):
        if b >= lo:
            lo = min(lo, a)
    return hl_merge(c, lo, hi)


def hl_merge(c, lo, hi):
    """Sorts and dedupes each rule's candles; marks the covered range and whether any rule is empty.

    On one ts the candle appended last wins: a refetch replaces a bar.
    """
    for tf in HL_TFS:
        rule = hd.RULES[tf]
        ts = c[f"{rule}_ts"].astype("int64")[::-1]
        _, keep = np.unique(ts, return_index=True)
        c.update({f"{rule}_{col}": c[f"{rule}_{col}"][::-1][keep] for col in HL_COLS})
        c[f"{rule}_ts"] = ts[keep]
    c.update(lo=lo, hi=hi, none=any(not len(c[f"{hd.RULES[tf]}_ts"]) for tf in HL_TFS))
    return c


HL_PAGE = 5000  # candles per candleSnapshot call


def hl_empty():
    return {f"{hd.RULES[tf]}_{col}": np.array([]) for tf in HL_TFS for col in HL_COLS}


def hl_fetch(coin, lo, hi):
    """Real candles of one coin on [lo, hi) from the API, as cache arrays; None when a request fails.

    The API serves at most HL_PAGE candles a call, so the range is fetched
    in pieces; endTime stops a millisecond short of hi, so the bar opening
    at hi, still forming, is never cached.
    """
    c = {}
    for tf in HL_TFS:
        rule, step = hd.RULES[tf], avbt_cpp.timeframe_seconds(tf)
        got = []
        for a in range(lo, hi, HL_PAGE * step):
            req = {"coin": coin, "interval": rule, "startTime": a * 1000,
                   "endTime": min(a + HL_PAGE * step, hi) * 1000 - 1}
            try:
                got += hl_post({"type": "candleSnapshot", "req": req}) or []
            except urllib.error.HTTPError as e:
                print(f"  {coin}: candles failed (HTTP {e.code})")
                return None
            time.sleep(1)
        for col, key in zip(HL_COLS, "tohlc"):
            c[f"{rule}_{col}"] = np.array([g[key] // 1000 if key == "t" else float(g[key]) for g in got])
    return c


def hl_bars(coin, start, end):
    """1-hour and 4-hour Hyperliquid bars of one coin on [start, end); None when it has none.

    One cache per coin, data/candles/mimic_hl_<coin>.npz, with the real
    candles and the range they cover; only the uncovered part is fetched.
    Missing bars are filled with the last close.
    """
    path = CACHE / f"mimic_hl_{coin.replace(':', '_')}.npz"
    lo, hi = (int(pd.Timestamp(x, tz="UTC").timestamp()) for x in (start, end))
    fresh = not path.exists()
    c = hl_seed(coin) if fresh else {k: v for k, v in np.load(path).items()}
    if c is None:
        c = hl_merge(hl_empty(), lo, lo)  # nothing covered yet
    elif c["none"]:
        return None  # a coin without candles stays so: no refetch
    if lo < c["lo"] or hi > c["hi"]:
        for a, b in ((lo, int(c["lo"])), (int(c["hi"]), hi)):
            if a < b:
                got = hl_fetch(coin, a, b)
                if got is None:  # a failed request: the coin has no candles
                    c.update(hl_empty())
                    break
                c.update({k: np.concatenate([c[k], got[k]]) for k in got})
        c, fresh = hl_merge(c, min(lo, int(c["lo"])), max(hi, int(c["hi"]))), True
    if fresh:
        save_npz(path, **c)
    if c["none"]:
        return None
    out = []
    for tf in HL_TFS:
        rule, step = hd.RULES[tf], avbt_cpp.timeframe_seconds(tf)
        df = pd.DataFrame({col: c[f"{rule}_{col}"] for col in HL_COLS}).set_index("ts")
        df = df[(df.index >= lo) & (df.index < hi)]
        if df.empty:
            return None
        df = df.reindex(np.arange(df.index[0], hi, step, dtype="int64"))
        df["minutes_with_data"] = np.where(df.close.isna(), 0, step // 60).astype("int32")
        df["close"] = df.close.ffill()
        for k in ("open", "high", "low"):
            df[k] = df[k].fillna(df.close)
        out.append(hd.to_bars(tf, df.reset_index(names="ts")))
    return out


VENUE = "avantis"


def use_venue(venue):
    """Hyperliquid has 1-hour and 4-hour bars only, so the timeframe knob shrinks."""
    global VENUE, TFS, TF_NAMES
    VENUE = venue
    TFS = HL_TFS if venue == "hyperliquid" else AV_TFS
    TF_NAMES = {avbt_cpp.timeframe_name(t): t for t in TFS}
    KNOBS["timeframe"] = list(TF_NAMES)


def load_markets(symbols, start, end):
    """Markets for the symbols with candles; drops the rest from symbols."""
    ids = PAIR_IDS if VENUE == "hyperliquid" or all(s in PAIR_IDS for s in symbols) else {v: k for k, v in vt._symbols().items()}
    markets = []
    for s in symbols:
        b = hl_bars(s, start, end) if VENUE == "hyperliquid" else bars(ids[s], start, end)
        if b is None or not len(b[0].ts):
            print(f"  {s}: no candles, left out")
            continue
        markets.append(avbt_cpp.Market(s, b))
    symbols[:] = [m.instrument for m in markets]
    return avbt_cpp.Markets(markets)


def settings(risk, scale=True):
    """The account: risk per trade is risk times leverage when `scale` is on."""
    s = avbt_cpp.PortfolioSettings()
    s.starting_balance, s.risk_per_trade, s.hard_stop = vt.ACCOUNT, risk, 1.0
    s.scale_risk_with_leverage = scale
    return s


def backtest(p, markets, costs, scale=True):
    """The mimic on `p`: the knobs, plus leverage and risk when p lacks them (LEVERAGE, RISK)."""
    params = {k: v for k, v in p.items() if k != "risk"}
    params["timeframe"] = TF_NAMES[params["timeframe"]]
    params.setdefault("leverage", LEVERAGE)
    return avbt_cpp.run("mimic", params, markets, costs, settings(p.get("risk", RISK), scale))


def rerun(saved, markets):
    """A saved fit's backtest with its saved fees and settings, which it prints."""
    c, s = saved.get("costs", {}), saved.get("settings", {})
    fees = (c.get("open_fee", FEE), c.get("close_fee", FEE))
    scale = s.get("scale_risk_with_leverage", False)
    params = {**saved["params"], "risk": s.get("risk_per_trade", saved["params"].get("risk", RISK))}
    print(f"fees {fees[0]:.3%} open, {fees[1]:.3%} close; risk {params['risk']}; scale_risk_with_leverage {scale}")
    return backtest(params, markets, {m: avbt_cpp.Costs(*fees) for m in saved["markets"]}, scale)


def on_grid(series, grid):
    """Equity as a return on the hourly grid, carried forward."""
    return series.reindex(series.index.union(grid)).ffill().bfill().loc[grid].to_numpy() / vt.ACCOUNT - 1


def hour_grid(start, end):
    """The hourly grid from `start` to `end` (dates): the close of each hour."""
    lo, hi = (int(pd.Timestamp(x, tz="UTC").timestamp()) for x in (start, end))
    return pd.Index(np.arange(lo, hi, HOUR, dtype="int64"))


CLOSES = {}  # (id(markets), first hour, hours) -> (markets, {instrument: 1-hour closes on the grid})


def market_closes(markets, grid):
    """Each market's 1-hour close at each grid hour, carried forward; computed once per Markets."""
    key = (id(markets), int(grid[0]), len(grid))
    hit = CLOSES.get(key)
    if hit is None or hit[0] is not markets:
        out = {}
        for mk in markets.markets:
            b = next(b for b in mk.timeframes if b.timeframe == TF.Hour1)
            close = pd.Series(b.close, index=b.ts + HOUR)
            out[mk.instrument] = close.reindex(close.index.union(grid)).ffill().bfill().loc[grid].to_numpy()
        hit = CLOSES[key] = (markets, out)
    return hit[1]


def mimic_pnl(r, markets, grid):
    """The mimic's P, N and T on the hourly grid (deployed_curve), from its equity and trades.

    A fill at a bar's open, time t, first shows in the equity at the bar's
    close, t plus the base timeframe (r.timeframe), so it belongs to the
    hour that close falls on: the notional a trade opens, its size at its
    entry price, counts in that hour, and its notional, its size times its
    market's 1-hour close, is held from that hour up to the hour before
    the exit shows. A position still open at the end (r.open_positions) is
    not a trade, but its mark is in the equity: it is held from its entry
    hour to the end of the grid.
    """
    grid = np.asarray(grid, dtype="int64")
    step = avbt_cpp.timeframe_seconds(r.timeframe)
    pnl = pd.Series(on_grid(mimic_equity(r), grid) * vt.ACCOUNT, index=grid)
    units, opened = {}, np.zeros(len(grid))
    held = [(t.instrument, t.side, t.size, t.entry_price, t.entry_time + step, t.exit_time + step) for t in r.trades]
    held += [(p.instrument, p.side, p.size, p.entry_price, p.entry_time + step, grid[-1] + 1) for p in r.open_positions]
    for instrument, side, size, entry_price, shows, ends in held:
        a, b = np.searchsorted(grid, [shows, ends], side="left")
        u = units.setdefault(instrument, np.zeros(len(grid) + 1))
        u[a] += size * (1 if side.name == "Long" else -1)
        u[b] -= size * (1 if side.name == "Long" else -1)
        if a < len(grid):
            opened[a] += size * entry_price
    closes = market_closes(markets, grid) if units else {}
    gross = sum((np.abs(np.cumsum(u)[:-1]) * closes[k] for k, u in units.items()), np.zeros(len(grid)))
    return pnl, gross, opened


def mimic_deployed(r, markets, grid):
    """The mimic's deployed-capital curve on the hourly grid (deployed_curve on mimic_pnl)."""
    return deployed_curve(*mimic_pnl(r, markets, grid))


def mimic_dropped(r, markets, grid):
    """The PnL the mimic's deployed curve drops (dropped_pnl on mimic_pnl)."""
    return dropped_pnl(*mimic_pnl(r, markets, grid))


def mimic_scores(r, markets, grid):
    """Both score sets of a backtest: on its deployed capital, and on its $10,000 account."""
    s = avbt_cpp.summary(r)
    P, N, T = mimic_pnl(r, markets, grid)
    lost, lost_share = dropped_pnl(P, N, T)
    return {"deployed": curve_stats(deployed_curve(P, N, T)),
            "account": {"sharpe": s["sharpe"], "max_drawdown": s["max_drawdown"], "return": s["total_return"]},
            "trades": s["trades"], "dropped_pnl": lost, "dropped_share": lost_share}


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
    gains tol of the MSE or less. All starts share one pool of backtest
    threads (the C++ run frees Python's lock) and one record of the runs
    done so far.
    """
    seen = {}

    def mse(p):
        key = effective(p)
        if key not in seen:
            r = backtest(p, markets, costs)
            seen[key] = float(np.mean((on_grid(mimic_deployed(r, markets, grid), grid) - target) ** 2))
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
            if before - best_mse <= tol * before:
                break
        return best, best_mse

    rng = np.random.default_rng(seed)
    starts = [first] + [{k: v[rng.integers(len(v))] for k, v in KNOBS.items()} for _ in range(restarts)]
    starts = [{k: v.item() if hasattr(v, "item") else v for k, v in p.items()} for p in starts]
    with ThreadPoolExecutor(workers or os.cpu_count()) as runs, ThreadPoolExecutor(len(starts)) as climbs:
        done = list(climbs.map(climb, range(len(starts)), starts))
    return min(done, key=lambda x: x[1])


def table(pairs):
    """Both score sets per side: on deployed capital first, on the account second."""
    print(f"{'':10}{'on deployed capital':>26}{'on the account':>26}")
    print(f"{'':10}{'sharpe':>8}{'max dd':>9}{'return':>9}" * 2 + f"{'trades':>8}")
    for name, s in pairs:
        s = score_sets(s)
        row = "".join(f"{s[k]['sharpe']:8.2f}{s[k]['max_drawdown']:9.1%}{s[k]['return']:9.1%}" for k in ("deployed", "account"))
        print(f"{name:10}{row}{s['trades']:8d}")


def report(out):
    print(f"\nwallet {out['address']}  markets {', '.join(out['markets'])}  {out['start']} to {out['end']}")
    print(f"mse {out['mse']:.5f}  (rmse {out['mse'] ** 0.5:.1%} of deployed capital)")
    table((name, out[name]) for name in ("wallet", "mimic"))
    warn_dropped("mimic", out["mimic"])
    print("strategy mimic, params:")
    for k, v in out["params"].items():
        print(f"  {k} = {v}")


def setup(address, top):
    """The wallet's curve and scores, and markets for its `top` busiest symbols that have candles."""
    if VENUE == "hyperliquid":
        since = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=HL_DAYS)).strftime("%Y-%m-%d")
        eq, wallet, counts, start, end, _ = hl_curve(address, since)
        print(f"total pnl ${wallet['pnl']:,.0f}; whole account ${wallet['start_account']:,.0f} at the start, "
              f"net deposits ${wallet['net_flows']:,.0f} since")
        if wallet["account_drift"] is not None and wallet["account_drift"] > 0.1:
            print(f"warning: the account snapshots are up to {wallet['account_drift']:.0%} off the computed account")
        if wallet["gap_share"] > 0.01:
            print(f"warning: the API is missing fills worth {wallet['gap_share']:.0%} of the traded notional; "
                  "the wallet's curve may be unreliable")
    else:
        eq, wallet, counts, start, end, _ = avantis_curve(address)
    warn_dropped("wallet", wallet)
    symbols = list(counts.index[:top])
    print(f"{counts.sum()} fills or orders, {start} to {end}; {counts.iloc[:top].sum()} of them in {', '.join(symbols)}")
    markets = load_markets(symbols, start, end)
    covered = float(counts[symbols].sum() / counts.sum())
    print(f"{covered:.0%} of the wallet's trades are in the kept markets")
    return {"eq": eq, "wallet": wallet, "symbols": symbols, "start": start, "end": end,
            "markets": markets, "covered_share": covered}


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
        if saved.get("avbt_version") != avbt_cpp.version:
            print(f"warning: saved with avbt {saved.get('avbt_version')}, this is {avbt_cpp.version}")
        use_venue(saved.get("venue", "avantis"))
        markets = load_markets(list(saved["markets"]), saved["start"], saved["end"])
        grid = hour_grid(saved["start"], saved["end"])
        rerun_scores = mimic_scores(rerun(saved, markets), markets, grid)
        table((("saved", saved["mimic"]), ("rerun", rerun_scores)))
        warn_dropped("rerun", rerun_scores)
        return

    if not args.address:
        ap.error("give a wallet address or --replay")
    use_venue(args.venue)
    address = checksum(args.address)
    w = setup(address, args.top)
    symbols, markets, start, end = w["symbols"], w["markets"], w["start"], w["end"]
    costs = {s: avbt_cpp.Costs(FEE, FEE) for s in symbols}
    grid = hour_grid(start, end)
    target = on_grid(w["eq"], grid)

    t0 = time.time()
    # Start on the wallet's usual side: with no filter on, both sides pass and it never opens.
    first = {**START, "side": -1 if w["wallet"]["short_share"] > 0.5 else 1}
    best, best_mse = fit(markets, costs, target, grid, first, args.restarts, args.passes,
                         workers=args.workers)
    out = {"address": address, "venue": VENUE, "avbt_version": avbt_cpp.version, "strategy": "mimic",
           "params": {**best, "leverage": LEVERAGE},
           "markets": symbols, "covered_share": w["covered_share"], "start": start, "end": end,
           "costs": {"open_fee": FEE, "close_fee": FEE},
           "settings": {"starting_balance": vt.ACCOUNT, "risk_per_trade": RISK, "hard_stop": 1.0,
                        "scale_risk_with_leverage": True},
           "mse": best_mse, "wallet": w["wallet"], "mimic": mimic_scores(backtest(best, markets, costs), markets, grid)}
    path = CACHE / f"mimic_{'hl_' if VENUE == 'hyperliquid' else ''}{address}.json"
    save_json(path, out, indent=1)
    report(out)
    print(f"\n{time.time() - t0:.0f} s; saved {path}")

if __name__ == "__main__":
    main()

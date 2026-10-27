"""Fit the C++ mimic strategy to a wallet's return on peak own capital.

Downloads the wallet's trades, builds its hourly return on peak own
capital, marked to market, loads bars for its busiest markets, then tunes
the mimic strategy two knobs at a time to minimize the MSE between the
two curves. It fits the whole history on purpose: no test period.

The curve fitted and scored is wallet_returns.ledger's return: total
PnL (realized plus unrealized) over the peak of the trader's own money
in positions. Nothing is compounded. Wallet and mimic
are measured the same way: the mimic's trades become Avantis close rows
(mimic_rows). Account size does not change it, but leverage does, so
leverage is a knob that starts at the wallet's median. Every hour where
the wallet's return is defined counts the same; the mimic counts 0
before its first open. See docs/mimic.md for the accepted limits.

Every score comes from that one return (own_scores): final, peak,
lowest, max drawdown and Sharpe.

The mimic pays FEE of notional at each open and each close, 0.05% per
round trip, and no funding. The wallet's return uses its real fees and
funding. A trade is one position from open to full close.

Run from the repository root:

    python3 examples/mimic.py 0xFFB7eF358cEe48DaFE15B63A625DFF9eA4E2268F

Writes data/candles/mimic_<address>.json: the strategy, its params, the
markets, dates, costs and settings, and the scores, and beside it
mimic_<address>_target.npz, the wallet's curve. To rerun it:

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

sys.path.insert(0, str(HERE))
import wallet_returns as W  # noqa: E402

avbt_cpp, TF = hd.avbt_cpp, hd.TF
CACHE = hd.ch.CACHE
FEE = 0.00025  # 0.025% of notional at each open and each close: 0.05% per round trip
HOUR, DAY = 3600, 86400
AV_TFS = TFS = (TF.Min15, TF.Hour1, TF.Hour4)
TF_NAMES = {avbt_cpp.timeframe_name(t): t for t in TFS}

# Account size does not change the return on peak own capital, so risk per
# trade is fixed: RISK times the leverage of the $10,000 account
# (scale_risk_with_leverage). Leverage does change it, so it is a knob;
# LEVERAGE is its value when a run has none.
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
    "leverage": [1, 2, 3, 5, 10, 15, 20, 25, 30, 40, 50, 75, 100],  # main adds the wallet's median to a copy
}
GROUPS = {"rsi_": "rsi_on", "trend_": "trend_on", "move_": "move_on", "break_": "break_on", "hour_": "hours_on"}
START = {"timeframe": "1 hour", "side": 0, "rsi_on": False, "rsi_period": 14, "rsi_level": 30,
         "trend_on": False, "trend_period": 50, "trend_dir": 1, "move_on": False, "move_lag": 4,
         "move_size": 0.03, "move_dir": -1, "break_on": False, "break_period": 24, "break_dir": 1,
         "hours_on": False, "hour_from": 0, "hour_to": 24, "atr_period": 14, "hold": 24,
         "stop_atrs": 3, "tp_atrs": 3, "leverage": LEVERAGE}


def checksum(address):
    """EIP-55 case: the trade API only answers to it."""
    from Crypto.Hash import keccak

    a = address.lower().removeprefix("0x")
    h = keccak.new(digest_bits=256, data=a.encode()).hexdigest()
    return "0x" + "".join(c.upper() if int(h[i], 16) >= 8 else c for i, c in enumerate(a))


SCORE_KEYS = ("final", "peak", "lowest", "max_drawdown", "sharpe", "rmse")
OLD_KEYS = ("own", "account", "deployed", "return", *SCORE_KEYS)


def score_sets(s):
    """A side's saved scores as one flat set: SCORE_KEYS and "trades".

    An older file kept an "own" set beside an "account" set: its own
    final, peak, lowest and rmse carry over, the rest are NaN until it is
    fitted again.
    """
    if "own" not in s and "final" in s:
        return s
    own = s.get("own") or {}
    return {**{k: v for k, v in s.items() if k not in OLD_KEYS},
            **{k: own.get(k, float("nan")) for k in SCORE_KEYS}}


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
    """The wallet's stats, its orders per symbol, start and end on Avantis.

    An order is one position: symbol, side, index, open price and open
    minute. stats: "trades", the orders, and "short_share".
    """
    rows = json.loads(download(address).read_text())["trades"]
    whole = [r for r in rows if all(r.get(k) is not None for k in ("positionSize", "gross", "leverage"))
             and float(r["leverage"]) > 0 and pd.Timestamp(r["time"]).timestamp() >= r["openedAt"]]
    if len(whole) < len(rows):
        print(f"  {len(rows) - len(whole)} rows without size, gross or leverage skipped")
    rows = whole
    orders, last = set(), 0
    for r in rows:
        orders.add((r["symbol"], bool(r["buy"]), r["index"], round(float(r["openPrice"]), 6), r["openedAt"] // 60))
        PAIR_IDS[r["symbol"]] = r["pairIndex"]
        last = max(last, int(pd.Timestamp(r["time"]).timestamp()))
    start, end = day_bounds(min(r["openedAt"] for r in rows), last)[:2]
    counts = pd.Series([k[0] for k in orders]).value_counts()
    stats = {"trades": len(orders), "short_share": float(np.mean([not k[1] for k in orders]))}
    return stats, counts, start, end


HL = "https://api.hyperliquid.xyz/info"
HL_DAYS = 200  # Hyperliquid serves the last 5000 candles: about 208 days of 1-hour bars
HL_KEEP = ("coin", "px", "sz", "side", "time", "startPosition", "fee", "tid", "dir", "hash", "closedPnl")
# What a fill cache holds: hl_spot's fills, with HL_KEEP; a cache of anything else is refetched.
HL_KEEPS = "perps and outcome markets, with closedPnl"
HL_FILL_LIMIT = 10_000  # the API serves only a wallet's 10,000 most recent fills
HL_FUNDING_PAGE = 500  # funding items per userFunding call


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
    and merges by tid. A cache without tid, or with another "keeps" (from
    before outcome markets or closedPnl were kept), is refetched in full.
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


def hl_fills_capped(fills, since_ms=None):
    """True when `fills` may be cut at the start: the API serves only HL_FILL_LIMIT fills.

    With `since_ms` (a window start), only when the oldest fill is after
    it: the cache merges refreshes, so it can pass the limit with the
    whole window still in it.
    """
    if len(fills) < HL_FILL_LIMIT:
        return False
    return since_ms is None or min(f["time"] for f in fills) > since_ms


def hl_funding(address, since, end=None):
    """The wallet's funding payments from `since` to before `end` (ms), oldest first.

    Each is {"time": ms, "coin": str, "usdc": float}; usdc > 0 is received.
    Cached gzipped with its fetch time and the earliest time it covers,
    refreshed hourly from a day before the fetch, merged by (time, coin).
    A `since` before the cache's start refetches in full.
    """
    path = CACHE / f"wallet_hl_funding_{address.lower()}.json.gz"
    cache = load_json_gz(path) if path.exists() else None
    if isinstance(cache, dict) and cache.get("from", 0) > since:
        cache = None
    if hl_stale(cache):
        items = {(x["time"], x["coin"]): x for x in cache["funding"]} if cache else {}
        now = int(time.time() * 1000)
        start = cache["fetched"] - DAY * 1000 if cache else since
        while True:
            body = {"type": "userFunding", "user": address.lower(), "startTime": start}
            page = hl_post(body) or []
            for x in page:
                d = x["delta"]
                items[(x["time"], d["coin"])] = {"time": x["time"], "coin": d["coin"], "usdc": float(d["usdc"])}
            if len(page) < HL_FUNDING_PAGE:
                break
            last = page[-1]["time"]
            start = last if last > start else start + 1
            time.sleep(1)
        first = cache["from"] if cache else since
        cache = {"fetched": now, "from": first, "funding": sorted(items.values(), key=lambda x: x["time"])}
        save_json_gz(path, cache)
    return [x for x in cache["funding"] if x["time"] >= since and (end is None or x["time"] < end)]


def hl_leverage(address, coins):
    """The wallet's leverage setting per coin, {coin: float}, from activeAssetData.

    A coin whose request fails or has no leverage is None: the caller
    decides what to use. Cached hourly; a coin not yet cached is fetched.
    """
    path = CACHE / f"wallet_hl_leverage_{address.lower()}.json"
    cache = json.loads(path.read_text()) if path.exists() else None
    if hl_stale(cache):
        cache = {"fetched": int(time.time() * 1000), "leverage": {}}
    got = cache["leverage"]
    todo = [c for c in coins if c not in got]
    for coin in todo:
        try:
            r = hl_post({"type": "activeAssetData", "user": address.lower(), "coin": coin})
            got[coin] = float(r["leverage"]["value"])
        except Exception:
            got[coin] = None
    if todo or not path.exists():
        save_json(path, cache)
    return {c: got[c] for c in coins}


def hl_curve(address, since, end=None):
    """The wallet's hourly dollar PnL, its stats, its fills per coin, start and end.

    stats: "trades", "short_share", "pnl", the PnL at the end, and
    "gap_share". Total PnL is realized plus unrealized: each fill pays its price and FEE
    of its notional in cash, and each open position is worth its size times
    the hour's price, less the positions held at the start. A few fills are
    missing from the API; the position they move is still right, from each
    fill's startPosition, and is bought or sold at the next fill's price.
    gap_share is their notional over the notional of all fills in the
    window, gaps included: above a few percent the curve is unreliable.
    The hour's price is the close of the bar that ends on it, else the open
    of the bar that starts on it, else the last fill price.
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
    cash, value = np.zeros(len(grid)), np.zeros(len(grid))
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
    pnl = cash + value  # P(h): the dollar PnL known at each hour
    stats = {"trades": len(closes), "short_share": float(np.mean(closes)) if closes else 0.0,
             "pnl": float(pnl[-1]), "gap_share": float(missing / traded) if traded else 0.0}
    return pd.Series(pnl, index=grid), stats, pd.Series(counts).sort_values(ascending=False), start, end


def hl_portfolio(address):
    """The wallet's portfolio response, by history name, cached and refreshed hourly."""
    path = CACHE / f"wallet_hl_portfolio_{address.lower()}.json"
    cache = json.loads(path.read_text()) if path.exists() else None
    if hl_stale(cache):
        cache = {"fetched": int(time.time() * 1000), "portfolio": hl_post({"type": "portfolio", "user": address.lower()})}
        save_json(path, cache)
    return dict(cache["portfolio"])


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


def settings(risk, scale=True, hard_stop=1.0):
    """The account: risk per trade is risk times leverage when `scale` is on."""
    s = avbt_cpp.PortfolioSettings()
    s.starting_balance, s.risk_per_trade, s.hard_stop = vt.ACCOUNT, risk, hard_stop
    s.scale_risk_with_leverage = scale
    return s


def backtest(p, markets, costs, scale=True, hard_stop=1.0):
    """The mimic on `p`: the knobs, with LEVERAGE and RISK when p lacks leverage or risk."""
    params = {k: v for k, v in p.items() if k != "risk"}
    params["timeframe"] = TF_NAMES[params["timeframe"]]
    params.setdefault("leverage", LEVERAGE)
    return avbt_cpp.run("mimic", params, markets, costs, settings(p.get("risk", RISK), scale, hard_stop))


def rerun(saved, markets):
    """A saved fit's backtest with its saved fees and settings, which it prints."""
    c, s = saved.get("costs", {}), saved.get("settings", {})
    fees = (c.get("open_fee", FEE), c.get("close_fee", FEE))
    # Fits saved before profit share have no "profit_share": none.
    share = c.get("profit_share") or None
    scale = s.get("scale_risk_with_leverage", False)
    params = {**saved["params"], "risk": s.get("risk_per_trade", saved["params"].get("risk", RISK))}
    print(f"fees {fees[0]:.3%} open, {fees[1]:.3%} close; profit share {share}; risk {params['risk']}; "
          f"scale_risk_with_leverage {scale}")
    return backtest(params, markets, market_costs(saved["markets"], fees, share), scale)


PAIRS = []  # Veranta Copy's markets, read once


def pairs():
    """Veranta Copy's markets: symbol, max_leverage and min_position_usdc, among others."""
    if not PAIRS:
        import urllib.request
        with urllib.request.urlopen("https://verantacopy.com/api/pairs", timeout=60) as r:
            PAIRS.extend(json.load(r))
    return PAIRS


def min_positions(names):
    """Each market's smallest notional on Veranta; 0, and says so, when it is not listed. ADR 0022."""
    rows = {p["symbol"]: p["min_position_usdc"] for p in pairs()}
    out = {m: rows.get(m, rows.get(f"{m}/USD")) for m in names}
    bare = [m for m, v in out.items() if v is None]
    if bare:
        print(f"no Veranta min position for {', '.join(bare)}: run with none")
    return {m: float(v or 0.0) for m, v in out.items()}


def market_costs(names, fees, share=None):
    """Costs per market at the table spread and Veranta's min position; a market with no
    table row runs with no spread, and says so."""
    rows = avbt_cpp.veranta_spreads()
    bare = [m for m in names if m not in rows and f"{m}/USD" not in rows]
    if bare:
        print(f"no Veranta spread for {', '.join(bare)}: run with none")
    least = min_positions(names)
    return {m: avbt_cpp.Costs(*fees, profit_share=share, spread=0 if m in bare else None,
                              min_position=least[m]) for m in names}


def min_budget(r, costs):
    """The smallest account at which every open in result `r` clears its market's min position.

    Sizes scale with the account, so it is the account times the largest min over notional.
    Parts of one position, closed apart, add up to its notional.
    """
    opened = {}
    for t in r.trades:
        key = (t.instrument, t.entry_time)
        opened[key] = opened.get(key, 0.0) + t.size * t.entry_price
    need = [costs[m].min_position / n for (m, _), n in opened.items() if n > 0]
    return vt.ACCOUNT * max(need, default=0.0)


def on_grid(series, grid):
    """Equity as a return on the hourly grid, carried forward."""
    return series.reindex(series.index.union(grid)).ffill().bfill().loc[grid].to_numpy() / vt.ACCOUNT - 1


def hour_grid(start, end):
    """The hourly grid from `start` to `end` (dates): the close of each hour."""
    lo, hi = (int(pd.Timestamp(x, tz="UTC").timestamp()) for x in (start, end))
    return pd.Index(np.arange(lo, hi, HOUR, dtype="int64"))


PRICES = {}  # id(markets) -> (markets, market_prices(markets))


def market_prices(markets):
    """Each market's 1-hour closes, indexed by the second each is known: bar open plus an hour.

    Computed once per Markets.
    """
    hit = PRICES.get(id(markets))
    if hit is None or hit[0] is not markets:
        out = {}
        for mk in markets.markets:
            b = next(b for b in mk.timeframes if b.timeframe == TF.Hour1)
            out[mk.instrument] = pd.Series(np.asarray(b.close, float), index=np.asarray(b.ts) + HOUR)
        hit = PRICES[id(markets)] = (markets, out)
    return hit[1]


def iso(t):
    """A unix second as the ISO time an Avantis row carries."""
    return pd.Timestamp(int(t), unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def mimic_rows(r, grid, leverage=None):
    """A backtest's trades as Avantis close rows, for wallet_returns.ledger.

    A fill at a bar's open, time t, first shows at the bar's close, t plus
    the base timeframe (r.timeframe), so each row opens and closes one base
    step after its trade's fills. The margin is size times entry price
    over leverage; gross is the price PnL before costs, result plus fees
    plus holding costs. Trade.fees holds the open, close and profit-share
    fee together, so the ledger realizes the opening fee at the close, not at
    the open: one fee off for the length of each hold, accepted. Holding
    costs are the row's funding (+ is a cost), spread over the hold.

    A position still open at the end (r.open_positions) is a row that
    closes an hour after the last grid hour, so it is only marked, never
    realized; its gross, fee and funding are 0. A Position carries no
    leverage, so it takes `leverage`, the run's, else LEVERAGE.
    """
    step = avbt_cpp.timeframe_seconds(r.timeframe)

    def row(x, lev, closes, gross, fees, funding):
        return {"symbol": x.instrument, "buy": x.side.name == "Long", "leverage": lev,
                "openPrice": x.entry_price, "openedAt": x.entry_time + step, "time": iso(closes),
                "positionSize": x.size * x.entry_price / lev,
                "gross": gross, "closingFee": fees, "funding": funding}

    rows = [row(t, t.leverage, t.exit_time + step, t.result + t.fees + t.holding_costs, t.fees, t.holding_costs)
            for t in r.trades]
    end = int(grid[-1]) + HOUR
    lev = leverage or LEVERAGE
    rows += [row(p, getattr(p, "leverage", None) or lev, end, 0.0, 0.0, 0.0) for p in r.open_positions]
    return rows


def mimic_book(r, markets, grid, leverage=None):
    """The mimic's wallet_returns.ledger table on `grid`; None when it never trades."""
    grid = np.asarray(grid, dtype="int64")
    rows = mimic_rows(r, grid, leverage)
    return W.ledger("avantis", rows, market_prices(markets), times=grid) if rows else None


def mimic_own(r, markets, grid, leverage=None):
    """The mimic's return on peak own capital (ledger "return") on `grid`; NaN before its first open."""
    book = mimic_book(r, markets, grid, leverage)
    return pd.Series(np.nan if book is None else book["return"].to_numpy(), index=np.asarray(grid, dtype="int64"))


def own_mse(mimic, target):
    """Mean of (mimic - target)^2 over hours where target is defined; the mimic counts 0 before its first open."""
    m, t = np.asarray(mimic, float), np.asarray(target, float)
    ok = ~np.isnan(t)
    if not ok.any():
        return float("nan")
    return float(np.mean((np.where(np.isnan(m[ok]), 0.0, m[ok]) - t[ok]) ** 2))


def own_scores(book, target=None):
    """The scores of a return on peak own capital: SCORE_KEYS.

    book: wallet_returns.ledger's table on the hourly grid, or None. Final,
    peak and lowest are its return over the hours where it is defined.
    Max drawdown is the largest fall of 1 + return from a high, over the
    high. Sharpe is daily_sharpe's. rmse is the fit's against target, NaN
    without one.
    """
    nan = float("nan")
    empty = book is None or not len(book)
    rho = np.full(0 if target is None else len(target), nan) if empty else book["return"].to_numpy(float)
    rmse = own_mse(rho, target) ** 0.5 if target is not None else nan
    v = rho[np.isfinite(rho)]
    if not len(v):
        return {**dict.fromkeys(SCORE_KEYS, nan), "rmse": rmse}
    line = 1 + v
    high = np.maximum.accumulate(line)
    fall = np.where(high > 0, (high - line) / np.where(high > 0, high, 1.0), 0.0)
    return {"final": float(v[-1]), "peak": float(v.max()), "lowest": float(v.min()),
            "max_drawdown": float(fall.max()), "sharpe": daily_sharpe(book), "rmse": rmse}


def daily_sharpe(book):
    """Sharpe of the return on peak own capital, read at each UTC midnight.

    A day's return is its PnL change over the capital at its end; days
    with no capital yet are left out. The mean over the sample standard
    deviation, times sqrt(365); NaN under 30 days or with no spread
    beyond float noise.
    """
    day = book[book["time"] % DAY == 0]
    pnl, cap = day["pnl"].to_numpy(float), day["capital"].to_numpy(float)[1:]
    r = (np.diff(pnl) / np.where(cap > 0, cap, 1.0))[cap > 0]
    if len(r) < 30 or not r.std(ddof=1) > 1e-12 * abs(r.mean()):
        return float("nan")
    return float(r.mean() / r.std(ddof=1) * np.sqrt(365))


def mimic_scores(r, markets, grid, target=None, leverage=None):
    """A backtest's scores on its return on peak own capital, and its trades."""
    return {**own_scores(mimic_book(r, markets, grid, leverage), target), "trades": avbt_cpp.summary(r)["trades"]}


def effective(p):
    """p without knobs whose switch is off, so equal runs share one cache key."""
    out = dict(p)
    for prefix, switch in GROUPS.items():
        if not p[switch]:
            for k in list(out):
                if k.startswith(prefix) and k != switch:
                    del out[k]
    return tuple(sorted(out.items()))


def fit(markets, costs, target, grid, first, restarts=4, passes=6, tol=0.01, workers=None, seed=0, knobs=None):
    """Climbs from `first` and from `restarts` random starts, all at once; keeps the best.

    knobs: the values each knob tries; default KNOBS.

    Each start climbs two knobs at a time, every pair per pass, until a pass
    gains tol of the MSE or less. All starts share one pool of backtest
    threads (the C++ run frees Python's lock) and one record of the runs
    done so far.
    """
    seen = {}
    knobs = knobs or KNOBS

    def mse(p):
        key = effective(p)
        if key not in seen:
            r = backtest(p, markets, costs)
            seen[key] = own_mse(mimic_own(r, markets, grid, p.get("leverage")), target)
        return seen[key]

    def climb(i, best):
        best_mse = mse(best)
        for n in range(1, passes + 1):
            before = best_mse
            for a, b in itertools.combinations(knobs, 2):
                tries = [{**best, a: x, b: y} for x in knobs[a] for y in knobs[b]]
                for p, m in zip(tries, runs.map(mse, tries)):
                    if m < best_mse - 1e-12:
                        best, best_mse = p, m
            print(f"start {i}, pass {n}: mse {best_mse:.5f}, {len(seen)} backtests", flush=True)
            if before - best_mse <= tol * before:
                break
        return best, best_mse

    rng = np.random.default_rng(seed)
    starts = [first] + [{k: v[rng.integers(len(v))] for k, v in knobs.items()} for _ in range(restarts)]
    starts = [{k: v.item() if hasattr(v, "item") else v for k, v in p.items()} for p in starts]
    with ThreadPoolExecutor(workers or os.cpu_count()) as runs, ThreadPoolExecutor(len(starts)) as climbs:
        done = list(climbs.map(climb, range(len(starts)), starts))
    return min(done, key=lambda x: x[1])


def pct(v):
    """v as a percent 9 wide; "-" when it is NaN."""
    return f"{v:9.1%}" if np.isfinite(v) else f"{'-':>9}"


def table(pairs):
    """Each side's scores, all on its return on peak own capital."""
    print(f"{'':10}{'final':>9}{'peak':>9}{'lowest':>9}{'max dd':>9}{'sharpe':>8}{'rmse':>9}{'trades':>8}")
    for name, s in pairs:
        s = score_sets(s)
        sharpe = f"{s['sharpe']:8.2f}" if np.isfinite(s["sharpe"]) else f"{'-':>8}"
        print(f"{name:10}{pct(s['final'])}{pct(s['peak'])}{pct(s['lowest'])}{pct(s['max_drawdown'])}"
              f"{sharpe}{pct(s['rmse'])}{s['trades']:8d}")


def report(out):
    print(f"\nwallet {out['address']}  markets {', '.join(out['markets'])}  {out['start']} to {out['end']}")
    print(f"mse {out['mse']:.5f}  (rmse {out['mse'] ** 0.5:.1%} in return points)")
    table((name, out[name]) for name in ("wallet", "mimic"))
    print("strategy mimic, params:")
    for k, v in out["params"].items():
        print(f"  {k} = {v}")


ALWAYS = ("U >= -margin open", "12 return >= -100%")  # printed on Hyperliquid, pass or fail


def wallet_note(w):
    """Prints the wallet's return at the end, its capital, and the issue #15 checks it fails."""
    rho = w["target"].dropna()
    final = f"{rho.iloc[-1]:.1%}" if len(rho) else "none"
    capital = f"; capital ${w['capital']:,.0f}" if "capital" in w else ""
    print(f"wallet return on peak own capital at the end {final}{capital}")
    for name, ok, detail in w.get("checks", []):
        if ok is False or VENUE == "hyperliquid" and name in ALWAYS:
            print(f"  {'ok  ' if ok else 'FAIL'} {name}: {detail}")


def setup(address, top):
    """The wallet's scores, its return on peak own capital, and markets for its `top` busiest symbols.

    Besides the scores and markets: "book", the wallet's
    wallet_returns.ledger table on the hourly grid from start to end;
    "target", its return, NaN before its first open; "capital", its capital at the end; "leverage", its median
    leverage; and "checks", the issue #15 checks on its table.
    """
    if VENUE == "hyperliquid":
        since = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=HL_DAYS)).strftime("%Y-%m-%d")
        _, wallet, counts, start, end = hl_curve(address, since)
        print(f"total pnl ${wallet['pnl']:,.0f}")
        if wallet["gap_share"] > 0.01:
            print(f"warning: the API is missing fills worth {wallet['gap_share']:.0%} of the traded notional; "
                  "the wallet's curve may be unreliable")
    else:
        wallet, counts, start, end = avantis_curve(address)
    grid = hour_grid(start, end)
    me = sys.modules[__name__]
    if VENUE == "hyperliquid":
        book, checks = W.run_hyperliquid(me, address, since=start, end=end, times=grid)
    else:
        book, checks = W.run_avantis(me, address, times=grid)
    rho = book["return"].to_numpy(float) if len(book) else np.full(len(grid), np.nan)
    if not np.isfinite(rho).any():
        sys.exit(f"the wallet has no hour from {start} to {end} where its return is defined; nothing to fit")
    capital = float(book["capital"].iloc[-1]) if len(book) else 0.0
    symbols = list(counts.index[:top])
    print(f"{counts.sum()} fills or orders, {start} to {end}; {counts.iloc[:top].sum()} of them in {', '.join(symbols)}")
    markets = load_markets(symbols, start, end)
    covered = float(counts[symbols].sum() / counts.sum())
    print(f"{covered:.0%} of the wallet's trades are in the kept markets")
    return {"wallet": {**wallet, **own_scores(book if len(book) else None)}, "book": book, "symbols": symbols, "start": start, "end": end,
            "markets": markets, "covered_share": covered, "target": pd.Series(rho, index=grid),
            "capital": capital, "leverage": book.attrs.get("leverage", 1.0), "checks": checks}


def target_path(path):
    """Where a saved fit's wallet curve is kept: beside it, <name>_target.npz."""
    path = Path(path)
    return path.with_name(path.stem + "_target.npz")


def load_target(path, grid):
    """A saved wallet curve on `grid`, or None when there is none."""
    path = target_path(path)
    if not path.exists():
        return None
    z = np.load(path)
    return pd.Series(z["rho"], index=z["t"]).reindex(np.asarray(grid))


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
        if "fit" not in saved:
            print("warning: this fit was made on the return on deployed capital, an old curve; "
                  "it is rerun and scored on peak own capital")
        use_venue(saved.get("venue", "avantis"))
        markets = load_markets(list(saved["markets"]), saved["start"], saved["end"])
        grid = hour_grid(saved["start"], saved["end"])
        target = load_target(args.replay, grid)
        if target is None:
            print(f"the wallet curve file {target_path(args.replay).name} is missing, so the RMSE cannot be recomputed")
        lever = saved["params"].get("leverage", LEVERAGE)
        rerun_scores = mimic_scores(rerun(saved, markets), markets, grid, target, lever)
        table((("saved", saved["mimic"]), ("rerun", rerun_scores)))
        return

    if not args.address:
        ap.error("give a wallet address or --replay")
    use_venue(args.venue)
    address = checksum(args.address)
    w = setup(address, args.top)
    symbols, markets, start, end = w["symbols"], w["markets"], w["start"], w["end"]
    costs = market_costs(symbols, (FEE, FEE))
    grid = hour_grid(start, end)
    target = w["target"]
    lever = float(np.clip(w["leverage"], 1, 100))
    knobs = {**KNOBS, "leverage": sorted({*KNOBS["leverage"], lever})}

    t0 = time.time()
    # Start on the wallet's usual side and leverage: with no filter on, both sides pass and it never opens.
    first = {**START, "side": -1 if w["wallet"]["short_share"] > 0.5 else 1, "leverage": lever}
    best, best_mse = fit(markets, costs, target, grid, first, args.restarts, args.passes,
                         workers=args.workers, knobs=knobs)
    mimic = mimic_scores(backtest(best, markets, costs), markets, grid, target, best["leverage"])
    used = costs[symbols[0]]  # every market has the same fees
    out = {"address": address, "venue": VENUE, "avbt_version": avbt_cpp.version, "strategy": "mimic",
           "fit": "own", "params": best,
           "markets": symbols, "covered_share": w["covered_share"], "start": start, "end": end,
           "costs": {"open_fee": used.open_fee, "close_fee": used.close_fee,
                     "profit_share": used.profit_share,
                     "min_position": {m: costs[m].min_position for m in symbols}},
           "settings": {"starting_balance": vt.ACCOUNT, "risk_per_trade": RISK, "hard_stop": 1.0,
                        "scale_risk_with_leverage": True},
           "mse": best_mse, "wallet": w["wallet"], "mimic": mimic}
    path = CACHE / f"mimic_{'hl_' if VENUE == 'hyperliquid' else ''}{address}.json"
    save_npz(target_path(path), t=np.asarray(grid, dtype="int64"), rho=target.to_numpy(float))
    save_json(path, out, indent=1)
    report(out)
    wallet_note(w)
    print(f"\n{time.time() - t0:.0f} s; saved {path}")


if __name__ == "__main__":
    main()

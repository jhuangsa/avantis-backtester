"""Wallet return on peak own capital, realized plus unrealized (issue #15).

ledger() turns a wallet's Avantis closes or Hyperliquid fills, plus
prices, into one row per time: realized and unrealized PnL, fees,
funding, margin open, own money in positions, capital and return.

Own money is O = M - R: the margin open at cost less the PnL realized.
Capital K is the running peak of O, and the return is P / K, where
P = R + U. Nothing is compounded; deposits and idle cash do not count.

The state at time t counts every event at or before t. K takes the peak
of O over every event time and t itself, so evaluating at any times
gives the same values as the hourly grid where they share a time.

Run from the repository root:

    python3 examples/wallet_returns.py ADDRESS...
    python3 examples/wallet_returns.py --venue hyperliquid ADDRESS...

Writes data/candles/wallet_returns_<venue>_<address>.csv per wallet (the
git-ignored cache) and examples/wallet_returns_<venue>.html. Downloads
come from mimic.py, imported only inside main().
"""

from __future__ import annotations

import numpy as np
import pandas as pd

HOUR = 3600
COLUMNS = ["time", "realized", "unrealized_price", "unrealized_funding",
           "unrealized", "fees", "funding", "pnl", "margin_open", "own",
           "capital", "return", "margin_estimated"]


def ceil_hour(t):
    return -(-int(np.ceil(t)) // HOUR) * HOUR


def grid(first, last):
    """Every UTC hour end from ceil_hour(first) to ceil_hour(last)."""
    return np.arange(ceil_hour(first), ceil_hour(last) + 1, HOUR)


def price_at(series, t, fallback):
    """The last price with index <= t, else fallback (broadcast)."""
    t = np.asarray(t, float)
    out = np.broadcast_to(np.asarray(fallback, float), t.shape).copy()
    if series is None or len(series) == 0:
        return out
    s = series.sort_index()
    i = np.searchsorted(s.index.to_numpy(float), t, side="right") - 1
    known = i >= 0
    out[known] = s.to_numpy(float)[i[known]]
    return out


def has_prices(prices, market):
    s = prices.get(market)
    return s is not None and len(s) > 0


def unix(iso):
    ts = pd.to_datetime(pd.Series(iso), utc=True)
    return ((ts - pd.Timestamp(0, tz="UTC")) / pd.Timedelta(seconds=1)).to_numpy()


def num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(x) else x


def avantis_rows(records):
    """Usable closes as a DataFrame, and the count skipped."""
    keep, skipped = [], 0
    for r in records:
        m, g, lev = (num(r.get(k)) for k in ("positionSize", "gross", "leverage"))
        if m is None or g is None or lev is None or lev <= 0:
            skipped += 1
            continue
        keep.append(dict(
            market=r["symbol"], side=1.0 if r["buy"] else -1.0, lev=lev,
            p0=float(r["openPrice"]), a=float(r["openedAt"]), close=r["time"],
            m=m, g=g, phi=num(r.get("closingFee")) or 0.0,
            f=num(r.get("funding")) or 0.0))
    d = pd.DataFrame(keep)
    if len(d):
        d["c"] = unix(d.pop("close"))
        late = d["c"] >= d["a"]
        skipped += int((~late).sum())
        d = d[late].reset_index(drop=True)
    return d, skipped


def avantis_state(d, prices, t):
    """Columns at times t (array) for Avantis closes d."""
    a, c = d["a"].to_numpy()[:, None], d["c"].to_numpy()[:, None]
    closed, live = c <= t, (a <= t) & (t < c)
    m, f = d["m"].to_numpy()[:, None], d["f"].to_numpy()[:, None]
    p = np.vstack([price_at(prices.get(r.market), t, r.p0)
                   for r in d.itertuples()])
    move = (d["side"] * d["m"] * d["lev"]).to_numpy()[:, None]
    u_price = np.where(live, move * (p / d["p0"].to_numpy()[:, None] - 1), 0)
    held = np.where(live, (t - a) / np.where(c > a, c - a, 1), 0)
    gain = (d["g"] - d["phi"] - d["f"]).to_numpy()[:, None]
    return dict(
        realized=(closed * gain).sum(0),
        unrealized_price=u_price.sum(0),
        unrealized_funding=-(f * held).sum(0),
        fees=(closed * d["phi"].to_numpy()[:, None]).sum(0),
        funding=(closed * f).sum(0),
        margin_open=(live * m).sum(0))


def trade(q, e, i, d, px):
    """Apply signed size d at px to coin i; return the PnL realized."""
    if q[i] == 0 or np.sign(d) == np.sign(q[i]):
        e[i] = (abs(q[i]) * e[i] + abs(d) * px) / abs(q[i] + d)
        q[i] += d
        return 0.0
    done = min(abs(d), abs(q[i])) * (px - e[i]) * np.sign(q[i])
    rest = q[i] + d
    if abs(rest) <= 1e-12 * max(abs(q[i]), abs(d)):
        rest = 0.0
    if np.sign(rest) not in (0, np.sign(q[i])):
        e[i] = px
    q[i] = rest
    return done


def hl_replay(fills, prices, start):
    """Event times, per-coin size and entry after each event, cumulative
    price PnL and fees, the coins carried in at start, and the gap share.

    A fill whose startPosition differs from the replayed size is first
    preceded by a gap fill, at its price and with no fee, that closes
    the difference (fills the API never returned)."""
    fills = sorted(fills, key=lambda x: x["time"])
    coins = sorted({x["coin"] for x in fills})
    k = {c: i for i, c in enumerate(coins)}
    q, e = np.zeros(len(coins)), np.zeros(len(coins))
    carried = []
    for c in coins:
        first = next(x for x in fills if x["coin"] == c)
        q0 = float(first["startPosition"])
        if q0 != 0:
            s = prices.get(c)
            p = price_at(s, start, np.nan)[()]
            if np.isnan(p) and s is not None and len(s):
                s = s.sort_index()
                p = float(s[s.index >= start].iloc[0])
            q[k[c]], e[k[c]] = q0, float(first["px"]) if np.isnan(p) else p
            carried.append(c)
    times, Q, E, pnl, fee = [], [], [], [], []
    if carried:
        times.append(float(start))
        Q.append(q.copy()), E.append(e.copy()), pnl.append(0.0), fee.append(0.0)
    done, paid, gaps, total = 0.0, 0.0, 0.0, 0.0
    for x in fills:
        i, px = k[x["coin"]], float(x["px"])
        sz, sp = float(x["sz"]), float(x["startPosition"])
        gap = sp - q[i]
        if abs(gap) <= max(1e-12, 1e-9 * max(abs(sp), abs(q[i]))):
            gap = 0.0
        done += trade(q, e, i, gap, px) if gap else 0.0
        gaps += abs(gap) * px
        total += (sz + abs(gap)) * px
        done += trade(q, e, i, sz * (1 if x["side"] == "B" else -1), px)
        paid += float(x["fee"])
        times.append(x["time"] / 1000)
        Q.append(q.copy()), E.append(e.copy()), pnl.append(done), fee.append(paid)
    share = gaps / total if total else 0.0
    return (np.array(times), np.array(Q), np.array(E), np.array(pnl),
            np.array(fee), coins, carried, share)


def hl_state(ev, funding, prices, lever, t):
    """Columns at times t (array) for a Hyperliquid replay ev."""
    times, Q, E, pnl, fee, coins = ev[:6]
    i = np.searchsorted(times, t, side="right") - 1
    seen = i >= 0
    j = np.maximum(i, 0)
    q, e = Q[j] * seen[:, None], E[j] * seen[:, None]
    p = np.column_stack([price_at(prices.get(c), t, e[:, n])
                         for n, c in enumerate(coins)])
    lam = np.array([lever[c] for c in coins])
    ft = np.array([x["time"] / 1000 for x in funding], float)
    fu = np.array([float(x["usdc"]) for x in funding], float)
    o = np.argsort(ft, kind="stable")
    cum = np.concatenate([[0.0], np.cumsum(fu[o])])
    got = cum[np.searchsorted(ft[o], t, side="right")]
    fees = np.where(seen, fee[j], 0.0)
    u = (q * (p - e)).sum(1)
    return dict(
        realized=np.where(seen, pnl[j], 0.0) - fees + got,
        unrealized_price=u, unrealized_funding=np.zeros(len(t)),
        fees=fees, funding=-got,
        margin_open=(np.abs(q) * e / lam).sum(1))


def empty(**attrs):
    df = pd.DataFrame({c: pd.Series(dtype=float) for c in COLUMNS})
    df["time"] = df["time"].astype(np.int64)
    df["margin_estimated"] = df["margin_estimated"].astype(bool)
    df.attrs.update(dict(breach=False, no_prices=[], skipped=0, carried=[],
                         gap_share=0.0),
                    **attrs)
    return df


def ledger(venue, records, prices, *, funding=(), leverage=None, start=None,
           times=None) -> pd.DataFrame:
    """One row per time of a wallet's PnL, capital and return.

    venue: "avantis" (records = cached trade-history closes) or
    "hyperliquid" (records = fills; funding = userFunding rows;
    leverage = {coin: current leverage}; start = window start, unix s).
    prices: {market: Series of prices indexed by the unix second at
    which each is known}. times: unix seconds to evaluate at; default
    the hourly grid from the first open to the last event.
    """
    if venue not in ("avantis", "hyperliquid"):
        raise ValueError(f"unknown venue {venue!r}")
    records, funding = list(records), list(funding)
    if venue == "avantis":
        d, skipped = avantis_rows(records)
        if not len(d):
            return empty(skipped=skipped)
        markets = set(d["market"])
        first, events = d["a"].min(), np.concatenate([d["a"], d["c"]])
        state = lambda t: avantis_state(d, prices, t)
        attrs = dict(skipped=skipped, carried=[], gap_share=0.0)
    else:
        if not records:
            return empty()
        markets = {x["coin"] for x in records}
        lever = leverage or {}
        missing = sorted(markets - set(lever))
        if missing:
            raise ValueError(f"no leverage for {missing}")
        if start is None:
            start = min(x["time"] for x in records) / 1000 // HOUR * HOUR
        else:
            funding = [x for x in funding if x["time"] / 1000 >= start]
        ev = hl_replay(records, prices, start)
        first = start if ev[6] else ev[0].min()
        events = np.concatenate([ev[0], [x["time"] / 1000 for x in funding]])
        state = lambda t: hl_state(ev, funding, prices, lever, t)
        attrs = dict(skipped=0, carried=ev[6], gap_share=ev[7])
    t = grid(first, events.max()) if times is None else np.asarray(times)
    t = t.astype(float)
    every = np.union1d(events, t)
    step = max(1, 2_000_000 // max(len(records), 1))
    s = pd.concat([pd.DataFrame(state(every[i:i + step]))
                   for i in range(0, len(every), step)], ignore_index=True)
    s["own"] = s["margin_open"] - s["realized"]
    s["capital"] = s["own"].cummax().where(every >= first)
    s = s.iloc[np.searchsorted(every, t)].reset_index(drop=True)
    s.insert(0, "time", t.astype(np.int64))
    s["unrealized"] = s["unrealized_price"] + s["unrealized_funding"]
    s["pnl"] = s["realized"] + s["unrealized"]
    s["return"] = s["pnl"] / s["capital"]
    s["margin_estimated"] = venue == "hyperliquid"
    s = s[COLUMNS]
    s.attrs.update(
        breach=bool((s["return"] < -1 - 1e-9).any()),
        no_prices=sorted(m for m in markets if not has_prices(prices, m)),
        **attrs)
    return s


# --- command line ---

import json  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
LEADERBOARD = HERE.parent / "data" / "verantacopy-traders-all-time-returns.csv"
PAGE_POINTS = 1500  # most points per curve on the page
GAP_LIMIT = 0.01  # the share of missing fills mimic.py warns at


def usd(x):
    return f"${x:,.2f}"


def fnum(v):
    """A record's number; missing or blank is 0."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if np.isnan(x) else x


def kept(r):
    """An Avantis row that ledger() uses (the rest are attrs["skipped"])."""
    if any(r.get(k) is None for k in ("positionSize", "gross", "leverage",
                                       "openedAt", "time")):
        return False
    close = pd.Timestamp(r["time"]).timestamp()
    return fnum(r["leverage"]) > 0 and close >= fnum(r["openedAt"])


def checks(df, venue, records=(), *, funding=(), board=None, history=None,
           capped=False, fallback=()):
    """Checks 8-12 and 15-20 of issue #15 on one wallet's table.

    Returns (name, ok, detail) tuples; ok None is a note, not a pass or
    a fail. records and funding: what ledger() was given. board: the
    wallet's leaderboard row (Avantis). history: the pnlHistory as a
    Series by unix second (Hyperliquid). capped: the fills reached the
    API limit. fallback: coins whose leverage was guessed.
    Checks 15 and 16 read R at the last row, so they assume the table
    runs to the last event (the default grid).
    """
    out = []

    def add(name, ok, detail):
        out.append((name, None if ok is None else bool(ok), detail))

    if not len(df):
        add("table", None, "no trades in the window")
        return out
    a, records = df.attrs, list(records)
    cols = ("pnl", "realized", "unrealized", "margin_open", "capital",
            "return", "time")
    P, R, U, M, K, rho, t = (df[c].to_numpy(float) for c in cols)
    size = np.nanmax(np.abs(np.r_[P, R, M, np.nan_to_num(K)]))
    tol = 1e-7 * max(1.0, size)

    gap = np.abs(P - R - U).max()
    add("8 P = R + U", gap <= tol, f"largest gap {usd(gap)}")

    flat = np.abs(M) <= tol
    worst = np.abs(U[flat]).max() if flat.any() else 0.0
    add("9 U = 0 with no margin open", worst <= tol,
        f"{int(flat.sum())} hours with no margin open; "
        f"largest |U| {usd(worst)}")

    d = np.diff(K)
    fell = max(0.0, -np.nanmin(d)) if np.isfinite(d).any() else 0.0
    ok, detail = fell <= tol, f"largest fall {usd(fell)}"
    if venue == "avantis" and records:
        rows = [r for r in records if kept(r)]
        capped_loss = all(
            fnum(r["gross"]) - fnum(r.get("closingFee"))
            - fnum(r.get("funding")) >= -fnum(r["positionSize"]) - tol
            for r in rows)
        if capped_loss:
            opens = np.sort([fnum(r["openedAt"]) for r in rows])
            up = np.flatnonzero(d > tol) + 1
            n = (np.searchsorted(opens, t[up], "right")
                 - np.searchsorted(opens, t[up - 1], "right"))
            bad = int((n == 0).sum())
            ok = ok and bad == 0
            detail += f"; {bad} rises without an open"
        else:
            detail += ("; rises only at opens: not asserted, "
                       "a close lost more than its margin")
    add("10 capital never falls", ok, detail)

    live = np.isfinite(rho) & (K > 0) & (np.abs(P) > tol)
    bad = int((np.sign(rho[live]) != np.sign(P[live])).sum())
    add("11 sign of return = sign of P", bad == 0, f"{bad} hours differ")

    low = np.nanmin(rho) if np.isfinite(rho).any() else 0.0
    breach = bool(a.get("breach", low < -1 - 1e-9))
    note = "; the margin estimate is too low" if breach and \
        venue == "hyperliquid" else ""
    add("12 return >= -100%", not breach, f"lowest {low:.2%}{note}")

    under = U < -M - tol
    if under.any():
        with np.errstate(divide="ignore"):
            worst = (U[under] / M[under]).min()  # -inf with no margin
        add("U >= -margin open", False,
            f"{int(under.sum())} hours break it; worst U/M {worst:.2f}; "
            "the margin estimate is too low")
    else:
        add("U >= -margin open", True, "holds at every hour")

    if venue == "avantis":
        rows = [r for r in records if kept(r)]
        sent = [r for r in rows if r.get("usdcSent") is not None]
        want = sum(fnum(r["usdcSent"]) - fnum(r["positionSize"])
                   for r in sent)
        g = R[-1] - want
        ok = abs(g) <= tol + 0.01 * len(sent) if len(sent) == len(rows) \
            else None
        add("15 R = sum(usdcSent - positionSize)", ok,
            f"R {usd(R[-1])}, sum {usd(want)}, gap {usd(g)}; "
            f"{len(rows) - len(sent)} rows without usdcSent")
        add("skipped rows", None if a.get("skipped") else True,
            f"{a.get('skipped', 0)} rows without size, gross or leverage")
        if board is None:
            add("17 leaderboard", None, "not in leaderboard")
        else:
            bp = fnum(board["all_time_realized_pnl_usdc"])
            bm = fnum(board["collateral_used_usdc"])
            br = fnum(board["all_time_return_pct"])
            m = sum(fnum(r["positionSize"]) for r in rows)
            cover = m / bm if bm else 0.0
            g = R[-1] - bp
            room = max(1000.0, 0.05 * abs(bp))  # the PnL tolerance
            ok = abs(g) <= room if cover >= 0.99 else None
            add("17 leaderboard PnL", ok,
                f"R {usd(R[-1])} vs leaderboard {usd(bp)}: gap {usd(g)}")
            ms = sum(fnum(r["positionSize"]) for r in sent)
            ours = 100 * want / ms if ms else float("nan")
            add("17 leaderboard return",
                abs(ours - br) <= 100 * room / bm if cover >= 0.99
                else None,
                f"from the history {ours:.2f}% vs listed {br:.2f}% "
                f"(within {100 * room / bm:.2f} points: the PnL tolerance)")
            add("17 coverage", cover >= 0.9,
                f"history margin / leaderboard margin {cover:.1%}")
    else:
        carried = list(a.get("carried", []))
        gs = float(a.get("gap_share", 0.0))
        want = sum(fnum(f.get("closedPnl")) - fnum(f.get("fee"))
                   for f in records)
        want += sum(fnum(x.get("usdc")) for x in funding)
        g = R[-1] - want
        exact = not carried and gs == 0
        add("16 R = closedPnl - fee + funding",
            abs(g) <= tol + 0.01 * len(records) if exact else None,
            f"R {usd(R[-1])}, sum {usd(want)}, gap {usd(g)}"
            + ("" if exact else "; carried positions or missing fills "
               "make a gap expected"))
        if history is None or not len(history):
            add("18 portfolio pnlHistory", None, "no pnlHistory")
        else:
            h = history.sort_index()
            x, y = h.index.to_numpy(float), h.to_numpy(float)
            dh = np.interp(t[-1], x, y) - np.interp(t[0], x, y)
            dp = P[-1] - P[0]
            g = dp - dh
            add("18 change in P vs pnlHistory",
                abs(g) <= 0.1 * max(abs(dh), abs(dp)) + 1.0,
                f"P {usd(dp)}, pnlHistory {usd(dh)}, gap {usd(g)} "
                "(spot or flows the perp fills miss)")
        add("19 fill limit", not capped,
            "reached: older fills in the window are gone" if capped
            else "under the limit")
        add("20 gap_share", gs <= GAP_LIMIT,
            f"missing fills worth {gs:.2%} of the traded notional")
        add("carried in", None, ", ".join(carried) or "none")
        add("leverage fallback", None if fallback else True,
            ", ".join(fallback) or "none")
    if a.get("no_prices"):
        add("no prices", None, ", ".join(a["no_prices"]))
    return out


def board_row(address, path=LEADERBOARD):
    """The wallet's leaderboard row as a dict, or None."""
    if not Path(path).exists():
        return None
    b = pd.read_csv(path)
    hit = b[b["wallet_address"].str.lower() == address.lower()]
    return None if hit.empty else hit.iloc[0].to_dict()


def pnl_history(portfolio):
    """The perp (else whole-account) all-time pnlHistory by unix second."""
    for name in ("perpAllTime", "allTime"):
        h = portfolio.get(name) or {}
        if h.get("pnlHistory"):
            return pd.Series({t // 1000: float(v)
                              for t, v in h["pnlHistory"]}).sort_index()
    return None


def run_avantis(M, address):
    rows = json.loads(M.download(address).read_text())["trades"]
    good = [r for r in rows if kept(r)]
    if not good:
        return ledger("avantis", rows, {}), []
    first = int(min(r["openedAt"] for r in good))
    last = int(max(unix([r["time"] for r in good])))
    start, end = M.day_bounds(first, last)[:2]
    prices = {}
    for sym, pid in {r["symbol"]: r["pairIndex"] for r in good}.items():
        b = M.bars(pid, start, end)
        if b is not None and len(b[1].ts):
            prices[sym] = pd.Series(b[1].close, index=b[1].ts + HOUR)
    df = ledger("avantis", rows, prices)
    return df, checks(df, "avantis", rows, board=board_row(address))


def run_hyperliquid(M, address):
    now = pd.Timestamp.now(tz="UTC")
    since = (now - pd.Timedelta(days=M.HL_DAYS)).strftime("%Y-%m-%d")
    end = (now + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    lo = int(pd.Timestamp(since, tz="UTC").timestamp())
    every = M.hl_fills(address)
    fills = [f for f in every if f["time"] >= lo * 1000]
    funding = M.hl_funding(address, lo * 1000)
    coins = sorted({f["coin"] for f in fills} | {x["coin"] for x in funding})
    lev = M.hl_leverage(address, coins)
    known = [v for v in lev.values() if v]
    guess = float(np.median(known)) if known else 1.0
    fallback = sorted(c for c, v in lev.items() if not v)
    lev = {c: v or guess for c, v in lev.items()}
    prices = {}
    for c in sorted({f["coin"] for f in fills}):
        b = M.hl_bars(c, since, end)
        if b is not None:
            prices[c] = pd.Series(b[0].close, index=b[0].ts + HOUR)
    df = ledger("hyperliquid", fills, prices, funding=funding,
                leverage=lev, start=lo)
    res = checks(df, "hyperliquid", fills, funding=funding,
                 history=pnl_history(M.hl_portfolio(address)),
                 capped=M.hl_fills_capped(every, lo * 1000),
                 fallback=fallback)
    return df, res


def short(address):
    return f"{address[:6]}…{address[-4:]}"


def wallet_view(name, venue, df, results, points=PAGE_POINTS):
    """One wallet's page data: curves thinned to `points`, and its checks."""
    step = max(1, -(-len(df) // points))
    d = df.iloc[::step]
    if len(df) and d.index[-1] != df.index[-1]:
        d = pd.concat([d, df.iloc[[-1]]])

    def col(c, n):
        return [round(float(v), n) if np.isfinite(v) else None for v in d[c]]

    return {"name": name, "venue": venue,
            "t": d["time"].astype(int).tolist(), "ret": col("return", 5),
            "realized": col("realized", 2),
            "unrealized": col("unrealized", 2), "pnl": col("pnl", 2),
            "estimated": bool(df["margin_estimated"].any()),
            "checks": [[n, ok, det] for n, ok, det in results]}


def page(views):
    """The self-contained HTML page for wallet_view() dicts."""
    data = json.dumps(views).replace("</", "<\\/")
    return PAGE.replace("/*DATA*/null", data)


ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")


def main(argv=None, M=None):
    """The command line; M (mimic) may be given, for tests."""
    import argparse
    import sys

    def address(s):
        if not ADDRESS.fullmatch(s):
            raise argparse.ArgumentTypeError(f"not an address: {s!r}")
        return s

    ap = argparse.ArgumentParser(
        description="Wallet return on peak own capital (issue #15).")
    ap.add_argument("--venue", choices=["avantis", "hyperliquid"],
                    default="avantis")
    ap.add_argument("addresses", nargs="+", type=address)
    args = ap.parse_args(argv)
    if M is None:
        sys.path.insert(0, str(HERE))
        import mimic as M

    M.use_venue(args.venue)
    run = run_hyperliquid if args.venue == "hyperliquid" else run_avantis
    views, skipped = [], 0
    for raw in args.addresses:
        address = M.checksum(raw)
        try:
            df, res = run(M, address)
        except SystemExit as e:
            df, res = None, e
        if df is None or not len(df):
            print(f"{short(address)}: skipped ({res or 'no trades'})")
            skipped += 1
            continue
        path = M.CACHE / f"wallet_returns_{args.venue}_{address}.csv"
        df.to_csv(path, index=False)
        print(f"{short(address)}: {len(df)} hours, wrote {path.name}")
        for n, ok, det in res:
            mark = {True: "ok  ", False: "FAIL", None: "note"}[ok]
            print(f"  {mark} {n}: {det}")
        views.append(wallet_view(short(address), args.venue, df, res))
    out = HERE / f"wallet_returns_{args.venue}.html"
    out.write_text(page(views))
    print(f"wrote {out}")
    if skipped:
        raise SystemExit(1)


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wallet Returns</title>
<style>
:root{--surface:#fcfcfb;--panel:#f4f3f0;--line:#e3e2de;--ink:#0b0b0b;
--ink2:#52514e;--ink3:#8a8984;--s1:#2a78d6;--s2:#eb6834;--s3:#1f9e6e;
--bad:#c4321f}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
--surface:#1a1a19;--panel:#232321;--line:#383835;--ink:#fff;--ink2:#c3c2b7;
--ink3:#8a8984;--s1:#3987e5;--s2:#d95926;--s3:#2fb383;--bad:#ef6a55}}
:root[data-theme="dark"]{--surface:#1a1a19;--panel:#232321;--line:#383835;
--ink:#fff;--ink2:#c3c2b7;--ink3:#8a8984;--s1:#3987e5;--s2:#d95926;
--s3:#2fb383;--bad:#ef6a55}
*{box-sizing:border-box}
body{margin:0;background:var(--surface);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:1000px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:19px;margin:48px 0 4px}
h3{font-size:15px;margin:24px 0 6px;color:var(--ink2);font-weight:500}
.sub{color:var(--ink2);margin:0 0 16px}
.legend{display:flex;gap:18px;color:var(--ink2);font-size:13px;margin:4px 0}
.legend i{display:inline-block;width:14px;height:3px;border-radius:2px;
vertical-align:middle;margin-right:6px}
.chart{position:relative}
svg{display:block;width:100%;height:auto;overflow:visible}
.grid line{stroke:var(--line)}.axis text{fill:var(--ink3);font-size:11px}
.tip{position:absolute;pointer-events:none;background:var(--panel);
border:1px solid var(--line);border-radius:8px;padding:6px 10px;
font-size:13px;font-variant-numeric:tabular-nums;display:none;
white-space:nowrap}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px;
font-variant-numeric:tabular-nums}
td,th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);
vertical-align:top}th{color:var(--ink2);font-weight:500}
td:first-child,td:nth-child(2){white-space:nowrap}
.ok{color:var(--s3)}.fail{color:var(--bad);font-weight:600}
.note{color:var(--ink3)}
</style></head><body><main>
<h1>Wallet returns</h1>
<p class="sub">Return = total PnL (realized plus unrealized) over capital,
the most of the trader's own money ever in positions at once. Nothing is
compounded; deposits, withdrawals and idle cash do not count.</p>
<div id="wallets"></div>
</main>
<script>
const D=/*DATA*/null;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[c]);
const pct=v=>(v*100).toFixed(1)+"%",usd=v=>(v<0?"-$":"$")+Math.abs(v).toLocaleString("en-US",{maximumFractionDigits:0});
const day=t=>new Date(t*1000).toISOString().slice(0,10);
function chart(el,t,series,fmt){
  const W=960,H=280,L=64,R=12,T=10,B=26,n=t.length;
  const all=series.flatMap(s=>s.v).filter(v=>v!=null);
  const lo=Math.min(0,...all),hi=Math.max(0,...all),pad=(hi-lo)*.06||1;
  const y0=lo-pad,y1=hi+pad,x=i=>L+(W-L-R)*i/Math.max(n-1,1),y=v=>T+(H-T-B)*(1-(v-y0)/(y1-y0));
  let p=`<g class="grid">`,ax=`<g class="axis">`;
  for(let k=0;k<=4;k++){const v=y0+(y1-y0)*k/4;p+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}"/>`;
    ax+=`<text x="${L-6}" y="${y(v)+4}" text-anchor="end">${fmt(v)}</text>`}
  for(let k=0;k<=5;k++){const i=Math.round((n-1)*k/5);ax+=`<text x="${x(i)}" y="${H-6}" text-anchor="${k<5?"middle":"end"}">${day(t[i])}</text>`}
  p+=`</g>`+ax+`</g>`;
  for(const s of series){let d="",up=false;
    s.v.forEach((v,i)=>{if(v==null){up=false;return}d+=(up?"L":"M")+x(i).toFixed(1)+","+y(v).toFixed(1);up=true});
    p+=`<path d="${d}" fill="none" stroke="var(${s.c})" stroke-width="2" stroke-linejoin="round"/>`}
  p+=`<line class="cx" y1="${T}" y2="${H-B}" stroke="var(--ink3)" stroke-dasharray="3 3" visibility="hidden"/>`;
  el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img">${p}<rect x="${L}" y="0" width="${W-L-R}" height="${H}" fill="transparent"/></svg><div class="tip"></div>`;
  const svg=el.querySelector("svg"),tip=el.querySelector(".tip"),cx=el.querySelector(".cx");
  svg.addEventListener("pointermove",ev=>{
    const b=svg.getBoundingClientRect(),sx=(ev.clientX-b.left)*W/b.width;
    const i=Math.max(0,Math.min(n-1,Math.round((sx-L)/(W-L-R)*(n-1))));
    cx.setAttribute("x1",x(i));cx.setAttribute("x2",x(i));cx.setAttribute("visibility","visible");
    tip.innerHTML=`<b>${day(t[i])}</b><br>`+series.map(s=>`<span style="color:var(${s.c})">●</span> ${s.n} ${s.v[i]==null?"–":fmt(s.v[i])}`).join("<br>");
    tip.style.display="block";const px=x(i)*b.width/W;
    tip.style.left=(px>b.width*.6?px-tip.offsetWidth-12:px+12)+"px";tip.style.top="8px"});
  svg.addEventListener("pointerleave",()=>{tip.style.display="none";cx.setAttribute("visibility","hidden")});
}
const legend=s=>`<div class="legend">${s.map(([n,c])=>`<span><i style="background:var(${c})"></i>${n}</span>`).join("")}</div>`;
const mark=ok=>ok===true?`<span class="ok">ok</span>`:ok===false?`<span class="fail">fail</span>`:`<span class="note">note</span>`;
const root=document.getElementById("wallets");
D.forEach((w,k)=>{
  const s=document.createElement("section"),last=w.t.length-1;
  const head=last<0?"no trades":`${day(w.t[0])} to ${day(w.t[last])} · return ${w.ret[last]==null?"–":pct(w.ret[last])} · PnL ${usd(w.pnl[last])}`;
  s.innerHTML=`<h2>${esc(w.name)} · ${esc(w.venue)}</h2><p class="sub">${head}`+
    (w.estimated?" · margin estimated from the current leverage setting":"")+`</p>`+
    (w.t.length>1?`<h3>Return on peak own capital</h3><div class="chart" id="r${k}"></div>`+
    `<h3>PnL, realized and unrealized</h3>`+legend([["Realized","--s1"],["Unrealized","--s2"],["Total","--s3"]])+
    `<div class="chart" id="p${k}"></div>`:"")+
    `<h3>Checks</h3><div class="scroll"><table><tr><th>Check</th><th>Result</th><th>Detail</th></tr>`+
    w.checks.map(([n,ok,d])=>`<tr><td>${esc(n)}</td><td>${mark(ok)}</td><td>${esc(d)}</td></tr>`).join("")+`</table></div>`;
  root.appendChild(s);
  if(w.t.length>1){
    chart(document.getElementById("r"+k),w.t,[{n:"Return",v:w.ret,c:"--s1"}],pct);
    chart(document.getElementById("p"+k),w.t,[{n:"Realized",v:w.realized,c:"--s1"},{n:"Unrealized",v:w.unrealized,c:"--s2"},{n:"Total",v:w.pnl,c:"--s3"}],usd);
  }
});
</script></body></html>
"""

if __name__ == "__main__":
    main()

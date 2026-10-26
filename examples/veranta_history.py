"""Download a wallet's Veranta trade history into the cache mimic.py reads.

Avantis is now Veranta. Its trades since the v2 contracts (mid-August
2026) are not in the old history API (veranta_top5.HISTORY), which
mimic.py downloads from, and the v2 trade history,
api.veranta.xyz/v2/history/trade-history, has none before them. This
reads both: the old closes as they are, kept in
data/candles/wallet_<address>_v1.json, and from the v2 history each close (market close, stop loss, take profit, liquidation,
partial or full) as one row in the format of veranta_top5._record, to
data/candles/wallet_<address>.json, with the v2 rows themselves under
"v2". mimic.py then finds it there and does not download, and
wallet_returns.run_avantis builds the wallet's PnL from the v2 rows
book by book (wallet_returns.veranta_replay): each increase at its own
time and price.

Each row:
- opens at its position's MARKET_OPEN, and carries the collateral the
  close took out ("positionSize" is that margin, as in the old rows);
- openPrice is the position's average entry, as the API gives it;
- closingFee is the open fee plus the close fee; funding is the funding
  plus the borrow fee.

A position opens with MARKET_OPEN or LIMIT_OPEN. A close whose open is
not in the history opened before it starts: it is dated from the
history's first row, and it says how many and their net PnL.

A POSITION_INCREASE row has no link to its position, so in the close
rows its margin counts from the position's first open. The ledger does
not use the close rows for v2 trades; it uses "v2". It says how much of
the collateral came from increases.

It checks the history against the Veranta Copy trader page
(verantacopy.com/api/traders/<address>): the count of closes, the
realized PnL, and the 20 recent trades, and stops if one fails.

Costs the history leaves out come from the wallet's USDC transfers on
Base, read from saved BaseScan token-transfer pages (--pages DIR):
- each open and increase pays its collateral plus a fee; the fee is the
  USDC paid in its transaction less its collateral ("paidFee" on the v2
  row). The history has no fee for an increase at all.
- before the v2 history, the USDC paid in less the old closes' margin
  (and the margin of positions carried into v2) is the old opening fees:
  spread over those closes by margin.
- margin open now (Veranta Copy positions) below the margin the history
  leaves open was used up inside the positions: "drain", charged evenly
  from the oldest open position to now.
It checks that every payout from the Vault Manager in the v2 window is a
close's USDC sent, and every payment to Trading Storage is an open or
increase, and that the rebuilt total PnL at the end equals the USDC
received less paid, plus the open margin and its price PnL.

Run from the repository root (BaseScan pages with curl, 100 rows each):

    mkdir -p data/candles/basescan_<address>
    for p in $(seq 1 <pages>); do curl -s "https://basescan.org/tokentxns?a=<address>&ps=100&p=$p" \
        -o data/candles/basescan_<address>/p$p.html; sleep 0.5; done
    python3 examples/veranta_history.py --pages data/candles/basescan_<address> <address>
    python3 examples/mimic.py <address>

--force downloads again over a cached file; a download from the old API
is kept beside it as wallet_<address>_v1.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import re
from pathlib import Path

import numpy as np
import requests

import mimic as M

HISTORY = "https://api.veranta.xyz/v2/history/trade-history/{address}/{page}/20"
COPY = "https://verantacopy.com/api/traders/{address}"
PAIRS = "https://verantacopy.com/api/pairs"
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
STORAGE = "0x8a311d7048c35985aa31c131b9a13e03a5f7422d"  # Veranta Trading Storage: takes margin and fees
VAULT = "0xe9fb8c70af1b99f2baaa07aa926fcf3d237348dd"  # Veranta Vault Manager: pays closes


def get(url, tries=5):
    """The JSON at url; retries a 5xx answer, waiting 2, 4, 8 and 16 s."""
    for i in range(tries):
        r = requests.get(url, timeout=60)
        if r.status_code < 500 or i == tries - 1:
            r.raise_for_status()
            return r.json()
        time.sleep(2 ** (i + 1))


def history(address):
    """Every row of the wallet's trade history, newest first. Pages start at 1.

    A wallet trading while it downloads pushes rows onto later pages, so
    a row can come twice: kept once.
    """
    first = get(HISTORY.format(address=address, page=1))
    rows = list(first.get("trades") or [])
    for page in range(2, (first.get("pageCount") or 1) + 1):
        time.sleep(0.1)
        rows += get(HISTORY.format(address=address, page=page)).get("trades") or []
    seen, out = set(), []
    for r in rows:
        key = (r["txHash"], r["type"], r["orderId"], r["orderIdOfOpenTrade"], r["collateral"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    if len(out) < first.get("count", 0):
        sys.exit(f"got {len(out)} rows; the API counts {first['count']}")
    return out


def records(rows):
    """The closes as veranta_top5._record rows, the share of collateral added by
    increases, and the orphans: closes whose open is not in the history.

    An orphan opened before the v2 history starts, so it is dated from the
    history's first row: its realized PnL is right, its marks before that
    are missing.
    """
    opens = {r["orderId"]: r for r in rows if r["type"] in ("MARKET_OPEN", "LIMIT_OPEN")}
    start = min((r["timestamp"] for r in rows), default=0)
    out, orphans = [], []
    for r in rows:
        if r["open"]:
            continue
        o = opens.get(r["orderIdOfOpenTrade"])
        if o is None:
            orphans.append(r)
            o = {"timestamp": start}
        out.append({
            "time": r["time"], "gross": r["grossPnl"], "pairIndex": r["pairIndex"], "symbol": r["market"],
            "index": r["orderIdOfOpenTrade"], "buy": r["side"] == "long", "leverage": r["leverage"],
            "openPrice": r["openPrice"], "tp": r["tp"], "sl": r["sl"], "collateral": r["collateral"],
            "positionSize": r["collateral"], "closePrice": r["closePrice"], "usdcSent": r["usdcSentToTrader"],
            "openedAt": o["timestamp"], "closingFee": (r["openFee"] or 0) + (r["closeFee"] or 0),
            "funding": (r["funding"] or 0) + (r["borrowFee"] or 0), "type": r["type"],
            "partial": bool(r["isPartialClose"]),
        })
    added = sum(r["collateral"] for r in rows if r["type"] == "POSITION_INCREASE")
    opened = sum(r["collateral"] for r in opens.values())
    return out, added / (added + opened) if added + opened else 0.0, orphans


def old_closes(address):
    """The wallet's closes from the old history API, cached in wallet_<address>_v1.json."""
    path = M.CACHE / f"wallet_{address}_v1.json"
    if not path.exists():
        first = M.vt._get(M.vt.HISTORY.format(address=address, page=1))
        rows = list(first.get("portfolio") or [])
        for page in range(2, (first.get("pageCount") or 1) + 1):
            time.sleep(0.12)
            rows += M.vt._get(M.vt.HISTORY.format(address=address, page=page)).get("portfolio") or []
        symbols = M.vt._symbols() if rows else {}
        M.save_json(path, {"address": address, "count": len(rows), "trades": [M.vt._record(r, symbols) for r in rows]})
    return json.loads(path.read_text())["trades"]


def check(address, rows, recs, orphans=(), old=()):
    """Stops unless the history matches the Veranta Copy page.

    The count includes the old closes.
    """
    page = get(COPY.format(address=address.lower()))
    stats, fails = page["stats"], []
    n = len(recs) + len(old)
    if n != stats["n_closes"]:
        fails.append(f"{n} closes; the page counts {stats['n_closes']}")
    # The page's realized PnL is the v2 net PnL (gross less the close fee, funding and
    # borrow fee) plus, for each old close, the USDC sent back less its collateral.
    net = sum(r["netPnl"] for r in rows if not r["open"]) + sum(r["usdcSent"] - r["positionSize"] for r in old)
    if abs(net - stats["realized_pnl"]) > 0.01 + 1e-6 * abs(net):
        fails.append(f"net PnL {net:,.2f}; the page says {stats['realized_pnl']:,.2f}")
    keys = ("txHash", "type", "market", "side", "collateral", "openPrice", "closePrice", "grossPnl")
    recent = [tuple(t[k] for k in keys) for t in page["recent_trades"]]
    if recent != [tuple(r[k] for k in keys) for r in rows[:len(recent)]]:
        fails.append("the page's recent trades differ from the history's newest rows")
    print(f"checks against the Veranta Copy page: {n} closes, net PnL ${net:,.2f}, "
          f"{len(recent)} recent trades: {'pass' if not fails else 'FAIL'}")
    if fails:
        sys.exit("\n".join(fails))


def transfers(directory):
    """USDC transfers from saved BaseScan token-transfer pages: tx, t, dir, who, amt."""
    from bs4 import BeautifulSoup

    out = []
    pages = sorted(Path(directory).glob("p*.html"), key=lambda f: int(re.findall(r"\d+", f.stem)[0]))
    for f in pages:
        for tr in BeautifulSoup(f.read_text(), "html.parser").select("table tbody tr"):
            td = tr.find_all("td")
            if len(td) < 13:
                continue
            way = td[9].get_text(strip=True)
            mark = lambda c: [x.get("data-highlight-target") for x in c.find_all(attrs={"data-highlight-target": True})]
            token, who = mark(td[12]), mark(td[10 if way == "OUT" else 8])
            if not token or token[0].lower() != USDC:
                continue
            out.append({"tx": td[1].get_text(strip=True).lower(), "t": int(td[7].get_text(strip=True)), "dir": way,
                        "method": td[2].get_text(strip=True),
                        "who": (who or [""])[0].lower(),
                        "amt": float(td[11].get_text(" ", strip=True).split()[0].replace(",", ""))})
    return out


def costs(rows, old, orphans, flows, live):
    """Charges paidFee on v2 opens and increases and carryFee on orphans, adds
    the old fees to the old closes' closingFee, and returns the drain and the
    problems found. live: the Veranta Copy page's open positions.
    """
    start = min(r["timestamp"] for r in rows)
    paid = {}
    for f in flows:
        if f["who"] == STORAGE and f["dir"] == "OUT":
            paid[f["tx"]] = paid.get(f["tx"], 0.0) + f["amt"]
    got = {}
    for f in flows:
        if f["who"] == VAULT and f["dir"] == "IN":
            got[f["tx"]] = got.get(f["tx"], 0.0) + f["amt"]
    problems, ours, unpaid = [], set(), []
    for r in rows:
        tx = r["txHash"].lower()
        if r["open"]:
            ours.add(tx)
            if tx in paid:
                r["paidFee"] = max(0.0, paid[tx] - r["collateral"])
            else:
                unpaid.append(r)
        elif r["usdcSentToTrader"]:
            ours.add(tx)
            if abs(got.get(tx, 0.0) - r["usdcSentToTrader"]) > 0.01:
                problems.append(f"close {tx} sent {r['usdcSentToTrader']:.2f}, the chain {got.get(tx, 0.0):.2f}")
    # An open paid from an order placed earlier has no payment of its own: its fee
    # is the median rate of the paid ones. The rest of what was paid in and not
    # matched, less refunds ("unmatched"), has no trade in the history: the
    # check reports it, and the rebuilt PnL leaves it out.
    rate = np.median([r["paidFee"] / r["positionSize"] for r in rows if "paidFee" in r] or [0.0])
    for r in unpaid:
        r["paidFee"] = rate * r["positionSize"]
    # Margin taken out of an open position ("Update Margin") is the trader's money back, not PnL.
    margin_back = lambda f: f["dir"] == "IN" and f.get("method", "").startswith("Update Margin")
    late = [f for f in flows if f["who"] in (STORAGE, VAULT) and f["t"] >= start and f["tx"] not in ours
            and not margin_back(f)]
    sign = lambda f: 1.0 if f["dir"] == "OUT" else -1.0
    unmatched = sum(sign(f) * f["amt"] for f in late) - sum(r["collateral"] + r["paidFee"] for r in unpaid)
    if late or unpaid:
        print(f"{len(unpaid)} opens paid by earlier orders (fee at {rate:.4%}); {len(late)} transfers "
              f"match no row (${unmatched:,.2f} net paid in)")
    # Before v2: the USDC paid in, less the margin of what it opened, is the old opening fees.
    # Margin taken back before v2 left the old closes' payouts short of their PnL: it is credited
    # back with the fees, spread over them by margin. Margin taken back in v2 shrinks what is left open.
    # Before v2 the chain is the record: whatever the Vault Manager paid beyond the old
    # closes' payouts (margin taken back, or a payout the old history lacks) is credited.
    before = sum(f["amt"] for f in flows if f["who"] == STORAGE and f["dir"] == "OUT" and f["t"] < start)
    back = sum(f["amt"] for f in flows if f["who"] == VAULT and f["dir"] == "IN" and f["t"] < start)
    taken = max(0.0, back - sum(r["usdcSent"] for r in old))
    taken_v2 = sum(f["amt"] for f in flows if margin_back(f) and f["t"] >= start)
    margin = sum(r["positionSize"] for r in old) + sum(r["collateral"] for r in orphans)
    fee = max(0.0, before - margin) - taken
    for r in old:
        r["closingFee"] = (r.get("closingFee") or 0.0) + fee * r["positionSize"] / margin
    for r in orphans:
        r["carryFee"] = fee * r["collateral"] / margin
    carried = {id(r) for r in orphans}
    left = (sum(r["collateral"] for r in rows if r["open"])
            - sum(r["collateral"] for r in rows if not r["open"] and id(r) not in carried) - taken_v2)
    now = sum(p["collateral"] for p in live)
    drain = {"amount": max(0.0, left - now), "from": min((p["opened_at"] for p in live), default=None),
             "to": max(r["timestamp"] for r in rows)}
    print(f"fees on opens and increases ${sum(r['paidFee'] for r in rows if r['open']):,.2f}, "
          f"old opening fees ${fee + taken:,.2f} less ${taken:,.2f} paid back before v2 beyond the old closes, "
          f"margin used up in open positions ${drain['amount']:,.2f}")
    return drain, unmatched, problems


def onchain_total(flows, live, at, prices):
    """USDC received from Veranta less paid to it, plus the open margin and its
    price PnL at time at (prices: {market: Series})."""
    net = sum((1.0 if f["dir"] == "IN" else -1.0) * f["amt"] for f in flows if f["who"] in (STORAGE, VAULT))
    u = 0.0
    for p in live:
        px = M.W.price_at(prices.get(p["symbol"]), at, np.nan)[()]
        u += (1 if p["side"] == "long" else -1) * (px / p["open_price"] - 1) * p["notional"]
    return net + sum(p["collateral"] for p in live) + u


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("address")
    ap.add_argument("--force", action="store_true", help="download again over a cached file")
    ap.add_argument("--pages", help="a folder of saved BaseScan token-transfer pages: charges the costs")
    args = ap.parse_args()

    address = M.checksum(args.address)
    path = M.CACHE / f"wallet_{address}.json"
    if path.exists() and not args.force:
        sys.exit(f"{path} exists; --force downloads again")
    v1 = path.with_name(f"{path.stem}_v1.json")
    if path.exists() and not v1.exists() and json.loads(path.read_text()).get("venue") != "veranta":
        path.rename(v1)  # an old-API download: it is the old closes
        print(f"kept the old download as {v1.name}")
    rows = history(address)
    old = old_closes(address)
    recs, added, orphans = records(rows)
    check(address, rows, recs, orphans, old)
    if orphans:
        lost = sum(r["netPnl"] or 0 for r in orphans)
        print(f"{len(orphans)} closes opened before the v2 history (net PnL ${lost:,.2f}): dated from its first row")
    first, last = rows[-1]["time"][:10], rows[0]["time"][:10]
    print(f"{len(old)} old closes; {len(rows)} v2 rows from {first} to {last}; {len(recs)} closes kept; "
          f"{added:.0%} of the collateral came from position increases")
    out = {"address": address, "venue": "veranta"}
    if args.pages:
        flows = transfers(args.pages)
        live = get(COPY.format(address=address.lower()))["positions"]
        drain, unmatched, problems = costs(rows, old, orphans, flows, live)
        for p in problems:
            print(f"  problem: {p}")
        out["drain"], out["unmatched"] = drain, unmatched
    else:
        print("no --pages: opening and increase fees and used-up margin are not charged")
    recs = recs + old
    M.save_json(path, {**out, "count": len(recs), "trades": recs, "v2": rows})
    print(f"saved {path}")
    if args.pages:
        import wallet_returns as W

        df, _ = W.run_avantis(M, address)
        at = int(df["time"].iloc[-1])
        prices = {}
        for sym in {p["symbol"] for p in live}:
            pid = next(r["pairIndex"] for r in rows if r["market"] == sym)
            b = M.bars(pid, *M.day_bounds(at - 86400, at)[:2])
            prices[sym] = M.pd.Series(np.asarray(b[1].close, float), index=np.asarray(b[1].ts) + 3600)
        chain = onchain_total(flows, live, at, prices)
        pnl = float(df["pnl"].iloc[-1])
        gap = pnl - chain
        print(f"total PnL at {M.iso(at)}: rebuilt ${pnl:,.2f}, on-chain ${chain:,.2f}, gap ${gap:,.2f}: "
              f"{'pass' if abs(gap) <= 0.01 * max(1000.0, abs(chain)) else 'FAIL'}")


if __name__ == "__main__":
    main()

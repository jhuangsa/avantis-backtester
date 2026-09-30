"""Rebuild the five $10,000 wallets in examples/veranta_top5.html.

The page is not an engine score. It resizes five Avantis wallets so the
busiest moment of margin is $10,000, then adds each close after a fee of
1 bp in and 1 bp out. The closed trades are the history snapshot in
examples/veranta_trades/.

From the repository root:

    python3 examples/veranta_top5.py

That rebuilds the five books and exits with an error if they differ from
the page. To fetch the history again:

    python3 examples/veranta_top5.py --download
"""

import json
import math
import statistics
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRADES = HERE / "veranta_trades"
PAGE = HERE / "veranta_top5.html"

# 1 bp of notional on the open and 1 bp on the close.
ROUND_TRIP_FEE = 0.0002
ACCOUNT = 10_000

HISTORY = "https://api.avantisfi.com/v2/history/portfolio/history/{address}/{page}/20"
PAIRS = "https://verantacopy.com/api/pairs"

# The five wallets on the page, in the order the curves are discussed.
WALLETS = (
    {
        "id": "d49c1",
        "address": "0xd49C1dC7f12D56FD34c0148F47ECdCaaCBe9f78C",
        "blurb": "Short alts in a few sessions, and a stack of oil shorts.",
        "color": "#d38bdb",
    },
    {
        "id": "dd49e",
        "address": "0xDd49E41Ab17B74180a90A4f182eD9B56669F441b",
        "blurb": "Shorts after alt rallies, usually closed within hours.",
        "color": "#ef7b5b",
    },
    {
        "id": "eda73",
        "address": "0xEDa73C9fA6A90CEA2BCC5529E9E12C012e13655B",
        "blurb": "Multi-day shorts in ZORA, AVNT, and oil.",
        "color": "#f0b429",
    },
    {
        "id": "ffb7e",
        "address": "0xFFB7eF358cEe48DaFE15B63A625DFF9eA4E2268F",
        "blurb": "Shorts into fast alt spikes, leverage near one.",
        "color": "#3cbfa0",
    },
    {
        "id": "dce95",
        "address": "0xdcE95463E2f3F778BbeBea3bf01d80FCF5736E14",
        "blurb": "Long gold, added to winners, and one large stock loss.",
        "color": "#6cb6e0",
    },
)


def _get(url):
    request = urllib.request.Request(url, headers={"user-agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.load(response)


def _symbols():
    pairs = _get(PAIRS)
    if not isinstance(pairs, list):
        raise SystemExit("pair list")
    symbols = {}
    for pair in pairs:
        symbols[pair["index"]] = pair["symbol"]
    return symbols


def _record(item, symbols):
    event = item.get("event", {}).get("args", {})
    trade = event.get("t") or {}
    fee = event.get("_feeInfo") or event.get("feeInfo") or {}
    pair_index = trade.get("pairIndex")
    if pair_index not in symbols:
        raise SystemExit(f"pair {pair_index}")
    return {
        "time": item.get("timeStamp"),
        "gross": item.get("_grossPnl"),
        "pairIndex": pair_index,
        "symbol": symbols[pair_index],
        "index": trade.get("index"),
        "buy": trade.get("buy") if "buy" in trade else trade.get("isBuy"),
        "leverage": trade.get("leverage"),
        "openPrice": trade.get("openPrice"),
        "tp": trade.get("tp"),
        "sl": trade.get("sl"),
        "collateral": trade.get("initialPosToken"),
        "positionSize": event.get("positionSizeUSDC"),
        "closePrice": event.get("price"),
        "usdcSent": event.get("usdcSentToTrader"),
        "openedAt": trade.get("timestamp"),
        "closingFee": fee.get("closingFee"),
        "funding": fee.get("r"),
    }


def download(directory=TRADES):
    """Fetch each wallet's closed trades into directory/{id}.json."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    symbols = _symbols()
    for wallet in WALLETS:
        address = wallet["address"]
        first = _get(HISTORY.format(address=address, page=1))
        pages = first.get("pageCount") or 1
        rows = list(first.get("portfolio") or [])
        for page in range(2, pages + 1):
            time.sleep(0.12)
            body = _get(HISTORY.format(address=address, page=page))
            rows.extend(body.get("portfolio") or [])
        if len(rows) != first.get("count"):
            raise SystemExit(f"{wallet['id']} {len(rows)} closes, count {first.get('count')}")
        payload = {
            "address": address,
            "count": len(rows),
            "trades": [_record(item, symbols) for item in rows],
        }
        path = directory / f"{wallet['id']}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload) + "\n")
        temporary.replace(path)
        print(f"{wallet['id']} {len(rows)} closes")
    return directory


def _closes(path):
    document = json.loads(Path(path).read_text())
    rows = []
    for trade in document["trades"]:
        opened = datetime.fromtimestamp(trade["openedAt"], timezone.utc)
        close = datetime.fromisoformat(trade["time"].replace("Z", "+00:00"))
        collateral = float(trade.get("collateral") or 0)
        position = float(trade.get("positionSize") or 0)
        leverage = float(trade.get("leverage") or 0)
        gross = float(trade.get("gross") or 0)
        # Notional is the margin closed times the leverage.
        margin = position if position > 0 else collateral
        fee = margin * leverage * ROUND_TRIP_FEE
        rows.append(
            {
                "symbol": trade["symbol"],
                "side": "long" if trade["buy"] else "short",
                "opened": opened,
                "close": close,
                "collateral": collateral,
                "index": trade.get("index"),
                "open_price": round(float(trade.get("openPrice") or 0), 6),
                "net": gross - fee,
            }
        )
    return rows


def _book(wallet, rows):
    # Partial closes of one order share a single pile of margin. The order
    # is the symbol, the side, the index, the open price to 6 decimals, and
    # the open minute. Margin starts at the collateral on the earliest close.
    groups = defaultdict(list)
    for row in rows:
        opened = row["opened"].replace(second=0, microsecond=0)
        key = (row["symbol"], row["side"], row["index"], row["open_price"], opened)
        groups[key].append(row)

    margin_events = []
    closes = []
    for group in groups.values():
        group.sort(key=lambda row: row["close"])
        start = min(row["opened"] for row in group)
        previous = group[0]["collateral"]
        margin_events.append((start, previous))
        for index, row in enumerate(group):
            closes.append((row["close"], row["net"]))
            if index < len(group) - 1:
                nxt = group[index + 1]["collateral"]
                margin_events.append((row["close"], nxt - previous))
                previous = nxt
            else:
                margin_events.append((row["close"], -previous))

    margin_events.sort(key=lambda item: (item[0], 0 if item[1] > 0 else 1))
    margin = 0.0
    peak = 0.0
    for _, delta in margin_events:
        margin += delta
        if margin > peak:
            peak = margin
    if peak <= 0:
        raise SystemExit(f"{wallet['id']} peak")

    closes.sort()
    merged = []
    for moment, net in closes:
        if merged and merged[-1][0] == moment:
            merged[-1][1] += net
        else:
            merged.append([moment, net])

    scale = ACCOUNT / peak
    start = min(row["opened"] for row in rows)
    path = [(start, float(ACCOUNT))]
    drawdown = [(start, 0.0)]
    cumulative = 0.0
    equity = float(ACCOUNT)
    peak_equity = float(ACCOUNT)
    max_drawdown = 0.0
    drawdown_dollars = 0.0
    for moment, net in merged:
        cumulative += net
        equity = ACCOUNT + scale * cumulative
        path.append((moment, equity))
        if equity > peak_equity:
            peak_equity = equity
        drop = equity - peak_equity
        if drop < drawdown_dollars:
            drawdown_dollars = drop
            max_drawdown = drop / peak_equity
        drawdown.append((moment, equity / peak_equity - 1))

    day = merged[0][0].date()
    last_day = merged[-1][0].date()
    cursor = 0
    cumulative = 0.0
    previous = float(ACCOUNT)
    returns = []
    while day <= last_day:
        while cursor < len(merged) and merged[cursor][0].date() <= day:
            cumulative += merged[cursor][1]
            cursor += 1
        marked = ACCOUNT + scale * cumulative
        returns.append(marked / previous - 1)
        previous = marked
        day += timedelta(days=1)
    sharpe = statistics.mean(returns) / statistics.stdev(returns) * math.sqrt(365)

    def pack(series):
        return [[int(moment.timestamp()), round(value, 2)] for moment, value in series]

    return {
        "id": wallet["id"],
        "address": wallet["address"],
        "blurb": wallet["blurb"],
        "color": wallet["color"],
        "peak_margin": round(peak, 2),
        "positions": len(groups),
        "closes": len(rows),
        "their_net": round(sum(row["net"] for row in rows), 2),
        "end": round(path[-1][1], 2),
        "pnl": round(path[-1][1] - ACCOUNT, 2),
        "max_dd": max_drawdown,
        "dd_usd": round(drawdown_dollars, 2),
        "sharpe": sharpe,
        "days": len(returns),
        "first": int(start.timestamp()),
        "last": int(merged[-1][0].timestamp()),
        "equity": pack(path),
        "drawdown": pack(drawdown),
    }


def build(directory=TRADES):
    directory = Path(directory)
    books = []
    for wallet in WALLETS:
        path = directory / f"{wallet['id']}.json"
        books.append(_book(wallet, _closes(path)))
    books.sort(key=lambda book: -book["end"])
    return books


def published(path=PAGE):
    text = Path(path).read_text()
    marker = '<script id="book" type="application/json">'
    start = text.find(marker)
    end = text.find("</script>", start)
    if start < 0 or end < 0:
        raise SystemExit("book")
    return json.loads(text[start + len(marker) : end])


def drifts(built=None, page=None):
    """Lines describing where a rebuilt book differs from the page."""
    built = build() if built is None else built
    page = published() if page is None else page
    by_id = {book["id"]: book for book in built}
    lines = []
    if [book["id"] for book in built] != [book["id"] for book in page]:
        lines.append("order")
    scalars = (
        "address",
        "blurb",
        "color",
        "peak_margin",
        "positions",
        "closes",
        "their_net",
        "end",
        "pnl",
        "dd_usd",
        "days",
        "first",
        "last",
    )
    for book in page:
        other = by_id.get(book["id"])
        if other is None:
            lines.append(f"{book['id']} missing")
            continue
        for key in scalars:
            if other[key] != book[key]:
                lines.append(f"{book['id']} {key} {other[key]!r} {book[key]!r}")
        for key in ("sharpe", "max_dd"):
            if other[key] != book[key] and not math.isclose(
                other[key], book[key], rel_tol=0, abs_tol=1e-12
            ):
                lines.append(f"{book['id']} {key} {other[key]!r} {book[key]!r}")
        for key in ("equity", "drawdown"):
            if other[key] != book[key]:
                lines.append(f"{book['id']} {key} {len(other[key])} {len(book[key])}")
    return lines


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    directory = TRADES
    if "--download" in argv:
        directory = download(directory)
    books = build(directory)
    mismatch = drifts(books, published())
    for book in books:
        print(
            f"{book['id']}  {book['closes']} closes  "
            f"Sharpe {book['sharpe']:.2f}  "
            f"ends at {book['end']:.2f}  "
            f"profit {book['pnl']:.2f}  "
            f"max drawdown {book['max_dd'] * 100:.1f}%"
        )
    if mismatch:
        print("\n".join(mismatch))
        return 1
    print("The five books match examples/veranta_top5.html.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

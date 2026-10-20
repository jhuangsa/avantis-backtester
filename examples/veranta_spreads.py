"""Veranta's default spreads: read them from the risk API, write the C++ table.

    python3 examples/veranta_spreads.py query spreads.csv
    python3 examples/veranta_spreads.py rows spreads.csv

`query` asks the risk API for every pair's spread on a small order (about
$1,000), long and short, open and close, and writes a CSV. A closed market
answers 403 and gets no values. Do not commit the CSV.

`rows` rewrites the rows of cpp/include/avbt/spreads.hpp from a CSV: one row
per pair with all four values, the smallest of the four as its spread (the
rest is the live book), no cap (the API shows none), read on today's date
unless the CSV has a read_on column. Rebuild the C++ after it.
"""
import csv
import datetime
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

PAIRS = "https://socket-api-pub.avantisfi.com/socket-api/v1/data"
SPREAD = "https://prod-api.avantisfi.com/risk/v2/spread"
TRADER = "0x1234567890abcdef1234567890abcdef12345678"  # any address
HEADER = pathlib.Path(__file__).parent.parent / "cpp/include/avbt/spreads.hpp"
SIDES = {"long_open": (True, True), "short_open": (False, True),
         "long_close": (True, False), "short_close": (False, False)}


def post(url, body):
    """(HTTP code, JSON body or None)."""
    req = urllib.request.Request(url, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, None


def spread(index, coins, is_long, is_open):
    """(HTTP code, half-spread as a fraction of price, or "")."""
    code, body = post(SPREAD, {
        "pairIndex": index, "trader": TRADER,
        "coinSize10": str(round(coins * 1e10)), "isLong": is_long,
        "isOpen": is_open, "orderType": 0, "wantedPrice10": "0",
        "leverage10": str(round(10 * 1e10))})
    time.sleep(0.3)  # be polite
    if body and "spreadPctWithoutFlow10" in body:
        return code, int(body["spreadPctWithoutFlow10"]) / 1e12  # percent * 1e10
    return code, ""


def price(info):
    """A price from open interest over coins, or None."""
    for side in ("long", "short"):
        coins, usd = info["coinOI"][side], info["openInterest"][side]
        if coins > 1e-6 and usd > 0:
            return usd / coins
    return None


def query(path):
    with urllib.request.urlopen(PAIRS, timeout=30) as r:
        pairs = json.load(r)["data"]["pairInfos"]
    today = datetime.date.today().isoformat()
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["index", "pair", *SIDES, "http_codes", "read_on"])
        for k in sorted(pairs, key=int):
            info = pairs[k]
            p = price(info)
            coins = 1000 / p if p else 1.0
            got = [spread(int(k), coins, *SIDES[s]) for s in SIDES]
            codes = "/".join(str(c) for c, _ in got)
            w.writerow([k, f"{info['from']}/{info['to']}", *[v for _, v in got], codes, today])
            print(k, info["from"], codes, flush=True)


def rows(path):
    today = datetime.date.today().isoformat()
    out = []
    for r in csv.DictReader(open(path)):
        if r["http_codes"].split("/")[:4] != ["201"] * 4:
            continue  # closed or no config: no row
        s = min(float(r[c]) for c in SIDES)
        read_on = r.get("read_on") or today
        out.append(f'    {{"{r["pair"]}", {s!r}, no_cap, "{read_on}"}},')
    text = HEADER.read_text()
    head, rest = text.split("    // BEGIN ROWS\n")
    _, tail = rest.split("    // END ROWS\n")
    HEADER.write_text(head + "    // BEGIN ROWS\n" + "\n".join(out) + "\n    // END ROWS\n" + tail)
    print(f"{len(out)} rows written to {HEADER}")


if __name__ == "__main__":
    mode, path = sys.argv[1], sys.argv[2]
    {"query": query, "rows": rows}[mode](path)

"""Snapback on hourly BTC-USD or ETH-USD, 2022 through 2023.

Once an hour, look at the last day and the last hour. Day up and hour down
buys. Day down and hour up sells. Any other hour is flat, which closes an
open trade. The fill is the next hour's open.

From the repository root:

    python3 examples/btc_snapback.py
    python3 examples/btc_snapback.py ETH-USD

Prints the score and writes an HTML chart beside this file.
"""

import json
import math
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import Bar, Hypothesis, backtest

START = datetime(2022, 1, 1, tzinfo=timezone.utc)
END = datetime(2024, 1, 1, tzinfo=timezone.utc)
PRODUCT = "BTC-USD"
BAR_SIZE = "1h"
DAY = 24
CHUNK = timedelta(hours=250)
HERE = Path(__file__).resolve().parent
CHART = HERE / "btc_snapback_2022_2023.html"
CACHE = Path.home() / ".cache" / "avantis-backtester" / "btc-usd-1h-2022-2023.json"
SOURCE = "https://api.exchange.coinbase.com/products/BTC-USD/candles"


def use_product(name):
    """Point the download and the chart at BTC-USD or ETH-USD."""
    global PRODUCT, CHART, CACHE, SOURCE
    name = name.upper()
    if name in ("BTC", "ETH"):
        name = f"{name}-USD"
    if name not in ("BTC-USD", "ETH-USD"):
        raise SystemExit("pass BTC-USD or ETH-USD")
    PRODUCT = name
    stem = name.split("-")[0].lower()
    CHART = HERE / f"{stem}_snapback_2022_2023.html"
    CACHE = Path.home() / ".cache" / "avantis-backtester" / f"{stem}-usd-1h-2022-2023.json"
    SOURCE = f"https://api.exchange.coinbase.com/products/{name}/candles"


def snapback(history):
    """Long when the day rose and the hour fell. Short for the mirror."""
    if len(history) < DAY + 1:
        return "flat"
    last = history[-1].close
    hour_ago = history[-2].close
    day_ago = history[-1 - DAY].close
    day_up = last > day_ago
    hour_down = last < hour_ago
    day_down = last < day_ago
    hour_up = last > hour_ago
    if day_up and hour_down:
        return "long"
    if day_down and hour_up:
        return "short"
    return "flat"


def iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_bytes(url):
    request = urllib.request.Request(url, headers={"User-Agent": "avantis-backtester-example"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except urllib.error.URLError:
        # This machine's Python trust store rejects the host. curl uses the system store.
        proc = subprocess.run(
            ["curl", "-sS", "-m", "60", "-A", "avantis-backtester-example", "-w", "\n%{http_code}", url],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise SystemExit(proc.stderr.strip() or "curl failed")
        body, _, code = proc.stdout.rpartition("\n")
        if code.strip() != "200":
            raise SystemExit(f"HTTP {code.strip()}: {body[:240]}")
        return body.encode()


def download():
    if CACHE.exists():
        cached = json.loads(CACHE.read_text())
        if cached.get("start") == iso(START) and cached.get("end") == iso(END):
            return cached["candles"], "Coinbase exchange, read from the local cache"
    rows = {}
    moment = START
    calls = 0
    while moment < END:
        stop = min(moment + CHUNK, END)
        url = f"{SOURCE}?granularity=3600&start={iso(moment)}&end={iso(stop)}"
        payload = json.loads(fetch_bytes(url))
        if not isinstance(payload, list):
            raise SystemExit(f"unexpected Coinbase payload: {str(payload)[:240]}")
        for candle in payload:
            stamp = int(candle[0])
            rows[stamp] = candle
        calls += 1
        moment = stop
        time.sleep(0.05)
    candles = [rows[stamp] for stamp in sorted(rows)]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"start": iso(START), "end": iso(END), "candles": candles}))
    return candles, f"Coinbase exchange, {calls} requests"


def load():
    candles, origin = download()
    times = []
    bars = []
    for candle in candles:
        stamp = int(candle[0])
        moment = datetime.fromtimestamp(stamp, timezone.utc)
        if moment < START or moment >= END:
            continue
        low, high, open_, close, volume = (float(x) for x in candle[1:6])
        if not all(math.isfinite(x) for x in (open_, high, low, close)):
            continue
        times.append(moment)
        bars.append(Bar(open=open_, high=high, low=low, close=close, volume=volume))
    gaps = [
        (left, right)
        for left, right in zip(times, times[1:])
        if right - left != timedelta(hours=1)
    ]
    return times, bars, origin, gaps


def trade_return(trade):
    if trade.side == "long":
        return trade.exit_price / trade.entry_price - 1
    return (trade.entry_price - trade.exit_price) / trade.entry_price


def equity_curve(bars, trades):
    """Mark the open trade at each close. Fee is zero, matching the backtest."""
    stake = 1.0
    curve = []
    active = None
    index = 0
    for i, bar in enumerate(bars):
        if active is not None and active.exit_bar == i and active.cause == "action":
            stake *= trade_return(active) + 1
            active = None
        if index < len(trades) and trades[index].entry_bar == i:
            active = trades[index]
            index += 1
        if active is None:
            curve.append(stake)
        elif active.side == "long":
            curve.append(stake * bar.close / active.entry_price)
        else:
            curve.append(stake * (1 + (active.entry_price - bar.close) / active.entry_price))
    return curve


def drawdown(curve):
    peak = curve[0]
    worst = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def polyline(values, width, height, left, top):
    low = min(values)
    high = max(values)
    span = high - low or 1.0
    step = (width - 1) / (len(values) - 1)
    points = []
    for i, value in enumerate(values):
        x = left + i * step
        y = top + (high - value) / span * (height - 1)
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points), low, high


def chart_svg(title, values, times, marks, width=920, height=280):
    left, right, top, bottom = 56, 16, 28, 32
    inner_w = width - left - right
    inner_h = height - top - bottom
    points, low, high = polyline(values, inner_w, inner_h, left, top)
    step = (inner_w - 1) / (len(values) - 1)
    ticks = []
    for year in (2022, 2023):
        moment = datetime(year, 1, 1, tzinfo=timezone.utc)
        for i, stamp in enumerate(times):
            if stamp >= moment:
                x = left + i * step
                ticks.append(
                    f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + inner_h}" stroke="#e4ddd0"/>'
                    f'<text x="{x + 4:.1f}" y="{height - 10}" fill="#6b645c" font-size="12">{year}</text>'
                )
                break
    dots = []
    for i, side in marks:
        x = left + i * step
        y = top + (high - values[i]) / (high - low or 1) * (inner_h - 1)
        color = "#1b7f4e" if side == "long" else "#a33b32"
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.2" fill="{color}"/>')
    return f'''<section>
<h2>{title}</h2>
<svg viewBox="0 0 {width} {height}" role="img">
  <rect width="{width}" height="{height}" fill="#f7f4ee"/>
  {''.join(ticks)}
  <polyline fill="none" stroke="#1f3a5f" stroke-width="1.4" points="{points}"/>
  {''.join(dots)}
  <text x="{left}" y="18" fill="#6b645c" font-size="12">{high:,.2f}</text>
  <text x="{left}" y="{top + inner_h}" fill="#6b645c" font-size="12">{low:,.2f}</text>
</svg>
</section>'''


def write_chart(times, bars, trades, curve, summary):
    closes = [bar.close for bar in bars]
    marks = [(trade.entry_bar, trade.side) for trade in trades]
    price = chart_svg(f"{summary['product']} close, entries marked", closes, times, marks)
    stake = chart_svg("Compounded stake, marked at each close", curve, times, [])
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{summary["product"]} snapback 2022–2023</title>
<style>
  body {{ margin: 32px auto; max-width: 960px; color: #1c1915; background: #f7f4ee;
         font: 16px/1.45 Georgia, serif; }}
  h1 {{ font-size: 28px; font-weight: 500; margin-bottom: 0; }}
  p {{ color: #3d3832; }}
  h2 {{ font-size: 16px; font-weight: 500; margin: 28px 0 8px; }}
  dl {{ display: grid; grid-template-columns: 10rem 1fr; gap: 4px 16px; }}
  dt {{ color: #6b645c; }}
  dd {{ margin: 0; }}
  .long {{ color: #1b7f4e; }}
  .short {{ color: #a33b32; }}
</style>
</head>
<body>
<h1>Hourly snapback, {summary["product"]}</h1>
<p>Once an hour: the last day up and the last hour down buys; the mirror sells; otherwise flat. A flat hour closes an open trade at the next open. {summary["origin"]}.</p>
<dl>
  <dt>Window</dt><dd>2022-01-01 through 2023-12-31, UTC</dd>
  <dt>Bars</dt><dd>{summary["bars"]:,} hourly, {summary["gaps"]} gap{"s" if summary["gaps"] != 1 else ""}</dd>
  <dt>Trades</dt><dd>{summary["trades"]:,} ({summary["longs"]:,} long, {summary["shorts"]:,} short)</dd>
  <dt>Wins</dt><dd>{summary["wins"]:,} of {summary["closed"]:,} closed</dd>
  <dt>Ending stake</dt><dd>{summary["stake"]:.4f}</dd>
  <dt>Return</dt><dd>{summary["ret"]:+.2%}</dd>
  <dt>Max drawdown</dt><dd>{summary["dd"]:.2%}</dd>
  <dt>Mean trade</dt><dd>{summary["mean"]:+.3%}</dd>
</dl>
{price}
<p><span class="long">Green</span> marks a long entry. <span class="short">Red</span> marks a short entry.</p>
{stake}
</body>
</html>'''
    CHART.write_text(html)


def main():
    use_product(sys.argv[1] if len(sys.argv) > 1 else "BTC-USD")
    times, bars, origin, gaps = load()
    if len(bars) < DAY + 2:
        raise SystemExit(f"not enough bars ({len(bars)}) from {origin}")
    result = backtest(Hypothesis("snapback", action=snapback), bars, fee=0.0, bar_size=BAR_SIZE)
    curve = equity_curve(bars, result.trades)
    if not math.isclose(curve[-1], result.ending_stake, rel_tol=1e-9, abs_tol=1e-9):
        raise SystemExit(f"chart stake {curve[-1]} != backtest {result.ending_stake}")
    closed = [trade for trade in result.trades if trade.cause == "action"]
    wins = sum(1 for trade in closed if trade_return(trade) > 0)
    returns = [trade_return(trade) for trade in result.trades]
    mean = sum(returns) / len(returns) if returns else 0.0
    longs = sum(1 for trade in result.trades if trade.side == "long")
    summary = {
        "product": PRODUCT,
        "origin": origin,
        "bars": len(bars),
        "gaps": len(gaps),
        "trades": len(result.trades),
        "longs": longs,
        "shorts": len(result.trades) - longs,
        "wins": wins,
        "closed": len(closed),
        "stake": result.ending_stake,
        "ret": result.ending_stake - 1,
        "dd": drawdown(curve),
        "mean": mean,
    }
    print(f"Hourly snapback on {PRODUCT}, 2022-01-01 through 2023-12-31 UTC")
    print("Day up and hour down: long. Day down and hour up: short. Otherwise flat.")
    print(f"source: {origin}")
    print(f"bars: {len(bars)}  gaps: {len(gaps)}  bar size: {result.bar_size}")
    for left, right in gaps:
        print(f"  missing hours after {left:%Y-%m-%d %H:%M} UTC, next bar {right:%Y-%m-%d %H:%M} UTC")
    print(f"trades: {len(result.trades)}  long: {longs}  short: {len(result.trades) - longs}")
    print(f"closed wins: {wins} of {len(closed)}")
    print(f"ending stake: {result.ending_stake:.6f}")
    print(f"return: {(result.ending_stake - 1):+.2%}")
    print(f"max drawdown: {summary['dd']:.2%}")
    print(f"mean trade: {mean:+.3%}")
    boundary = datetime(2023, 1, 1, tzinfo=timezone.utc)
    for i, moment in enumerate(times):
        if moment >= boundary:
            print(f"stake at 2023-01-01: {curve[i]:.6f}")
            break
    write_chart(times, bars, result.trades, curve, summary)
    print(f"chart: {CHART}")
    if sys.platform == "darwin":
        subprocess.run(["open", str(CHART)], check=False)


if __name__ == "__main__":
    main()

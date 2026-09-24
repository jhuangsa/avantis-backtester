"""Trace channel length and stop distance, and write the curves.

A completed trade pays 2 basis points. From the repository root:

    python3 examples/breakout30_curves.py btc.csv eth.csv -o curves.html
"""

import math
import multiprocessing as mp
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest import backtest
from breakout30 import BAR_SIZE, HOLD_DAYS, breakout
from breakout30_report import INK, MUTED, PAPER, SHEET, load, trade_return, two_sided_p

CHANNELS = tuple(range(2, 61))
STOPS = tuple(i / 4 for i in range(2, 25))
ROUND_TRIP_BPS = 2.0
FEE = ROUND_TRIP_BPS / 2.0 / 1e4
CHANNEL_LINES = (1.0, 1.5, 2.0, 3.0, 4.0, 5.0)
STOP_LINES = (10, 20, 40, 55, 70, 90)
LINE_COLOR = {
    1.0: "#9bb8b0",
    1.5: "#5ea093",
    2.0: "#0e7c66",
    3.0: "#1a6a73",
    4.0: "#1f4e79",
    5.0: "#3d3a55",
    10: "#9bb8b0",
    20: "#0e7c66",
    40: "#1a6a73",
    55: "#1f4e79",
    70: "#3d3a55",
    90: "#6b4f3a",
}

_BOOKS = {}


def _init(books):
    global _BOOKS
    _BOOKS = books


def _atr_cell(job):
    name, channel, window = job
    _days, bars, _skipped = _BOOKS[name]
    result = backtest(
        breakout(channel, atr_window=window), bars, fee=FEE, bar_size=BAR_SIZE
    )
    returns = [trade_return(trade, FEE) for trade in result.trades]
    mean = sum(returns) / len(returns) if returns else 0.0
    p_value = 1.0
    if len(returns) >= 2:
        var = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
        std = math.sqrt(var)
        if std > 0:
            p_value = two_sided_p(mean / (std / math.sqrt(len(returns))), len(returns) - 1)
    return {
        "name": name,
        "channel": channel,
        "window": window,
        "n": len(returns),
        "mean": mean,
        "ending": result.ending_stake,
        "p": p_value,
    }


def _cell(job):
    name, channel, stop = job
    _days, bars, _skipped = _BOOKS[name]
    result = backtest(breakout(channel, stop), bars, fee=FEE, bar_size=BAR_SIZE)
    returns = [trade_return(trade, FEE) for trade in result.trades]
    mean = sum(returns) / len(returns) if returns else 0.0
    p_value = 1.0
    if len(returns) >= 2:
        var = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
        std = math.sqrt(var)
        if std > 0:
            p_value = two_sided_p(mean / (std / math.sqrt(len(returns))), len(returns) - 1)
    return {
        "name": name,
        "channel": channel,
        "stop": stop,
        "n": len(returns),
        "mean": mean,
        "ending": result.ending_stake,
        "p": p_value,
    }


def _series(rows, key, values):
    found = []
    for value in values:
        points = [row for row in rows if row[key] == value]
        points.sort(key=lambda row: row["channel" if key == "stop" else "stop"])
        if points:
            found.append((value, points))
    return found


def _chart(title, rows, x_key, line_key, line_values, y_key, y_include, y_format):
    width, height = 920, 380
    left, right, top, bottom = 64, 132, 36, 40
    plot_w = width - left - right
    plot_h = height - top - bottom
    series = _series(rows, line_key, line_values)
    xs = [row[x_key] for _value, points in series for row in points]
    ys = [row[y_key] for _value, points in series for row in points]
    if not xs:
        return ""
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(min(ys), min(y_include)), max(max(ys), max(y_include))
    if x1 == x0:
        x1 += 1
    if y1 == y0:
        y1 += 1
    pad = (y1 - y0) * 0.08
    y0 -= pad
    y1 += pad

    def xpos(value):
        return left + (value - x0) / (x1 - x0) * plot_w

    def ypos(value):
        return top + (y1 - value) / (y1 - y0) * plot_h

    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img">',
        f'<rect width="{width}" height="{height}" fill="{SHEET}"/>',
        f'<text x="{left}" y="22" fill="{INK}" font-size="16">{title}</text>',
    ]
    for guide in y_include:
        y = ypos(guide)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="#8f3d45" stroke-width="1"/>'
        )
    ticks = [10, 20, 40, 55, 80] if x_key == "channel" else [1, 2, 3, 4, 5, 6]
    for tick in ticks:
        if x0 <= tick <= x1:
            x = xpos(tick)
            parts.append(
                f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + plot_h}" stroke="#d3dee3"/>'
                f'<text x="{x:.1f}" y="{height - 14}" text-anchor="middle" fill="{MUTED}" font-size="12">{tick:g}</text>'
            )
    parts.append(
        f'<text x="8" y="{top + 12}" fill="{MUTED}" font-size="12">{y_format(y1)}</text>'
        f'<text x="8" y="{top + plot_h}" fill="{MUTED}" font-size="12">{y_format(y0)}</text>'
    )
    for value, points in series:
        color = LINE_COLOR[value]
        coords = " ".join(f"{xpos(row[x_key]):.1f},{ypos(row[y_key]):.1f}" for row in points)
        parts.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="1.8" points="{coords}"/>'
        )
        for row in points:
            if (x_key == "channel" and row["channel"] in (10, 20, 55)) or (
                x_key == "stop" and row["stop"] in (1, 2, 4)
            ):
                parts.append(
                    f'<circle cx="{xpos(row[x_key]):.1f}" cy="{ypos(row[y_key]):.1f}" r="2.4" fill="{color}">'
                    f"<title>{row['channel']} days, {row['stop']:g} stop, {y_format(row[y_key])}, {row['n']} trades</title></circle>"
                )
        label = f"{value:g} stop" if line_key == "stop" else f"{value:g} days"
        parts.append(
            f'<text x="{width - 12}" y="{top + 18 + list(line_values).index(value) * 18}" text-anchor="end" fill="{color}" font-size="13">{label}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def _stake(value):
    return f"{value:.2f}"


def _bps(value):
    return f"{value * 1e4:+.0f}"


def render(rows):
    btc = [row for row in rows if row["name"] == "Bitcoin"]
    eth = [row for row in rows if row["name"] == "Ethereum"]
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Breakout-30 curves, 2 basis point round trip</title>
<style>
  body {{ margin: 0; background: {PAPER}; color: {INK};
         font: 18px/1.5 "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif; }}
  main {{ max-width: 980px; margin: 0 auto; padding: 48px 24px 72px; }}
  h1 {{ font-weight: 500; font-size: 34px; line-height: 1.15; letter-spacing: -0.02em; margin: 0 0 16px; }}
  h2 {{ font-weight: 500; font-size: 22px; margin: 32px 0 8px; }}
  p {{ max-width: 68ch; margin: 0 0 12px; }}
  .note {{ color: {MUTED}; font-size: 16px; }}
  svg {{ width: 100%; height: auto; display: block; background: {SHEET}; margin: 8px 0 18px; }}
  svg text {{ font-family: "Avenir Next", Avenir, "Trebuchet MS", sans-serif; }}
</style>
</head>
<body>
<main>
  <h1>How the result moves</h1>
  <p>Each line is one setting of the other knob, scored at every point along the axis. Channel length runs from {CHANNELS[0]} to {CHANNELS[-1]} days. The stop runs from {STOPS[0]:g} to {STOPS[-1]:g} average-true-range units. A completed trade pays 2 basis points. The hold stays at {HOLD_DAYS} daily bars. The red line is a stake of 1, or an average trade of zero.</p>
  <p class="note">Hover a dot for that cell. These curves report every point. They do not select a setting.</p>
  <h2>Bitcoin, as the channel gets longer</h2>
  {_chart("Ending stake", btc, "channel", "stop", CHANNEL_LINES, "ending", (1.0,), _stake)}
  {_chart("Average trade, basis points", btc, "channel", "stop", CHANNEL_LINES, "mean", (0.0,), _bps)}
  <h2>Bitcoin, as the stop gets wider</h2>
  {_chart("Ending stake", btc, "stop", "channel", STOP_LINES, "ending", (1.0,), _stake)}
  {_chart("Average trade, basis points", btc, "stop", "channel", STOP_LINES, "mean", (0.0,), _bps)}
  <h2>Ethereum, as the channel gets longer</h2>
  {_chart("Ending stake", eth, "channel", "stop", CHANNEL_LINES, "ending", (1.0,), _stake)}
  {_chart("Average trade, basis points", eth, "channel", "stop", CHANNEL_LINES, "mean", (0.0,), _bps)}
  <h2>Ethereum, as the stop gets wider</h2>
  {_chart("Ending stake", eth, "stop", "channel", STOP_LINES, "ending", (1.0,), _stake)}
  {_chart("Average trade, basis points", eth, "stop", "channel", STOP_LINES, "mean", (0.0,), _bps)}
</main>
</body>
</html>
'''


def main():
    if len(sys.argv) != 5 or sys.argv[3] != "-o":
        raise SystemExit(
            "usage: python3 examples/breakout30_curves.py btc.csv eth.csv -o curves.html"
        )
    books = {}
    for label, path in (("Bitcoin", sys.argv[1]), ("Ethereum", sys.argv[2])):
        books[label] = load(path)
    jobs = [
        (label, channel, stop)
        for label in books
        for channel in CHANNELS
        for stop in STOPS
    ]
    started = time.perf_counter()
    context = mp.get_context("forkserver")
    workers = min(2, len(jobs))
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=context,
        initializer=_init,
        initargs=(books,),
    ) as pool:
        rows = list(pool.map(_cell, jobs, chunksize=16))
    elapsed = time.perf_counter() - started
    Path(sys.argv[4]).write_text(render(rows), encoding="utf-8")
    print(f"{len(rows)} cells in {elapsed:.1f}s")
    for name in ("Bitcoin", "Ethereum"):
        subset = [row for row in rows if row["name"] == name]
        best = max(subset, key=lambda row: row["ending"])
        worst = min(subset, key=lambda row: row["ending"])
        print(
            f"{name} stake {worst['ending']:.2f} at {worst['channel']}d/{worst['stop']:g}"
            f" to {best['ending']:.2f} at {best['channel']}d/{best['stop']:g}"
        )


if __name__ == "__main__":
    main()

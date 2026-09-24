"""Score a 4 by 4 grid of channel length and stop distance.

Sixteen cells. Every cell is reported. The round trip is 2 basis points,
1 on the entry and 1 on the exit. From the repository root:

    python3 examples/breakout30_grid.py btc.csv eth.csv -o grid.html
"""

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from breakout30 import HOLD_DAYS
from breakout30_report import (
    INK,
    MUTED,
    PAPER,
    SHEET,
    _fmt_p,
    load,
    summarize,
)

CHANNELS = (10, 20, 40, 55)
STOPS = (1.0, 2.0, 3.0, 4.0)
ROUND_TRIP_BPS = 2.0
FEE = ROUND_TRIP_BPS / 2.0 / 1e4


def _cell(job):
    name, channel, stop, days, bars, skipped = job
    row = summarize(name, channel, days, bars, skipped, FEE, stop)
    keep = (
        "name",
        "channel",
        "n",
        "mean",
        "t",
        "p",
        "lo",
        "hi",
        "ending",
        "drawdown",
        "win_rate",
        "stops",
        "caps",
    )
    return {key: row[key] for key in keep} | {"stop": stop}


def _bps(value):
    return f"{value * 1e4:+.0f}"


def _color(value, low, high):
    if high == low:
        return SHEET
    if value >= 0:
        scale = value / max(high, 1e-9)
        amount = min(max(scale, 0.0), 1.0)
        red = round(247 - amount * (247 - 14))
        green = round(250 - amount * (250 - 124))
        blue = round(251 - amount * (251 - 102))
    else:
        scale = value / min(low, -1e-9)
        amount = min(max(scale, 0.0), 1.0)
        red = round(247 - amount * (247 - 143))
        green = round(250 - amount * (250 - 61))
        blue = round(251 - amount * (251 - 69))
    return f"#{red:02x}{green:02x}{blue:02x}"


def _heatmap(title, rows):
    means = [row["mean"] for row in rows]
    low, high = min(means), max(means)
    by_key = {(row["channel"], row["stop"]): row for row in rows}
    width, height = 920, 420
    left, top = 88, 56
    cell_w, cell_h = 190, 78
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img">',
        f'<rect width="{width}" height="{height}" fill="{SHEET}"/>',
        f'<text x="{left}" y="28" fill="{INK}" font-size="16">{title}</text>',
    ]
    for col, stop in enumerate(STOPS):
        x = left + col * cell_w
        parts.append(
            f'<text x="{x + cell_w / 2:.0f}" y="48" text-anchor="middle" fill="{MUTED}" font-size="13">{stop:g} ATR stop</text>'
        )
    for row_i, channel in enumerate(CHANNELS):
        y = top + row_i * cell_h
        parts.append(
            f'<text x="{left - 12}" y="{y + cell_h / 2 + 4:.0f}" text-anchor="end" fill="{MUTED}" font-size="13">{channel} days</text>'
        )
        for col, stop in enumerate(STOPS):
            cell = by_key[(channel, stop)]
            x = left + col * cell_w
            fill = _color(cell["mean"], low, high)
            mark = ""
            if channel == 20 and stop == 2:
                mark = f'<rect x="{x + 4}" y="{y + 4}" width="{cell_w - 14}" height="{cell_h - 10}" fill="none" stroke="{INK}" stroke-width="1.5"/>'
            parts.append(
                f'<rect x="{x + 6}" y="{y + 6}" width="{cell_w - 18}" height="{cell_h - 14}" fill="{fill}"/>'
                f"{mark}"
                f'<text x="{x + cell_w / 2:.0f}" y="{y + 32}" text-anchor="middle" fill="{INK}" font-size="16">{_bps(cell["mean"])} bps</text>'
                f'<text x="{x + cell_w / 2:.0f}" y="{y + 52}" text-anchor="middle" fill="{INK}" font-size="12">p {_fmt_p(cell["p"])} · stake {cell["ending"]:.2f}</text>'
            )
    parts.append("</svg>")
    return "\n".join(parts)


def _table(rows):
    ordered = sorted(rows, key=lambda row: (row["name"], row["channel"], row["stop"]))
    body = []
    for row in ordered:
        body.append(
            "<tr>"
            f"<td>{row['name']}</td><td>{row['channel']}</td><td>{row['stop']:g}</td>"
            f"<td>{row['n']}</td><td>{_bps(row['mean'])}</td>"
            f"<td>{_bps(row['lo'])} to {_bps(row['hi'])}</td>"
            f"<td>{row['t']:.2f}</td><td>{_fmt_p(row['p'])}</td>"
            f"<td>{row['ending']:.3f}</td><td>{row['drawdown'] * 100:+.1f}%</td>"
            f"<td>{row['stops']}</td><td>{row['caps']}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>Market</th><th>Channel</th><th>Stop</th><th>Trades</th>"
        "<th>Mean, bps</th><th>95% interval, bps</th><th>t</th><th>p</th>"
        "<th>Ending stake</th><th>Worst drop</th><th>Stops</th><th>30-day exits</th>"
        f"</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def render(rows):
    btc = [row for row in rows if row["name"] == "Bitcoin"]
    eth = [row for row in rows if row["name"] == "Ethereum"]
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Breakout-30 grid, 2 basis point round trip</title>
<style>
  body {{ margin: 0; background: {PAPER}; color: {INK};
         font: 18px/1.5 "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif; }}
  main {{ max-width: 980px; margin: 0 auto; padding: 48px 24px 72px; }}
  h1 {{ font-weight: 500; font-size: 34px; line-height: 1.15; letter-spacing: -0.02em; margin: 0 0 16px; }}
  h2 {{ font-weight: 500; font-size: 22px; margin: 36px 0 8px; }}
  p {{ max-width: 68ch; margin: 0 0 12px; }}
  .note {{ color: {MUTED}; font-size: 16px; }}
  svg {{ width: 100%; height: auto; display: block; background: {SHEET}; }}
  svg text {{ font-family: "Avenir Next", Avenir, "Trebuchet MS", sans-serif; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0 28px;
           font-family: "Avenir Next", Avenir, "Trebuchet MS", sans-serif; font-size: 14px; }}
  th, td {{ text-align: right; padding: 7px 8px; border-bottom: 1px solid #d3dee3; }}
  th:first-child, td:first-child {{ text-align: left; }}
  th {{ font-weight: 500; color: {MUTED}; }}
  @media (max-width: 720px) {{
    main {{ padding: 28px 14px 48px; }}
    h1 {{ font-size: 28px; }}
    table {{ display: block; overflow-x: auto; }}
  }}
</style>
</head>
<body>
<main>
  <h1>Sixteen Breakout-30 settings</h1>
  <p>Each cell is one backtest. The channel is 10, 20, 40, or 55 days. The stop is 1, 2, 3, or 4 average-true-range units. A completed trade pays 2 basis points, 1 on the entry and 1 on the exit. The hold is {HOLD_DAYS} daily bars. The outlined cell is the setting from the earlier report, 20 days and a 2-unit stop.</p>
  <p class="note">Color follows the average trade. Green is above zero and red is below zero, within that market's own range. The p-value is a Student t test that treats trades as independent. No cell is selected as a winner.</p>
  <h2>Bitcoin</h2>
  {_heatmap("Average trade after the 2 basis point round trip", btc)}
  <h2>Ethereum</h2>
  {_heatmap("Same sixteen settings, on the shorter Ethereum history", eth)}
  <h2>Every cell</h2>
  {_table(rows)}
</main>
</body>
</html>
'''


def main():
    if len(sys.argv) != 5 or sys.argv[3] != "-o":
        raise SystemExit(
            "usage: python3 examples/breakout30_grid.py btc.csv eth.csv -o grid.html"
        )
    books = []
    for label, path in (("Bitcoin", sys.argv[1]), ("Ethereum", sys.argv[2])):
        days, bars, skipped = load(path)
        books.append((label, days, bars, skipped))
    jobs = [
        (label, channel, stop, days, bars, skipped)
        for label, days, bars, skipped in books
        for channel in CHANNELS
        for stop in STOPS
    ]
    workers = min(8, len(jobs))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(_cell, jobs))
    Path(sys.argv[4]).write_text(render(rows), encoding="utf-8")
    for row in sorted(rows, key=lambda item: (item["name"], item["channel"], item["stop"])):
        print(
            f"{row['name']} {row['channel']} {row['stop']:g}: n={row['n']}"
            f" mean={row['mean'] * 1e4:.1f} p={row['p']:.3f} stake={row['ending']:.3f}"
        )


if __name__ == "__main__":
    main()

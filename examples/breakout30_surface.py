"""Two surfaces over channel length and the average-true-range window.

The stop is 2 units and the hold is 30 daily bars. A completed trade pays
2 basis points. From the repository root:

    python3 examples/breakout30_surface.py btc.csv eth.csv -o surface.html
"""

import json
import multiprocessing as mp
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from breakout30 import HOLD_DAYS, STOP_ATR
from breakout30_curves import CHANNELS, _atr_cell, _init
from breakout30_report import INK, MUTED, PAPER, load

WINDOWS = tuple(range(5, 41))


def _grid(rows):
    channels = [channel for channel in CHANNELS if 2 <= channel <= 60]
    windows = list(WINDOWS)
    found = {(row["channel"], row["window"]): row for row in rows}
    account = []
    p_values = []
    counts = []
    for channel in channels:
        account.append([(found[(channel, window)]["ending"] - 1.0) * 100.0 for window in windows])
        p_values.append([found[(channel, window)]["p"] for window in windows])
        counts.append([found[(channel, window)]["n"] for window in windows])
    return channels, windows, account, p_values, counts


def _page(books):
    payload = {}
    for name, rows in books.items():
        channels, windows, account, p_values, counts = _grid(rows)
        low = min(rows, key=lambda row: row["p"])
        payload[name] = {
            "channels": channels,
            "windows": windows,
            "account": account,
            "p": p_values,
            "n": counts,
            "low": {
                "channel": low["channel"],
                "window": low["window"],
                "account": (low["ending"] - 1.0) * 100.0,
                "p": low["p"],
                "n": low["n"],
                "mean": low["mean"] * 1e4,
            },
        }
    data = json.dumps(payload)
    cards = []
    for name in ("Bitcoin", "Ethereum"):
        low = payload[name]["low"]
        cards.append(
            f"<p><b>{name}.</b> Lowest p is {low['p']:.3f}. "
            f"The account return there is {low['account']:+.0f}%. "
            f"Channel {low['channel']} days, average-true-range window {low['window']} days, "
            f"{low['n']} trades. The average trade is {low['mean']:+.0f} basis points.</p>"
        )
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Breakout-30 surfaces</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  body {{ margin: 0; background: {PAPER}; color: {INK};
         font: 18px/1.5 "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif; }}
  main {{ max-width: 1100px; margin: 0 auto; padding: 40px 20px 64px; }}
  h1 {{ font-weight: 500; font-size: 34px; line-height: 1.15; letter-spacing: -0.02em; margin: 0 0 12px; }}
  h2 {{ font-weight: 500; font-size: 22px; margin: 28px 0 4px; }}
  p {{ max-width: 68ch; margin: 0 0 12px; }}
  .note {{ color: {MUTED}; font-size: 16px; }}
  .plot {{ width: 100%; height: 620px; background: #fbfbfa; }}
  .scatter {{ width: 100%; height: 480px; background: #fbfbfa; }}
  .callout {{ border-left: 3px solid #c4622d; margin: 8px 0 20px; padding: 2px 0 2px 16px; }}
  .callout p {{ margin: 0 0 8px; }}
</style>
</head>
<body>
<main>
  <h1>Return and p, as a surface</h1>
  <p>The floor is channel length and the average-true-range window. Height on the first chart is the account return. Height on the second is the p-value, with a flat sheet at 0.05. Anything under that sheet is below 0.05. Drag a chart to turn it. The return chart and the p-value chart for a market turn together. A completed trade pays 2 basis points. The hold is {HOLD_DAYS} daily bars. The stop is fixed at {STOP_ATR:g} average-true-range units. Channel length runs from {CHANNELS[0]} to {CHANNELS[-1]} days. The average-true-range window runs from {WINDOWS[0]} to {WINDOWS[-1]} days.</p>
  <p class="note">Return is the compounded account, in percent, from a starting stake of 1. Blue is a loss and copper is a gain. On the p-value chart, copper is below 0.05 and gray is above it. The dot is the lowest p. Under each pair, the left subplot has channel length on the x-axis and the right subplot has the average-true-range window. The y-axis on both is the p-value, with a line at 0.05.</p>
  <div class="callout">{''.join(cards)}</div>
  <h2>Bitcoin return</h2>
  <div id="btc-return" class="plot"></div>
  <h2>Bitcoin p-value</h2>
  <div id="btc-p" class="plot"></div>
  <div id="btc-compare" class="scatter"></div>
  <h2>Ethereum return</h2>
  <div id="eth-return" class="plot"></div>
  <h2>Ethereum p-value</h2>
  <div id="eth-p" class="plot"></div>
  <div id="eth-compare" class="scatter"></div>
</main>
<script>
const DATA = {data};
const LIGHT = {{ambient: 0.9, diffuse: 0.45, specular: 0.04, roughness: 0.95, fresnel: 0.04}};
const RETURN_SCALE = [[0, "#1e3a5f"], [0.5, "#f4f6f8"], [1, "#c4622d"]];
const P_SCALE = [[0, "#c4622d"], [0.05, "#c4622d"], [0.0501, "#d5dde4"], [1, "#eef1f4"]];

function span(matrix) {{
  const flat = matrix.flat();
  return [Math.min(...flat), Math.max(...flat)];
}}

function plane(book) {{
  return {{
    type: "surface",
    x: book.windows,
    y: book.channels,
    z: book.channels.map(() => book.windows.map(() => 0.05)),
    opacity: 0.42,
    showscale: false,
    hovertemplate: "p = 0.05<extra></extra>",
    colorscale: [[0, "#1e3a5f"], [1, "#1e3a5f"]],
    lighting: {{ambient: 1, diffuse: 0, specular: 0, roughness: 1, fresnel: 0}},
    contours: {{z: {{show: false}}}}
  }};
}}

function marker(book, field) {{
  const low = book.low;
  return {{
    type: "scatter3d",
    mode: "markers",
    x: [low.window],
    y: [low.channel],
    z: [field === "p" ? low.p : low.account],
    marker: {{size: 5, color: "#c4622d", line: {{color: "#1c2834", width: 2}}}},
    hovertemplate: "lowest p " + low.p.toFixed(3) + "<br>return " + low.account.toFixed(1) + "%<br>" + low.channel + " days, ATR window " + low.window + "<extra></extra>"
  }};
}}

function surface(element, book, field, title, scale, cmin, cmax, cmid, withPlane) {{
  const trace = {{
    type: "surface",
    x: book.windows,
    y: book.channels,
    z: book[field],
    customdata: book.n,
    colorscale: scale,
    cmin: cmin,
    cmax: cmax,
    lighting: LIGHT,
    colorbar: {{title: {{text: title, side: "right"}}, thickness: 12, len: 0.65, outlinewidth: 0, tickfont: {{size: 12}}}},
    hovertemplate: "channel %{{y}} days<br>ATR window %{{x}}<br>" + title + " %{{z:.2f}}<br>%{{customdata}} trades<extra></extra>",
    contours: {{z: {{show: field !== "p", usecolormap: true, highlightcolor: "#1c2834", project: {{z: false}}}}}}
  }};
  if (cmid !== null) trace.cmid = cmid;
  const traces = [trace, marker(book, field)];
  if (withPlane) traces.push(plane(book));
  const zaxis = {{title: title, backgroundcolor: "#fbfbfa", gridcolor: "#e1e6ea", color: "#1c2834"}};
  if (field === "p") zaxis.range = [0, 1];
  Plotly.newPlot(element, traces, {{
    paper_bgcolor: "#fbfbfa",
    margin: {{l: 0, r: 0, t: 8, b: 0}},
    showlegend: false,
    scene: {{
      xaxis: {{title: "ATR window, days", backgroundcolor: "#fbfbfa", gridcolor: "#e1e6ea", color: "#1c2834"}},
      yaxis: {{title: "Channel, days", backgroundcolor: "#fbfbfa", gridcolor: "#e1e6ea", color: "#1c2834"}},
      zaxis: zaxis,
      camera: {{eye: {{x: 1.45, y: -1.65, z: 0.85}}}},
      aspectmode: "manual",
      aspectratio: {{x: 1.1, y: 1.4, z: 0.72}}
    }}
  }}, {{responsive: true, displaylogo: false}});
}}

function linkCameras(left, right) {{
  let busy = false;
  function follow(source, target) {{
    document.getElementById(source).on("plotly_relayout", (event) => {{
      const camera = event["scene.camera"];
      if (!camera || busy) return;
      busy = true;
      Plotly.relayout(target, {{"scene.camera": camera}}).then(() => {{ busy = false; }});
    }});
  }}
  follow(left, right);
  follow(right, left);
}}

function compare(element, book) {{
  const groups = {{
    channel: {{under: empty(), over: empty()}},
    window: {{under: empty(), over: empty()}}
  }};
  function empty() {{
    return {{x: [], y: [], text: []}};
  }}
  book.channels.forEach((channel, i) => {{
    book.windows.forEach((windowDays, j) => {{
      const side = book.p[i][j] < 0.05 ? "under" : "over";
      const text = channel + " days, ATR window " + windowDays + ", " + book.n[i][j] + " trades";
      groups.channel[side].x.push(channel);
      groups.channel[side].y.push(book.p[i][j]);
      groups.channel[side].text.push(text);
      groups.window[side].x.push(windowDays);
      groups.window[side].y.push(book.p[i][j]);
      groups.window[side].text.push(text);
    }});
  }});
  function traces(bucket, side, xaxis, yaxis, showlegend) {{
    const copper = side === "under";
    return {{
      type: "scatter", mode: "markers",
      x: bucket.x, y: bucket.y, text: bucket.text,
      xaxis: xaxis, yaxis: yaxis,
      name: copper ? "p below 0.05" : "p above 0.05",
      legendgroup: side,
      showlegend: showlegend,
      marker: {{size: copper ? 8 : 6, color: copper ? "#c4622d" : "#8ea0b0"}},
      hovertemplate: "%{{text}}<br>p %{{y:.3f}}<extra></extra>"
    }};
  }}
  const low = book.low;
  const axis = {{zeroline: false, gridcolor: "#e1e6ea", color: "#1c2834", range: [0, 1], title: "p-value"}};
  Plotly.newPlot(element, [
    traces(groups.channel.over, "over", "x", "y", true),
    traces(groups.channel.under, "under", "x", "y", true),
    traces(groups.window.over, "over", "x2", "y2", false),
    traces(groups.window.under, "under", "x2", "y2", false),
    {{type: "scatter", mode: "markers", x: [low.channel], y: [low.p], showlegend: false, marker: {{size: 12, color: "#c4622d", symbol: "circle-open", line: {{width: 2}}}}, hovertemplate: "lowest p<extra></extra>"}},
    {{type: "scatter", mode: "markers", x: [low.window], y: [low.p], xaxis: "x2", yaxis: "y2", showlegend: false, marker: {{size: 12, color: "#c4622d", symbol: "circle-open", line: {{width: 2}}}}, hovertemplate: "lowest p<extra></extra>"}}
  ], {{
    paper_bgcolor: "#fbfbfa",
    plot_bgcolor: "#fbfbfa",
    margin: {{l: 58, r: 24, t: 36, b: 48}},
    legend: {{orientation: "h", y: 1.14, font: {{size: 12}}}},
    xaxis: {{title: "Channel, days", domain: [0, 0.46], zeroline: false, gridcolor: "#e1e6ea", color: "#1c2834"}},
    xaxis2: {{title: "ATR window, days", domain: [0.54, 1], anchor: "y2", zeroline: false, gridcolor: "#e1e6ea", color: "#1c2834"}},
    yaxis: Object.assign({{anchor: "x"}}, axis),
    yaxis2: Object.assign({{anchor: "x2"}}, axis),
    shapes: [
      {{type: "line", xref: "x", yref: "y", x0: book.channels[0], x1: book.channels[book.channels.length - 1], y0: 0.05, y1: 0.05, line: {{color: "#1e3a5f", width: 1}}}},
      {{type: "line", xref: "x2", yref: "y2", x0: book.windows[0], x1: book.windows[book.windows.length - 1], y0: 0.05, y1: 0.05, line: {{color: "#1e3a5f", width: 1}}}}
    ]
  }}, {{responsive: true, displaylogo: false}});
}}

const btcReturn = span(DATA.Bitcoin.account);
const ethReturn = span(DATA.Ethereum.account);
const returnLimit = Math.max(Math.abs(btcReturn[0]), Math.abs(btcReturn[1]), Math.abs(ethReturn[0]), Math.abs(ethReturn[1]));
surface("btc-return", DATA.Bitcoin, "account", "Account return, %", RETURN_SCALE, -returnLimit, returnLimit, 0, false);
surface("btc-p", DATA.Bitcoin, "p", "p-value", P_SCALE, 0, 1, null, true);
surface("eth-return", DATA.Ethereum, "account", "Account return, %", RETURN_SCALE, -returnLimit, returnLimit, 0, false);
surface("eth-p", DATA.Ethereum, "p", "p-value", P_SCALE, 0, 1, null, true);
compare("btc-compare", DATA.Bitcoin);
compare("eth-compare", DATA.Ethereum);
linkCameras("btc-return", "btc-p");
linkCameras("eth-return", "eth-p");
</script>
</body>
</html>
'''


def main():
    if len(sys.argv) != 5 or sys.argv[3] != "-o":
        raise SystemExit(
            "usage: python3 examples/breakout30_surface.py btc.csv eth.csv -o surface.html"
        )
    books = {}
    for label, path in (("Bitcoin", sys.argv[1]), ("Ethereum", sys.argv[2])):
        books[label] = load(path)
    jobs = [
        (label, channel, window)
        for label in books
        for channel in CHANNELS
        if 2 <= channel <= 60
        for window in WINDOWS
    ]
    started = time.perf_counter()
    context = mp.get_context("forkserver")
    with ProcessPoolExecutor(
        max_workers=2,
        mp_context=context,
        initializer=_init,
        initargs=(books,),
    ) as pool:
        rows = list(pool.map(_atr_cell, jobs, chunksize=16))
    elapsed = time.perf_counter() - started
    grouped = {"Bitcoin": [], "Ethereum": []}
    for row in rows:
        grouped[row["name"]].append(row)
    Path(sys.argv[4]).write_text(_page(grouped), encoding="utf-8")
    print(f"{len(rows)} cells in {elapsed:.1f}s")
    for name, items in grouped.items():
        top = max(items, key=lambda row: row["ending"])
        low = min(items, key=lambda row: row["p"])
        print(
            f"{name} return {(top['ending'] - 1) * 100:.0f}% at {top['channel']}d/atr {top['window']}"
            f"  lowest p {low['p']:.3f} at {low['channel']}d/atr {low['window']}"
        )


if __name__ == "__main__":
    main()

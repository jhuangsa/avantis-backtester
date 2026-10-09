"""Tune range_basket for the highest return under a max drawdown limit.

Three searches, at limits of 10%, 15% and 20%, on 2026 (TUNE). Each winner
then runs unchanged on 2025 (TEST). Writes drawdown_limit.html: equity on
both windows, every run's return against its drawdown, and a table.

Run: python3 examples/sharpe_hunt/drawdown_limit.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from hunt import COSTS, TEST, TUNE, TF, avbt_cpp, markets, plain, settings  # noqa: E402

LIMITS = [0.10, 0.15, 0.20]
COINS = ["BTC", "ETH", "SOL", "DOGE", "AVAX"]
TFS = (TF.Min15, TF.Hour1, TF.Hour4)
START = {"timeframe": TF.Hour1, "window": 100, "atr_period": 14, "max_bars": 96, "max_open": 3,
         "take": 1.0, "depth": 0.5, "atr_stops": 7.0, "max_stop": 0.07, "leverage": 4.0}
KNOBS = {
    "leverage": [2.0, 3.0, 4.0, 5.0, 6.0, 8.0],
    "max_open": [3, 2, 4],
    "depth": [0.5, 0.4, 0.7],
    "atr_stops": [7.0, 6.0, 8.0],
    "take": [1.0, 0.8, 1.2],
    "max_bars": [96, 72, 120],
}
COLORS = ["#2a6fdb", "#e08a1e", "#2e9e5b"]


def curve(r):
    """Daily equity as growth of 1."""
    eq = pd.Series(r.equity, index=pd.to_datetime(r.clock, unit="s")).resample("1D").last().ffill()
    return {"x": [str(d.date()) for d in eq.index], "y": [round(v / eq.iloc[0], 4) for v in eq]}


def main():
    tune, test = markets(COINS, TUNE, TFS), markets(COINS, TEST, TFS)
    costs = {c: COSTS for c in COINS}
    rows = []
    for limit in LIMITS:
        s = avbt_cpp.optimize("range_basket", START, KNOBS, tune, costs, settings(), rounds=60,
                              max_drawdown=limit, min_gain=0.01)
        best = {**START, **s["best"]}
        held = avbt_cpp.run("range_basket", best, test, costs, settings())
        fit = avbt_cpp.run("range_basket", best, tune, costs, settings())
        rows.append({"limit": limit, "feasible": not math.isnan(s["score"]), "best": best,
                     "tune": avbt_cpp.summary(fit), "test": avbt_cpp.summary(held),
                     "tune_curve": curve(fit), "test_curve": curve(held),
                     "runs": [(r["max_drawdown"], r["total_return"]) for r in s["runs"]]})
        print(f"limit {limit:.0%}: return {s['total_return']:.1%}, drawdown {s['max_drawdown']:.1%}, "
              f"{len(s['runs'])} runs; 2025 return {rows[-1]['test']['total_return']:.1%}, "
              f"drawdown {rows[-1]['test']['max_drawdown']:.1%}")
    (HERE / "drawdown_limit.html").write_text(page(rows))


def page(rows):
    def lines(key):
        return [{**r[key], "name": f"limit {r['limit']:.0%}", "line": {"color": c}} for r, c in zip(rows, COLORS)]

    seen = {p for r in rows for p in r["runs"]}
    scatter = [{"x": [d for d, _ in seen], "y": [t for _, t in seen], "mode": "markers", "name": "every run",
                "marker": {"color": "#bbb", "size": 6}}]
    scatter += [{"x": [r["tune"]["max_drawdown"]], "y": [r["tune"]["total_return"]], "mode": "markers",
                 "name": f"winner, limit {r['limit']:.0%}", "marker": {"color": c, "size": 14, "symbol": "star"}}
                for r, c in zip(rows, COLORS)]
    shapes = [{"type": "line", "x0": r["limit"], "x1": r["limit"], "yref": "paper", "y0": 0, "y1": 1,
               "line": {"color": c, "dash": "dot"}} for r, c in zip(rows, COLORS)]
    keys = list(KNOBS)
    head = "".join(f"<th>{k}</th>" for k in keys)
    body = "".join(
        f"<tr><td>{r['limit']:.0%}</td>" + "".join(f"<td>{plain(r['best'][k])}</td>" for k in keys)
        + f"<td>{r['tune']['total_return']:.1%}</td><td>{r['tune']['max_drawdown']:.1%}</td>"
        + f"<td>{r['tune']['sharpe']:.2f}</td><td>{r['tune']['trades']}</td>"
        + f"<td>{r['test']['total_return']:.1%}</td><td>{r['test']['max_drawdown']:.1%}</td></tr>"
        for r in rows)
    data = json.dumps({"tune": lines("tune_curve"), "test": lines("test_curve"), "scatter": scatter,
                       "shapes": shapes})
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Drawdown limit</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#222}}
table{{border-collapse:collapse;font-size:14px;margin:12px 0}}td,th{{border:1px solid #ddd;padding:4px 8px;
text-align:right}}.chart{{height:380px}}</style></head><body>
<h1>range_basket: highest return under a drawdown limit</h1>
<p>Five coins in one account, 1-hour signals. Each search keeps only runs whose max drawdown on
2026-01-01 to 2026-10-01 is within the limit, and picks the highest return. The winner then runs
unchanged on 2025-03-25 to 2026-01-01, which the search never saw.</p>
<div style="overflow-x:auto"><table><tr><th>limit</th>{head}<th>2026 return</th><th>2026 drawdown</th><th>2026 Sharpe</th>
<th>trades</th><th>2025 return</th><th>2025 drawdown</th></tr>{body}</table></div>
<h2>Every run: return against drawdown (2026)</h2><div id="sc" class="chart"></div>
<h2>Equity, 2026 (tuned)</h2><div id="tu" class="chart"></div>
<h2>Equity, 2025 (not seen by the search)</h2><div id="te" class="chart"></div>
<script>const D={data};
const pct={{tickformat:".0%"}};
Plotly.newPlot("sc",D.scatter,{{xaxis:{{title:"max drawdown",...pct}},yaxis:{{title:"total return",...pct}},
shapes:D.shapes,margin:{{t:10}}}});
Plotly.newPlot("tu",D.tune,{{yaxis:{{title:"growth of 1"}},margin:{{t:10}}}});
Plotly.newPlot("te",D.test,{{yaxis:{{title:"growth of 1"}},margin:{{t:10}}}});
</script></body></html>"""


if __name__ == "__main__":
    main()

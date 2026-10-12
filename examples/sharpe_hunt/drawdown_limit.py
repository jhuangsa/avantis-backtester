"""Tune a library strategy for the highest return under a max drawdown limit.

Three searches, at limits of 10%, 15% and 20%, on 2026 (TUNE). Each winner
then runs unchanged on 2025 (TEST). Writes drawdown_limit_<name>.html
(drawdown_limit.html for range_basket): equity on both windows, every run's
return against its drawdown, and a table; and
results_drawdown/<name>_dd<limit>.json, each winner's full parameters.

The strategy is range_basket by default. Any other name takes its coins,
START and KNOBS from run_<name>.py. Every leverage param is searched over
1 to 10; every other number knob over more values around its first one.

Run: python3 examples/sharpe_hunt/drawdown_limit.py [name]
"""

from __future__ import annotations

import argparse
import importlib
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
    "max_open": [3, 2, 4],
    "depth": [0.5, 0.4, 0.7],
    "atr_stops": [7.0, 6.0, 8.0],
    "take": [1.0, 0.8, 1.2],
    "max_bars": [96, 72, 120],
}
COLORS = ["#2a6fdb", "#e08a1e", "#2e9e5b"]
LEVERAGE = list(range(1, 11))
SPREAD = [0.5, 0.67, 0.8, 0.9, 1.1, 1.25, 1.5, 2.0]


def setup(name):
    """Coins, start and knobs: range_basket's above, else run_<name>.py's."""
    if name == "range_basket":
        return COINS, START, KNOBS
    run = importlib.import_module(f"run_{name}")
    start = getattr(run, "START", {})
    return getattr(run, "COINS", None) or [start["instrument"]], start, run.KNOBS


def widen(name, knobs):
    """Leverage 1 to 10; each other number knob: its values plus first * SPREAD, in range."""
    info = {p.name: p for i in avbt_cpp.strategies() if i.name == name for p in i.params}
    out = {k: LEVERAGE for k in info if "leverage" in k}
    for k, vals in knobs.items():
        base = vals[0]
        if k in out or isinstance(base, bool) or not isinstance(base, (int, float)) or base <= 0:
            out.setdefault(k, vals)
            continue
        more = {base * f for f in SPREAD}
        more = {round(v) for v in more} if isinstance(base, int) else {round(v, 2) for v in more}
        more = {v for v in more if info[k].min <= v <= info[k].max and v > 0}
        out[k] = [base] + sorted((more | set(vals[1:])) - {base})
    return out


def curve(r):
    """Daily equity as growth of 1."""
    eq = pd.Series(r.equity, index=pd.to_datetime(r.clock, unit="s")).resample("1D").last().ffill()
    return {"x": [str(d.date()) for d in eq.index], "y": [round(v / eq.iloc[0], 4) for v in eq]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", default="range_basket", help="a library strategy with a run_<name>.py")
    name = ap.parse_args().name
    coins, start, knobs = setup(name)
    knobs = widen(name, knobs)
    tune, test = markets(coins, TUNE, TFS), markets(coins, TEST, TFS)
    costs = {c: COSTS for c in coins}
    rows = []
    for limit in LIMITS:
        s = avbt_cpp.optimize(name, start, knobs, tune, costs, settings(), rounds=60,
                              max_drawdown=limit, min_gain=0.01)
        best = {**start, **s["best"]}
        held = avbt_cpp.run(name, best, test, costs, settings())
        fit = avbt_cpp.run(name, best, tune, costs, settings())
        rows.append({"limit": limit, "feasible": not math.isnan(s["score"]), "best": best,
                     "tune": avbt_cpp.summary(fit), "test": avbt_cpp.summary(held),
                     "tune_curve": curve(fit), "test_curve": curve(held),
                     "runs": [(r["max_drawdown"], r["total_return"]) for r in s["runs"]]})
        print(f"limit {limit:.0%}: return {s['total_return']:.1%}, drawdown {s['max_drawdown']:.1%}, "
              f"{len(s['runs'])} runs; 2025 return {rows[-1]['test']['total_return']:.1%}, "
              f"drawdown {rows[-1]['test']['max_drawdown']:.1%}" + ("" if rows[-1]['feasible'] else "; no run under the limit"))
    save(name, coins, knobs, rows)
    html = "drawdown_limit.html" if name == "range_basket" else f"drawdown_limit_{name}.html"
    (HERE / html).write_text(page(name, coins, list(knobs), rows))


def save(name, coins, knobs, rows):
    """One strategy file per limit, in results_drawdown/."""
    out_dir = HERE / "results_drawdown"
    out_dir.mkdir(exist_ok=True)
    for r in rows:
        out = {"strategy": name, "coins": coins, "max_drawdown_limit": r["limit"],
               "feasible": r["feasible"], "params": {k: plain(v) for k, v in r["best"].items()},
               "tuned": r["tune"], "test_2025": r["test"], "runs": len(r["runs"]),
               "searched": {k: [plain(v) for v in vals] for k, vals in knobs.items()}}
        (out_dir / f"{name}_dd{round(r['limit'] * 100)}.json").write_text(json.dumps(out, indent=1))


def page(name, coins, keys, rows):
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
<h1>{name}: highest return under a drawdown limit</h1>
<p>{", ".join(coins)} in one account. Each search keeps only runs whose max drawdown on
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

"""Three mimic strategies off an efficient frontier: careful, balanced and bold.

By default (--by pnl) each strategy runs twice, on two trails:

- the long trail trades from 2025-01-01 to the first day of the current
  month (the AI Copy v5 catalog window), with the stop-out at -15%
  (hard stop 0.85). Its return, risk, budget and trades are the strategy's.
- the short trail trades over the wallet's window, from its first day, on
  the same bars from 2025-01-01 (the lines warm up; mimic open_from), with
  no stop-out. Its gap to the wallet is the strategy's.

The gap is, after the best scale k >= 0, the RMS of k * mimic - wallet
realized PnL over the hourly grid, over the RMS of the wallet's: 0 is the
same shape, 1 no closer than flat. The score is gap - --weight * return;
lower is better. It climbs two knobs at a time for the lowest score, from
the fit mimic.py saved and from --restarts (3) random starts (seed 0), up
to --passes (4) passes each, and keeps the --count (100) with the lowest
score. Settings that trade alike are kept once. Leverage is capped at the
lowest Veranta maximum leverage of the fit's markets (and 100), floored to
0.5 (mimic_drawdown.leverage_bound; --no-bound lifts it). A strategy needs
--min-trades (20) on the short trail and --min-long-trades (30) on the long.

With --by rmse it keeps instead every setting whose RMSE to the wallet's
return is at most --rmse (default twice the fit's), from climbs for the
highest return minus lambda times risk, once per --lambdas value, and
then the highest return in each of --count equal-sized risk bins. It runs
over the fit's window only.

Return is the final return on peak own capital (ledger()). Risk is the
standard deviation of its daily returns, read at each UTC midnight, times
sqrt(365): the same daily returns as mimic.daily_sharpe.

The frontier is the kept strategies that no other kept one beats: none
has lower or equal risk and a higher return. The risk levels are the
10th, 50th and 90th percentiles of the kept strategies' risk. At each
level the pick is the frontier strategy with the highest return at or
under that risk: careful, balanced and bold. Drawdown limits pick
nothing here (mimic_drawdown.py does that). See docs/mimic_frontier.md.

Run from the repository root, after mimic.py (and, for a Veranta wallet,
veranta_history.py):

    python3 examples/mimic_frontier.py data/candles/mimic_<address>.json
    python3 examples/mimic_frontier.py --by rmse data/candles/mimic_<address>.json

Writes data/candles/mimic_<address>_frontier_pnl.json (or _frontier.json)
beside it, and examples/mimic_frontier_<first 6 hex of the address>_pnl.html
(or .html): the frontier, and each pick's PnL and return beside the
wallet's. --replay redraws the page from the saved frontier, with no
search. With several fits and --page, it also writes one page with a
column per wallet, named by --names:

    python3 examples/mimic_frontier.py --replay --page examples/mimic_frontier_two_wallets.html \
        --names "velvet leopard,steady llama" data/candles/mimic_<a>.json data/candles/mimic_<b>.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

import mimic as M

HERE = Path(__file__).resolve().parent
NAMES = {"careful": 10, "balanced": 50, "bold": 90}
LONG_START = "2025-01-01"  # the long trail's first day: AI Copy v5's catalog window
LONG_STOP = 0.85  # the long trail stops out at -15% of the start, as v5's catalog run does
WEIGHT = 0.0005  # --weight: return breaks close calls on the gap; docs/mimic_frontier.md


def long_end(today=None):
    """The long trail's end: the first day of the current UTC month, as v5's catalog window."""
    t = pd.Timestamp(today or pd.Timestamp.now(tz="UTC")).tz_localize(None)
    return t.replace(day=1).strftime("%Y-%m-%d")


def score(s, weight):
    """Lower is better: the gap less `weight` times the long trail's return."""
    return s["gap"] - weight * s["final"]


def daily_risk(book):
    """The standard deviation of daily returns on own capital, times sqrt(365); NaN under 30 days."""
    if book is None or not len(book):
        return float("nan")
    day = book[book["time"] % M.DAY == 0]
    pnl, cap = day["pnl"].to_numpy(float), day["capital"].to_numpy(float)[1:]
    r = (np.diff(pnl) / np.where(cap > 0, cap, 1.0))[cap > 0]
    return float(r.std(ddof=1) * np.sqrt(365)) if len(r) >= 30 else float("nan")


def pnl_gap(mimic, wallet):
    """RMS of k * mimic - wallet over RMS of wallet, at the best k >= 0; 1 when it never trades."""
    m, w = np.nan_to_num(np.asarray(mimic, float)), np.nan_to_num(np.asarray(wallet, float))
    mm, ww = m @ m, w @ w
    if not mm > 0 or not ww > 0:
        return 1.0, 0.0
    k = max(0.0, (m @ w) / mm)
    return float(np.sqrt(np.mean((k * m - w) ** 2) / np.mean(w ** 2))), k


def keep(pool, count):
    """The highest return in each of `count` equal-sized risk bins; all of `pool` when it is smaller."""
    pool = sorted(pool, key=lambda s: s["risk"])
    if len(pool) <= count:
        return pool
    return [max(b, key=lambda s: s["final"]) for b in np.array_split(np.array(pool, dtype=object), count)]


def frontier(kept):
    """The kept strategies no other beats: lower or equal risk and higher return. Sorted by risk."""
    out, best = [], -np.inf
    for s in sorted(kept, key=lambda s: (s["risk"], -s["final"])):
        if s["final"] > best:
            out.append(s)
            best = s["final"]
    return out


def picks(kept, front):
    """Careful, balanced and bold: the frontier's highest return at or under each risk percentile."""
    out = {}
    for name, q in NAMES.items():
        level = float(np.percentile([s["risk"] for s in kept], q))
        under = [s for s in front if s["risk"] <= level]
        out[name] = {"percentile": q, "risk_level": level, **max(under, key=lambda s: s["final"])}
    return out


COLORS = {"careful": "#2f855a", "balanced": "#b7791f", "bold": "#c53030"}


def chart(path, title, kept, front, chosen, books, target):
    """--by rmse: the frontier above; below, each pick's PnL, and its return beside the wallet's.

    books: each pick's ledger table on the hourly grid; target: the wallet's return there.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(rows=3, cols=1, vertical_spacing=0.08, subplot_titles=(
        "kept strategies and the frontier", f"PnL, on a {M.vt.ACCOUNT:,.0f} account",
        "return on peak own capital, beside the wallet's"))
    fig.add_scatter(x=[s["risk"] for s in kept], y=[s["final"] for s in kept], mode="markers",
                    name="kept", marker={"color": "#9aa5b1", "size": 6},
                    text=[f"rmse {s['rmse']:.1%}, lev {s['params']['leverage']:g}, needs ${s.get('budget', 0):,.0f}" for s in kept], row=1, col=1)
    fig.add_scatter(x=[s["risk"] for s in front], y=[s["final"] for s in front], mode="lines+markers",
                    name="frontier", line={"color": "#2b6cb0"}, row=1, col=1)
    for name, s in chosen.items():
        c = COLORS[name]
        fig.add_scatter(x=[s["risk"]], y=[s["final"]], mode="markers+text", name=f"{name}, needs ${s.get('budget', 0):,.0f}", text=[name], legendgroup=name,
                        textposition="top center", marker={"color": c, "size": 14, "symbol": "star"}, row=1, col=1)
        fig.add_vline(x=s["risk_level"], line={"color": c, "dash": "dot", "width": 1}, row=1, col=1)
        t = pd.to_datetime(books[name]["time"], unit="s")
        fig.add_scatter(x=t, y=books[name]["pnl"], name=name, legendgroup=name, showlegend=False,
                        line={"color": c}, row=2, col=1)
        fig.add_scatter(x=t, y=books[name]["return"], name=name, legendgroup=name, showlegend=False, line={"color": c}, row=3, col=1)
    fig.add_scatter(x=pd.to_datetime(target.index, unit="s"), y=target.to_numpy(), name="wallet",
                    line={"color": "#1a202c", "dash": "dot"}, row=3, col=1)
    fig.update_layout(title=title, template="plotly_white", height=1200)
    fig.update_xaxes(title_text="risk: sd of daily returns, annualized", tickformat=".0%", row=1, col=1)
    fig.update_yaxes(title_text="final return", tickformat=".0%", row=1, col=1)
    fig.update_yaxes(title_text="PnL", tickprefix="$", row=2, col=1)
    fig.update_yaxes(title_text="return", tickformat=".0%", row=3, col=1)
    fig.write_html(path, include_plotlyjs="cdn")


def pnl_chart(path, title, panels):
    """--by pnl: a column per wallet. The frontier of the long trail; the short
    trail's total PnL times k beside the wallet's; the long trail's return beside the wallet's.

    panels: by_pnl's results, each with "name".
    """
    from plotly.subplots import make_subplots

    rows = ("{} kept and the frontier, long trail", "total PnL: wallet and picks × k, short trail",
            "return on peak own capital: wallet and picks, long trail")
    fig = make_subplots(rows=3, cols=len(panels), vertical_spacing=0.09, horizontal_spacing=0.08, subplot_titles=[
        f"{p['name']}: {t.format(len(p['kept']))}" for t in rows for p in panels])
    for col, p in enumerate(panels, 1):
        first = col == 1
        kept, front, chosen = p["kept"], p["frontier"], p["picks"]
        fig.add_scatter(x=[s["risk"] for s in kept], y=[s["final"] for s in kept], mode="markers", name="kept",
                        legendgroup="kept", showlegend=first, marker={"color": "#a0aec0", "size": 6},
                        text=[f"gap {s['gap']:.3f}, score {s['score']:.3f}, {s['trades']} trades, lev {s['params']['leverage']:g}"
                              for s in kept], row=1, col=col)
        fig.add_scatter(x=[s["risk"] for s in front], y=[s["final"] for s in front], mode="lines+markers",
                        name="frontier", legendgroup="frontier", showlegend=first, line={"color": "#2b6cb0"}, row=1, col=col)
        w = p["wallet"]
        fig.add_scatter(x=pd.to_datetime(w["time"], unit="s"), y=w["pnl"], name="wallet", legendgroup="wallet",
                        showlegend=first, line={"color": "#1a202c", "dash": "dot"}, row=2, col=col)
        fig.add_scatter(x=pd.to_datetime(w["time"], unit="s"), y=w["return"], name="wallet", legendgroup="wallet",
                        showlegend=False, line={"color": "#1a202c", "dash": "dot"}, row=3, col=col)
        for name, s in chosen.items():
            c = COLORS[name]
            fig.add_scatter(x=[s["risk"]], y=[s["final"]], mode="markers+text", text=[name], textposition="top center",
                            name=name, legendgroup=name, showlegend=first,
                            hovertext=f"gap {s['gap']:.3f}, {s['trades']} trades, lev {s['params']['leverage']:g}, "
                                      f"needs ${s['budget']:,.0f}",
                            marker={"color": c, "size": 14, "symbol": "star"}, row=1, col=col)
            fig.add_vline(x=s["risk_level"], line={"color": c, "dash": "dot", "width": 1}, row=1, col=col)
            b = p["short"][name]
            fig.add_scatter(x=pd.to_datetime(b["time"], unit="s"), y=b["pnl"] * s["k"], name=name, legendgroup=name,
                            showlegend=False, line={"color": c}, row=2, col=col)
            b = p["long"][name]
            fig.add_scatter(x=pd.to_datetime(b["time"], unit="s"), y=b["return"], name=name, legendgroup=name,
                            showlegend=False, line={"color": c}, row=3, col=col)
        fig.update_xaxes(title_text="risk: sd of daily returns, annualized", tickformat=".0%", row=1, col=col)
        fig.update_yaxes(title_text="final return", tickformat=".0%", row=1, col=col)
        fig.update_yaxes(title_text="PnL", tickprefix="$", row=2, col=col)
        fig.update_yaxes(title_text="return", tickformat=".0%", row=3, col=col)
    fig.update_layout(title=title, template="plotly_white", height=1300, hovermode="closest")
    fig.write_html(path, include_plotlyjs="cdn")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("saved", nargs="+", help="mimic_<address>.json files from mimic.py")
    ap.add_argument("--rmse", type=float, help="most RMSE to the wallet; default twice the fit's")
    ap.add_argument("--count", type=int, default=100, help="strategies to keep")
    ap.add_argument("--lambdas", default="0,0.25,0.5,1,2,4", help="risk weights, one climb each")
    ap.add_argument("--passes", type=int, default=4)
    ap.add_argument("--by", choices=["rmse", "pnl"], default="pnl",
                    help="rmse: all under --rmse, spread by risk; pnl: the lowest gap - weight * return")
    ap.add_argument("--restarts", type=int, default=3, help="random starts for --by pnl")
    ap.add_argument("--weight", type=float, default=WEIGHT, help="--by pnl: the score is gap - weight * return")
    ap.add_argument("--bound", action=argparse.BooleanOptionalAction, default=True,
                    help="--by pnl: cap leverage at the markets' Veranta maximum")
    ap.add_argument("--min-trades", type=int, default=20, help="--by pnl: fewer on the short trail is never kept")
    ap.add_argument("--min-long-trades", type=int, default=30, help="--by pnl: fewer on the long trail is never kept")
    ap.add_argument("--budget", type=float, help="the account; default the fit's (10,000). Opens below a "
                    "market's min position are refused, so a small budget trades less. ADR 0022")
    ap.add_argument("--replay", action="store_true", help="redraw the page from the saved <fit>_frontier.json; no search")
    ap.add_argument("--page", help="--by pnl: also write one page with a column per fit")
    ap.add_argument("--names", help="--page: the wallets' names, comma separated; default the addresses")
    args = ap.parse_args()
    if args.by == "rmse":
        return [by_rmse(args, Path(p)) for p in args.saved]
    panels = [by_pnl(args, Path(p)) for p in args.saved]
    if args.page:
        names = args.names.split(",") if args.names else [f"{p['address'][:8]}…" for p in panels]
        for p, n in zip(panels, names):
            p["name"] = n.strip()
        pnl_chart(args.page, f"Mimic frontier picks for {' and '.join(p['name'] for p in panels)} "
                  f"(weight {args.weight:g}; long trail from {LONG_START}; fees, spread and min position included)", panels)
        print(f"wrote {args.page}")


def by_rmse(args, path):
    saved = json.loads(path.read_text())
    if args.replay:
        saved = json.loads(path.with_name(f"{path.stem}_frontier.json").read_text())
    M.use_venue(saved.get("venue", "avantis"))
    M.vt.ACCOUNT = args.budget or saved.get("settings", {}).get("starting_balance", M.vt.ACCOUNT)
    start, end = saved["start"], saved["end"]
    markets = M.load_markets(list(saved["markets"]), start, end)
    grid = M.hour_grid(start, end)
    costs = M.market_costs(saved["markets"], (M.FEE, M.FEE))
    target = M.load_target(path, grid)
    if target is None:
        raise SystemExit(f"{M.target_path(path).name} is missing: no RMSE without the wallet's curve")
    seen = {}

    def scores(p):
        key = M.effective(p)
        if key not in seen:
            r = M.backtest(p, markets, costs)
            book = M.mimic_book(r, markets, grid, p["leverage"])
            s = {**M.own_scores(book, target), "trades": M.avbt_cpp.summary(r)["trades"], "risk": daily_risk(book),
                 "budget": M.min_budget(r, costs)}
            seen[key] = {**s, "params": p}
        return seen[key]

    def draw(kept, front, chosen, limit):
        books = {n: M.mimic_book(M.backtest(s["params"], markets, costs), markets, grid, s["params"]["leverage"])
                 for n, s in chosen.items()}
        page = HERE / f"mimic_frontier_{saved['address'][2:8].lower()}.html"
        how = f"RMSE ≤ {limit:.1%}"
        chart(page, f"Mimic frontier, {saved['address'][:8]}…, {how}", kept, front, chosen, books, target)
        print(f"wrote {page}")

    if args.replay:
        return draw(saved["kept"], saved["frontier"], saved["picks"], saved.get("rmse_limit"))

    first = dict(saved["params"])
    limit = args.rmse or 2 * scores(first)["rmse"]
    print(f"RMSE limit {limit:.1%}; the fit's is {scores(first)['rmse']:.1%}")

    def goal(p, lam):
        s = scores(p)
        if not (s["rmse"] <= limit and np.isfinite(s["final"]) and np.isfinite(s["risk"])):
            return -1e9 - (s["rmse"] if s["rmse"] == s["rmse"] else 1e9)
        return s["final"] - lam * s["risk"]

    with ThreadPoolExecutor(os.cpu_count()) as runs:
        for lam in map(float, args.lambdas.split(",")):
            best, best_g = first, goal(first, lam)
            for _ in range(args.passes):
                before = best_g
                for a, b in itertools.combinations(M.KNOBS, 2):
                    tries = [{**best, a: x, b: y} for x in M.KNOBS[a] for y in M.KNOBS[b]]
                    list(runs.map(scores, tries))
                    for p in tries:
                        g = goal(p, lam)
                        if g > best_g + 1e-12:
                            best, best_g = p, g
                if best_g <= before + 1e-6:
                    break
            s = scores(best)
            print(f"lambda {lam:g}: final {s['final']:.1%}, risk {s['risk']:.1%}, rmse {s['rmse']:.1%}; "
                  f"{len(seen)} backtests", flush=True)

    pool = [s for s in seen.values()
            if s["rmse"] <= limit and np.isfinite(s["final"]) and np.isfinite(s["risk"])]
    kept = keep(pool, args.count)
    front = frontier(kept)
    chosen = picks(kept, front)
    print(f"{len(pool)} runs under the limit; kept {len(kept)}; {len(front)} on the frontier")
    print(f"{'':>9}{'pctl':>6}{'risk lvl':>9}{'risk':>9}{'final':>9}{'max dd':>9}{'sharpe':>8}{'rmse':>9}{'trades':>8}")
    for name, s in chosen.items():
        print(f"{name:>9}{s['percentile']:6d}{M.pct(s['risk_level'])}{M.pct(s['risk'])}{M.pct(s['final'])}"
              f"{M.pct(s['max_drawdown'])}{s['sharpe']:8.2f}{M.pct(s['rmse'])}{s['trades']:8d}"
              f"  lev {s['params']['leverage']:g}")

    out = {**saved, "rmse_limit": limit, "kept": kept, "frontier": front, "picks": chosen}
    M.save_json(path.with_name(f"{path.stem}_frontier.json"), out, indent=1)
    draw(kept, front, chosen, limit)


def by_pnl(args, path):
    """Climbs for the lowest score, keeps the --count lowest, and picks off their frontier.

    Returns the saved frontier with each pick's books on both trails and the wallet's, for pnl_chart.
    """
    saved = json.loads(path.read_text())
    if args.replay:
        saved = json.loads(path.with_name(f"{path.stem}_frontier_pnl.json").read_text())
    M.use_venue(saved.get("venue", "avantis"))
    M.vt.ACCOUNT = args.budget or saved.get("settings", {}).get("starting_balance", M.vt.ACCOUNT)
    start, end = saved["start"], saved["end"]
    lo, hi = min(start, LONG_START), saved.get("long", {}).get("end") or long_end()
    short_mk = M.load_markets(list(saved["markets"]), lo, end)
    long_mk = M.load_markets(list(saved["markets"]), LONG_START, hi)
    grid, long_grid = M.hour_grid(start, end), M.hour_grid(LONG_START, hi)
    opens = float(grid[0])  # the short trail opens nothing before the wallet's first day
    costs = M.market_costs(saved["markets"], (M.FEE, M.FEE))
    wallet, _ = M.W.run_avantis(M, saved["address"], times=grid)
    wallet_pnl = wallet["realized"].to_numpy(float)

    def runs(p):
        """The long trail's result and book, then the short trail's."""
        lr = M.backtest(p, long_mk, costs, hard_stop=LONG_STOP)
        sr = M.backtest({**p, "open_from": opens}, short_mk, costs)
        return (lr, M.mimic_book(lr, long_mk, long_grid, p["leverage"]),
                sr, M.mimic_book(sr, short_mk, grid, p["leverage"]))

    def draw(out):
        chosen = out["picks"]
        books = {n: runs(s["params"]) for n, s in chosen.items()}
        panel = {**out, "wallet": wallet, "long": {n: b[1] for n, b in books.items()},
                 "short": {n: b[3] for n, b in books.items()}, "name": f"{saved['address'][:8]}…"}
        page = HERE / f"mimic_frontier_{saved['address'][2:8].lower()}_pnl.html"
        pnl_chart(page, f"Mimic frontier, {saved['address'][:8]}…, weight {out['weight']:g}", [panel])
        print(f"wrote {page}")
        return panel

    if args.replay:
        return draw(saved)

    first = dict(saved["params"])
    if args.bound:
        from mimic_drawdown import cap_leverage, leverage_bound

        bound = leverage_bound(saved["markets"])
        first = cap_leverage(first, bound)
        print(f"leverage bound {bound:g}x; the fit's {saved['params']['leverage']:g}x starts at {first['leverage']:g}x")
    print(f"long trail {LONG_START} to {hi}; short trail {start} to {end}; weight {args.weight:g}")
    seen = {}

    def scores(p):
        key = M.effective(p)
        if key not in seen:
            lr, lb, sr, sb = runs(p)
            s = {**M.own_scores(lb), "trades": M.avbt_cpp.summary(lr)["trades"], "risk": daily_risk(lb),
                 "budget": M.min_budget(lr, costs), "short_trades": M.avbt_cpp.summary(sr)["trades"]}
            s["gap"], s["k"] = pnl_gap(np.zeros(len(grid)) if sb is None else sb["realized"], wallet_pnl)
            s["score"] = score(s, args.weight)
            seen[key] = {**s, "params": p}
        return seen[key]

    def ok(s):
        return (s["short_trades"] >= max(1, args.min_trades) and s["trades"] >= max(1, args.min_long_trades)
                and np.isfinite(s["final"]) and np.isfinite(s["risk"]))

    def goal(p):
        s = scores(p)
        return -s["score"] if ok(s) else -1e9

    rng = np.random.default_rng(0)
    starts = [first] + [{k: v[rng.integers(len(v))] for k, v in M.KNOBS.items()} for _ in range(args.restarts)]
    starts = [{k: v.item() if hasattr(v, "item") else v for k, v in p.items()} for p in starts]
    with ThreadPoolExecutor(os.cpu_count()) as pool:
        for i, best in enumerate(starts):
            best_g = goal(best)
            for _ in range(args.passes):
                before = best_g
                for a, b in itertools.combinations(M.KNOBS, 2):
                    tries = [{**best, a: x, b: y} for x in M.KNOBS[a] for y in M.KNOBS[b]]
                    list(pool.map(scores, tries))
                    for p in tries:
                        g = goal(p)
                        if g > best_g + 1e-12:
                            best, best_g = p, g
                if best_g <= before + 1e-6:
                    break
            s = scores(best)
            print(f"start {i}: score {s['score']:.3f}, gap {s['gap']:.3f}, return {s['final']:.1%}; "
                  f"{len(seen)} backtests", flush=True)

    # Settings that trade alike score alike: keep one of each result.
    same = {}
    for s in seen.values():
        if ok(s):
            same.setdefault((round(s["final"], 9), round(s["risk"], 9), s["trades"], round(s["gap"], 9)), s)
    pool = list(same.values())
    spread(pool)
    kept = sorted(pool, key=lambda s: s["score"])[:args.count]
    front = frontier(kept)
    chosen = picks(kept, front)
    print(f"{len(pool)} distinct results; kept the {len(kept)} lowest, score {kept[0]['score']:.3f} to "
          f"{kept[-1]['score']:.3f}; {len(front)} on the frontier")
    print(f"{'':>9}{'pctl':>6}{'risk lvl':>9}{'risk':>9}{'final':>9}{'max dd':>9}{'sharpe':>8}{'gap':>7}{'trades':>8}"
          f"  leverage, budget needed")
    for name, s in chosen.items():
        sh = f"{s['sharpe']:8.2f}" if s["sharpe"] == s["sharpe"] else f"{'-':>8}"
        print(f"{name:>9}{s['percentile']:6d}{M.pct(s['risk_level'])}{M.pct(s['risk'])}{M.pct(s['final'])}"
              f"{M.pct(s['max_drawdown'])}{sh}{s['gap']:7.3f}{s['trades']:8d}  lev {s['params']['leverage']:g}"
              f"  ${s['budget']:,.0f}")
    out = {**saved, "settings": {**saved.get("settings", {}), "starting_balance": M.vt.ACCOUNT},
           "by": "pnl", "weight": args.weight, "long": {"start": LONG_START, "end": hi, "hard_stop": LONG_STOP},
           "kept": kept, "frontier": front, "picks": chosen}
    M.save_json(path.with_name(f"{path.stem}_frontier_pnl.json"), out, indent=1)
    return draw(out)


def spread(pool):
    """Prints how far the gap and the return spread over the pool: what --weight trades between."""
    for key in ("gap", "final"):
        v = np.array([s[key] for s in pool], float)
        q = np.percentile(v, [10, 25, 50, 75, 90])
        print(f"  {key:>5}: 10/25/50/75/90th percentiles " + " ".join(f"{x:.3f}" for x in q))


if __name__ == "__main__":
    main()

"""Tune a saved mimic fit under max drawdown limits of 10%, 15% and 20%.

Starts from the fit mimic.py saved, then climbs two knobs at a time,
from that fit, the tighter limits' winners and --restarts random starts.
Until a run is under the limit, a lower drawdown is better. All limits
share one record of the runs done.

The goal (--goal): return, the highest final return on peak own capital;
fit, the highest Sharpe minus --weight times the RMSE to the wallet; pay,
the highest final return minus --weight times that RMSE. With --lift it
tunes at 10% only, then raises that winner's leverage 5% a step, keeping
the last step under each looser limit.

--step X (with --lift) raises leverage by X at a time instead, up to the
bound, and keeps the highest step under the limit that makes the same
number of trades as the run it lifts. --bound caps leverage at the
lowest maximum leverage of the fit's markets on Veranta (and 100),
floored to 0.5: knob values above it are dropped before the random
starts are drawn, and the fit's leverage starts at most at it.
--min-trades N scores a run with fewer than N trades as out of the
limit, so a single held position cannot pass. The AI
Copy v5 search is:

    python3 examples/mimic_drawdown.py --goal pay --weight 50 --lift --step 0.5 --bound \
        --min-trades 20 data/candles/mimic_<address>.json

Run from the repository root, after mimic.py:

    python3 examples/mimic_drawdown.py --goal pay --lift --weight 50 data/candles/mimic_<address>.json

Writes data/candles/mimic_<address>_<goal>..._<start>_dd<limit>.json beside it.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

import mimic as M

LIMITS = [0.10, 0.15, 0.20]


def floor_half(x):
    """x rounded down to a multiple of 0.5."""
    return float(np.floor(x * 2) / 2)


def leverage_bound(markets):
    """The lowest Veranta maximum leverage of the markets, and 100, floored to 0.5."""
    import requests

    top = {p["symbol"]: p["max_leverage"] for p in requests.get("https://verantacopy.com/api/pairs", timeout=60).json()}
    return floor_half(min([100.0] + [top[m] for m in markets]))


def cap_leverage(first, bound):
    """Drops leverage knob values above bound; the start's leverage at most bound."""
    M.KNOBS["leverage"] = [v for v in M.KNOBS["leverage"] if v <= bound]
    return {**first, "leverage": floor_half(min(first["leverage"], bound))}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("saved", help="a mimic_<address>.json from mimic.py")
    ap.add_argument("--passes", type=int, default=4)
    ap.add_argument("--restarts", type=int, default=3, help="random starts per limit")
    ap.add_argument("--start", help="first date; default the fit's")
    ap.add_argument("--end", help="end date; default the fit's")
    ap.add_argument("--goal", choices=["return", "fit", "pay"], default="return",
                    help="fit: highest Sharpe minus weight times RMSE to the wallet; pay: highest return minus weight times RMSE")
    ap.add_argument("--lift", action="store_true",
                    help="tune at the first limit only, then raise its leverage until each looser limit")
    ap.add_argument("--weight", type=float, default=1.0, help="RMSE weight for --goal fit")
    ap.add_argument("--step", type=float, help="with --lift: raise leverage by this, keeping the trade count")
    ap.add_argument("--bound", action="store_true", help="cap leverage at the markets' Veranta maximum")
    ap.add_argument("--min-trades", type=int, default=0, help="fewer trades scores as out of the limit")
    args = ap.parse_args()

    path = Path(args.saved)
    saved = json.loads(path.read_text())
    M.use_venue(saved.get("venue", "avantis"))
    start, end = args.start or saved["start"], args.end or saved["end"]
    markets = M.load_markets(list(saved["markets"]), start, end)
    grid = M.hour_grid(start, end)
    costs = {s: M.avbt_cpp.Costs(M.FEE, M.FEE) for s in saved["markets"]}
    target = M.load_target(path, grid) if args.goal != "return" else None
    first = dict(saved["params"])
    bound = 100.0
    if args.bound:
        bound = leverage_bound(saved["markets"])
        first = cap_leverage(first, bound)
        print(f"leverage bound {bound:g}x; the fit's {saved['params']['leverage']:g}x starts at {first['leverage']:g}x")
    seen = {}

    def scores(p):
        key = M.effective(p)
        if key not in seen:
            r = M.backtest(p, markets, costs)
            seen[key] = M.mimic_scores(r, markets, grid, target, p["leverage"])
        return seen[key]

    def goal(p, limit):
        s = scores(p)
        if not s["final"] == s["final"]:  # never trades
            return -2e9
        if args.goal == "fit":
            g = s["sharpe"] - args.weight * s["rmse"]
            g = g if g == g else -1e8
        elif args.goal == "pay":
            g = s["final"] - args.weight * s["rmse"]
        else:
            g = s["final"]
        ok = s["max_drawdown"] <= limit and s["trades"] >= args.min_trades
        return g if ok else -1e9 - s["max_drawdown"]

    print(f"{'limit':>6}{'final':>9}{'peak':>9}{'lowest':>9}{'max dd':>9}{'sharpe':>8}{'rmse':>9}{'trades':>8}")
    with ThreadPoolExecutor(os.cpu_count()) as runs:
        def climb(best, limit):
            best_g = goal(best, limit)
            for _ in range(args.passes):
                before = best_g
                for a, b in itertools.combinations(M.KNOBS, 2):
                    tries = [{**best, a: x, b: y} for x in M.KNOBS[a] for y in M.KNOBS[b]]
                    list(runs.map(scores, tries))
                    for p in tries:
                        g = goal(p, limit)
                        if g > best_g + 1e-12:
                            best, best_g = p, g
                if best_g <= before + 1e-6:
                    break
            return best, best_g

        # Each looser limit also starts from the tighter winners, so it never scores worse.
        rng = np.random.default_rng(0)
        randoms = [{k: v[rng.integers(len(v))] for k, v in M.KNOBS.items()} for _ in range(args.restarts)]
        randoms = [{k: v.item() if hasattr(v, "item") else v for k, v in p.items()} for p in randoms]
        winners = []
        for limit in LIMITS:
            if args.lift and winners and args.step:
                # Raise leverage by step up to the bound; keep the highest step under
                # the limit that trades as often as the run it lifts.
                src = winners[-1]
                best, n = src, scores(src)["trades"]
                for k in range(1, int(round((bound - src["leverage"]) / args.step)) + 1):
                    up = {**src, "leverage": round(src["leverage"] + k * args.step, 3)}
                    s = scores(up)
                    if s["max_drawdown"] <= limit and s["trades"] == n:
                        best = up
            elif args.lift and winners:
                # Raise leverage 5% a step; keep the last one under the limit.
                best = winners[-1]
                while best["leverage"] < 100:
                    up = {**best, "leverage": round(min(100, best["leverage"] * 1.05), 3)}
                    if not scores(up)["max_drawdown"] <= limit:  # NaN: it never trades
                        break
                    best = up
            else:
                starts = [first] + winners + randoms
                best, _ = max((climb(p, limit) for p in starts), key=lambda x: x[1])
            winners.append(best)
            s = scores(best)
            sh = f"{s['sharpe']:8.2f}" if s["sharpe"] == s["sharpe"] else f"{'-':>8}"
            print(f"{limit:6.0%}{M.pct(s['final'])}{M.pct(s['peak'])}{M.pct(s['lowest'])}"
                  f"{M.pct(s['max_drawdown'])}{sh}{M.pct(s['rmse'])}{s['trades']:8d}  lev {best['leverage']:g}", flush=True)
            out = {**saved, "params": best, "mimic": s, "drawdown_limit": limit,
                   "start": start, "end": end}
            M.save_json(path.with_name(f"{path.stem}_{args.goal}{'_lift' if args.lift else ''}{f'_step{args.step:g}' if args.step else ''}{'_bound' if args.bound else ''}{f'_t{args.min_trades}' if args.min_trades else ''}{'' if args.weight == 1 else f'_w{args.weight:g}'}_{start}_dd{round(limit * 100)}.json"), out, indent=1)
    print(f"{len(seen)} backtests")


if __name__ == "__main__":
    main()

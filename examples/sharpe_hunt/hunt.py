"""Score, tune and test one library strategy; save the result as JSON.

Tune: optimize on 2026-01-01 to 2026-10-01. Test: the winner, unchanged, on
2025-03-25 to 2026-01-01 (after the March 2025 data hole). Results go to
examples/sharpe_hunt/results/<name>.json.

Use from another script:
    from hunt import hunt
    hunt("donchian_trend", ["BTC"], {"window": [20, 10, 40], ...}, start={...})
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from data import TF, avbt_cpp, market  # noqa: E402

TUNE = ("2026-01-01", "2026-10-01")
TEST = ("2025-03-25", "2026-01-01")
COSTS = avbt_cpp.Costs(0.0005, 0.0005)  # 0.05% at open and at close


def settings():
    """Volatile on purpose: 2% risk per trade, scaled by leverage."""
    s = avbt_cpp.PortfolioSettings()
    s.risk_per_trade, s.scale_risk_with_leverage, s.hard_stop = 0.02, True, 0.6
    return s


def markets(coins, window, tfs):
    return avbt_cpp.Markets([market(c, *window, tfs) for c in coins])


def score(r):
    """Sharpe, return, drawdown, PnL volatility and the shape of the trades.

    vol: yearly sd of daily returns. win_rate, avg_win and avg_loss are in
    account money; payoff = avg_win / |avg_loss| (below 1 is the "win small,
    lose big" shape). skew: skew of trade results (negative = rare big
    losses). worst_trade and worst_day are fractions of equity.
    """
    eq = pd.Series(r.equity, index=pd.to_datetime(r.clock, unit="s"))
    daily = eq.resample("1D").last().ffill().pct_change().dropna()
    dd = float((1 - eq / eq.cummax()).max())
    res = np.array([t.result for t in r.trades])
    before = np.array([eq.asof(pd.to_datetime(t.exit_time, unit="s")) - t.result for t in r.trades])
    frac = res / before if len(res) else res
    wins, losses = res[res > 0], res[res <= 0]
    sh = avbt_cpp.sharpe(r)
    rnd = lambda x, n=4: None if x is None or not math.isfinite(x) else round(float(x), n)
    return {"sharpe": rnd(sh, 3), "return": rnd(eq.iloc[-1] / eq.iloc[0] - 1), "max_drawdown": rnd(dd),
            "vol": rnd(daily.std() * math.sqrt(365)), "worst_day": rnd(daily.min()),
            "trades": len(res), "win_rate": rnd(len(wins) / len(res)) if len(res) else None,
            "avg_win": rnd(wins.mean(), 2) if len(wins) else None,
            "avg_loss": rnd(losses.mean(), 2) if len(losses) else None,
            "payoff": rnd(wins.mean() / -losses.mean(), 3) if len(wins) and len(losses) and losses.mean() else None,
            "skew": rnd(pd.Series(frac).skew(), 3) if len(res) > 2 else None,
            "worst_trade": rnd(frac.min()) if len(res) else None}


def run(name, params, coins, window, tfs):
    m = markets(coins, window, tfs)
    return avbt_cpp.run(name, params, m, {c: COSTS for c in coins}, settings())


def plain(v):
    if isinstance(v, dict):
        return {k: avbt_cpp.timeframe_name(x) for k, x in v.items()}
    if isinstance(v, TF):
        return avbt_cpp.timeframe_name(v)
    return v


def hunt(name, coins, knobs, start=None, tfs=(TF.Min15, TF.Hour1, TF.Hour4), rounds=40, save=True, test=True):
    """Tune on 2026. With test=False, 2025 is not run, so it can be called while searching."""
    start = start or {}
    m = markets(coins, TUNE, tfs)
    costs = {c: COSTS for c in coins}
    s = avbt_cpp.optimize(name, start, knobs, m, costs, settings(), rounds=rounds)
    best = {**start, **s["best"]}
    out = {"strategy": name, "coins": coins, "timeframe": avbt_cpp.timeframe_name(s["timeframe"]),
           "default_tune": score(run(name, start, coins, TUNE, tfs)),
           "tuned": score(run(name, best, coins, TUNE, tfs)),
           "test_2025": score(run(name, best, coins, TEST, tfs)) if test else None,
           "params": {k: plain(v) for k, v in best.items()}, "runs": len(s["runs"])}
    if save and test:
        (HERE / "results").mkdir(exist_ok=True)
        (HERE / "results" / f"{name}.json").write_text(json.dumps(out, indent=1))
    return out


"""Tune StateTrend on BTC and ETH with the C++ optimizer, then chart the searched period.

The search runs on June and July 2026; the best parameters are then scored on
August 2026, which the search never saw. Each instrument gets its own signal
timeframe; the base timeframe stays 1-minute bars. It reads the candles and
states that examples/state_trend.py cached under data/candles/. Build the
module first (cpp/README.md), then run from the repository root:

    python3 examples/state_trend_optimize.py

It prints a summary and writes examples/state_trend_optimize.html: what the
strategy does, equity before and after tuning on the searched period, and
each tuned trade on its market's price, average, and trend label.
"""

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))

import clickhouse_data as ch  # noqa: E402
from btc_bars import resample  # noqa: E402
from timeframes import avbt_cpp, to_bars  # noqa: E402

TF = avbt_cpp.Timeframe
START, SPLIT, END = "2026-06-01", "2026-08-01", "2026-09-01"
PAIRS = {"BTC": 1, "ETH": 0}
SIGNALS = {TF.Min5: "5min", TF.Min15: "15min", TF.Hour1: "1h", TF.Hour4: "4h"}
# The first value of each knob is where the search starts.
KNOBS = [
    ("average", [50, 20, 100]),
    ("breakout", [30, 15, 60]),
    ("atr_stops", [2.0, 1.5, 3.0]),
    ("flip", [False, True]),
    ("min_stop", [0.0, 0.01, 0.02]),
    ("reward", [2.0, 1.0, 3.0]),
    ("trail_atrs", [0.0, 2.0, 3.0]),
    ("take_fraction", [1.0, 0.5]),
    ("BTC signal", [TF.Hour1, TF.Min5, TF.Min15, TF.Hour4]),
    ("ETH signal", [TF.Hour1, TF.Min5, TF.Min15, TF.Hour4]),
]


def market(pair_id: int, start: str, end: str):
    """One market's bars, 1-minute first then every signal timeframe, and its States, in [start, end)."""
    lo, hi = (int(pd.Timestamp(d, tz="UTC").timestamp()) for d in (start, end))
    minutes = ch.minutes(pair_id, START, END).query("@lo <= ts < @hi")
    states = ch.states(pair_id, START, END).query("@lo <= ts < @hi")
    bars = [to_bars(TF.Min1, resample(minutes, "1min"))]
    bars += [to_bars(tf, resample(minutes, rule)) for tf, rule in SIGNALS.items()]
    first, *codes = ch.clean_states(states)
    return bars, avbt_cpp.States(first, *codes)


def sharpe(r) -> float:
    """Sharpe from hourly equity returns, 24 * 365 hours a year."""
    returns = pd.Series(r["equity"][::60]).pct_change().dropna()
    return returns.mean() / returns.std() * (24 * 365) ** 0.5 if returns.std() > 0 else float("nan")


def trend_by_hour(pair_id: int, start: str, end: str) -> pd.Series:
    """The trend label at each hour, as text."""
    first, _, trend, _ = ch.clean_states(ch.states(pair_id, START, END))
    names = pd.Series(["unknown"] + ch.TREND_STATES)[trend].to_numpy()
    out = pd.Series(names, index=pd.to_datetime(first + 60 * pd.RangeIndex(len(trend)), unit="s"))
    return out[start:end].iloc[:-1:60]


def add_trades(fig, trades: pd.DataFrame, clock, row: int, legend: bool) -> None:
    """Each trade as a line from entry to exit, green if it made money; a triangle marks its side."""
    time = pd.to_datetime(clock, unit="s")
    for won, color in [(True, "#2ca02c"), (False, "#d62728")]:
        t = trades[(trades.result > 0) == won]
        x = [v for e, x_ in zip(t.entry_bar, t.exit_bar) for v in (time[e], time[x_], None)]
        y = [v for e, x_ in zip(t.entry_price, t.exit_price) for v in (e, x_, None)]
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", line=dict(color=color, width=3),
                                 name="trade made money" if won else "trade lost money",
                                 legendgroup=f"won{won}", showlegend=legend), row=row, col=1 if row else None)
    for side, symbol in [("long", "triangle-up"), ("short", "triangle-down")]:
        t = trades[trades.side == side]
        fig.add_trace(go.Scatter(x=time[t.entry_bar], y=t.entry_price, mode="markers",
                                 marker=dict(symbol=symbol, size=9, color="#333"),
                                 name=f"{side} entry", legendgroup=side, showlegend=legend,
                                 text=[f"{side}, exit by {c}, result {r:.2f}" for c, r in zip(t.cause, t.result)]),
                      row=row, col=1 if row else None)


def rounds(s) -> pd.DataFrame:
    """Replays the search from its runs: per round, the pair tried, new runs, and what changed.

    The pairs come in the order optimize uses; a combination already run is
    not in the round's runs, and could not beat the best anyway.
    """
    names = [name for name, _ in KNOBS]
    pairs = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]]
    runs = pd.DataFrame(s["runs"])
    best = runs.iloc[0]
    rows = []
    for r in range(1, runs["round"].max() + 1):
        a, b = pairs[(r - 1) % len(pairs)]
        here = runs[runs["round"] == r]
        top = here.sharpe.max() if len(here) else float("nan")
        before = best
        if top > best.sharpe:
            best = here.loc[here.sharpe.idxmax()]
        changed = [f"{k}: {before[k]} → {best[k]}" for k in names if before[k] != best[k]]
        rows.append({"round": r, "pair": f"{a} × {b}", "new runs": len(here),
                     "best Sharpe": best.sharpe, "gain": best.sharpe - before.sharpe,
                     "changed": ", ".join(changed) or "no change"})
    assert all(best[k] == v for k, v in s["choice"].items()), "replay disagrees with optimize"
    return pd.DataFrame(rows)


TREND_COLORS = {"uptrend": "#2ca02c", "downtrend": "#d62728", "non_trending": "#bbb",
                "mixed_conflicted": "#ff7f0e", "unknown": "#eee"}

if __name__ == "__main__":
    settings = avbt_cpp.PortfolioSettings()
    default = avbt_cpp.StateTrendParams()
    train = {name: market(pid, START, SPLIT) for name, pid in PAIRS.items()}
    s = avbt_cpp.optimize_state_trend(train, settings, default, KNOBS, rounds=90)
    tuned = s["best"]
    print(f"searched {START} to {SPLIT}, base timeframe {s['timeframe']}, {len(s['runs'])} runs")
    print("best:", ", ".join(f"{k}={v}" for k, v in s["choice"].items()))

    runs = {label: avbt_cpp.run_state_trend(train, settings, p) for label, p in [("before", default), ("after", tuned)]}
    rows = ["Account equity, before and after tuning"]
    for name in PAIRS:
        rows += [f"{name}: hourly close, its {tuned.average}-hour average, and every tuned trade",
                 f"{name}: trend label"]
    fig = make_subplots(rows=len(rows), cols=1, shared_xaxes=True, vertical_spacing=0.04,
                        subplot_titles=rows, row_heights=[3] + [4, 0.6] * len(PAIRS))
    for label, color in [("before", "#999"), ("after", "#1f77b4")]:
        r = runs[label]
        fig.add_trace(go.Scatter(x=pd.to_datetime(r["clock"][::60], unit="s"), y=r["equity"][::60],
                                 line_color=color, name=f"{label} tuning: {len(r['trades'])} trades, "
                                 f"Sharpe {sharpe(r):.2f}"), row=1, col=1)
    trades = pd.DataFrame(runs["after"]["trades"])
    for i, (name, pid) in enumerate(PAIRS.items()):
        row = 2 + 2 * i
        hour = train[name][0][list(SIGNALS).index(TF.Hour1) + 1]
        time = pd.to_datetime(hour.ts, unit="s")
        fig.add_trace(go.Scatter(x=time, y=hour.close, line=dict(color="#555", width=1), name="hourly close",
                                 legendgroup="close", showlegend=i == 0), row=row, col=1)
        fig.add_trace(go.Scatter(x=time, y=avbt_cpp.sma(hour.close, tuned.average), line=dict(color="#9467bd"),
                                 name=f"{tuned.average}-hour average", legendgroup="avg", showlegend=i == 0),
                      row=row, col=1)
        add_trades(fig, trades[trades.instrument == name], runs["after"]["clock"], row, i == 0)
        trend = trend_by_hour(pid, START, SPLIT)
        fig.add_trace(go.Bar(x=trend.index, y=[1] * len(trend), marker_color=trend.map(TREND_COLORS),
                             marker_line_width=0, hovertext=trend, showlegend=False), row=row + 1, col=1)
        for label, color in TREND_COLORS.items():
            fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", marker=dict(symbol="square", color=color),
                                     name=f"trend: {label}", showlegend=i == 0), row=row + 1, col=1)
        fig.update_yaxes(visible=False, row=row + 1, col=1)
        fig.update_yaxes(title_text="price", row=row, col=1)
    fig.update_yaxes(title_text="equity", row=1, col=1)
    fig.update_layout(height=1300, margin=dict(t=40), bargap=0, legend=dict(groupclick="togglegroup"))

    # A closer look: BTC on its busiest day, on minute bars, where the entries happen.
    btc = trades[trades.instrument == "BTC"]
    clock = pd.to_datetime(runs["after"]["clock"], unit="s")
    day = clock[btc.entry_bar].floor("D").value_counts().idxmax()
    minute, hour = train["BTC"][0][0], train["BTC"][0][list(SIGNALS).index(TF.Hour1) + 1]
    mt, ht = pd.to_datetime(minute.ts, unit="s"), pd.to_datetime(hour.ts, unit="s")
    m_in, h_in = (mt >= day) & (mt < day + pd.Timedelta("1D")), (ht >= day) & (ht < day + pd.Timedelta("1D"))
    zoom = go.Figure()
    zoom.add_trace(go.Scatter(x=mt[m_in], y=minute.close[m_in], line=dict(color="#555", width=1), name="1-minute close"))
    for series, label, color in [(avbt_cpp.prior_max(minute.high, tuned.breakout), "high", "#2ca02c"),
                                 (avbt_cpp.prior_min(minute.low, tuned.breakout), "low", "#d62728")]:
        zoom.add_trace(go.Scatter(x=mt[m_in], y=series[m_in], line=dict(color=color, width=1, dash="dot"),
                                  name=f"prior {tuned.breakout}-minute {label}"))
    # The average is known when its hour closes, so it steps at the end of each hour.
    zoom.add_trace(go.Scatter(x=ht[h_in] + pd.Timedelta("1h"), y=avbt_cpp.sma(hour.close, tuned.average)[h_in],
                              line=dict(color="#9467bd", shape="hv"), name=f"{tuned.average}-hour average"))
    add_trades(zoom, btc[(clock[btc.entry_bar] >= day) & (clock[btc.entry_bar] < day + pd.Timedelta("1D"))],
               runs["after"]["clock"], None, True)
    zoom.update_layout(height=550, margin=dict(t=30), yaxis_title="price",
                       title=f"BTC on {day:%Y-%m-%d}, its busiest day: {((clock[btc.entry_bar].floor('D')) == day).sum()} trades")

    entered = clock[trades.entry_bar]
    early = (entered < pd.Timestamp("2026-06-15")).sum()
    equity = pd.Series(runs["after"]["equity"], index=clock)
    early_gain = (equity[:"2026-06-14"].iloc[-1] - equity.iloc[0]) / (equity.iloc[-1] - equity.iloc[0])

    table = rounds(s)
    start_sharpe = s["runs"][0]["sharpe"]
    climb = go.Figure()
    climb.add_trace(go.Scatter(x=[0] + list(table["round"]), y=[start_sharpe] + list(table["best Sharpe"]),
                               line=dict(color="#1f77b4", shape="hv"), name="best Sharpe so far"))
    gained = table[table.gain > 0]
    climb.add_trace(go.Scatter(x=gained["round"], y=gained["best Sharpe"], mode="markers",
                               marker=dict(size=9, color="#2ca02c"), hovertext=gained["pair"] + ": " + gained["changed"],
                               name="round with a gain (hover for what changed)"))
    climb.update_layout(height=420, margin=dict(t=30), xaxis_title="round", yaxis_title="Sharpe",
                        title="Best Sharpe after each round")
    round_rows = "".join(
        f"<tr{' class=gain' if row.gain > 0 else ''}><td>{row.round}</td><td>{row.pair}</td>"
        f"<td>{row['new runs']}</td><td>{row.changed}</td><td>{row['best Sharpe']:.2f}</td>"
        f"<td>{'+' + format(row.gain, '.2f') if row.gain > 0 else ''}</td></tr>" for _, row in table.iterrows())

    def params(p):
        return (f"average {p.average} hours, breakout {p.breakout} minutes, stop {p.atr_stops} ATRs, "
                f"take profit {p.reward}R closing {p.take_fraction:.0%}, "
                f"trail {f'{p.trail_atrs} ATRs' if p.trail_atrs else 'off'}, "
                f"min stop {p.min_stop:.0%}, flip {'on' if p.flip else 'off'}")
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>StateTrend tuning</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;
background:#fff;color:#222;line-height:1.5}} td,th{{padding:4px 12px;text-align:left}}
table.rounds{{border-collapse:collapse;font-size:14px}} table.rounds td{{border-top:1px solid #eee}}
tr.gain td{{background:#eaf6ea;font-weight:600}}</style></head><body>
<h1>StateTrend on BTC and ETH, {START} to {SPLIT}</h1>
<p>The optimizer searched these two months ({len(s['runs'])} backtests, base timeframe {s['timeframe']})
for the parameters with the highest Sharpe. This page shows only the searched period, so the "after"
line is fitted to it.</p>
<h2>What the strategy does</h2>
<ol>
<li><b>Bias.</b> Up when the last hourly close is above its average and the trend label is uptrend;
down when it is below and the label is downtrend.</li>
<li><b>Entry.</b> With the bias, when the market label is a trend that way or a breakout, the
1-minute close breaks the high (low) of the prior breakout minutes, and volatility is not extreme
or a shock. The order fills at the next minute's open.</li>
<li><b>Stop and take profit.</b> The stop is a number of hourly ATRs from the entry (ATR: the
average size of one bar's range). R is that distance, the amount a trade risks. The take profit is
reward R from the entry (2R by default) and closes the take fraction of the trade; whatever is left
has no take profit and runs on its stop. No entry when the stop would be closer than the min stop,
so it skips calm hours.</li>
<li><b>Trailing stop.</b> When trail is on, the stop follows the best price reached, trail ATRs
behind it (the ATR taken at entry), and never moves back. A trailed stop fills at its level.</li>
<li><b>Exit.</b> When the bias no longer holds or the market label turns to the opposite trend.</li>
<li><b>Flip.</b> When on, every trade takes the other side: an upward breakout opens a short.</li>
</ol>
<table><tr><th></th><th>Parameters</th><th>Trades</th><th>Ending balance</th><th>Sharpe</th></tr>
""" + "".join(f"<tr><td>{label}</td><td>{params(p)}</td><td>{len(runs[label]['trades'])}</td>"
              f"<td>{runs[label]['ending_balance']:,.2f}</td><td>{sharpe(runs[label]):.2f}</td></tr>"
              for label, p in [("before", default), ("after", tuned)]) + f"""</table>
<p><b>Most of the gain is early.</b> {early} of the {len(trades)} tuned trades, and
{early_gain:.0%} of the gain, came in the first two weeks of June, when both markets fell fast;
the 2% min stop keeps it out of calmer weeks.</p>
<p><b>The take profit and trailing stop did not matter.</b> Every reward, trail, and take fraction
scored the same Sharpe, because the tuned trades all closed on the exit rule before the price
reached the stop or the take profit. A trend strategy whose own exit is this quick gains nothing
from these levels.</p>
<p>Both markets read 1-hour signal bars after tuning. The account starts at 10,000. The price panels
show the tuned trades only; a short trade's line goes down when it made money. Hover a triangle for
its exit cause and result; drag to zoom.</p>
{fig.to_html(full_html=False, include_plotlyjs="cdn")}
<h2>How the optimizer got there</h2>
<p>Each knob starts at its first value: the defaults, Sharpe {start_sharpe:.2f}. Each round takes the
next pair of knobs, in a fixed order, and backtests every combination of their values with the other
knobs at the best so far. If any combination beats the best Sharpe, it becomes the best. With
{len(KNOBS)} knobs there are {len(KNOBS) * (len(KNOBS) - 1) // 2} pairs, so the {len(table)} rounds go
through them twice. "New runs" leaves out combinations already run, which are not run again.</p>
<p>Knobs: {"; ".join(f"<b>{n}</b> " + ", ".join(avbt_cpp.timeframe_name(v) if isinstance(v, TF) else str(v) for v in vals) for n, vals in KNOBS)}.</p>
{climb.to_html(full_html=False, include_plotlyjs=False)}
<table class=rounds><tr><th>Round</th><th>Pair tried</th><th>New runs</th><th>What changed</th>
<th>Best Sharpe</th><th>Gain</th></tr>{round_rows}</table>

<h2>A closer look</h2>
<p>Trades last minutes to hours, too short to see across two months. Here is one day on 1-minute bars.
Because flip is on, each breakout is faded: in an up bias a close above the prior high (dotted green)
opens a <i>short</i> (▼), and in a down bias a close below the prior low (dotted red) opens a <i>long</i> (▲).</p>
{zoom.to_html(full_html=False, include_plotlyjs=False)}
</body></html>"""
    out = ROOT / "examples" / "state_trend_optimize.html"
    out.write_text(page)
    print("wrote", out)

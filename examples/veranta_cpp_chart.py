"""Chart the C++ results: the five Veranta strategies, and strategies 1 and 2 combined.

Each strategy gets a summary row and a chart: the close with every trade's
entry and exit, and the account's equity at every bar's close. The bars are
the ones examples/veranta_rules_cpp.py builds from the cached candles.

Strategies 1 and 2 both trade ZORA. Combined, they share one account and one
ZORA position: the strategy that opens it owns it, and the other's entries
wait until it closes. The combined run uses strategy 2's window, which holds
strategy 1's.

Build the module first (see veranta_rules_cpp.py), then run from the root:

    python3 examples/veranta_cpp_chart.py

It writes examples/veranta_cpp_chart.html.
"""

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import veranta_rules_cpp as vc

avbt_cpp, vr = vc.avbt_cpp, vc.vr
OUT = Path(__file__).with_suffix(".html")
CAUSE_COLOR = {"take_profit": "#1a9850", "stop": "#d73027", "order": "#4575b4",
               "liquidation": "#000000", "hard_stop": "#000000"}


def runs():
    """(title, {instrument: bars dataframe}, C++ result) for each chart."""
    out = []
    for rule in vr.RULES:
        wallet, market, source, symbol, make = rule
        df = vc.load(wallet, market, source, symbol)
        name = market.split("/")[0].replace("XAU", "GOLD")
        result = vc.run(vc.CPP[make], {name: vc.cpp_bars(df)}, {"instrument": name})
        out.append((make.__name__, {name: df}, result))
    rally = next(r for r in vr.RULES if r[4] is vr.rally_short)
    zora = vc.load(*rally[:4])
    both = vc.run("late_day_and_rally", {"ZORA": vc.cpp_bars(zora)})
    out.append(("late_day_short + rally_short", {"ZORA": zora}, both))
    return out


def summary(title, frames, r):
    trades = vc.trade_rows(r)
    equity = pd.Series(r.equity)
    drawdown = (equity / equity.cummax() - 1).min()
    # Mean over standard deviation of the bar-to-bar equity returns, times
    # the square root of the bars in a year. Risk-free rate 0.
    returns = equity.pct_change().dropna()
    per_year = 365 * 24 * 3600 / avbt_cpp.timeframe_seconds(r.timeframe)
    sharpe = returns.mean() / returns.std() * per_year ** 0.5 if returns.std() > 0 else float("nan")
    causes = pd.Series([t["cause"] for t in trades]).value_counts().to_dict()
    wins = sum(t["result"] > 0 for t in trades)
    df = next(iter(frames.values()))
    return {
        "strategy": title,
        "market": ", ".join(frames),
        "from": pd.to_datetime(df.ts.iloc[0], unit="s").strftime("%Y-%m-%d"),
        "to": pd.to_datetime(df.ts.iloc[-1], unit="s").strftime("%Y-%m-%d"),
        "bars": len(df),
        "trades": len(trades),
        "win rate": f"{wins / len(trades):.0%}" if trades else "-",
        "ending balance": f"{r.ending_balance:,.2f}",
        "return": f"{r.ending_balance / 10000 - 1:+.2%}",
        "max drawdown": f"{drawdown:.2%}",
        "sharpe": f"{sharpe:.2f}",
        "exits": ", ".join(f"{k} {v}" for k, v in sorted(causes.items())),
    }


def figure(title, frames, r):
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.6, 0.4],
                        vertical_spacing=0.04, subplot_titles=("close and trades", "equity"))
    for name, df in frames.items():
        time = pd.to_datetime(df.ts, unit="s")
        fig.add_trace(go.Scatter(x=time, y=df.close, name=f"{name} close",
                                 line=dict(color="#888", width=1)), 1, 1)
        mine = [t for t in vc.trade_rows(r) if t["instrument"] == name]
        fig.add_trace(go.Scatter(
            x=[time[t["entry_bar"]] for t in mine], y=[t["entry_price"] for t in mine],
            mode="markers", name="entry", marker=dict(symbol="triangle-down", size=8, color="#333"),
        ), 1, 1)
        for cause, color in CAUSE_COLOR.items():
            some = [t for t in mine if t["cause"] == cause]
            if some:
                fig.add_trace(go.Scatter(
                    x=[time[t["exit_bar"]] for t in some], y=[t["exit_price"] for t in some],
                    mode="markers", name=f"exit: {cause}", marker=dict(size=7, color=color),
                ), 1, 1)
    time = pd.to_datetime(next(iter(frames.values())).ts, unit="s")
    fig.add_trace(go.Scatter(x=time, y=r.equity, name="equity",
                             line=dict(color="#4575b4", width=1.5)), 2, 1)
    fig.update_layout(title=title, height=600, template="plotly_white",
                      legend=dict(orientation="h", y=-0.08))
    return fig


def main():
    results = runs()
    table = pd.DataFrame([summary(*r) for r in results]).to_html(index=False, border=0)
    charts = [figure(*r).to_html(full_html=False, include_plotlyjs="cdn" if i == 0 else False)
              for i, r in enumerate(results)]
    OUT.write_text(f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Veranta strategies in C++</title>
<style>body{{font-family:system-ui,sans-serif;margin:24px;background:#fff;color:#222}}
table{{border-collapse:collapse;font-size:13px}}td,th{{padding:4px 10px;text-align:left;border-bottom:1px solid #ddd}}</style>
</head><body>
<h1>Veranta strategies in C++</h1>
<p>Starting balance 10,000; 1% of the balance risked per trade at its stop; fee 1 basis point of
notional at every fill; leverage 1. Equity is the balance plus open positions valued at each bar's close.
Sharpe is the mean of the hourly equity returns divided by their standard deviation,
times the square root of 8,760 (the hours in a year; these markets trade every hour), with a
risk-free rate of 0.
The last chart copies strategies 1 and 2 into one strategy on one ZORA account: the strategy that
opens the position owns it, and the other's entries wait until it closes.</p>
{table}
{''.join(charts)}
</body></html>""")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()

"""Write an HTML report for Breakout-30 on two daily CSV files.

The backtest is the one in examples/breakout30.py. Each fill pays the fee
named there. From the repository root:

    python3 examples/breakout30_report.py btc.csv eth.csv -o report.html
"""

import math
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest import backtest
from breakout30 import BAR_SIZE, CHANNEL_DAYS, FEE, STOP_ATR, breakout, load

PAPER = "#e7eef0"
SHEET = "#f7fafb"
INK = "#1c2834"
MUTED = "#4d6270"
GRID = "#d3dee3"
TEAL = "#0e7c66"
LOSS = "#8f3d45"
NAVY = "#1f4e79"


def _betacf(a, b, x):
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-300:
        d = 1e-300
    d = 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-14:
            return h
    return h


def regularized_beta(a, b, x):
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_bt = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    front = math.exp(log_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def two_sided_p(t_stat, df):
    """Two-sided p-value for a Student t statistic."""
    x = df / (df + t_stat * t_stat)
    return regularized_beta(df / 2.0, 0.5, x)


def t_critical(df):
    """Two-sided 5% critical value, found by bisection."""
    lo, hi = 0.0, 20.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if two_sided_p(mid, df) > 0.05:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _check_table():
    # Published two-sided 5% points: df 10 → 2.228, df 60 → 2.000.
    if abs(two_sided_p(2.228, 10) - 0.05) > 0.001:
        raise RuntimeError("t table")
    if abs(t_critical(10) - 2.228) > 0.01:
        raise RuntimeError("t critical 10")
    if abs(t_critical(60) - 2.000) > 0.02:
        raise RuntimeError("t critical 60")


def trade_return(trade, fee):
    """Account return of one closed trade, after the fee on both fills."""
    if trade.side == "long":
        gross = trade.exit_price / trade.entry_price
    else:
        gross = 1.0 + (trade.entry_price - trade.exit_price) / trade.entry_price
    if trade.cause == "still open":
        return gross - 1.0
    return gross * (1.0 - fee) * (1.0 - fee) - 1.0


def equity_path(bars, trades, fee):
    """Daily marked stake. A close uses the trade's exit price, not that day's close."""
    by_entry = {trade.entry_bar: trade for trade in trades}
    stake = 1.0
    open_trade = None
    entry_stake = None
    path = []
    for i, bar in enumerate(bars):
        if open_trade is None and i in by_entry:
            open_trade = by_entry[i]
            stake *= 1.0 - fee
            entry_stake = stake
        if open_trade is not None and i == open_trade.exit_bar:
            if open_trade.side == "long":
                stake = entry_stake * (open_trade.exit_price / open_trade.entry_price)
            else:
                stake = entry_stake * (
                    1.0
                    + (open_trade.entry_price - open_trade.exit_price)
                    / open_trade.entry_price
                )
            if open_trade.cause != "still open":
                stake *= 1.0 - fee
            open_trade = None
            path.append(stake)
        elif open_trade is not None:
            if open_trade.side == "long":
                mark = entry_stake * (bar.close / open_trade.entry_price)
            else:
                mark = entry_stake * (
                    1.0
                    + (open_trade.entry_price - bar.close) / open_trade.entry_price
                )
            path.append(mark)
        else:
            path.append(stake)
    return path


def max_drawdown(path):
    peak = path[0]
    worst = 0.0
    for value in path:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1.0)
    return worst


def summarize(name, channel, days, bars, skipped, fee, stop=STOP_ATR):
    result = backtest(breakout(channel, stop), bars, fee=fee, bar_size=BAR_SIZE)
    path = equity_path(bars, result.trades, fee)
    if abs(path[-1] - result.ending_stake) > 1e-9:
        raise RuntimeError((name, channel, path[-1], result.ending_stake))
    returns = [trade_return(trade, fee) for trade in result.trades]
    n = len(returns)
    mean = sum(returns) / n
    var = sum((item - mean) ** 2 for item in returns) / (n - 1)
    std = math.sqrt(var)
    se = std / math.sqrt(n)
    t_stat = mean / se
    df = n - 1
    p_value = two_sided_p(t_stat, df)
    crit = t_critical(df)
    half = crit * se
    winners = [item for item in returns if item > 0]
    losers = [item for item in returns if item <= 0]
    in_market = 0
    for trade in result.trades:
        in_market += trade.exit_bar - trade.entry_bar + 1
    years = {}
    for i, day in enumerate(days):
        year = day[:4]
        years.setdefault(year, [path[i], path[i]])
        years[year][1] = path[i]
    year_rows = []
    previous = 1.0
    for year in sorted(years):
        end = years[year][1]
        year_rows.append({"year": year, "return": end / previous - 1.0})
        previous = end
    drawdowns = []
    peak = path[0]
    for value in path:
        peak = max(peak, value)
        drawdowns.append(value / peak - 1.0)
    return {
        "name": name,
        "channel": channel,
        "fee": fee,
        "skipped": skipped,
        "start": days[0],
        "end": days[-1],
        "days": days,
        "n_bars": len(days),
        "n": n,
        "longs": sum(trade.side == "long" for trade in result.trades),
        "shorts": sum(trade.side == "short" for trade in result.trades),
        "stops": sum(trade.cause == "stop loss" for trade in result.trades),
        "caps": sum(trade.cause == "time exit" for trade in result.trades),
        "mean": mean,
        "std": std,
        "t": t_stat,
        "df": df,
        "p": p_value,
        "lo": mean - half,
        "hi": mean + half,
        "win_rate": len(winners) / n,
        "avg_win": sum(winners) / len(winners) if winners else 0.0,
        "avg_loss": sum(losers) / len(losers) if losers else 0.0,
        "ending": result.ending_stake,
        "drawdown": max_drawdown(path),
        "time_in_market": in_market / len(days),
        "path": path,
        "drawdowns": drawdowns,
        "trades": [
            {
                "entry": days[trade.entry_bar],
                "exit": days[trade.exit_bar],
                "side": trade.side,
                "cause": trade.cause,
                "return": trade_return(trade, fee),
            }
            for trade in result.trades
        ],
        "years": year_rows,
    }


def _fmt_p(p_value):
    if p_value < 0.001:
        return "< 0.001"
    return f"{p_value:.3f}"


def _bps(value):
    return f"{value * 1e4:+.0f}"


def _pct(value):
    return f"{value * 100:+.1f}%"


def _date(day):
    return datetime.strptime(day, "%Y-%m-%d")


def _x_scale(days, left, width):
    start = _date(days[0])
    end = _date(days[-1])
    span = max((end - start).days, 1)

    def xpos(day):
        return left + (_date(day) - start).days / span * width

    return xpos, start.year, end.year


def _y_scale(values, top, height, include):
    low = min(min(values), min(include))
    high = max(max(values), max(include))
    if high == low:
        high += 1.0
    pad = (high - low) * 0.08
    low -= pad
    high += pad

    def ypos(value):
        return top + (high - value) / (high - low) * height

    return ypos, low, high


def _year_lines(days, xpos, top, height):
    parts = []
    seen = set()
    for day in days:
        year = day[:4]
        near_new_year = day[5:7] == "01" and day[8:10] <= "07"
        if year in seen or not near_new_year:
            continue
        seen.add(year)
        x = xpos(day)
        parts.append(
            f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + height}" stroke="{GRID}"/>'
            f'<text x="{x + 4:.1f}" y="{top + height + 16}" fill="{MUTED}" font-size="12">{year}</text>'
        )
    if days[0][:4] not in seen:
        parts.insert(
            0,
            f'<text x="{xpos(days[0]) + 4:.1f}" y="{top + height + 16}" fill="{MUTED}" font-size="12">{days[0][:4]}</text>',
        )
    return "".join(parts)


def significance_svg(rows):
    width, height = 920, 300
    left, right, top = 168, 150, 28
    plot_w = width - left - right
    bounds = [0.0]
    for row in rows:
        bounds.extend((row["lo"], row["hi"], row["mean"]))
    xpos, low, high = _y_scale(bounds, 0, plot_w, [0.0])
    # _y_scale maps a value onto a vertical span. Use it horizontally.
    def x_of(value):
        return left + (value - low) / (high - low) * plot_w

    zero = x_of(0.0)
    labels_y = []
    marks = []
    step = 56
    for i, row in enumerate(rows):
        y = top + 36 + i * step
        labels_y.append(y)
        color = TEAL if row["lo"] > 0 else LOSS if row["hi"] < 0 else INK
        marks.append(
            f'<line x1="{x_of(row["lo"]):.1f}" y1="{y}" x2="{x_of(row["hi"]):.1f}" y2="{y}" stroke="{color}" stroke-width="2"/>'
            f'<line x1="{x_of(row["lo"]):.1f}" y1="{y - 6}" x2="{x_of(row["lo"]):.1f}" y2="{y + 6}" stroke="{color}" stroke-width="2"/>'
            f'<line x1="{x_of(row["hi"]):.1f}" y1="{y - 6}" x2="{x_of(row["hi"]):.1f}" y2="{y + 6}" stroke="{color}" stroke-width="2"/>'
            f'<circle cx="{x_of(row["mean"]):.1f}" cy="{y}" r="5" fill="{color}"/>'
            f'<text x="{left - 12}" y="{y + 4}" text-anchor="end" fill="{INK}" font-size="14">{row["name"]}, {row["channel"]}-day</text>'
            f'<text x="{width - 16}" y="{y + 4}" text-anchor="end" fill="{MUTED}" font-size="13">p {_fmt_p(row["p"])}</text>'
        )
    ticks = []
    # A few round basis-point ticks that fall inside the axis.
    span_bps = (high - low) * 1e4
    if span_bps > 2000:
        step_bps = 500
    elif span_bps > 800:
        step_bps = 200
    elif span_bps > 400:
        step_bps = 100
    else:
        step_bps = 50
    start_bps = math.floor(low * 1e4 / step_bps) * step_bps
    end_bps = math.ceil(high * 1e4 / step_bps) * step_bps
    bps = start_bps
    while bps <= end_bps:
        value = bps / 1e4
        if low <= value <= high:
            x = x_of(value)
            ticks.append(
                f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{height - 36}" stroke="{GRID}"/>'
                f'<text x="{x:.1f}" y="{height - 14}" text-anchor="middle" fill="{MUTED}" font-size="12">{bps:+.0f}</text>'
            )
        bps += step_bps
    return f'''<svg viewBox="0 0 {width} {height}" role="img">
  <rect width="{width}" height="{height}" fill="{SHEET}"/>
  {''.join(ticks)}
  <line x1="{zero:.1f}" y1="{top}" x2="{zero:.1f}" y2="{height - 36}" stroke="{LOSS}" stroke-width="1.5"/>
  {''.join(marks)}
</svg>'''


def equity_svg(rows):
    width, height = 920, 340
    left, right, top, bottom = 56, 20, 20, 32
    plot_w = width - left - right
    plot_h = height - top - bottom
    days = rows[0]["days"]
    xpos, _, _ = _x_scale(days, left, plot_w)
    values = [value for row in rows for value in row["path"]]
    ypos, low, high = _y_scale(values, top, plot_h, [1.0])
    one = ypos(1.0)
    lines = []
    colors = (TEAL, NAVY)
    for row, color in zip(rows, colors):
        points = []
        for day, value in zip(row["days"], row["path"]):
            points.append(f"{xpos(day):.1f},{ypos(value):.1f}")
        dash = "" if row["channel"] == 20 else ' stroke-dasharray="5 4"'
        lines.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="1.6"{dash} points="{" ".join(points)}"/>'
        )
    return _panel(width, height, left, top, plot_h, xpos, days, ypos, low, high, one, lines, "stake")


def drawdown_svg(rows):
    width, height = 920, 180
    left, right, top, bottom = 56, 20, 12, 28
    plot_w = width - left - right
    plot_h = height - top - bottom
    days = rows[0]["days"]
    xpos, _, _ = _x_scale(days, left, plot_w)
    values = [value for row in rows for value in row["drawdowns"]]
    ypos, low, high = _y_scale(values, top, plot_h, [0.0])
    zero = ypos(0.0)
    lines = []
    colors = (TEAL, NAVY)
    for row, color in zip(rows, colors):
        coords = [f"{xpos(day):.1f},{ypos(value):.1f}" for day, value in zip(row["days"], row["drawdowns"])]
        area = f"{xpos(row['days'][0]):.1f},{zero:.1f} " + " ".join(coords) + f" {xpos(row['days'][-1]):.1f},{zero:.1f}"
        opacity = "0.18" if row["channel"] == 20 else "0.10"
        lines.append(f'<polygon points="{area}" fill="{color}" opacity="{opacity}"/>')
        lines.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="1.3" points="{" ".join(coords)}"/>'
        )
    return _panel(width, height, left, top, plot_h, xpos, days, ypos, low, high, zero, lines, "drawdown")


def trades_svg(rows):
    width, height = 920, 280
    left, right, top, bottom = 56, 20, 16, 32
    plot_w = width - left - right
    plot_h = height - top - bottom
    days = rows[0]["days"]
    xpos, _, _ = _x_scale(days, left, plot_w)
    returns = [trade["return"] for row in rows for trade in row["trades"]]
    ypos, low, high = _y_scale(returns, top, plot_h, [0.0])
    zero = ypos(0.0)
    dots = []
    colors = {20: TEAL, 55: NAVY}
    for row in rows:
        color = colors[row["channel"]]
        for trade in row["trades"]:
            dots.append(
                f'<circle cx="{xpos(trade["exit"]):.1f}" cy="{ypos(trade["return"]):.1f}" r="3.5" fill="{color}" opacity="0.85">'
                f'<title>{trade["exit"]} {trade["side"]} {trade["cause"]} {_bps(trade["return"])} bps</title></circle>'
            )
    return _panel(width, height, left, top, plot_h, xpos, days, ypos, low, high, zero, dots, "bps")


def _panel(width, height, left, top, plot_h, xpos, days, ypos, low, high, zero, body, kind):
    if kind == "stake":
        hi_label, lo_label = f"{high:.2f}", f"{low:.2f}"
    elif kind == "drawdown":
        hi_label, lo_label = f"{high * 100:.0f}%", f"{low * 100:.0f}%"
    else:
        hi_label, lo_label = f"{high * 1e4:+.0f}", f"{low * 1e4:+.0f}"
    return f'''<svg viewBox="0 0 {width} {height}" role="img">
  <rect width="{width}" height="{height}" fill="{SHEET}"/>
  {_year_lines(days, xpos, top, plot_h)}
  <line x1="{left}" y1="{zero:.1f}" x2="{width - 20}" y2="{zero:.1f}" stroke="{MUTED}" stroke-width="1"/>
  {''.join(body)}
  <text x="8" y="{top + 12}" fill="{MUTED}" font-size="12">{hi_label}</text>
  <text x="8" y="{top + plot_h}" fill="{MUTED}" font-size="12">{lo_label}</text>
</svg>'''


def _finding(rows):
    sentences = []
    for row in rows:
        label = f"{row['name']} {row['channel']}-day"
        if row["lo"] > 0:
            sentences.append(
                f"{label} clears zero: the interval is {_bps(row['lo'])} to {_bps(row['hi'])} basis points, p {_fmt_p(row['p'])}."
            )
        elif row["hi"] < 0:
            sentences.append(
                f"{label} is below zero: the interval is {_bps(row['lo'])} to {_bps(row['hi'])} basis points, p {_fmt_p(row['p'])}."
            )
        else:
            sentences.append(
                f"{label} does not clear zero: the interval is {_bps(row['lo'])} to {_bps(row['hi'])} basis points, p {_fmt_p(row['p'])}."
            )
    return " ".join(sentences)


def _legend():
    return f'''<p class="legend"><span class="swatch" style="background:{TEAL}"></span>20-day channel
<span class="swatch" style="background:{NAVY}"></span>55-day channel
<span class="swatch line" style="background:{LOSS}"></span>zero</p>'''


def _table(rows):
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f"<td>{row['name']}</td>"
            f"<td>{row['channel']}</td>"
            f"<td>{row['n']}</td>"
            f"<td>{row['win_rate'] * 100:.0f}%</td>"
            f"<td>{_bps(row['mean'])}</td>"
            f"<td>{_bps(row['lo'])} to {_bps(row['hi'])}</td>"
            f"<td>{row['t']:.2f}</td>"
            f"<td>{_fmt_p(row['p'])}</td>"
            f"<td>{row['ending']:.3f}</td>"
            f"<td>{_pct(row['drawdown'])}</td>"
            "</tr>"
        )
    return f'''<table>
<thead><tr>
<th>Market</th><th>Channel</th><th>Trades</th><th>Winners</th>
<th>Mean, bps</th><th>95% interval, bps</th><th>t</th><th>p</th>
<th>Ending stake</th><th>Worst drop</th>
</tr></thead>
<tbody>{''.join(body)}</tbody>
</table>'''


def _year_table(btc_rows, eth_rows):
    years = []
    for row in btc_rows + eth_rows:
        for item in row["years"]:
            if item["year"] not in years:
                years.append(item["year"])
    years.sort()
    header = "".join(f"<th>{year}</th>" for year in years)
    body = []
    for row in btc_rows + eth_rows:
        by_year = {item["year"]: item["return"] for item in row["years"]}
        cells = "".join(
            f"<td>{_pct(by_year[year])}</td>" if year in by_year else "<td></td>"
            for year in years
        )
        body.append(f"<tr><td>{row['name']}, {row['channel']}-day</td>{cells}</tr>")
    return f'''<table>
<thead><tr><th>Account change by year</th>{header}</tr></thead>
<tbody>{''.join(body)}</tbody>
</table>'''


def render(btc_rows, eth_rows):
    rows = btc_rows + eth_rows
    btc = btc_rows[0]
    each_fill_bps = btc["fee"] * 1e4
    round_trip_bps = each_fill_bps * 2
    fill_unit = "basis point" if abs(each_fill_bps - 1) < 1e-9 else "basis points"
    trip_unit = "basis point" if abs(round_trip_bps - 1) < 1e-9 else "basis points"
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Breakout-30 on Avantis daily bars</title>
<style>
  body {{ margin: 0; background: {PAPER}; color: {INK};
         font: 18px/1.5 "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif; }}
  main {{ max-width: 960px; margin: 0 auto; padding: 48px 24px 72px; }}
  h1 {{ font-weight: 500; font-size: 34px; line-height: 1.15; letter-spacing: -0.02em; margin: 0 0 16px; }}
  h2 {{ font-weight: 500; font-size: 22px; margin: 40px 0 8px; }}
  p {{ max-width: 68ch; margin: 0 0 12px; }}
  .note {{ color: {MUTED}; font-size: 16px; }}
  figure {{ margin: 16px 0 8px; }}
  figcaption {{ font-family: "Avenir Next", Avenir, "Trebuchet MS", sans-serif;
                font-size: 15px; color: {MUTED}; margin: 0 0 8px; }}
  svg {{ width: 100%; height: auto; display: block; }}
  svg text {{ font-family: "Avenir Next", Avenir, "Trebuchet MS", sans-serif; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0 28px;
           font-family: "Avenir Next", Avenir, "Trebuchet MS", sans-serif; font-size: 14px; }}
  th, td {{ text-align: right; padding: 8px 8px; border-bottom: 1px solid {GRID}; }}
  th:first-child, td:first-child {{ text-align: left; }}
  th {{ font-weight: 500; color: {MUTED}; }}
  .legend {{ font-family: "Avenir Next", Avenir, "Trebuchet MS", sans-serif; font-size: 14px; color: {MUTED}; }}
  .swatch {{ display: inline-block; width: 14px; height: 8px; margin: 0 6px 0 14px; vertical-align: middle; }}
  .swatch.line {{ height: 2px; }}
  @media (max-width: 720px) {{
    main {{ padding: 28px 14px 48px; }}
    h1 {{ font-size: 28px; }}
    table {{ display: block; overflow-x: auto; }}
  }}
</style>
</head>
<body>
<main>
  <h1>Breakout-30 on Avantis daily bars</h1>
  <p>Every fill pays {each_fill_bps:g} {fill_unit}. A trade that enters and exits pays {round_trip_bps:g} {trip_unit}. The stop pays it too. The original research also charged 0.1 basis points for every hour a trade was open. That holding cost is not in this report.</p>
  <p>{_finding(rows)}</p>
  <p class="note">The interval is a Student t 95% interval for the average trade. The test treats trades as independent. They share one market and come in a sequence, so this reading is generous. Bitcoin uses {btc['n_bars']} daily bars from {btc['start']} to {btc['end']}, after leaving out {btc['skipped']} incomplete days. Ethereum runs from {eth_rows[0]['start']} to {eth_rows[0]['end']}.</p>

  <h2>Is the average trade away from zero?</h2>
  <figure>
    <figcaption>Average trade after the round-trip fee, in basis points. The bar is the 95% interval. The red line is zero.</figcaption>
    {significance_svg(rows)}
  </figure>

  {_table(rows)}

  <h2>Bitcoin account</h2>
  {_legend()}
  <figure>
    <figcaption>Stake if it starts at 1 and each trade uses the whole account. Marked at the daily close while a trade is open, and at the fill when it closes.</figcaption>
    {equity_svg(btc_rows)}
  </figure>
  <figure>
    <figcaption>Drop from the highest stake so far.</figcaption>
    {drawdown_svg(btc_rows)}
  </figure>
  <figure>
    <figcaption>Each Bitcoin trade on the day it closed. Hover a dot for the side and the reason.</figcaption>
    {trades_svg(btc_rows)}
  </figure>

  <h2>Ethereum account</h2>
  {_legend()}
  <figure>
    <figcaption>Same rules, on the shorter Ethereum history.</figcaption>
    {equity_svg(eth_rows)}
  </figure>
  <figure>
    <figcaption>Drop from the highest stake so far.</figcaption>
    {drawdown_svg(eth_rows)}
  </figure>
  <figure>
    <figcaption>Each Ethereum trade on the day it closed.</figcaption>
    {trades_svg(eth_rows)}
  </figure>

  <h2>Year by year</h2>
  <p class="note">Change in the compounded account during that calendar year, after fees. An empty cell means that market has no bars then.</p>
  {_year_table(btc_rows, eth_rows)}

  <p class="note">The rule buys when the daily close is above the prior channel high and sells short when it is below the prior channel low. The fill is the next day's open. The stop is two average-true-range units from that open. There is no profit target. A trade still open after 30 daily bars leaves at the next open. One position at a time. The source is the benchmarks feed of Crypto.BTC/USD and Crypto.ETH/USD, rolled up to UTC days.</p>
</main>
</body>
</html>
'''


def main():
    _check_table()
    if len(sys.argv) not in (5, 7) or sys.argv[3] != "-o":
        raise SystemExit(
            "usage: python3 examples/breakout30_report.py btc.csv eth.csv -o report.html [--round-trip-bps 2]"
        )
    if len(sys.argv) == 7:
        if sys.argv[5] != "--round-trip-bps":
            raise SystemExit("usage: --round-trip-bps N")
        round_trip_bps = float(sys.argv[6])
        fee = round_trip_bps / 2.0 / 1e4
    else:
        fee = FEE
    books = []
    for label, path in (("Bitcoin", sys.argv[1]), ("Ethereum", sys.argv[2])):
        days, bars, skipped = load(path)
        books.append((label, days, bars, skipped))
    btc_rows = [
        summarize("Bitcoin", channel, *books[0][1:], fee) for channel in CHANNEL_DAYS
    ]
    eth_rows = [
        summarize("Ethereum", channel, *books[1][1:], fee) for channel in CHANNEL_DAYS
    ]
    Path(sys.argv[4]).write_text(render(btc_rows, eth_rows), encoding="utf-8")
    for row in btc_rows + eth_rows:
        print(
            f"{row['name']} {row['channel']}: n={row['n']} mean={row['mean'] * 1e4:.1f} bps"
            f" t={row['t']:.2f} p={row['p']:.4f} stake={row['ending']:.4f}"
            f" dd={row['drawdown'] * 100:.1f}%"
        )


if __name__ == "__main__":
    main()

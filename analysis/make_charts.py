#!/usr/bin/env python3
"""Charts for the Wonyotti BitMEX history.

Reads the raw exports in data/ and writes PNGs to docs/figures/.
Run from anywhere:

    python3 analysis/make_charts.py
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "docs" / "figures"

INK = "#1c1917"
MUTED = "#78716c"
GRID = "#e7e5e4"
NAVY = "#1e3a5f"
TEAL = "#0f766e"
RED = "#b4534b"
GOLD = "#a16207"
BAND = "#e7e5e4"
PAPER = "#fafaf9"


def _font():
    have = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Avenir Next", "Avenir", "Helvetica Neue", "Helvetica"):
        if name in have:
            return name
    return "DejaVu Sans"


def style():
    plt.rcParams.update(
        {
            "font.family": _font(),
            "font.size": 11,
            "text.color": INK,
            "axes.labelcolor": INK,
            "axes.edgecolor": "#d6d3d1",
            "axes.linewidth": 0.6,
            "axes.facecolor": PAPER,
            "figure.facecolor": "white",
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "axes.axisbelow": True,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "legend.frameon": False,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )


def claim(ax, title, subtitle=None):
    ax.set_title(title, loc="left", fontsize=13, color=INK, pad=22 if subtitle else 10, fontweight="medium")
    if subtitle:
        ax.annotate(
            subtitle,
            xy=(0, 1),
            xycoords="axes fraction",
            xytext=(0, 6),
            textcoords="offset points",
            fontsize=8.5,
            color=MUTED,
            ha="left",
            va="bottom",
            annotation_clip=False,
        )


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    fig.savefig(path, dpi=140, bbox_inches="tight", pad_inches=0.35, facecolor="white")
    plt.close(fig)
    print(f"wrote {path.relative_to(ROOT)}")


def parse_day(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def load_wallet():
    """Daily closes rebuilt from the amount column.

    walletbalance is unusable once Excel wrote it in scientific notation,
    and several rows in a day repeat the end-of-batch balance.
    """
    by_day = defaultdict(lambda: {"pnl": 0, "dep": 0, "wd": 0})
    with (DATA / "aoa-wallet-2018-03-01-2021-12-31.csv").open(
        newline="", encoding="utf-8-sig"
    ) as f:
        for row in csv.DictReader(f):
            kind = (row.get("transacttype") or "").strip()
            if not kind or row.get("transactstatus") != "Completed":
                continue
            amt = int(row["amount"])
            bucket = by_day[row["date"]]
            if kind == "RealisedPNL":
                bucket["pnl"] += amt
                bucket.setdefault("by_symbol", defaultdict(int))
                sym = (row.get("address") or "").strip() or "(blank)"
                bucket["by_symbol"][sym] += amt
            elif kind == "Deposit":
                bucket["dep"] += amt
            elif kind == "Withdrawal":
                bucket["wd"] += amt

    days = sorted(by_day)
    equity = []
    generated = []
    pnl_btc = []
    cum_pnl = cum_dep = cum_wd = 0
    symbol_pnl = defaultdict(int)
    out_days = []
    for d in days:
        b = by_day[d]
        cum_pnl += b["pnl"]
        cum_dep += b["dep"]
        cum_wd += b["wd"]
        for sym, amt in b.get("by_symbol", {}).items():
            symbol_pnl[sym] += amt
        out_days.append(parse_day(d))
        equity.append((cum_dep + cum_pnl + cum_wd) / 1e8)
        generated.append((cum_dep + cum_pnl) / 1e8)
        pnl_btc.append(b["pnl"] / 1e8)
    return {
        "days": out_days,
        "equity": equity,
        "generated": generated,
        "pnl": pnl_btc,
        "symbol_pnl": dict(symbol_pnl),
    }


def connect():
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute(
        f"""
        CREATE VIEW exec AS
        SELECT * FROM read_csv_auto(
          '{(DATA / "aoa-execution-*.csv").as_posix()}',
          header=true,
          union_by_name=true
        )
        """
    )
    return con


def chart_equity(w):
    fig, ax = plt.subplots(figsize=(11.2, 5.6))
    ax.plot(w["days"], w["generated"], color=TEAL, lw=1.6, label="Deposits + realised PnL")
    ax.plot(w["days"], w["equity"], color=NAVY, lw=1.8, label="Left in the wallet")
    ax.fill_between(w["days"], w["equity"], w["generated"], color=TEAL, alpha=0.08, lw=0)

    def on(day):
        return w["days"].index(parse_day(day))

    i_trough = on("2021-05-20")
    i_peak = on("2021-06-19")
    ax.scatter([w["days"][i_trough]], [w["equity"][i_trough]], s=28, color=RED, zorder=4)
    ax.scatter([w["days"][i_peak]], [w["equity"][i_peak]], s=28, color=NAVY, zorder=4)
    ax.annotate(
        f"20 May 2021\n{w['equity'][i_trough]:,.0f} BTC",
        xy=(w["days"][i_trough], w["equity"][i_trough]),
        xytext=(-70, 36),
        textcoords="offset points",
        fontsize=9,
        color=RED,
        ha="right",
        arrowprops={"arrowstyle": "-", "color": RED, "lw": 0.6},
    )
    ax.annotate(
        f"19 Jun 2021\n{w['equity'][i_peak]:,.0f} BTC still in the wallet",
        xy=(w["days"][i_peak], w["equity"][i_peak]),
        xytext=(12, 16),
        textcoords="offset points",
        fontsize=9,
        color=NAVY,
        ha="left",
    )
    end = w["equity"][-1]
    gen = w["generated"][-1]
    ax.annotate(
        f"Left with {end:,.0f} BTC",
        xy=(w["days"][-1], end),
        xytext=(-10, 14),
        textcoords="offset points",
        fontsize=9,
        color=NAVY,
        ha="right",
    )
    claim(
        ax,
        "The wallet ended at 737 BTC. The trading had produced about 3,550.",
        "The shaded gap is BTC withdrawn. The May 2021 drop is a loss, not a withdrawal.",
    )
    ax.set_ylabel("Bitcoin")
    ax.legend(loc="upper left")
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.set_ylim(0, max(w["generated"]) * 1.08)
    fig.tight_layout()
    save(fig, "01_equity.png")
    return {"end": end, "generated_end": gen, "peak_wallet": w["equity"][i_peak], "trough": w["equity"][i_trough]}


def chart_2018(w, con):
    fig, ax = plt.subplots(figsize=(11.2, 5.2))
    cut = parse_day("2019-01-01")
    days = [d for d in w["days"] if d < cut]
    eq = [e for d, e in zip(w["days"], w["equity"]) if d < cut]
    ax.plot(days, eq, color=NAVY, lw=1.6)
    ax.fill_between(days, eq, 0, color=NAVY, alpha=0.06, lw=0)

    rows = con.execute(
        """
        SELECT CAST("date" AS DATE) d
        FROM exec
        WHERE "text" = 'Liquidation' AND "date" < '2019-01-01'
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchall()
    liq_days = [r[0] for r in rows]
    by_day = dict(zip(days, eq))
    xs, ys = [], []
    for d in liq_days:
        if d in by_day:
            xs.append(d)
            ys.append(by_day[d])
    ax.scatter(xs, ys, s=22, color=RED, zorder=4, label=f"Day with a liquidation ({len(xs)})")

    zero = parse_day("2018-03-22")
    if zero in by_day:
        ax.annotate(
            "Withdrew the account to zero",
            xy=(zero, by_day[zero]),
            xytext=(18, 28),
            textcoords="offset points",
            fontsize=9,
            color=RED,
            arrowprops={"arrowstyle": "-", "color": RED, "lw": 0.6},
        )
    claim(
        ax,
        "2018 was a small, hot account. Liquidations cluster in the first months.",
        "Each red dot is a day with at least one liquidation fill. Deposits stop after 2018.",
    )
    ax.set_ylabel("Bitcoin left in the wallet")
    ax.legend(loc="upper left")
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.set_ylim(0, max(eq) * 1.18)
    fig.tight_layout()
    save(fig, "02_equity_2018.png")
    return {"liq_days_2018": len(xs)}


def chart_monthly(w):
    month = defaultdict(float)
    for d, p in zip(w["days"], w["pnl"]):
        if p == 0:
            continue
        month[(d.year, d.month)] += p
    keys = sorted(month)
    dates = [date(y, m, 1) for y, m in keys]
    vals = [month[k] for k in keys]
    colors = [TEAL if v >= 0 else RED for v in vals]

    fig, ax = plt.subplots(figsize=(11.4, 5.4))
    ax.bar(dates, vals, width=20, color=colors, align="center")
    ax.axhline(0, color="#d6d3d1", lw=0.6)

    ranked = sorted(zip(vals, dates), key=lambda t: t[0])
    for v, d in ranked[:3] + ranked[-3:]:
        ax.annotate(
            f"{v:+.0f}",
            xy=(d, v),
            xytext=(0, 4 if v >= 0 else -11),
            textcoords="offset points",
            ha="center",
            va="bottom" if v >= 0 else "top",
            fontsize=8,
            color=TEAL if v >= 0 else RED,
        )
    claim(
        ax,
        "A few months did the work. Q4 2021 gave a large piece back.",
        "Monthly realised PnL, in BTC. Labels mark the three best and three worst months.",
    )
    ax.set_ylabel("Bitcoin")
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    save(fig, "03_monthly_pnl.png")
    return {
        "best": [(d.isoformat()[:7], round(v, 1)) for v, d in ranked[-3:]],
        "worst": [(d.isoformat()[:7], round(v, 1)) for v, d in ranked[:3]],
    }


def chart_concentration(w):
    vals = [p for p in w["pnl"] if p != 0]
    vals.sort(reverse=True)
    total = sum(vals)
    cume = []
    s = 0.0
    for v in vals:
        s += v
        cume.append(s)
    big = [v for v in vals if v >= 50]
    other = [v for v in vals if 0 < v < 50]
    red = [v for v in vals if v < 0]
    n_big = len(big)
    share = sum(big) / total

    fig, axes = plt.subplots(1, 2, figsize=(11.4, 5.2), gridspec_kw={"width_ratios": [1.25, 1]})
    ax = axes[0]
    ax.plot(range(1, len(cume) + 1), cume, color=NAVY, lw=1.6)
    ax.axhline(total, color=MUTED, lw=0.8, ls="--")
    ax.annotate(
        f"net {total:,.0f}",
        xy=(len(cume), total),
        xytext=(-2, 5),
        textcoords="offset points",
        ha="right",
        fontsize=8,
        color=MUTED,
    )
    ax.scatter([n_big], [cume[n_big - 1]], color=GOLD, s=32, zorder=4)
    ax.annotate(
        f"{n_big} days of +50 BTC or more\n{share:.0%} of the net result",
        xy=(n_big, cume[n_big - 1]),
        xytext=(24, -28),
        textcoords="offset points",
        fontsize=9,
        color=GOLD,
        arrowprops={"arrowstyle": "-", "color": GOLD, "lw": 0.6},
    )
    claim(ax, "Rank the days from best to worst.")
    ax.set_xlabel("Days included, best first")
    ax.set_ylabel("Cumulative realised PnL, BTC")

    ax = axes[1]
    labels = [f"Days ≥ 50 BTC\n({n_big} days)", f"Other green days\n({len(other)})", f"Red days\n({len(red)})"]
    heights = [sum(big), sum(other), sum(red)]
    colors = [GOLD, TEAL, RED]
    bars = ax.bar(labels, heights, color=colors, width=0.72)
    ax.axhline(0, color="#d6d3d1", lw=0.6)
    ax.axhline(total, color=NAVY, lw=0.8, ls="--")
    ax.annotate(
        f"net {total:+,.0f}",
        xy=(2.48, total),
        xytext=(0, 5),
        textcoords="offset points",
        ha="right",
        fontsize=8,
        color=NAVY,
    )
    for bar, h in zip(bars, heights):
        ax.annotate(
            f"{h:+,.0f}",
            xy=(bar.get_x() + bar.get_width() / 2, h / 2),
            ha="center",
            va="center",
            fontsize=10,
            color="white",
            fontweight="medium",
        )
    ax.set_ylim(min(heights) * 1.18, max(heights) * 1.22)
    claim(ax, "The small green days cancel the red ones.")
    ax.set_ylabel("Bitcoin")
    fig.tight_layout()
    save(fig, "04_pnl_concentration.png")
    return {
        "n_big": n_big,
        "share": share,
        "big": sum(big),
        "other": sum(other),
        "red": sum(red),
        "total": total,
        "n_green_other": len(other),
        "n_red": len(red),
        "n_days": len(vals),
    }


def chart_position(con):
    rows = con.execute(
        """
        WITH t AS (
          SELECT CAST(transacttime AS TIMESTAMP) AS ts,
                 CAST("date" AS DATE) AS d,
                 CASE WHEN side = 'Buy' THEN lastqty ELSE -lastqty END AS q
          FROM exec
          WHERE symbol = 'XBTUSD' AND exectype = 'Trade'
        ),
        p AS (
          SELECT ts, d, sum(q) OVER (ORDER BY ts ROWS UNBOUNDED PRECEDING) AS pos
          FROM t
        )
        SELECT d,
               arg_max(pos, ts) / 1e6 AS eod_m,
               min(pos) / 1e6 AS lo_m,
               max(pos) / 1e6 AS hi_m
        FROM p
        GROUP BY d
        ORDER BY d
        """
    ).fetchall()
    days = [r[0] for r in rows]
    eod = [r[1] for r in rows]
    lo = [r[2] for r in rows]
    hi = [r[3] for r in rows]

    fig, ax = plt.subplots(figsize=(11.2, 5.4))
    ax.fill_between(days, lo, hi, color=BAND, lw=0, label="Low to high that day")
    ax.plot(days, eod, color=NAVY, lw=1.0, label="Position at the last fill")
    ax.axhline(0, color=MUTED, lw=0.7)
    claim(
        ax,
        "The Bitcoin position was large, and it sat short more often than long.",
        "XBTUSD inventory in millions of USD. Positive is long. The pale band is the intraday range.",
    )
    ax.set_ylabel("Million USD")
    ax.legend(loc="upper left")
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    save(fig, "05_xbt_position.png")
    # trade-sampled median is a different statistic; report end-of-day median here
    ordered = sorted(eod)
    mid = ordered[len(ordered) // 2]
    return {"eod_median_m": mid, "max_hi_m": max(hi), "min_lo_m": min(lo)}


def chart_symbols(w):
    items = sorted(w["symbol_pnl"].items(), key=lambda kv: kv[1])
    # Keep symbols that moved the account by at least 10 BTC.
    items = [(s, v / 1e8) for s, v in items if abs(v) >= 10 * 1e8]
    fig_h = max(4.8, 0.38 * len(items) + 1.6)
    fig, ax = plt.subplots(figsize=(10.2, fig_h))
    colors = [TEAL if v >= 0 else RED for _, v in items]
    ax.barh([s for s, _ in items], [v for _, v in items], color=colors, height=0.72)
    ax.axvline(0, color="#d6d3d1", lw=0.6)
    for s, v in items:
        ax.annotate(
            f"{v:+,.0f}",
            xy=(v, s),
            xytext=(4 if v >= 0 else -4, 0),
            textcoords="offset points",
            va="center",
            ha="left" if v >= 0 else "right",
            fontsize=8,
            color=MUTED,
        )
    claim(
        ax,
        "XBTUSD and ETHUSD produced the Bitcoin. Most other contracts were small.",
        "Realised PnL by the symbol stored on the wallet row, in BTC. Only symbols beyond ±10 BTC.",
    )
    ax.set_xlabel("Bitcoin")
    fig.tight_layout()
    save(fig, "06_pnl_by_symbol.png")
    return {s: round(v, 1) for s, v in items}


def chart_maker(con):
    rows = con.execute(
        """
        SELECT year(CAST(transacttime AS TIMESTAMP)) AS y,
               sum(abs(foreignnotional)) / 1e9 AS usd_bn,
               sum(abs(foreignnotional)) FILTER (WHERE lastliquidityind = 'AddedLiquidity') / 1e9 AS maker_bn,
               count(DISTINCT orderid) AS orders
        FROM exec
        WHERE symbol = 'XBTUSD' AND exectype = 'Trade'
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchall()
    years = [str(r[0]) for r in rows]
    usd = [r[1] for r in rows]
    maker = [r[2] for r in rows]
    taker = [a - b for a, b in zip(usd, maker)]
    orders = [r[3] for r in rows]
    pct = [100 * m / u for m, u in zip(maker, usd)]

    fig, axes = plt.subplots(2, 1, figsize=(8.6, 6.4), sharex=True)
    ax = axes[0]
    ax.bar(years, maker, color=TEAL, width=0.66, label="Maker")
    ax.bar(years, taker, bottom=maker, color="#d6d3d1", width=0.66, label="Taker")
    for x, p, u in zip(years, pct, usd):
        ax.annotate(f"{p:.0f}% maker", xy=(x, u), xytext=(0, 4), textcoords="offset points", ha="center", fontsize=8, color=TEAL)
    claim(ax, "XBTUSD volume grew, and more of it was passive.")
    ax.set_ylabel("Billion USD of fills")
    ax.legend(loc="upper left")

    ax = axes[1]
    ax.bar(years, orders, color=NAVY, width=0.66)
    for x, n in zip(years, orders):
        ax.annotate(f"{n:,.0f}", xy=(x, n), xytext=(0, 4), textcoords="offset points", ha="center", fontsize=8, color=NAVY)
    claim(ax, "The order count fell while the volume rose.")
    ax.set_ylabel("Distinct XBTUSD orders")
    fig.tight_layout()
    save(fig, "07_maker_and_orders.png")
    return {"years": years, "usd_bn": [round(u, 2) for u in usd], "maker_pct": [round(p, 1) for p in pct], "orders": orders}


def chart_order_size(con):
    rows = con.execute(
        """
        WITH o AS (
          SELECT max(orderqty) AS q
          FROM exec
          WHERE symbol = 'XBTUSD' AND exectype = 'Trade'
          GROUP BY orderid
        )
        SELECT
          count(*) FILTER (WHERE q < 100000),
          count(*) FILTER (WHERE q >= 100000 AND q < 250000),
          count(*) FILTER (WHERE q >= 250000 AND q < 500000),
          count(*) FILTER (WHERE q >= 500000 AND q < 1000000),
          count(*) FILTER (WHERE q >= 1000000 AND q < 2000000),
          count(*) FILTER (WHERE q >= 2000000 AND q < 5000000),
          count(*) FILTER (WHERE q >= 5000000 AND q < 10000000),
          count(*) FILTER (WHERE q >= 10000000),
          quantile_cont(q, 0.5)
        FROM o
        """
    ).fetchone()
    labels = ["<100k", "100–250k", "250–500k", "500k–1M", "1–2M", "2–5M", "5–10M", "10M"]
    counts = list(rows[:8])
    med = rows[8]
    fig, ax = plt.subplots(figsize=(10.4, 5.0))
    ax.bar(labels, counts, color=NAVY, width=0.72)
    for lab, n in zip(labels, counts):
        ax.annotate(f"{n:,.0f}", xy=(lab, n), xytext=(0, 3), textcoords="offset points", ha="center", fontsize=8, color=MUTED)
    claim(
        ax,
        f"A typical XBTUSD order was {med/1e6:.2f} million USD.",
        "Distinct orders, bucketed by the largest orderqty seen on that order. Not fill size.",
    )
    ax.set_ylabel("Orders")
    ax.set_xlabel("Order size, USD")
    fig.tight_layout()
    save(fig, "08_order_size.png")
    return {"median": med, "counts": counts, "labels": labels}


def chart_clock(con):
    rows = con.execute(
        """
        SELECT extract('hour' FROM CAST(transacttime AS TIMESTAMP) + INTERVAL 9 HOUR) AS h,
               sum(abs(foreignnotional)) / 1e9 AS usd_bn
        FROM exec
        WHERE symbol = 'XBTUSD' AND exectype = 'Trade'
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchall()
    hours = [int(r[0]) for r in rows]
    usd = [r[1] for r in rows]
    dur = con.execute(
        """
        WITH o AS (
          SELECT epoch(max(CAST(transacttime AS TIMESTAMP)) - min(CAST(transacttime AS TIMESTAMP))) AS dur
          FROM exec
          WHERE symbol = 'XBTUSD' AND exectype = 'Trade'
            AND orderid <> '00000000-0000-0000-0000-000000000000'
          GROUP BY orderid
        )
        SELECT quantile_cont(dur, 0.5),
               100.0 * count(*) FILTER (WHERE dur < 60) / count(*)
        FROM o
        """
    ).fetchone()
    fig, ax = plt.subplots(figsize=(11.0, 4.8))
    ax.bar(hours, usd, color=NAVY, width=0.78)
    claim(
        ax,
        "Fills showed up at every hour of the Korean day.",
        f"XBTUSD fill notional by hour in KST (UTC+9), axis starting at zero. Median order lasts {dur[0]:.1f}s, and {dur[1]:.0f}% finish within a minute, so this is close to when he traded.",
    )
    ax.set_ylabel("Billion USD")
    ax.set_xlabel("Hour in Korea standard time")
    ax.set_xticks(range(0, 24, 2))
    ax.set_ylim(0, max(usd) * 1.25)
    fig.tight_layout()
    save(fig, "09_fill_clock.png")
    return {"min_bn": min(usd), "max_bn": max(usd), "median_order_s": dur[0], "pct_under_60s": dur[1]}


def chart_costs(con, total_pnl):
    row = con.execute(
        """
        SELECT
          sum(execcomm) FILTER (WHERE exectype = 'Trade') / 1e8,
          sum(execcomm) FILTER (WHERE exectype = 'Funding') / 1e8
        FROM exec
        """
    ).fetchone()
    fee = row[0]
    funding = row[1]
    # Positive execcomm is BTC paid. Negative is BTC received.
    fees_paid = fee if fee > 0 else 0.0
    funding_received = -funding if funding < 0 else 0.0
    labels = ["Realised PnL", "Funding received", "Trade fees paid"]
    vals = [total_pnl, funding_received, fees_paid]
    colors = [NAVY, TEAL, RED]
    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    bars = ax.bar(labels, vals, color=colors, width=0.62)
    for bar, v in zip(bars, vals):
        ax.annotate(
            f"{v:,.0f} BTC",
            xy=(bar.get_x() + bar.get_width() / 2, v),
            xytext=(0, 5),
            textcoords="offset points",
            ha="center",
            fontsize=10,
            color=INK,
        )
    claim(
        ax,
        "Fees and funding are a rounding error next to the price result.",
        "The wallet total already contains both. Funding was collected. Trade fees were paid.",
    )
    ax.set_ylabel("Bitcoin")
    ax.set_ylim(0, max(vals) * 1.18)
    fig.tight_layout()
    save(fig, "10_fees_and_funding.png")
    return {"fees_paid": fees_paid, "funding_received": funding_received, "fee_raw": fee, "funding_raw": funding}


def main():
    style()
    print("loading wallet")
    w = load_wallet()
    print("opening executions")
    con = connect()
    stats = {
        "equity": chart_equity(w),
        "y2018": chart_2018(w, con),
        "monthly": chart_monthly(w),
        "concentration": chart_concentration(w),
        "position": chart_position(con),
        "symbols": chart_symbols(w),
        "maker": chart_maker(con),
        "orders": chart_order_size(con),
        "clock": chart_clock(con),
    }
    stats["costs"] = chart_costs(con, stats["concentration"]["total"])
    print("--- stats ---")
    for k, v in stats.items():
        print(k, v)


if __name__ == "__main__":
    main()

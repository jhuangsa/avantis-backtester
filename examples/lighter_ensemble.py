"""Donchian ensemble on Lighter perps.

The traded price, the spread, and the funding are Lighter's. Lighter's own
stats start on 2026-07-30, which is shorter than a 100-day channel, so the
channel is warmed up on the matching Avantis daily series, scaled into
Lighter's price units. Profit and loss start only once Lighter's own opens
exist.

Four breakout systems, 10, 20, 50, and 100 daily bars, each stay long after a
close above the prior channel high and short after a close below the prior
channel low. The ensemble vote is their average. Equal risk means each name's
weight is the vote times a common risk budget divided by that name's daily
ATR. The book targets 10% annualized volatility if the names were uncorrelated.

Costs, charged on the notional that changes hands: Lighter premium taker fee
of 2.8 bps, plus half that name's median bid-ask spread, plus hourly funding.
A standard account pays no fee; that case is reported beside the premium case.

The lookback grid is fixed before looking at the result. It is the robustness
neighbourhood, and it is the set scored by White's Reality Check, Hansen's
SPA, and the deflated Sharpe ratio.
"""

import json
import math
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache" / "avantis-backtester" / "lighter-ensemble"
URL = os.environ.get("CH_URL", "https://klvu1o0hu6.us-east-1.aws.clickhouse.cloud:8443")
LIGHTER_START = date(2026, 7, 30)
ANN_VOL = 0.10
FEE_PREMIUM = 0.00028  # 2.8 bps, premium taker, zero LIT staked
BLOCK = 5
BOOTS = 2000
SEED = 7
CENTER = (10, 20, 50, 100)
GRIDS = (
    (8, 16, 40, 80),
    (9, 18, 45, 90),
    (10, 20, 50, 100),
    (11, 22, 55, 110),
    (12, 24, 60, 120),
    (10, 20, 40, 80),
    (10, 30, 60, 120),
    (7, 14, 50, 100),
    (15, 30, 50, 100),
)
WARM = max(max(g) for g in GRIDS)


def ch(sql, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return
    user = os.environ["CH_USER"]
    password = os.environ["CH_PASSWORD"]
    partial = dest.with_suffix(dest.suffix + ".part")
    proc = subprocess.run(
        ["curl", "-sS", "--fail-with-body", "--max-time", "240", "--user", f"{user}:{password}", URL, "--data-binary", "@-"],
        input=sql,
        text=True,
        stdout=partial.open("w"),
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        partial.unlink(missing_ok=True)
        raise SystemExit(proc.stderr.strip() or "clickhouse failed")
    head = partial.open().readline()
    if head.startswith("Code:"):
        detail = partial.read_text(errors="replace")[:500]
        partial.unlink()
        raise SystemExit(detail)
    partial.replace(dest)


def sql_list(symbols):
    return ",".join("'" + s.replace("'", "") + "'" for s in symbols)


def candidates(sym):
    out = [
        f"Crypto.{sym}/USD",
        f"Equity.US.{sym}/USD",
        f"Equity.{sym}/USD",
        f"Metal.{sym}/USD",
        f"Commodities.{sym}/USD",
        f"FX.{sym}/USD",
    ]
    if sym.endswith("USD") and len(sym) == 6:
        out.append(f"FX.{sym[:3]}/{sym[3:]}")
    if sym.startswith("1000") and len(sym) > 4:
        base = sym[4:]
        out.append(f"Crypto.{base}/USD")
    return out


def load_map():
    lighter = []
    span = {}
    for line in Path("/tmp/lighter_symbols.tsv").read_text().splitlines():
        if line.strip():
            lighter.append(line.split("\t")[0])
    for line in Path("/tmp/avantis_symbol_span.tsv").read_text().splitlines():
        if not line.strip():
            continue
        sym, start, end, days = line.split("\t")
        span[sym] = (start[:10], end[:10], int(days))
    mapping = {}
    for sym in lighter:
        for cand in candidates(sym):
            if cand in span and span[cand][0] <= "2026-03-01":
                mapping[sym] = cand
                break
    return mapping


def download(mapping):
    CACHE.mkdir(parents=True, exist_ok=True)
    av_syms = sorted(set(mapping.values()))
    listed = sql_list(av_syms)
    ch(
        f"""
SELECT symbol, pair_name, toString(min(timestamp)), toString(max(timestamp)), uniqExact(toDate(timestamp))
FROM market_data.avantis_candles_1m
WHERE symbol IN ({listed}) AND timestamp >= toDateTime('2026-01-01 00:00:00')
GROUP BY symbol, pair_name
FORMAT TSV
SETTINGS max_execution_time=180
""",
        CACHE / "pairs.tsv",
    )
    best = {}
    for line in (CACHE / "pairs.tsv").read_text().splitlines():
        if not line.strip():
            continue
        sym, pair, _a, _b, days = line.split("\t")
        days = int(days)
        if sym not in best or days > best[sym][1]:
            best[sym] = (pair, days)
    pair_filter = ",".join(
        f"('{s.replace(chr(39), '')}','{p.replace(chr(39), '')}')" for s, (p, _) in best.items()
    )
    ch(
        f"""
SELECT symbol, toString(d), o, h, l, c FROM (
  SELECT
    c.symbol AS symbol,
    toDate(c.timestamp) AS d,
    argMin(c.open, c.timestamp) AS o,
    max(c.high) AS h,
    min(c.low) AS l,
    argMax(c.close, c.timestamp) AS c
  FROM market_data.avantis_candles_1m AS c
  WHERE c.timestamp >= toDateTime('2026-01-01 00:00:00')
    AND (c.symbol, c.pair_name) IN ({pair_filter})
  GROUP BY symbol, d
)
ORDER BY symbol, d
FORMAT TSV
SETTINGS max_execution_time=180
""",
        CACHE / "avantis_daily.tsv",
    )
    ch(
        """
SELECT symbol, toString(d), o, h, l, c, n FROM (
  SELECT
    symbol,
    toDate(captured_at) AS d,
    argMin(mid_price, captured_at) AS o,
    max(mid_price) AS h,
    min(mid_price) AS l,
    argMax(mid_price, captured_at) AS c,
    count() AS n
  FROM market_data.lighter_market_stats_updates
  WHERE mid_price > 0
  GROUP BY symbol, d
)
WHERE n >= 600
ORDER BY symbol, d
FORMAT TSV
SETTINGS max_execution_time=180
""",
        CACHE / "lighter_daily.tsv",
    )
    ch(
        """
SELECT symbol, med_spread FROM (
  SELECT symbol, quantile(0.5)((best_ask_price - best_bid_price) / mid_price) AS med_spread
  FROM market_data.lighter_market_stats_updates
  WHERE mid_price > 0 AND best_bid_price > 0 AND best_ask_price >= best_bid_price
  GROUP BY symbol
)
FORMAT TSV
SETTINGS max_execution_time=180
""",
        CACHE / "spreads.tsv",
    )
    ch(
        """
SELECT symbol, toString(funding_timestamp), fr FROM (
  SELECT symbol, funding_timestamp, any(funding_rate) AS fr
  FROM market_data.lighter_market_stats_updates
  GROUP BY symbol, funding_timestamp
)
ORDER BY symbol, funding_timestamp
FORMAT TSV
SETTINGS max_execution_time=180
""",
        CACHE / "funding.tsv",
    )


def parse_day(text):
    return datetime.strptime(text[:10], "%Y-%m-%d").date()


def load_bars(mapping):
    avantis = {sym: {} for sym in mapping.values()}
    for line in (CACHE / "avantis_daily.tsv").read_text().splitlines():
        if not line.strip():
            continue
        sym, day, o, h, l, c = line.split("\t")
        if sym not in avantis:
            continue
        avantis[sym][parse_day(day)] = (float(o), float(h), float(l), float(c))
    lighter = {sym: {} for sym in mapping}
    for line in (CACHE / "lighter_daily.tsv").read_text().splitlines():
        if not line.strip():
            continue
        sym, day, o, h, l, c, _n = line.split("\t")
        if sym not in lighter:
            continue
        lighter[sym][parse_day(day)] = (float(o), float(h), float(l), float(c))
    spreads = {}
    for line in (CACHE / "spreads.tsv").read_text().splitlines():
        if not line.strip():
            continue
        sym, med = line.split("\t")
        spreads[sym] = float(med)
    funding = {sym: {} for sym in mapping}
    for line in (CACHE / "funding.tsv").read_text().splitlines():
        if not line.strip():
            continue
        sym, ts, fr = line.split("\t")
        if sym not in funding:
            continue
        moment = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        # Stored number is percent per hour. 0.0012 means 0.0012% per hour.
        funding[sym][moment] = float(fr) / 100.0
    return avantis, lighter, spreads, funding


def splice_one(lighter_name, avantis_name, lighter, avantis):
    live = lighter.get(lighter_name) or {}
    hist = avantis.get(avantis_name) or {}
    overlap = sorted(set(live) & set(hist))
    if len(overlap) < 10:
        return None
    ratios = np.array([live[d][3] / hist[d][3] for d in overlap[:10] if hist[d][3] > 0 and live[d][3] > 0])
    if len(ratios) < 10:
        return None
    scale = float(np.median(ratios))
    cv = float(ratios.std() / ratios.mean()) if ratios.mean() else 99
    if not math.isfinite(scale) or scale <= 0 or cv > 0.05:
        return None
    bars = {}
    first_clock = min(live)
    for day, ohlc in hist.items():
        if day >= first_clock:
            continue
        o, h, l, c = ohlc
        if min(o, h, l, c) <= 0:
            continue
        bars[day] = (o * scale, h * scale, l * scale, c * scale, False)
    for day, ohlc in live.items():
        o, h, l, c = ohlc
        if min(o, h, l, c) <= 0:
            continue
        bars[day] = (o, h, l, c, True)
    days = sorted(bars)
    live_days = [day for day in days if bars[day][4]]
    if not live_days:
        return None
    first_live = live_days[0]
    if days.index(first_live) < WARM:
        return None
    return {
        "days": days,
        "ohlc": [bars[d][:4] for d in days],
        "live": np.array([bars[d][4] for d in days]),
        "scale": scale,
        "first": first_live,
    }


def wilder_atr(high, low, close, n=14):
    tr = np.zeros(len(close))
    tr[0] = high[0] - low[0]
    for i in range(1, len(close)):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    atr = np.full(len(close), np.nan)
    if len(close) <= n:
        return atr
    # First ATR is the mean of the first n true ranges, published on that bar.
    atr[n - 1] = tr[:n].mean()
    for i in range(n, len(close)):
        atr[i] = (atr[i - 1] * (n - 1) + tr[i]) / n
    return atr


def breakout_state(high, low, close, window):
    state = np.zeros(len(close))
    cur = 0.0
    for i in range(window, len(close)):
        upper = high[i - window : i].max()
        lower = low[i - window : i].min()
        if close[i] > upper:
            cur = 1.0
        elif close[i] < lower:
            cur = -1.0
        state[i] = cur
    return state


def prepare(books):
    prepared = {}
    for name, book in books.items():
        o = np.array([x[0] for x in book["ohlc"]])
        h = np.array([x[1] for x in book["ohlc"]])
        l = np.array([x[2] for x in book["ohlc"]])
        c = np.array([x[3] for x in book["ohlc"]])
        prepared[name] = {
            "days": book["days"],
            "index": {day: i for i, day in enumerate(book["days"])},
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "live": book["live"],
            "atr": wilder_atr(h, l, c, 14),
            "votes": {w: breakout_state(h, l, c, w) for w in sorted({x for g in GRIDS for x in g})},
        }
    return prepared


def portfolio(prepared, windows, fee, spreads, funding, names):
    """Equal-risk book. Returns the daily net simple return on each mark day."""
    calendar = sorted({d for name in names for i, d in enumerate(prepared[name]["days"]) if prepared[name]["live"][i]})
    # Keep days present in at least half the universe, so one thin name cannot set the clock.
    keep = []
    for day in calendar:
        have = sum(1 for name in names if day in prepared[name]["index"] and prepared[name]["live"][prepared[name]["index"][day]])
        if have >= max(10, len(names) // 2):
            keep.append(day)
    calendar = keep
    m = len(names)
    daily_risk = ANN_VOL / math.sqrt(365.0) / math.sqrt(m)
    weights = {name: {} for name in names}
    for name in names:
        book = prepared[name]
        for day, i in book["index"].items():
            vote = float(np.mean([book["votes"][w][i] for w in windows]))
            atr = book["atr"][i]
            price = book["close"][i]
            if not math.isfinite(atr) or atr <= 0 or price <= 0:
                weights[name][day] = 0.0
                continue
            raw = vote * daily_risk / (atr / price)
            weights[name][day] = float(np.clip(raw, -0.15, 0.15))

    rows = []
    prev_w = {name: 0.0 for name in names}
    for k in range(1, len(calendar) - 1):
        signal_day = calendar[k - 1]
        entry_day = calendar[k]
        exit_day = calendar[k + 1]
        gross = 0.0
        cost = 0.0
        fund = 0.0
        traded = 0.0
        gross_exposure = 0.0
        for name in names:
            book = prepared[name]
            slip = 0.5 * max(spreads.get(name, 0.0), 0.0)
            missing = (
                entry_day not in book["index"]
                or exit_day not in book["index"]
                or not book["live"][book["index"][entry_day]]
                or not book["live"][book["index"][exit_day]]
            )
            if missing:
                turn = abs(prev_w[name])
                traded += turn
                cost += turn * (fee + slip)
                prev_w[name] = 0.0
                continue
            w = weights[name].get(signal_day, 0.0)
            ent = book["open"][book["index"][entry_day]]
            ex = book["open"][book["index"][exit_day]]
            if ent <= 0 or ex <= 0:
                prev_w[name] = 0.0
                continue
            turn = abs(w - prev_w[name])
            traded += turn
            cost += turn * (fee + slip)
            gross += w * (ex / ent - 1.0)
            gross_exposure += abs(w)
            start = datetime(entry_day.year, entry_day.month, entry_day.day, tzinfo=timezone.utc)
            end = datetime(exit_day.year, exit_day.month, exit_day.day, tzinfo=timezone.utc)
            pay = 0.0
            cursor = start + timedelta(hours=1)
            rates = funding.get(name, {})
            while cursor <= end:
                rate = rates.get(cursor)
                if rate is not None:
                    pay += rate
                cursor += timedelta(hours=1)
            fund += -w * pay
            prev_w[name] = w
        rows.append(
            {
                "day": exit_day.isoformat(),
                "net": gross - cost + fund,
                "gross": gross,
                "cost": cost,
                "funding": fund,
                "traded": traded,
                "exposure": gross_exposure,
            }
        )
    return rows


def position_trades(prepared, windows, fee, spreads, funding, names, oos_start):
    """One trade is one name held in one direction until the vote flips or goes flat.

    The return is in basis points of that name's notional, after a round trip of
    the taker fee and half the spread, and after funding while the trade is open.
    """
    calendar = sorted({d for name in names for i, d in enumerate(prepared[name]["days"]) if prepared[name]["live"][i]})
    keep = []
    for day in calendar:
        have = sum(1 for name in names if day in prepared[name]["index"] and prepared[name]["live"][prepared[name]["index"][day]])
        if have >= max(10, len(names) // 2):
            keep.append(day)
    calendar = keep
    trades = []
    for name in names:
        book = prepared[name]
        slip = 0.5 * max(spreads.get(name, 0.0), 0.0)
        spell = None
        last_exit = None
        last_exit_day = None

        def finish(spell, exit_px, exit_day):
            if spell is None or exit_px is None or spell["entry"] <= 0 or exit_px <= 0:
                return
            gross = spell["sign"] * (exit_px / spell["entry"] - 1.0)
            net = gross - 2.0 * (fee + slip) - spell["sign"] * spell["fund"]
            trades.append(
                {
                    "name": name,
                    "entry": spell["entry_day"].isoformat(),
                    "exit": exit_day.isoformat(),
                    "side": "long" if spell["sign"] > 0 else "short",
                    "net_bps": net * 1e4,
                    "year": spell["entry_day"].year,
                    "oos": spell["entry_day"] >= oos_start,
                }
            )

        for k in range(1, len(calendar) - 1):
            signal_day = calendar[k - 1]
            entry_day = calendar[k]
            exit_day = calendar[k + 1]
            if (
                entry_day not in book["index"]
                or exit_day not in book["index"]
                or signal_day not in book["index"]
                or not book["live"][book["index"][entry_day]]
                or not book["live"][book["index"][exit_day]]
            ):
                if spell is not None:
                    finish(spell, last_exit, last_exit_day)
                    spell = None
                continue
            vote = float(np.mean([book["votes"][w][book["index"][signal_day]] for w in windows]))
            sign = 1 if vote > 0 else -1 if vote < 0 else 0
            ent = book["open"][book["index"][entry_day]]
            ex = book["open"][book["index"][exit_day]]
            if ent <= 0 or ex <= 0:
                if spell is not None:
                    finish(spell, last_exit, last_exit_day)
                    spell = None
                continue
            if spell is not None and spell["sign"] != sign:
                finish(spell, ent, entry_day)
                spell = None
            if sign == 0:
                continue
            if spell is None:
                spell = {"sign": sign, "entry": ent, "entry_day": entry_day, "fund": 0.0}
            start = datetime(entry_day.year, entry_day.month, entry_day.day, tzinfo=timezone.utc)
            end = datetime(exit_day.year, exit_day.month, exit_day.day, tzinfo=timezone.utc)
            cursor = start + timedelta(hours=1)
            rates = funding.get(name, {})
            while cursor <= end:
                rate = rates.get(cursor)
                if rate is not None:
                    spell["fund"] += rate
                cursor += timedelta(hours=1)
            last_exit = ex
            last_exit_day = exit_day
        if spell is not None:
            finish(spell, last_exit, last_exit_day)
    return trades


def trade_sharpe(trades):
    """Annualized Sharpe of closed trades. Trades per year uses first entry to last."""
    if len(trades) < 2:
        return None
    values = np.array([t["net_bps"] for t in trades], dtype=float)
    deviation = float(values.std(ddof=1))
    if deviation == 0:
        return None
    per_trade = float(values.mean() / deviation)
    days = sorted(datetime.strptime(t["entry"], "%Y-%m-%d").date() for t in trades)
    span_years = (days[-1] - days[0]).days / 365.25
    if span_years <= 0:
        return None
    return per_trade * math.sqrt(len(trades) / span_years)


def trade_stats(trades):
    import hf_2bps as hf

    rows = [hf.Row(t["net_bps"], t["year"], t["oos"], t["side"]) for t in trades]
    full, oos, by_year = hf.split(rows)
    groups = {"OOS": [t for t in trades if t["oos"]], "full": trades}
    samples = {"OOS": hf.describe(oos), "full": hf.describe(full)}
    for year, sample in sorted(by_year.items()):
        samples[str(year)] = hf.describe(sample)
        groups[str(year)] = [t for t in trades if t["year"] == year]
    for key, stats in samples.items():
        stats["sharpe"] = trade_sharpe(groups[key])
    primary, verdict = hf.protocol(hf._pairs(full), hf._pairs(oos))
    return {"samples": samples, "primary": primary, "verdict": verdict, "n": len(trades)}


def sharpe(returns):
    x = np.asarray(returns, dtype=float)
    if len(x) < 2 or x.std(ddof=1) == 0:
        return None
    return float(x.mean() / x.std(ddof=1) * math.sqrt(365.0))


def max_drawdown(returns):
    equity = np.cumprod(1.0 + np.asarray(returns, dtype=float))
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    return float(dd.min()) if len(dd) else 0.0


def period_sharpe(returns):
    """Sharpe of one observation, the unit the deflated Sharpe formula uses."""
    x = np.asarray(returns, dtype=float)
    if len(x) < 2 or x.std(ddof=1) == 0:
        return None
    return float(x.mean() / x.std(ddof=1))


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p):
    """Inverse standard normal. Acklam's approximation, accurate to about 1e-9."""
    if p <= 0.0 or p >= 1.0:
        raise ValueError("probability")
    a = ( -3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00 )
    b = ( -5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01, -1.328068155288572e01 )
    c = ( -7.784894002430293e-03, -3.223964580411063e-01, -2.400758277161838e00, -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00 )
    d = ( 7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00 )
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def deflated_sharpe(returns, n_trials):
    x = np.asarray(returns, dtype=float)
    t = len(x)
    sr = period_sharpe(x)
    if sr is None or t < 10 or n_trials < 1:
        return None
    skew = float(((x - x.mean()) ** 3).mean() / (x.std(ddof=0) ** 3))
    kurt = float(((x - x.mean()) ** 4).mean() / (x.std(ddof=0) ** 4))  # Pearson, normal is 3
    bracket = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if bracket <= 0:
        return None
    sr_var = bracket / (t - 1)
    emc = 0.5772156649015329
    n = max(int(n_trials), 1)
    if n == 1:
        sr0 = 0.0
    else:
        sr0 = math.sqrt(sr_var) * (
            (1.0 - emc) * norm_ppf(1.0 - 1.0 / n) + emc * norm_ppf(1.0 - 1.0 / (n * math.e))
        )
    z = (sr - sr0) / math.sqrt(sr_var)
    return {
        "sr_annual": sr * math.sqrt(365.0),
        "dsr": float(norm_cdf(z)),
        "sr0_annual": sr0 * math.sqrt(365.0),
        "skew": skew,
        "kurtosis": kurt,
        "n": t,
        "trials": n,
    }


def stationary_indices(t, mean_block, rng):
    p = 1.0 / mean_block
    idx = np.empty(t, dtype=int)
    idx[0] = rng.integers(0, t)
    renew = rng.random(t) < p
    jumps = rng.integers(0, t, size=t)
    for i in range(1, t):
        idx[i] = jumps[i] if renew[i] else (idx[i - 1] + 1) % t
    return idx


def reality_and_spa(matrix):
    """White's Reality Check and Hansen's SPA. Columns are lookback sets.

    The benchmark is zero. The null is that every lookback has mean return <= 0.
    Days are resampled together, so dependence across lookbacks stays intact.
    """
    x = np.asarray(matrix, dtype=float)
    t, k = x.shape
    means = x.mean(axis=0)
    sigma = x.std(axis=0, ddof=1)
    sigma = np.where(sigma > 0, sigma, np.inf)
    observed = math.sqrt(t) * float(means.max())
    observed_spa = math.sqrt(t) * float(np.max(means / sigma))
    hurdle = sigma * math.sqrt(2.0 * math.log(math.log(t)) / t)
    center = np.where(means >= -hurdle, means, 0.0)
    rng = np.random.default_rng(SEED)
    rc_hit = 0
    spa_hit = 0
    recentered = x - means
    for _ in range(BOOTS):
        ix = stationary_indices(t, BLOCK, rng)
        draw = recentered[ix]
        rc_hit += math.sqrt(t) * float(draw.mean(axis=0).max()) >= observed
        spa_draw = x[ix].mean(axis=0) - center
        spa_hit += math.sqrt(t) * float(np.max(spa_draw / sigma)) >= observed_spa
    return {
        "rc_p": (rc_hit + 1) / (BOOTS + 1),
        "spa_p": (spa_hit + 1) / (BOOTS + 1),
        "best_mean_daily": float(means.max()),
        "days": t,
        "configs": k,
        "boots": BOOTS,
    }


def split_oos(rows, frac=0.2):
    n = len(rows)
    cut = int(n * (1.0 - frac))
    return rows[:cut], rows[cut:]


def pack_curve(rows):
    equity = []
    level = 1.0
    for row in rows:
        level *= 1.0 + row["net"]
        equity.append({"day": row["day"], "equity": level, "net": row["net"]})
    return equity


def summarize(rows, n_trials):
    nets = [r["net"] for r in rows]
    return {
        "days": len(rows),
        "sharpe": sharpe(nets),
        "mean_daily_bps": float(np.mean(nets) * 1e4) if nets else None,
        "vol_annual": float(np.std(nets, ddof=1) * math.sqrt(365) * 100) if len(nets) > 1 else None,
        "total_return": float(np.prod(1.0 + np.asarray(nets)) - 1.0) if nets else None,
        "max_dd": max_drawdown(nets) if nets else None,
        "fees_and_spread": float(sum(r["cost"] for r in rows)),
        "funding": float(sum(r["funding"] for r in rows)),
        "avg_exposure": float(np.mean([r["exposure"] for r in rows])) if rows else None,
        "dsr": deflated_sharpe(nets, n_trials),
    }


def self_check():
    high = np.arange(1, 140, dtype=float)
    low = high - 0.5
    close = high - 0.1
    state = breakout_state(high, low, close, 10)
    if state[-1] != 1.0:
        raise SystemExit("breakout self-check failed")
    flat = np.full(40, 100.0)
    state = breakout_state(flat, flat - 1, flat - 0.2, 10)
    if state[-1] != 0.0:
        raise SystemExit("flat self-check failed")


def main():
    self_check()
    mapping = load_map()
    print(f"mapped names with history back to March 2026 or earlier: {len(mapping)}")
    download(mapping)
    avantis, lighter, spreads, funding = load_bars(mapping)
    books = {}
    dropped = {"overlap": 0, "scale": 0, "warm": 0}
    for lighter_name, avantis_name in mapping.items():
        book = splice_one(lighter_name, avantis_name, lighter, avantis)
        if book is None:
            dropped["overlap"] += 1
            continue
        books[lighter_name] = book
    print(f"spliced books: {len(books)}  dropped: {dropped['overlap']}")
    prepared = prepare(books)
    names = sorted(prepared)
    center_rows = portfolio(prepared, CENTER, FEE_PREMIUM, spreads, funding, names)
    free_rows = portfolio(prepared, CENTER, 0.0, spreads, funding, names)
    gross_rows = portfolio(prepared, CENTER, 0.0, {n: 0.0 for n in names}, {n: {} for n in names}, names)
    print(f"center days {len(center_rows)} names {len(names)}")

    grid_rows = {}
    for windows in GRIDS:
        grid_rows[windows] = portfolio(prepared, windows, FEE_PREMIUM, spreads, funding, names)
    # Align grid returns on the center calendar. A missing day is a zero.
    days = [r["day"] for r in center_rows]
    matrix = []
    sharpes = []
    for windows in GRIDS:
        by_day = {r["day"]: r["net"] for r in grid_rows[windows]}
        matrix.append([by_day.get(day, 0.0) for day in days])
        ins, oos = split_oos(grid_rows[windows])
        sharpes.append(
            {
                "windows": list(windows),
                "full": sharpe([r["net"] for r in grid_rows[windows]]),
                "oos": sharpe([r["net"] for r in oos]),
                "is": sharpe([r["net"] for r in ins]),
            }
        )
    matrix = np.array(matrix, dtype=float).T
    tests = reality_and_spa(matrix)
    ins, oos = split_oos(center_rows)
    oos_sharpes = [row["oos"] for row in sharpes if row["oos"] is not None]
    positive = sum(1 for s in oos_sharpes if s > 0)
    center_oos = sharpe([r["net"] for r in oos])
    stable = center_oos is not None and center_oos > 0 and positive >= math.ceil(0.7 * len(sharpes))
    gate_sharpe = bool(center_oos is not None and center_oos > 0 and stable)
    gate_sig = bool(tests["spa_p"] < 0.05 and summarize(center_rows, len(GRIDS))["dsr"]["dsr"] >= 0.95)
    classes = []
    for name in names:
        src = mapping[name]
        if src.startswith("Crypto"):
            classes.append("crypto")
        elif src.startswith("Equity"):
            classes.append("equity")
        elif src.startswith("FX"):
            classes.append("fx")
        else:
            classes.append("other")
    result = {
        "universe": len(names),
        "mapped": len(mapping),
        "classes": {k: classes.count(k) for k in ("crypto", "equity", "fx", "other")},
        "lighter_from": LIGHTER_START.isoformat(),
        "windows": list(CENTER),
        "ann_vol_target": ANN_VOL,
        "fee_premium": FEE_PREMIUM,
        "names": names,
        "center": summarize(center_rows, len(GRIDS)),
        "center_is": summarize(ins, len(GRIDS)),
        "center_oos": summarize(oos, len(GRIDS)),
        "standard_account": summarize(free_rows, 1),
        "gross_of_costs": summarize(gross_rows, 1),
        "neighbours": sharpes,
        "neighbour_oos_positive": positive,
        "tests": tests,
        "gates": {
            "oos_sharpe_and_stable": gate_sharpe,
            "significant_after_correction": gate_sig,
        },
        "curve": pack_curve(center_rows),
        "oos_start": oos[0]["day"] if oos else None,
    }
    out = CACHE / "result.json"
    out.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: result[k] for k in ("universe", "classes", "center", "center_oos", "standard_account", "gross_of_costs", "tests", "gates", "neighbour_oos_positive")}, indent=2))
    print(f"wrote {out}")
    publish(result)
    return result


def fmt(value, digits=2, signed=False):
    if value is None:
        return "—"
    number = float(value)
    body = f"{abs(number):.{digits}f}"
    if number < 0:
        return "−" + body
    if signed and number > 0:
        return "+" + body
    return body


def pct(value):
    if value is None:
        return "—"
    return fmt(value * 100, 2, signed=True) + "%"


def equity_svg(curve, oos_start):
    vals = [(row["equity"] - 1.0) * 100.0 for row in curve]
    lo = min(min(vals), 0.0)
    hi = max(max(vals), 0.0)
    pad = (hi - lo) * 0.12 or 1.0
    lo -= pad
    hi += pad
    width, height, left, right, top, bottom = 860, 280, 52, 16, 16, 32
    def x_of(i):
        return left + i / (len(vals) - 1) * (width - left - right)
    def y_of(v):
        return top + (hi - v) / (hi - lo) * (height - top - bottom)
    oos_i = next(i for i, row in enumerate(curve) if row["day"] >= oos_start)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Ensemble equity, percent">',
        f'<rect x="{x_of(oos_i):.1f}" y="{top}" width="{x_of(len(vals)-1) - x_of(oos_i):.1f}" height="{height - top - bottom}" fill="rgba(138,90,18,0.10)"/>',
        f'<line x1="{left}" y1="{y_of(0):.1f}" x2="{width - right}" y2="{y_of(0):.1f}" stroke="#1c1428" stroke-width="1"/>',
    ]
    for tick in (0, -2, -4):
        if lo <= tick <= hi:
            parts.append(
                f'<text x="{left - 8}" y="{y_of(tick) + 4:.1f}" text-anchor="end" fill="#5e5672" font-size="12" font-family="Instrument Sans, sans-serif">{tick}%</text>'
            )
    d = " ".join(("M" if i == 0 else "L") + f"{x_of(i):.1f} {y_of(v):.1f}" for i, v in enumerate(vals))
    parts.append(f'<path d="{d}" fill="none" stroke="#1c1428" stroke-width="1.7"/>')
    parts.append(f'<text x="{left}" y="{height - 10}" fill="#5e5672" font-size="12" font-family="Instrument Sans, sans-serif">{curve[0]["day"][:7].replace("-", " ")}</text>')
    parts.append(f'<text x="{x_of(oos_i):.1f}" y="{height - 10}" fill="#5e5672" font-size="12" font-family="Instrument Sans, sans-serif">14 Sep</text>')
    parts.append("</svg>")
    return "".join(parts)


def stat_cells(stats):
    mean = stats.get("mean")
    tee = stats.get("t")
    label = stats.get("label") or ""

    def klass(value):
        if value is None:
            return "num"
        if value > 0:
            return "num pos"
        if value < 0:
            return "num neg"
        return "num"

    verdict_class = {"Confirmed": "pos", "Weak": "weak", "Invalidated": "neg"}.get(label, "")
    win = "—" if stats.get("win") is None else f"{stats['win']:.1f}%"
    sharpe = stats.get("sharpe")
    return (
        f"<td class=\"num\">{stats.get('n', 0)}</td>"
        f"<td class=\"{klass(mean)}\">{fmt(mean, signed=True)}</td>"
        f"<td class=\"{klass(tee)}\">{fmt(tee)}</td>"
        f"<td class=\"{klass(sharpe)}\">{fmt(sharpe)}</td>"
        f"<td class=\"num\">{win}</td>"
        f"<td class=\"{verdict_class}\">{label}</td>"
    )


def section_html(result):
    center = result["center"]
    early = result["center_is"]
    late = result["center_oos"]
    tests = result["tests"]
    rows = []
    for row in result["neighbours"]:
        label = "/".join(str(n) for n in row["windows"])
        mark = ' style="font-weight:500"' if tuple(row["windows"]) == CENTER else ""
        rows.append(
            "<tr>"
            f"<th scope=\"row\"{mark}>{label}</th>"
            f"<td class=\"num\">{fmt(row['full'], signed=True)}</td>"
            f"<td class=\"num\">{fmt(row['is'], signed=True)}</td>"
            f"<td class=\"num\">{fmt(row['oos'], signed=True)}</td>"
            "</tr>"
        )
    classes = result["classes"]
    scored = result.get("trade_table") or {}
    samples = scored.get("samples") or {}
    verdict = scored.get("verdict") or "Invalidated"
    rank_class = {"Confirmed": "confirmed", "Weak": "weak", "Invalidated": "invalidated"}.get(verdict, "invalidated")
    order = [("OOS", "Out of sample"), ("full", "Whole book"), ("2025", "2025"), ("2026", "2026")]
    body_rows = []
    for key, label in order:
        stats = samples.get(key)
        if not stats or not stats.get("n"):
            continue
        body_rows.append(f"<tr><th scope=\"row\">{label}</th>{stat_cells(stats)}</tr>")
    return f"""<!-- ENSEMBLE-START -->
  <article class="strategy" id="ensemble">
    <p class="rank {rank_class}">6</p>
    <div class="body">
      <h3>Lighter Donchian</h3>
      <p class="spec">Four daily breakouts vote. Equal risk. Premium fee, half the spread, and funding.</p>
      <p class="status {rank_class}">Verdict on the closed trades: {verdict}. The book’s own Sharpe over all {center['days']} sessions is {fmt(center['sharpe'], signed=True)}.</p>
      <h4>The rule</h4>
      <ol class="steps">
        <li><strong>The idea.</strong> Four trend systems each vote on every Lighter perpetual that has enough history. You hold the average of the votes, and you size each name so that a quiet name and a wild name risk the same fraction of equity.</li>
        <li><strong>One vote.</strong> The 10-bar system goes long when the daily close breaks above the high of the previous 10 daily bars, and short when it breaks the low. It stays in that position until the opposite break. The 20-bar, 50-bar, and 100-bar systems do the same. The vote you trade is the average of the four, so it steps from −1 to +1 by quarters.</li>
        <li><strong>When you trade.</strong> The break is known at the close. The position starts at the next day’s open and is marked to the open after that.</li>
        <li><strong>How big.</strong> Weight equals the vote times a risk budget, divided by that name’s 14-day average true range. The budget is set so the book would run near 10% annualized volatility if the names did not move together. They do move together: realized volatility was {fmt(center['vol_annual'], 0)}%. No name exceeds 15% of equity. Average gross exposure was {fmt(center['avg_exposure'], 2)}.</li>
        <li><strong>What it costs.</strong> A premium Lighter account pays a 2.8 bp taker fee on the notional that changes hands. The book also pays half that name’s median bid-ask spread, and Lighter’s hourly funding. A long pays when the funding rate is positive. A standard account pays no fee; spread and funding remain.</li>
        <li><strong>The tape.</strong> Lighter’s own prices, spreads, and funding start on 30 July 2026. A 100-bar channel is older than that tape, so the earlier highs and lows come from the matching Avantis daily series, rescaled into Lighter’s price. {result['universe']} names kept a stable scale: {classes['crypto']} crypto, {classes['equity']} equities, {classes['fx']} FX, {classes['other']} other. Each row in the table is one name, held one way, until that vote flips or goes flat.</li>
      </ol>
      <table class="stats">
        <thead>
          <tr><th>Sample</th><th class="num">Trades</th><th class="num">Mean bps</th><th class="num">t</th><th class="num">Sharpe</th><th class="num">Win</th><th>Verdict</th></tr>
        </thead>
        <tbody>
          {''.join(body_rows)}
        </tbody>
      </table>
      <p class="fine">Same scoring as the five above: mean and t are on the closed trades, in net basis points, and a sample under 30 trades cannot be Confirmed. Out of sample here is an entry on or after {result['oos_start']}. There is no 2025 row because this tape starts in 2026.</p>
      <h4>The gates in the proposal</h4>
      <table class="stats" style="min-width:0">
        <thead>
          <tr><th>Gate</th><th>Asked for</th><th>This run</th></tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">1. Backtest</th>
            <td>Positive net Sharpe out of sample, and the neighbours agree</td>
            <td>Last 12 sessions, from {result['oos_start']}: Sharpe {fmt(late['sharpe'], signed=True)}, and 9 of 9 neighbours positive. The first 44 sessions: Sharpe {fmt(early['sharpe'], signed=True)}. The whole tape: Sharpe {fmt(center['sharpe'], signed=True)}.</td>
          </tr>
          <tr>
            <th scope="row">2. Significance</th>
            <td>White’s Reality Check or Hansen’s SPA, and a deflated Sharpe, on the whole grid</td>
            <td>Reality Check p = {fmt(tests['rc_p'], 2)}. SPA p = {fmt(tests['spa_p'], 2)}. Deflated Sharpe probability on the 10/20/50/100 book, charged for 9 tries, is {fmt(center['dsr']['dsr'], 2)}. None of these clear 0.05 or 0.95.</td>
          </tr>
          <tr>
            <th scope="row">3. Shadow</th>
            <td>Four to eight weeks of live signals against the recorded book</td>
            <td>Not started.</td>
          </tr>
          <tr>
            <th scope="row">4. Live, small</th>
            <td>Minimum size, leverage capped at 2×, then a step toward 2–4×</td>
            <td>Not started. Gates 1 and 2 are not both a reason to.</td>
          </tr>
        </tbody>
      </table>
      <p>Read gate 1 with the length attached. Twelve sessions at the end of the tape are positive, and every nearby lookback is positive on those same twelve sessions. That is one bounce, counted nine times. Over all {center['days']} sessions the premium-fee book returned {pct(center['total_return'])}, Sharpe {fmt(center['sharpe'], signed=True)}, deepest drawdown {pct(center['max_dd'])}. Funding took {fmt(abs(center['funding']) * 100, 2)}% of equity. Fees and half-spreads took {fmt(center['fees_and_spread'] * 100, 2)}%. With the fee set to zero, Sharpe is {fmt(result['standard_account']['sharpe'], signed=True)}. Before every cost, Sharpe is {fmt(result['gross_of_costs']['sharpe'], signed=True)}.</p>
      <figure class="chart">
        {equity_svg(result['curve'], result['oos_start'])}
        <figcaption>Compounded return of the 10/20/50/100 book, premium taker fee, half spread, and funding. The pale band is the last 12 sessions. This path is the account, not an equal-weighted sum of trades.</figcaption>
      </figure>
      <h4>Neighbouring lookbacks</h4>
      <p class="section-lead">Same markets, same costs, same equal-risk rule. Only the four windows change. Sharpe is annualized from the daily book.</p>
      <table class="stats">
        <thead>
          <tr><th>Windows</th><th class="num">All {center['days']}</th><th class="num">First 44</th><th class="num">Last 12</th></tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
      <p class="fine">The best full-tape Sharpe in this grid is {fmt(max(row['full'] for row in result['neighbours']), signed=True)}, on 15/30/50/100. The bootstrap still puts that best result near the middle of what zero-mean lookbacks produce. Block length {BLOCK} days, {BOOTS} resamples, days drawn together so the nine books keep their overlap.</p>
    </div>
  </article>
<!-- ENSEMBLE-END -->
"""


def ensure_trade_table(result):
    if result.get("trade_table") and result["trade_table"]["samples"]["full"].get("sharpe") is not None:
        return result
    mapping = load_map()
    avantis, lighter, spreads, funding = load_bars(mapping)
    books = {}
    for lighter_name, avantis_name in mapping.items():
        book = splice_one(lighter_name, avantis_name, lighter, avantis)
        if book is not None:
            books[lighter_name] = book
    prepared = prepare(books)
    names = sorted(prepared)
    oos_day = datetime.strptime(result["oos_start"], "%Y-%m-%d").date()
    trades = position_trades(prepared, CENTER, FEE_PREMIUM, spreads, funding, names, oos_day)
    result["trade_table"] = trade_stats(trades)
    print(
        f"closed trades {result['trade_table']['n']} "
        f"verdict {result['trade_table']['verdict']} "
        f"full n {result['trade_table']['samples']['full']['n']} "
        f"mean {result['trade_table']['samples']['full']['mean']}"
    )
    return result


def publish(result=None):
    import re

    if result is None:
        result = json.loads((CACHE / "result.json").read_text())
    result = ensure_trade_table(result)
    block = section_html(result)
    for path in (HERE / "folio_template.html", HERE / "hf_2bps.html"):
        text = path.read_text()
        if "<!-- ENSEMBLE-START -->" in text:
            pre, rest = text.split("<!-- ENSEMBLE-START -->", 1)
            _, post = rest.split("<!-- ENSEMBLE-END -->", 1)
            text = pre + post
        text = re.sub(r"\n  <a class=\"lighter-banner\" href=\"#ensemble\">.*?</a>\n", "\n", text, count=1, flags=re.S)
        text = re.sub(r"\s*<li><a href=\"#ensemble\">.*?</a></li>", "", text, count=1)
        needle = '<li><a href="#weekend">Weekend gap</a></li>'
        if needle not in text:
            raise SystemExit(f"no weekend link in {path}")
        text = text.replace(
            needle,
            needle + '\n    <li><a href="#ensemble">Lighter Donchian</a></li>',
            1,
        )
        footer = '<footer class="colophon">'
        if footer not in text:
            raise SystemExit(f"no footer in {path}")
        text = text.replace(footer, block + "\n  " + footer, 1)
        path.write_text(text)
        print(f"patched {path.name}")


if __name__ == "__main__":
    if "--publish" in sys.argv:
        publish()
    else:
        main()


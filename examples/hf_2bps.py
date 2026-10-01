"""Score the five 2 bp Avantis cells on ETH (and BTC, for the residual).

From the repository root, with ClickHouse credentials in the environment:

    CH_USER=... CH_PASSWORD=... python3 examples/hf_2bps.py

CH_URL defaults to the untagged market-data host, CLICKHOUSE_ORIGINAL_HOST
in .env. Candles are cached under
data/candles/hf_2bps/. Pass --refresh to download again.

The series is symbol Crypto.ETH/USD, pair_name ETH_UPSIDE/USD (BTC_UPSIDE/USD
for the residual). That pair's raw row count, including four identical
duplicate minutes, is 906,629. A 5m or 15m bar is emitted only when the
bucket's closing minute is present. That is the resample that produces
181,397 five-minute bars and 60,494 fifteen-minute bars.

The picture of these results is examples/hf_2bps.html. Rebuild it with
examples/build_hf_folio.py after a fresh score.

Fills, stops, targets, time exits, and the 1 bp fee per fill are the engine's.
A session flatten is a rule exit: it is known on the last bar before 20:00 UTC
and paid at the next open. A caller-built Line carries the clock and the
residual, which are not indicators.
"""

import argparse
import csv
import math
import os
import subprocess
import sys
import time
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest import (
    Above,
    All,
    Bar,
    Below,
    Cross,
    Hypothesis,
    Indicator,
    Line,
    Price,
    Threshold,
    backtest,
)

FEE = 0.0001
START = datetime(2025, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 24, tzinfo=timezone.utc)
OOS_START = START + 0.8 * (END - START)
OOS_TS = OOS_START.timestamp()
SESSION_START = 13 * 60 + 30
SESSION_END = 20 * 60
CACHE = Path(__file__).resolve().parents[1] / "data" / "candles" / "hf_2bps"
ROOT = Path(__file__).resolve().parents[1]

Row = namedtuple("Row", "net_bps year oos cause")

PUBLISHED = {
    "TOD-NYL-ETH-tp8-sl3-T26": {
        "OOS": (126, 10.03, 2.16, 40.5),
        "full": (631, -0.73, -0.38, 29.5),
        "2025": (365, -3.58, None, None),
        "2026": (266, 3.17, None, None),
    },
    "TOD-NYL-ETH-tp8-sl2-T26": {
        "OOS": (126, 8.76, 2.18, 33.3),
        "full": (631, -0.95, -0.59, 22.0),
    },
    "S4-DON-W116-tp6p0-sl2-T196-ETH": {
        "OOS": (83, 33.98, 1.88, 34.9),
        "full": (388, 10.70, 1.18, 29.1),
        "2025": (227, 6.04, None, None),
        "2026": (161, 17.28, None, None),
    },
    "P4-RSI7-eg21-tp125-T26-ETH": {
        "full": (352, 5.01, 1.66, 34.7),
        "2025": (187, 7.09, None, None),
        "2026": (165, 2.65, None, None),
        "OOS": (76, 1.33, 0.23, 36.8),
    },
    "R1-ZF-W48-Z1p5-tp8-T24-ETH": {
        "OOS": (1357, 1.10, 0.94, 38.2),
        "full": (7559, -0.61, -1.18, 34.1),
        "2025": (4552, -1.03, None, None),
        "2026": (3007, 0.02, None, None),
    },
    "O1-WGAP-g60-tp5-T180-ETH": {
        "full": (69, 3.94, 0.82, 44.9),
        "2025": (41, 1.17, None, None),
        "2026": (28, 8.00, None, None),
        "OOS": (12, 1.33, 0.11, 41.7),
    },
}


def resample(rows, minutes):
    """Clock buckets. Emit one only when its closing minute is present.

    `rows` is sorted unique (unix_open, open, high, low, close). The bar's
    open time is the bucket start. Its open is the first present minute.
    """
    if minutes < 1:
        raise ValueError("minutes")
    step = minutes * 60
    close_off = (minutes - 1) * 60
    out = []
    index = 0
    count = len(rows)
    while index < count:
        start = rows[index][0] - (rows[index][0] % step)
        stop = start + step
        close_ts = start + close_off
        open_ = rows[index][1]
        high = rows[index][2]
        low = rows[index][3]
        close = None
        saw_close = False
        while index < count and rows[index][0] < stop:
            ts, _, hi, lo, cl = rows[index]
            if hi > high:
                high = hi
            if lo < low:
                low = lo
            if ts == close_ts:
                saw_close = True
                close = cl
            index += 1
        if saw_close:
            out.append((start, open_, high, low, close))
    return out


def trade_net_return(entry, exit_price, side, cause, fee):
    """Net simple return of one trade. A mark at the last close pays no exit fee."""
    if side == "long":
        gross = exit_price / entry
    else:
        gross = 1 + (entry - exit_price) / entry
    factor = (1 - fee) * gross
    if cause != "still open":
        factor *= 1 - fee
    return factor - 1


def t_stat(values):
    count = len(values)
    if count < 2:
        return None
    mean = sum(values) / count
    var = sum((value - mean) ** 2 for value in values) / (count - 1)
    if var == 0:
        return math.inf if mean > 0 else 0.0
    return mean / math.sqrt(var / count)


def year_ok(rows):
    """rows are (net bps, year). Years with n < 10 do not vote."""
    buckets = {}
    for net, year in rows:
        buckets.setdefault(year, []).append(net)
    eligible = [nets for nets in buckets.values() if len(nets) >= 10]
    if not eligible:
        return True
    positive = sum(1 for nets in eligible if sum(nets) / len(nets) > 0)
    if len(eligible) >= 4:
        return positive >= 3
    return positive == len(eligible)


def sample_label(rows):
    """Label one sample. Confirmed requires t >= 2 and every eligible year up.

    Weak is the band under that bar: mean positive and t >= 1. The published
    Donchian full sample (t = 1.18) is Weak and the residual OOS (t = 0.94)
    is Invalidated, so 1.0 is the cut used here.
    """
    count = len(rows)
    if count < 30:
        return "Inconclusive"
    values = [net for net, _ in rows]
    mean = sum(values) / count
    stat = t_stat(values)
    if mean > 0 and stat is not None and stat >= 2 and year_ok(rows):
        return "Confirmed"
    if mean > 0 and stat is not None and stat >= 1:
        return "Weak"
    return "Invalidated"


def protocol(full_rows, oos_rows):
    """Primary sample is OOS when it has at least 80 trades, else the full sample.

    A full-sample primary cannot be Confirmed.
    """
    if len(oos_rows) >= 80:
        return "OOS", sample_label(oos_rows)
    label = sample_label(full_rows)
    if label == "Confirmed":
        label = "Weak"
    return "full", label


def _pairs(rows):
    return [(row.net_bps, row.year) for row in rows]


def describe(rows):
    count = len(rows)
    if count == 0:
        return {"n": 0, "mean": None, "t": None, "win": None, "label": "Inconclusive"}
    values = [row.net_bps for row in rows]
    mean = sum(values) / count
    wins = sum(1 for value in values if value > 0)
    return {
        "n": count,
        "mean": mean,
        "t": t_stat(values),
        "win": 100 * wins / count,
        "label": sample_label(_pairs(rows)),
    }


def causes_of(rows):
    found = {}
    for row in rows:
        found[row.cause] = found.get(row.cause, 0) + 1
    return found


def score(hypothesis, bars, times, bar_size):
    result = backtest(hypothesis, bars, fee=FEE, bar_size=bar_size)
    rows = []
    for trade in result.trades:
        net = trade_net_return(
            trade.entry_price, trade.exit_price, trade.side, trade.cause, FEE
        )
        ts = times[trade.entry_bar]
        year = datetime.fromtimestamp(ts, timezone.utc).year
        rows.append(Row(net * 1e4, year, ts >= OOS_TS, trade.cause))
    return result, rows


def split(rows):
    full = rows
    oos = [row for row in rows if row.oos]
    by_year = {}
    for row in rows:
        by_year.setdefault(row.year, []).append(row)
    return full, oos, by_year


def _fmt(value, digits):
    if value is None:
        return "—"
    if value == math.inf:
        return "inf"
    return f"{value:.{digits}f}"


def _print_sample(name, stats, published):
    line = (
        f"  {name:<6} {stats['n']:6d}  {_fmt(stats['mean'], 2):>8}  "
        f"{_fmt(stats['t'], 2):>6}  {_fmt(stats['win'], 1):>6}  {stats['label']}"
    )
    if published is not None:
        pn, pmean, pt, pwin = published
        line += f"    published n {pn}  mean {_fmt(pmean, 2)}  t {_fmt(pt, 2)}  win {_fmt(pwin, 1)}"
    print(line)


def report(cell, rows, bar_note):
    full, oos, by_year = split(rows)
    primary, verdict = protocol(_pairs(full), _pairs(oos))
    published = PUBLISHED.get(cell, {})
    print(f"\n{cell}")
    print(f"  {bar_note}")
    print(f"  primary {primary}  protocol {verdict}")
    print("  sample      n   mean bps      t    win%  label")
    order = [("OOS", oos), ("full", full)]
    for year in sorted(by_year):
        order.append((str(year), by_year[year]))
    for name, sample in order:
        _print_sample(name, describe(sample), published.get(name))
    cause = causes_of(full)
    print("  causes " + ", ".join(f"{name} {cause[name]}" for name in sorted(cause)))
    return {
        "cell": cell,
        "primary": primary,
        "verdict": verdict,
        "samples": {name: describe(sample) for name, sample in order},
        "causes": cause,
    }


def minute_of_day(ts):
    return (ts % 86400) // 60


def tod_flags(times):
    """First in-session bar is the long signal. The last bar before 20:00 flattens."""
    count = len(times)
    go = [0.0] * count
    flat = [0.0] * count
    days = {}
    for index, ts in enumerate(times):
        days.setdefault(ts // 86400, []).append(index)
    skipped = 0
    for day, indexes in days.items():
        in_session = [
            index
            for index in indexes
            if SESSION_START <= minute_of_day(times[index]) < SESSION_END
        ]
        if not in_session:
            skipped += 1
            continue
        first = in_session[0]
        if first + 1 >= count:
            skipped += 1
            continue
        nxt = times[first + 1]
        if nxt // 86400 != day or minute_of_day(nxt) >= SESSION_END:
            skipped += 1
            continue
        go[first] = 1.0
        before = [index for index in indexes if minute_of_day(times[index]) < SESSION_END]
        last = before[-1]
        if last + 1 < count:
            flat[last] = 1.0
    return tuple(go), tuple(flat), skipped


def weekend_flags(times, bars):
    """Fade a UTC weekend move of at least 60 bps. Signal is the first Monday bar."""
    by_day = {}
    for index, ts in enumerate(times):
        by_day.setdefault(ts // 86400, []).append(index)
    long_go = [0.0] * len(times)
    short_go = [0.0] * len(times)
    for day, indexes in by_day.items():
        if (day + 3) % 7 != 0:
            continue
        friday = day - 3
        if friday not in by_day:
            continue
        friday_close = bars[by_day[friday][-1]].close
        monday = indexes[0]
        if friday_close <= 0:
            continue
        gap_bps = (bars[monday].open / friday_close - 1) * 10000
        if gap_bps <= -60:
            long_go[monday] = 1.0
        elif gap_bps >= 60:
            short_go[monday] = 1.0
    return tuple(long_go), tuple(short_go)


def rolling_z(values, window, ddof):
    """Z of values[i] against values[i-window+1 : i+1]. Zero variance is undefined."""
    out = [None] * len(values)
    denom = window - ddof
    if window < 2 or denom <= 0:
        return out
    for index in range(window - 1, len(values)):
        chunk = values[index - window + 1 : index + 1]
        mean = sum(chunk) / window
        var = sum((item - mean) ** 2 for item in chunk) / denom
        if var <= 0:
            continue
        out[index] = (values[index] - mean) / math.sqrt(var)
    return out


def join_eth_btc(eth_rows, btc_rows):
    btc_close = {ts: close for ts, _, _, _, close in btc_rows}
    times = []
    bars = []
    residual = []
    for ts, open_, high, low, close in eth_rows:
        other = btc_close.get(ts)
        if other is None or other <= 0 or close <= 0:
            continue
        times.append(ts)
        bars.append(Bar(open=open_, high=high, low=low, close=close, volume=0.0))
        residual.append(math.log(other) - math.log(close))
    return times, bars, residual


def as_bars(rows):
    times = []
    bars = []
    for ts, open_, high, low, close in rows:
        times.append(ts)
        bars.append(Bar(open=open_, high=high, low=low, close=close, volume=0.0))
    return times, bars


def tod_hypothesis(name, stop, go, flat):
    return Hypothesis(
        name=name,
        side="long",
        long_entry=Cross(Line("session", go), Threshold(0.5, "on"), "above"),
        long_rule_exit=Above(Line("flatten", flat), Threshold(0.5, "off")),
        distance_kind="percent",
        take_profit_size=0.008,
        stop_loss_size=stop,
        time_exit=26,
    )


def donchian_hypothesis():
    upper = Indicator("donchian", "high", window=116, output="upper")
    return Hypothesis(
        name="S4-DON-W116-tp6p0-sl2-T196-ETH",
        side="long",
        long_entry=Cross(Price("close"), upper, "above"),
        distance_kind="average true range",
        take_profit_size=6.0,
        stop_loss_size=2.0,
        time_exit=196,
        atr_window=14,
    )


def rsi_hypothesis():
    rsi = Indicator("rsi", "rsi", window=7)
    ema = Indicator("ema", "ema", window=21)
    return Hypothesis(
        name="P4-RSI7-eg21-tp125-T26-ETH",
        side="both",
        long_entry=All(
            (
                Cross(rsi, Threshold(30, "oversold"), "above"),
                Above(Price("close"), ema),
            )
        ),
        short_entry=All(
            (
                Cross(rsi, Threshold(70, "overbought"), "below"),
                Below(Price("close"), ema),
            )
        ),
        distance_kind="percent",
        take_profit_size=0.0125,
        stop_loss_size=0.003,
        time_exit=26,
    )


def residual_hypothesis(z_values):
    residual = Line("z", z_values)
    return Hypothesis(
        name="R1-ZF-W48-Z1p5-tp8-T24-ETH",
        side="both",
        long_entry=Cross(residual, Threshold(1.5, "z_hi"), "above"),
        short_entry=Cross(residual, Threshold(-1.5, "z_lo"), "below"),
        distance_kind="percent",
        take_profit_size=0.008,
        stop_loss_size=0.003,
        time_exit=24,
    )


def weekend_hypothesis(long_go, short_go):
    return Hypothesis(
        name="O1-WGAP-g60-tp5-T180-ETH",
        side="both",
        long_entry=Cross(Line("gap_down", long_go), Threshold(0.5, "down"), "above"),
        short_entry=Cross(Line("gap_up", short_go), Threshold(0.5, "up"), "above"),
        distance_kind="percent",
        take_profit_size=0.005,
        stop_loss_size=0.003,
        time_exit=180,
    )


def credentials():
    missing = [name for name in ("CH_USER", "CH_PASSWORD") if not os.environ.get(name)]
    if missing:
        raise SystemExit("set " + " and ".join(missing) + " to download candles")
    from btc_bars import clickhouse_url

    return clickhouse_url(), os.environ["CH_USER"], os.environ["CH_PASSWORD"]


def fetch_candles(symbol, pair_name, dest, refresh):
    if dest.exists() and dest.stat().st_size > 0 and not refresh:
        print(f"cache {dest.name}")
        return
    url, user, password = credentials()
    sql = f"""
SELECT
  toUnixTimestamp(timestamp) AS ts,
  any(open) AS open,
  any(high) AS high,
  any(low) AS low,
  any(close) AS close
FROM market_data.avantis_candles_1m
WHERE symbol = '{symbol}'
  AND pair_name = '{pair_name}'
  AND timestamp >= toDateTime('2025-01-01 00:00:00')
  AND timestamp < toDateTime('2026-09-24 00:00:00')
GROUP BY timestamp
ORDER BY timestamp
FORMAT CSVWithNames
"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(".csv.part")
    print(f"download {symbol} {pair_name}")
    with partial.open("w") as handle:
        proc = subprocess.run(
            ["curl", "-sS", "--fail-with-body", "--user", f"{user}:{password}", url, "--data-binary", "@-"],
            input=sql,
            text=True,
            stdout=handle,
            stderr=subprocess.PIPE,
        )
    if proc.returncode != 0:
        detail = proc.stderr.strip()
        if partial.exists():
            detail = (detail + "\n" + partial.read_text(errors="replace")[:400]).strip()
            partial.unlink()
        raise SystemExit(detail or "clickhouse download failed")
    head = partial.open().readline()
    if head.startswith("Code:"):
        detail = partial.read_text(errors="replace")[:400]
        partial.unlink()
        raise SystemExit(detail)
    partial.replace(dest)


def load_csv(path):
    rows = []
    with path.open(newline="") as handle:
        for record in csv.DictReader(handle):
            rows.append(
                (
                    int(record["ts"]),
                    float(record["open"]),
                    float(record["high"]),
                    float(record["low"]),
                    float(record["close"]),
                )
            )
    rows.sort()
    deduped = []
    previous = None
    for row in rows:
        if previous == row[0]:
            continue
        deduped.append(row)
        previous = row[0]
    return deduped


def load_pair(symbol, pair_name, filename, refresh):
    path = CACHE / filename
    fetch_candles(symbol, pair_name, path, refresh)
    return load_csv(path)


def engine_commit():
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() or "unknown"


def want(only, name):
    return only is None or name in only


def main():
    parser = argparse.ArgumentParser(description="Backtest the five 2 bp Avantis cells.")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--only",
        action="append",
        choices=("tod", "donchian", "rsi", "residual", "weekend"),
    )
    parser.add_argument("--ddof", type=int, default=0, choices=(0, 1))
    args = parser.parse_args()
    only = set(args.only) if args.only else None

    print(f"engine {engine_commit()}")
    print(
        f"window {START:%Y-%m-%d %H:%M} UTC inclusive to {END:%Y-%m-%d %H:%M} exclusive"
    )
    print(f"OOS starts {OOS_START:%Y-%m-%d %H:%M:%S} UTC (calendar last 20%)")
    print(f"fee per fill {FEE}  (round trip {2 * FEE})")

    started = time.perf_counter()
    eth_1m = load_pair("Crypto.ETH/USD", "ETH_UPSIDE/USD", "eth_upside_1m.csv", args.refresh)
    need_btc = want(only, "residual")
    btc_1m = (
        load_pair("Crypto.BTC/USD", "BTC_UPSIDE/USD", "btc_upside_1m.csv", args.refresh)
        if need_btc
        else []
    )
    print(f"ETH 1m distinct {len(eth_1m)}  (raw research count 906629 includes 4 duplicate minutes)")
    if eth_1m[len(eth_1m) // 2][4] < 500 or eth_1m[len(eth_1m) // 2][4] > 20000:
        raise SystemExit("ETH closes are not in the expected range")
    eth_15 = resample(eth_1m, 15)
    eth_5 = resample(eth_1m, 5)
    print(f"ETH 15m {len(eth_15)}  ETH 5m {len(eth_5)}")
    if len(eth_1m) != 906625 or len(eth_15) != 60494 or len(eth_5) != 181397:
        raise SystemExit(
            "bar counts do not match the research series "
            "(1m 906625 distinct, 15m 60494, 5m 181397)"
        )

    summaries = []
    if want(only, "tod"):
        times, bars = as_bars(eth_15)
        go, flat, skipped = tod_flags(times)
        print(f"\nNY session signals {int(sum(go))}  days skipped {skipped}")
        for name, stop in (
            ("TOD-NYL-ETH-tp8-sl3-T26", 0.003),
            ("TOD-NYL-ETH-tp8-sl2-T26", 0.002),
        ):
            t0 = time.perf_counter()
            _, rows = score(tod_hypothesis(name, stop, go, flat), bars, times, "15m")
            print(f"scored {name} in {time.perf_counter() - t0:.1f}s")
            summaries.append(report(name, rows, "ETH 15m, long, one signal per UTC day"))

    if want(only, "donchian"):
        times, bars = as_bars(eth_15)
        t0 = time.perf_counter()
        _, rows = score(donchian_hypothesis(), bars, times, "15m")
        print(f"\nscored Donchian in {time.perf_counter() - t0:.1f}s")
        summaries.append(
            report(
                "S4-DON-W116-tp6p0-sl2-T196-ETH",
                rows,
                "ETH 15m, long, close crosses the engine Donchian high (prior 116 bars)",
            )
        )

    if want(only, "rsi"):
        times, bars = as_bars(eth_5)
        t0 = time.perf_counter()
        _, rows = score(rsi_hypothesis(), bars, times, "5m")
        print(f"\nscored RSI in {time.perf_counter() - t0:.1f}s")
        summaries.append(
            report(
                "P4-RSI7-eg21-tp125-T26-ETH",
                rows,
                "ETH 5m, both sides, RSI(7) reclaim gated by EMA(21), no rule exit",
            )
        )

    if want(only, "residual"):
        if btc_1m[len(btc_1m) // 2][4] < 10000:
            raise SystemExit("BTC closes are not in the expected range")
        btc_5 = resample(btc_1m, 5)
        times, bars, residual = join_eth_btc(eth_5, btc_5)
        print(f"\nBTC 5m {len(btc_5)}  inner join {len(times)}  z ddof {args.ddof}")
        z_values = rolling_z(residual, 48, args.ddof)
        t0 = time.perf_counter()
        _, rows = score(residual_hypothesis(z_values), bars, times, "5m")
        print(f"scored residual in {time.perf_counter() - t0:.1f}s")
        summaries.append(
            report(
                "R1-ZF-W48-Z1p5-tp8-T24-ETH",
                rows,
                f"ETH 5m on the BTC inner join, fade z of log BTC − log ETH, window 48, ddof {args.ddof}",
            )
        )

    if want(only, "weekend"):
        times, bars = as_bars(eth_1m)
        long_go, short_go = weekend_flags(times, bars)
        print(
            f"\nweekend signals long {int(sum(long_go))} short {int(sum(short_go))}"
        )
        t0 = time.perf_counter()
        _, rows = score(weekend_hypothesis(long_go, short_go), bars, times, "1m")
        print(f"scored weekend gap in {time.perf_counter() - t0:.1f}s")
        summaries.append(
            report(
                "O1-WGAP-g60-tp5-T180-ETH",
                rows,
                "ETH 1m, fade Monday open vs prior Friday close when |gap| >= 60 bps, fill next open",
            )
        )

    print(f"\ndone in {time.perf_counter() - started:.1f}s")
    return summaries


if __name__ == "__main__":
    main()

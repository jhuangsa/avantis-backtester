"""Score the five ideas in examples/veranta_top5.html as hypotheses.

examples/veranta_top5.py replays what the five wallets did. This scores what
the page says they looked for, on hourly bars, through the engine. Each rule
is known at an hour's close and filled at the next hour's open. The sizes
come from the page's descriptions of the wallets' own results, so every
score here is in-sample.

Bars are public exchange candles standing in for the Avantis price: Gate.io spot
for ZORA, Binance spot for DYM and AVNT, and Binance PAXGUSDT for gold. Oil
has no public stand-in and is left out. Each window is that wallet's first
to last open in its market. From the repository root:

    python3 examples/veranta_rules.py
"""

import json
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import Above, Bar, Below, Hypothesis, Indicator, Line, Price, Threshold, backtest

HERE = Path(__file__).resolve().parent
TRADES = HERE / "veranta_trades"
CACHE = Path(__file__).resolve().parents[1] / "data" / "candles" / "veranta_rules"
BINANCE = "https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval=1h&startTime={start}&limit=1000"
GATE = "https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={symbol}&interval=1h&from={start}&to={end}"
HOUR = 3600
FEE = 0.0001
WARMUP = 80 * HOUR


def _get(url):
    request = urllib.request.Request(url, headers={"user-agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def _binance(symbol, start, end):
    rows, cursor = [], start * 1000
    while cursor <= end * 1000:
        page = _get(BINANCE.format(symbol=symbol, start=cursor))
        if not page:
            break
        rows.extend([k[0] // 1000, *map(float, k[1:5])] for k in page)
        cursor = page[-1][0] + HOUR * 1000
    return rows


def _gate(symbol, start, end):
    # Gate rows are time, quote volume, close, high, low, open, base volume, closed.
    rows, cursor = [], start
    while cursor <= end:
        stop = min(cursor + 900 * HOUR, end)
        for k in _get(GATE.format(symbol=symbol, start=cursor, end=stop)):
            rows.append([int(k[0]), float(k[5]), float(k[3]), float(k[4]), float(k[2])])
        cursor = stop + HOUR
        time.sleep(0.1)
    return sorted({r[0]: r for r in rows}.values())


def candles(source, symbol, start, end):
    path = CACHE / f"{symbol}-{start}-{end}.json"
    if path.exists():
        return json.loads(path.read_text())
    fetch = _gate if source == "gate" else _binance
    rows = [r for r in fetch(symbol, start, end) if start <= r[0] <= end]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))
    return rows


def change(bars, hours):
    """The close over the close `hours` bars earlier, minus one."""
    return [None if i < hours else bars[i].close / bars[i - hours].close - 1 for i in range(len(bars))]


def flag(*parts):
    """1 where every part is true, 0 where one is false, None where one is undefined."""
    out = []
    for values in zip(*parts):
        out.append(None if any(v is None for v in values) else float(all(values)))
    return out


def sma(bars, window):
    closes = [b.close for b in bars]
    return [None if i + 1 < window else sum(closes[i + 1 - window : i + 1]) / window for i in range(len(bars))]


ON = Threshold(0.5, "on")


def late_day_short(times, bars):
    # 1. 0xd49C: ZORA shorts opened at 17:00 UTC after a 72-hour rise; out in
    # about three hours near +2% of price. The 16:00 close decides; 17:00 fills.
    rise = change(bars, 72)
    hour = [datetime.fromtimestamp(t, timezone.utc).hour for t in times]
    go = flag([h == 16 for h in hour], [None if r is None else r >= 0.10 for r in rise])
    return Hypothesis(
        name="1-ZORA-1600-rise72-10-tp2-sl5-T3",
        side="short",
        short_entry=Above(Line("late_rise", go), ON),
        distance_kind="percent",
        take_profit_size=0.02,
        stop_loss_size=0.05,
        time_exit=3,
    )


def rally_short(times, bars):
    # 2. 0xDd49: the prior day up about 10% and the close above a 21-hour
    # average; usually closed within a few hours.
    day = change(bars, 24)
    avg = sma(bars, 21)
    go = flag(
        [None if d is None else d >= 0.10 for d in day],
        [None if a is None else b.close > a for b, a in zip(bars, avg)],
    )
    return Hypothesis(
        name="2-ZORA-day10-above-sma21-tp2-sl5-T4",
        side="short",
        short_entry=Above(Line("rally", go), ON),
        distance_kind="percent",
        take_profit_size=0.02,
        stop_loss_size=0.05,
        time_exit=4,
    )


def campaign_short(times, bars):
    # 3. 0xEDa7: AVNT shorts after a day up about 9%, held for days for 20% to
    # 40% moves, stops near 30% away.
    day = change(bars, 24)
    go = [None if d is None else float(d >= 0.09) for d in day]
    return Hypothesis(
        name="3-AVNT-day9-tp20-sl30-T120",
        side="short",
        short_entry=Above(Line("rally", go), ON),
        distance_kind="percent",
        take_profit_size=0.20,
        stop_loss_size=0.30,
        time_exit=120,
    )


def spike_short(times, bars):
    # 4. 0xFFB7: DYM shorts in an hour whose high is 5% over the prior close;
    # winners near +6%, losers near -8%, about 16 hours.
    spike = [None] + [bars[i].high / bars[i - 1].close - 1 for i in range(1, len(bars))]
    return Hypothesis(
        name="4-DYM-spike5-tp6-sl8-T16",
        side="short",
        short_entry=Above(Line("spike", spike), Threshold(0.05, "spike")),
        distance_kind="percent",
        take_profit_size=0.06,
        stop_loss_size=0.08,
        time_exit=16,
    )


def gold_trend_long(times, bars):
    # 5. 0xdcE9: gold longs above a 55-hour average after a positive 72-hour
    # drift. The page gives no exit; this leaves below the average or 1.5% down.
    # The engine holds one trade, so their adds are not modelled.
    drift = change(bars, 72)
    line = sma(bars, 55)
    go = flag(
        [None if a is None else b.close > a for b, a in zip(bars, line)],
        [None if d is None else d > 0 for d in drift],
    )
    avg = Indicator("sma", "sma55", window=55)
    return Hypothesis(
        name="5-PAXG-above-sma55-drift72-sl1.5-exit-below",
        side="long",
        long_entry=Above(Line("trend", go), ON),
        long_rule_exit=Below(Price("close"), avg),
        distance_kind="percent",
        stop_loss_size=0.015,
    )


RULES = (
    ("d49c1", "ZORA/USD", "gate", "ZORA_USDT", late_day_short),
    ("dd49e", "ZORA/USD", "gate", "ZORA_USDT", rally_short),
    ("eda73", "AVNT/USD", "binance", "AVNTUSDT", campaign_short),
    ("ffb7e", "DYM/USD", "binance", "DYMUSDT", spike_short),
    ("dce95", "XAU/USD", "binance", "PAXGUSDT", gold_trend_long),
)


def score(wallet, market, source, symbol, make):
    trades = json.loads((TRADES / f"{wallet}.json").read_text())["trades"]
    opens = [t["openedAt"] for t in trades if t["symbol"] == market]
    start = min(opens) // HOUR * HOUR - WARMUP
    end = max(opens) // HOUR * HOUR + 48 * HOUR
    rows = candles(source, symbol, start, end)
    times = [r[0] for r in rows]
    bars = [Bar(open=o, high=h, low=l, close=c, volume=0.0) for _, o, h, l, c in rows]
    hypothesis = make(times, bars)
    result = backtest(hypothesis, bars, fee=FEE, bar_size="1h")
    net = [
        ((t.exit_price - t.entry_price) if t.side == "long" else (t.entry_price - t.exit_price)) / t.entry_price
        - 2 * FEE
        for t in result.trades
    ]
    day = lambda t: datetime.fromtimestamp(t, timezone.utc).date()
    print(f"{hypothesis.name}  ({symbol}, bar size {result.bar_size}, {day(times[0])} to {day(times[-1])})")
    if not net:
        print("  no trades\n")
        return
    held = sorted(t.exit_bar - t.entry_bar + 1 for t in result.trades)
    print(
        f"  trades {len(net)}, wins {sum(r > 0 for r in net)}, "
        f"mean {sum(net) / len(net):+.2%}, median hold {held[len(held) // 2]}h, "
        f"compounded {result.ending_stake - 1:+.1%}"
    )
    print(f"  causes {dict(Counter(t.cause for t in result.trades))}\n")


def main():
    for rule in RULES:
        score(*rule)


if __name__ == "__main__":
    main()

"""Build examples/hf_2bps.html from the cached 2 bp backtests.

Run from the repository root:

    PYTHONPATH=. python3 examples/build_hf_folio.py

The page is self-contained. Candle files come from the cache written by
examples/hf_2bps.py.
"""

import importlib.util
import json
import math
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
spec = importlib.util.spec_from_file_location("hf_2bps", HERE / "hf_2bps.py")
hf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hf)

CAUSE = {
    "stop loss": "sl",
    "take profit": "tp",
    "rule exit": "fl",
    "time exit": "tm",
    "still open": "op",
}

# Published rounding. The page is refused if a primary count or mean drifts.
EXPECT = {
    "ny": ("OOS", 126, 10.03),
    "donchian": ("OOS", 83, 33.98),
    "rsi": ("full", 352, 5.01),
    "residual": ("OOS", 1357, 1.10),
    "weekend": ("full", 69, 3.94),
}


def month_keys():
    keys = []
    year, month = 2025, 1
    while (year, month) <= (2026, 9):
        keys.append(f"{year}-{month:02d}")
        month += 1
        if month == 13:
            month = 1
            year += 1
    return keys


def enrich(hypothesis, bars, times, bar_size):
    result = hf.backtest(hypothesis, bars, fee=hf.FEE, bar_size=bar_size)
    trades = []
    stake = 1.0
    peak = 1.0
    drawdown = 0.0
    for trade in result.trades:
        net = hf.trade_net_return(
            trade.entry_price,
            trade.exit_price,
            trade.side,
            trade.cause,
            hf.FEE,
        )
        stake *= 1 + net
        peak = max(peak, stake)
        drawdown = min(drawdown, stake / peak - 1)
        moment = datetime.fromtimestamp(times[trade.entry_bar], timezone.utc)
        trades.append(
            [
                moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                round(net * 1e4, 6),
                CAUSE[trade.cause],
                trade.side[0],
                1 if times[trade.entry_bar] >= hf.OOS_TS else 0,
            ]
        )
    rows = [hf.Row(item[1], int(item[0][:4]), bool(item[4]), item[2]) for item in trades]
    return trades, rows, stake, drawdown


def pack_samples(rows):
    full, oos, by_year = hf.split(rows)
    samples = {}
    for name, sample in (("OOS", oos), ("full", full)):
        stats = hf.describe(sample)
        samples[name] = stats
    for year in sorted(by_year):
        stats = hf.describe(by_year[year])
        stats["label"] = None
        samples[str(year)] = stats
    primary, verdict = hf.protocol(hf._pairs(full), hf._pairs(oos))
    return samples, primary, verdict


def month_means(trades, keys):
    buckets = {key: [] for key in keys}
    for item in trades:
        buckets[item[0][:7]].append(item[1])
    out = {}
    for key in keys:
        values = buckets[key]
        out[key] = {
            "n": len(values),
            "mean": (sum(values) / len(values)) if values else None,
        }
    return out


def causes_of(trades):
    found = {}
    for item in trades:
        found[item[2]] = found.get(item[2], 0) + 1
    return found


def extremes(trades):
    if not trades:
        return None, None
    worst = min(trades, key=lambda item: item[1])
    best = max(trades, key=lambda item: item[1])
    return [best[0], best[1]], [worst[0], worst[1]]


def sides_of(trades):
    found = {}
    for item in trades:
        found.setdefault(item[3], []).append(item[1])
    out = {}
    for side, values in found.items():
        out[side] = {"n": len(values), "mean": sum(values) / len(values)}
    return out


def cell_from(hypothesis, bars, times, bar_size, keys):
    trades, rows, stake, drawdown = enrich(hypothesis, bars, times, bar_size)
    samples, primary, verdict = pack_samples(rows)
    best, worst = extremes(trades)
    return {
        "samples": samples,
        "primary": primary,
        "verdict": verdict,
        "causes": causes_of(trades),
        "stake": stake,
        "drawdown": drawdown,
        "months": month_means(trades, keys),
        "best": best,
        "worst": worst,
        "sides": sides_of(trades),
        "trades": trades,
    }


def check(cells):
    for key, (sample, count, mean) in EXPECT.items():
        stats = cells[key]["samples"][sample]
        got = round(stats["mean"], 2)
        if stats["n"] != count or got != mean:
            raise SystemExit(f"{key} {sample} is n {stats['n']} mean {got}, expected {count} {mean}")


def main():
    keys = month_keys()
    eth_1m = hf.load_pair("Crypto.ETH/USD", "ETH_UPSIDE/USD", "eth_upside_1m.csv", False)
    btc_1m = hf.load_pair("Crypto.BTC/USD", "BTC_UPSIDE/USD", "btc_upside_1m.csv", False)
    eth_15 = hf.resample(eth_1m, 15)
    eth_5 = hf.resample(eth_1m, 5)
    if len(eth_1m) != 906625 or len(eth_15) != 60494 or len(eth_5) != 181397:
        raise SystemExit("bar counts drifted")
    times_15, bars_15 = hf.as_bars(eth_15)
    times_5, bars_5 = hf.as_bars(eth_5)
    times_1, bars_1 = hf.as_bars(eth_1m)

    go, flat, skipped = hf.tod_flags(times_15)
    if skipped:
        raise SystemExit(f"NY session skipped {skipped} days")

    cells = {}
    cells["ny"] = cell_from(
        hf.tod_hypothesis("TOD-NYL-ETH-tp8-sl3-T26", 0.003, go, flat),
        bars_15,
        times_15,
        "15m",
        keys,
    )
    twin_trades, twin_rows, twin_stake, twin_dd = enrich(
        hf.tod_hypothesis("TOD-NYL-ETH-tp8-sl2-T26", 0.002, go, flat),
        bars_15,
        times_15,
        "15m",
    )
    twin_samples, twin_primary, twin_verdict = pack_samples(twin_rows)
    cells["ny"]["twin"] = {
        "cell": "TOD-NYL-ETH-tp8-sl2-T26",
        "samples": twin_samples,
        "primary": twin_primary,
        "verdict": twin_verdict,
        "stake": twin_stake,
        "drawdown": twin_dd,
        "causes": causes_of(twin_trades),
        "trades": twin_trades,
    }
    cells["donchian"] = cell_from(hf.donchian_hypothesis(), bars_15, times_15, "15m", keys)
    cells["rsi"] = cell_from(hf.rsi_hypothesis(), bars_5, times_5, "5m", keys)
    btc_5 = hf.resample(btc_1m, 5)
    joined_times, joined_bars, residual = hf.join_eth_btc(eth_5, btc_5)
    z_values = hf.rolling_z(residual, 48, 0)
    cells["residual"] = cell_from(
        hf.residual_hypothesis(z_values),
        joined_bars,
        joined_times,
        "5m",
        keys,
    )
    long_go, short_go = hf.weekend_flags(times_1, bars_1)
    cells["weekend"] = cell_from(
        hf.weekend_hypothesis(long_go, short_go),
        bars_1,
        times_1,
        "1m",
        keys,
    )
    check(cells)

    payload = {
        "meta": {
            "engine": hf.engine_commit(),
            "oos": hf.OOS_START.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "n1": len(eth_1m),
            "n5": len(eth_5),
            "n15": len(eth_15),
            "months": keys,
        },
        "cells": cells,
    }
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, dict):
            return {key: finite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [finite(item) for item in value]
        return value

    raw = json.dumps(finite(payload), separators=(",", ":"), allow_nan=False).replace("<", "\\u003c")
    template = (HERE / "folio_template.html").read_text()
    if "__PAYLOAD__" not in template:
        raise SystemExit("template is missing __PAYLOAD__")
    html = template.replace("__PAYLOAD__", raw)
    dest = HERE / "hf_2bps.html"
    dest.write_text(html)
    print(f"wrote {dest} ({dest.stat().st_size} bytes)")
    for key in ("ny", "donchian", "rsi", "residual", "weekend"):
        cell = cells[key]
        print(
            f"{key} {cell['verdict']} stake {cell['stake']:.4f} "
            f"dd {cell['drawdown']:.3%} trades {len(cell['trades'])}"
        )


if __name__ == "__main__":
    main()

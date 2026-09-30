"""BTC indicators: the C++ library through pybind11 against vectorized pandas.

Build the module first, from the repository root:

    cmake -S cpp -B cpp/build -Dpybind11_DIR=$(python3 -m pybind11 --cmakedir)
    cmake --build cpp/build

Then run:

    python3 examples/cpp_indicators_btc.py

Bars come from examples/btc_bars.py: BTC/USD minutes from ClickHouse, deduped,
resampled to 1m, 1h and 1d, with empty buckets filled forward. Both sides
compute the same six indicators on the same bars. The script checks that they
agree, times them, and writes examples/cpp_indicators_btc.html.
"""

import sys
import timeit
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpp" / "build"))
sys.path.insert(0, str(ROOT / "examples"))

import avbt_cpp  # noqa: E402
import btc_bars  # noqa: E402
import vector_indicators as vec  # noqa: E402

OUT = ROOT / "examples" / "cpp_indicators_btc.html"
RULES = {"1m": "1min", "1h": "1h", "1d": "1D"}
REPEATS = 7


def cpp_suite(bars, df):
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    return {
        "sma 4": avbt_cpp.sma(close, 4),
        "sma 5": avbt_cpp.sma(close, 5),
        "pct_change 1": avbt_cpp.pct_change(close, 1),
        "pct_change 24": avbt_cpp.pct_change(close, 24),
        "prior_max 20": avbt_cpp.prior_max(high, 20),
        "prior_max 55": avbt_cpp.prior_max(high, 55),
        "prior_min 20": avbt_cpp.prior_min(low, 20),
        "prior_min 55": avbt_cpp.prior_min(low, 55),
        "true_range": avbt_cpp.true_range(bars),
        "atr 14": avbt_cpp.atr(bars, 14),
    }


def vec_suite(df):
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    return {
        "sma 4": vec.sma(close, 4),
        "sma 5": vec.sma(close, 5),
        "pct_change 1": vec.pct_change(close, 1),
        "pct_change 24": vec.pct_change(close, 24),
        "prior_max 20": vec.prior_max(high, 20),
        "prior_max 55": vec.prior_max(high, 55),
        "prior_min 20": vec.prior_min(low, 20),
        "prior_min 55": vec.prior_min(low, 55),
        "true_range": vec.true_range(high, low, close),
        "atr 14": vec.atr(high, low, close, 14),
    }


def make_bars(df, bar_seconds):
    return avbt_cpp.Bars(
        bar_seconds,
        df["ts"].to_numpy(np.int64),
        df["open"].to_numpy(),
        df["high"].to_numpy(),
        df["low"].to_numpy(),
        df["close"].to_numpy(),
        df["minutes"].to_numpy(np.int32),
    )


def best_seconds(fn):
    """Best of REPEATS runs. The best run is the least disturbed by the machine."""
    return min(timeit.repeat(fn, number=1, repeat=REPEATS))


def compare(cpp, vect):
    rows = []
    for name in cpp:
        a, b = cpp[name], vect[name]
        same_nan = bool(np.array_equal(np.isnan(a), np.isnan(b)))
        both = ~np.isnan(a) & ~np.isnan(b)
        diff = float(np.max(np.abs(a[both] - b[both]))) if both.any() else 0.0
        rows.append((name, same_nan, diff))
    return rows


def run(label, rule):
    df, bar_seconds = btc_bars.load_bars(rule)
    bars = make_bars(df, bar_seconds)
    cpp = cpp_suite(bars, df)
    vect = vec_suite(df)
    parity = compare(cpp, vect)

    # Time each indicator on its own, on both sides.
    close, high, low = (df[c].to_numpy() for c in ("close", "high", "low"))
    calls = {
        "sma 5": (lambda: avbt_cpp.sma(close, 5), lambda: vec.sma(close, 5)),
        "pct_change 24": (
            lambda: avbt_cpp.pct_change(close, 24),
            lambda: vec.pct_change(close, 24),
        ),
        "prior_max 55": (
            lambda: avbt_cpp.prior_max(high, 55),
            lambda: vec.prior_max(high, 55),
        ),
        "prior_min 55": (
            lambda: avbt_cpp.prior_min(low, 55),
            lambda: vec.prior_min(low, 55),
        ),
        "true_range": (
            lambda: avbt_cpp.true_range(bars),
            lambda: vec.true_range(high, low, close),
        ),
        "atr 14": (
            lambda: avbt_cpp.atr(bars, 14),
            lambda: vec.atr(high, low, close, 14),
        ),
    }
    timings = {name: (best_seconds(c), best_seconds(v)) for name, (c, v) in calls.items()}
    bars_seconds = best_seconds(lambda: make_bars(df, bar_seconds))

    print(f"\n{label}: {len(df):,} bars, {int((df['minutes'] == 0).sum()):,} filled forward")
    for name, same_nan, diff in parity:
        flag = "ok" if same_nan and diff < 1e-6 else "MISMATCH"
        print(f"  {name:<14} NaN pattern {'same' if same_nan else 'DIFFERENT'}  max |diff| {diff:.3g}  {flag}")
    print(f"  {'indicator':<14} {'C++ ms':>9} {'pandas ms':>10} {'speedup':>8}")
    for name, (c, v) in timings.items():
        print(f"  {name:<14} {c * 1e3:>9.3f} {v * 1e3:>10.3f} {v / c:>7.1f}x")
    print(f"  building Bars once: {bars_seconds * 1e3:.3f} ms")
    return {
        "label": label,
        "df": df,
        "cpp": cpp,
        "parity": parity,
        "timings": timings,
        "bars_seconds": bars_seconds,
    }


def first_defined(series_map):
    """First row where every indicator is defined. Charts start there."""
    defined = np.logical_and.reduce([~np.isnan(v) for v in series_map.values()])
    return int(np.argmax(defined))


def indicator_figure(result, title):
    df, cpp = result["df"], result["cpp"]
    start = first_defined(cpp)
    t = pd.to_datetime(df["ts"].to_numpy()[start:], unit="s", utc=True)
    cut = {k: v[start:] for k, v in cpp.items()}
    d = df.iloc[start:]

    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.03,
        row_heights=[0.5, 0.17, 0.17, 0.16],
        subplot_titles=(
            "close, SMA 5, prior 55-bar high and low",
            "true range and ATR 14",
            "pct_change 24",
            "minutes per bar (0 = filled forward)",
        ),
    )
    fig.add_trace(go.Scatter(x=t, y=d["close"], name="close", line=dict(width=1, color="#444")), 1, 1)
    fig.add_trace(go.Scatter(x=t, y=cut["sma 5"], name="SMA 5", line=dict(width=1, color="#1f77b4")), 1, 1)
    fig.add_trace(go.Scatter(x=t, y=cut["prior_max 55"], name="prior_max 55", line=dict(width=1, color="#2ca02c")), 1, 1)
    fig.add_trace(go.Scatter(x=t, y=cut["prior_min 55"], name="prior_min 55", line=dict(width=1, color="#d62728")), 1, 1)
    fig.add_trace(go.Scatter(x=t, y=cut["true_range"], name="true range", line=dict(width=0.6, color="#bbb")), 2, 1)
    fig.add_trace(go.Scatter(x=t, y=cut["atr 14"], name="ATR 14", line=dict(width=1.4, color="#ff7f0e")), 2, 1)
    fig.add_trace(go.Scatter(x=t, y=cut["pct_change 24"] * 100, name="pct_change 24 (%)", line=dict(width=0.8, color="#9467bd")), 3, 1)
    fig.add_trace(go.Scatter(x=t, y=d["minutes"], name="minutes", line=dict(width=0.8, color="#8c564b")), 4, 1)
    fig.update_layout(title=title, height=900, template="plotly_white", legend=dict(orientation="h", y=1.04))
    return fig


def timing_figure(results):
    fig = go.Figure()
    for r in results:
        names = list(r["timings"])
        fig.add_trace(go.Bar(
            name=f"{r['label']} ({len(r['df']):,} bars)",
            x=names,
            y=[r["timings"][n][1] / r["timings"][n][0] for n in names],
        ))
    fig.add_hline(y=1, line_dash="dot", annotation_text="same speed")
    fig.update_layout(
        title="Speedup of C++ over vectorized pandas (pandas time / C++ time, best of 7)",
        yaxis_title="times faster", barmode="group", template="plotly_white", height=450,
    )
    return fig


def timing_table(results):
    rows = []
    for r in results:
        for name, (c, v) in r["timings"].items():
            rows.append(
                f"<tr><td>{r['label']}</td><td>{len(r['df']):,}</td><td>{name}</td>"
                f"<td>{c * 1e3:.3f}</td><td>{v * 1e3:.3f}</td><td>{v / c:.1f}x</td></tr>"
            )
    return (
        "<table><tr><th>bars</th><th>count</th><th>indicator</th><th>C++ ms</th>"
        "<th>pandas ms</th><th>speedup</th></tr>" + "".join(rows) + "</table>"
    )


def parity_table(results):
    rows = []
    for r in results:
        for name, same_nan, diff in r["parity"]:
            rows.append(
                f"<tr><td>{r['label']}</td><td>{name}</td>"
                f"<td>{'same' if same_nan else 'DIFFERENT'}</td><td>{diff:.3g}</td></tr>"
            )
    return (
        "<table><tr><th>bars</th><th>indicator</th><th>NaN pattern</th>"
        "<th>max |C++ − pandas|</th></tr>" + "".join(rows) + "</table>"
    )


def write_html(results):
    by_label = {r["label"]: r for r in results}
    parts = [
        timing_figure(results).to_html(full_html=False, include_plotlyjs="cdn"),
        indicator_figure(by_label["1h"], "BTC/USD hourly").to_html(full_html=False, include_plotlyjs=False),
        indicator_figure(by_label["1d"], "BTC/USD daily").to_html(full_html=False, include_plotlyjs=False),
    ]
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>BTC indicators, C++ vs pandas</title>
<style>body{{font-family:system-ui,sans-serif;margin:24px;color:#222}}table{{border-collapse:collapse;margin:12px 0 28px}}
td,th{{border:1px solid #ddd;padding:3px 10px;text-align:right}}th{{background:#f4f4f4}}</style></head><body>
<h1>BTC/USD indicators: C++ (pybind11) vs vectorized pandas</h1>
<p>Same bars on both sides: ClickHouse minutes, deduped, resampled, empty buckets filled forward.
Charts start at the first bar where every indicator is defined.</p>
{parts[0]}
<h2>Timings</h2>{timing_table(results)}
<h2>Agreement</h2>{parity_table(results)}
{parts[1]}{parts[2]}
</body></html>"""
    OUT.write_text(html)
    print(f"\nwrote {OUT.relative_to(ROOT)}")


def main():
    results = [run(label, rule) for label, rule in RULES.items()]
    write_html(results)


if __name__ == "__main__":
    main()

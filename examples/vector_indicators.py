"""Vectorized numpy/pandas twins of the C++ indicators in cpp/src/indicators.cpp.

Same names and NaN rules as avbt_cpp; true_range and atr take high/low/close arrays
instead of a Bars struct. No Python loops over data.

Run: python3 examples/vector_indicators.py
"""
import numpy as np
import pandas as pd


def _check(v, name):
    if int(v) < 1:
        raise ValueError(f"{name} must be >= 1")
    return int(v)


def _s(x):
    return pd.Series(np.asarray(x, dtype=np.float64))


def sma(x, period):
    p = _check(period, "period")
    return _s(x).rolling(p, min_periods=p).mean().to_numpy()


def pct_change(x, lag):
    k = _check(lag, "lag")
    s = _s(x)
    prev = s.shift(k)
    return ((s - prev) / prev).to_numpy()


def prior_max(x, n):
    n = _check(n, "n")
    return _s(x).rolling(n, min_periods=n).max().shift(1).to_numpy()


def prior_min(x, n):
    n = _check(n, "n")
    return _s(x).rolling(n, min_periods=n).min().shift(1).to_numpy()


def true_range(high, low, close):
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    pc = np.roll(np.asarray(close, dtype=np.float64), 1)
    with np.errstate(invalid="ignore"):
        tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    bad = ~(np.isfinite(h) & np.isfinite(l) & np.isfinite(pc))
    tr[bad] = np.nan
    if len(tr):
        tr[0] = h[0] - l[0]
    return tr


def atr(high, low, close, n):
    n = _check(n, "n")
    tr = true_range(high, low, close)
    m = len(tr)
    out = np.full(m, np.nan)
    if m <= n:
        return out
    seed = tr[1:n + 1].mean()  # NaN if any TR in the seed window is NaN
    src = np.full(m, np.nan)
    src[n] = seed
    src[n + 1:] = tr[n + 1:]
    sm = pd.Series(src).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy()
    # C++ recursion stays NaN forever after a NaN; ewm would skip it, so mask.
    idx = np.arange(m)
    tainted = np.cumsum(np.isnan(np.where(idx >= n, src, 0.0))) > 0
    out[n:] = np.where(tainted[n:], np.nan, sm[n:])
    return out


if __name__ == "__main__":
    h = [10, 12, 11, 15, 13]; l = [8, 9, 10, 14, 12]; c = [9, 11, 10.5, 14, 12]
    print("TR ", true_range(h, l, c))
    print("ATR", atr(h, l, c, 2))

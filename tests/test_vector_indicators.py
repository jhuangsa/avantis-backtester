import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))
sys.path.insert(0, str(ROOT / "cpp" / "build"))

import vector_indicators as vi  # noqa: E402

H = [10, 12, 11, 15, 13]
L = [8, 9, 10, 14, 12]
C = [9, 11, 10.5, 14, 12]
nan = np.nan


def close(a, b):
    np.testing.assert_allclose(a, b, rtol=1e-9, equal_nan=True)


def test_hand_true_range_and_atr():
    close(vi.true_range(H, L, C), [2, 3, 1, 4.5, 2])
    close(vi.atr(H, L, C, 2), [nan, nan, 2, 3.25, 2.625])
    close(vi.atr(H, L, C, 5), [nan] * 5)


def test_hand_windows():
    x = [1.0, 3, 2, 5, 4]
    close(vi.sma(x, 2), [nan, 2, 2.5, 3.5, 4.5])
    close(vi.pct_change(x, 1), [nan, 2, -1 / 3, 1.5, -0.2])
    close(vi.prior_max(x, 2), [nan, nan, 3, 3, 5])
    close(vi.prior_min(x, 2), [nan, nan, 1, 2, 2])


def test_atr_nan_propagates_forever():
    c = list(C); c[2] = nan  # TR[3] undefined
    out = vi.atr(H, L, c, 2)
    assert np.isfinite(out[2]) and np.isnan(out[3:]).all()


@pytest.mark.parametrize("f", [vi.sma, vi.pct_change, vi.prior_max, vi.prior_min])
def test_bad_args(f):
    with pytest.raises(ValueError):
        f([1.0, 2.0], 0)


def test_bad_atr_arg():
    with pytest.raises(ValueError):
        vi.atr(H, L, C, 0)


def _walk(n=5000, seed=7):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.005, n))
    l = np.minimum(o, c) * (1 - rng.uniform(0, 0.005, n))
    return o, h, l, c


def _bars(cpp, o, h, l, c):
    n = len(c)
    ts = np.arange(n, dtype=np.int64) * 60
    return cpp.Bars(60, ts, o, h, l, c, np.zeros(n, dtype=np.int32))


@pytest.mark.parametrize("w", [1, 2, 14, 50, 200])
def test_parity(w):
    cpp = pytest.importorskip("avbt_cpp")
    o, h, l, c = _walk()
    close(vi.sma(c, w), cpp.sma(c, w))
    close(vi.pct_change(c, w), cpp.pct_change(c, w))
    close(vi.prior_max(c, w), cpp.prior_max(c, w))
    close(vi.prior_min(c, w), cpp.prior_min(c, w))
    b = _bars(cpp, o, h, l, c)
    close(vi.true_range(h, l, c), cpp.true_range(b))
    close(vi.atr(h, l, c, w), cpp.atr(b, w))


@pytest.mark.parametrize("gap", [5, 13, 14, 1000])
def test_parity_gap(gap):
    cpp = pytest.importorskip("avbt_cpp")
    o, h, l, c = _walk()
    c = c.copy(); c[gap] = np.nan
    b = _bars(cpp, o, h, l, c)
    close(vi.true_range(h, l, c), cpp.true_range(b))
    close(vi.atr(h, l, c, 14), cpp.atr(b, 14))


def test_parity_errors():
    cpp = pytest.importorskip("avbt_cpp")
    for f in (cpp.sma, cpp.pct_change, cpp.prior_max, cpp.prior_min):
        with pytest.raises(ValueError):
            f(np.ones(3), 0)

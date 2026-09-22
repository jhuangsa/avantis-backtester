"""SMA and average true range. Every other kind is deferred to the catalog."""

import math


def canon_kind(kind):
    return " ".join(kind.strip().lower().replace("-", " ").replace("_", " ").split())


def price_series(bars, field):
    if field not in ("open", "high", "low", "close"):
        raise ValueError(field)
    return [_finite(getattr(bar, field)) for bar in bars]


def values(kind, bars, window, price, output, params):
    canon = canon_kind(kind)
    if canon == "sma":
        return _sma_line(bars, window, price, output)
    if canon == "atr":
        return _atr_line(bars, window, price, output)
    try:
        from backtest import catalog
    except ImportError as exc:
        raise ValueError(f"unknown indicator {kind}") from exc
    fn = getattr(catalog, "values", None)
    if fn is None:
        raise ValueError(f"unknown indicator {kind}")
    try:
        result = fn(
            kind,
            bars,
            window=window,
            price=price,
            output=output,
            params=params,
        )
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"unknown indicator {kind}") from exc
    if result is None:
        raise ValueError(f"unknown indicator {kind}")
    return result


def _sma_line(bars, window, price, output):
    if output != "value":
        raise ValueError(output)
    if not _whole(window):
        raise ValueError("sma requires a window")
    prices = price_series(bars, price)
    out = [None] * len(prices)
    for i in range(window - 1, len(prices)):
        chunk = prices[i - window + 1 : i + 1]
        if any(value is None for value in chunk):
            continue
        out[i] = sum(chunk) / window
    return out


def _atr_line(bars, window, price, output):
    if output != "value":
        raise ValueError(output)
    if price not in ("open", "high", "low", "close"):
        raise ValueError(price)
    n = 14 if window is None else window
    if not _whole(n):
        raise ValueError("atr window")
    return _atr(bars, n)


def _atr(bars, n):
    """Wilder ATR. The seed at index n is the mean of TR[1] through TR[n], not TR[0]."""
    true_ranges = []
    for i, bar in enumerate(bars):
        high = _finite(bar.high)
        low = _finite(bar.low)
        if high is None or low is None:
            true_ranges.append(None)
            continue
        if i == 0:
            true_ranges.append(high - low)
            continue
        previous = _finite(bars[i - 1].close)
        if previous is None:
            true_ranges.append(None)
            continue
        true_ranges.append(
            max(high - low, abs(high - previous), abs(low - previous))
        )
    out = [None] * len(bars)
    if len(bars) <= n:
        return out
    seed = true_ranges[1 : n + 1]
    if len(seed) != n or any(value is None for value in seed):
        return out
    out[n] = sum(seed) / n
    for i in range(n + 1, len(bars)):
        if out[i - 1] is None or true_ranges[i] is None:
            continue
        out[i] = (out[i - 1] * (n - 1) + true_ranges[i]) / n
    return out


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _whole(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1

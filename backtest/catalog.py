"""One indicator output series, aligned to the caller's bars.

SMA and ATR are not kinds here. ATR below is only the Wilder line that
Supertrend and Keltner read. An undefined bar is None.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol


class _Bar(Protocol):
    open: float
    high: float
    low: float
    close: float
    volume: float


_PARAM_NAMES = frozenset(
    {
        "fast",
        "slow",
        "signal",
        "multiplier",
        "deviation",
        "deviation_lower",
        "session",
        "left",
        "right",
        "anchor",
        "step",
        "maximum",
        "k_smooth",
        "d_smooth",
    }
)

_PRICES = frozenset({"open", "high", "low", "close"})


def _pos_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive whole number")
    return value


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _positive(value: object, label: str) -> float:
    number = _finite(value, label)
    if number <= 0:
        raise ValueError(f"{label} must be positive")
    return number


def _nonneg(value: object, label: str) -> float:
    number = _finite(value, label)
    if number < 0:
        raise ValueError(f"{label} must be >= 0")
    return number


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _parse_params(
    params: tuple[tuple[str, int | float | str], ...],
) -> dict[str, int | float | str]:
    if not isinstance(params, tuple):
        raise ValueError("params must be a tuple of pairs")
    parsed: dict[str, int | float | str] = {}
    for item in params:
        if not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], str):
            raise ValueError("params must be a tuple of pairs")
        key, val = item
        if key not in _PARAM_NAMES:
            raise ValueError(f"unknown parameter {key}")
        if key in parsed:
            raise ValueError(f"duplicate parameter {key}")
        parsed[key] = val
    return parsed


def _only(parsed: dict[str, int | float | str], allowed: frozenset[str]) -> None:
    for key in parsed:
        if key not in allowed:
            raise ValueError(f"unknown parameter {key}")


def _length(window: int | None, default: int | None) -> int:
    if window is None:
        if default is None:
            raise ValueError("window is required")
        return default
    return _pos_int(window, "window")


def _primary(
    window: int | None,
    parsed: dict[str, int | float | str],
    name: str,
    default: int,
) -> int:
    named = parsed.get(name)
    if window is not None and named is not None and named != window:
        raise ValueError(f"window and {name} disagree")
    if named is not None:
        return _pos_int(named, name)
    if window is not None:
        return _pos_int(window, "window")
    return default


def _opt_int(parsed: dict[str, int | float | str], name: str, default: int) -> int:
    if name not in parsed:
        return default
    return _pos_int(parsed[name], name)


def _reject_window(window: int | None) -> None:
    if window is not None:
        raise ValueError("window does not apply")


def _need(parsed: dict[str, int | float | str], name: str) -> int | float | str:
    if name not in parsed:
        raise ValueError(f"{name} is required")
    return parsed[name]


def _output(output: str, allowed: frozenset[str]) -> None:
    if output not in allowed:
        raise ValueError(f"unknown output {output!r}")


def _blank(n: int) -> list[float | None]:
    return [None] * n


def _columns(
    bars: Sequence[_Bar], price: str
) -> tuple[list[float | None], list[float | None], list[float | None], list[float | None], list[float | None], list[float | None]]:
    opens: list[float | None] = []
    highs: list[float | None] = []
    lows: list[float | None] = []
    closes: list[float | None] = []
    volumes: list[float | None] = []
    source: list[float | None] = []
    for bar in bars:
        opens.append(_num(bar.open))
        highs.append(_num(bar.high))
        lows.append(_num(bar.low))
        closes.append(_num(bar.close))
        volumes.append(_num(bar.volume))
        source.append(_num(getattr(bar, price)))
    return opens, highs, lows, closes, volumes, source


def _sma(values: Sequence[float | None], n: int) -> list[float | None]:
    out = _blank(len(values))
    if n < 1:
        return out
    for i in range(n - 1, len(values)):
        window = values[i - n + 1 : i + 1]
        if any(item is None for item in window):
            continue
        out[i] = sum(window) / n  # type: ignore[arg-type]
    return out


def _ema(values: Sequence[float | None], n: int) -> list[float | None]:
    out = _blank(len(values))
    if n < 1:
        return out
    k = 2 / (n + 1)
    seed_at: int | None = None
    for i in range(n - 1, len(values)):
        window = values[i - n + 1 : i + 1]
        if all(item is not None for item in window):
            seed_at = i
            out[i] = sum(window) / n  # type: ignore[arg-type]
            break
    if seed_at is None:
        return out
    prev = out[seed_at]
    for i in range(seed_at + 1, len(values)):
        price = values[i]
        if price is None or prev is None:
            prev = None
            continue
        prev = k * price + (1 - k) * prev
        out[i] = prev
    return out


def _wma(values: Sequence[float | None], n: int) -> list[float | None]:
    out = _blank(len(values))
    if n < 1:
        return out
    denom = n * (n + 1) / 2
    for i in range(n - 1, len(values)):
        window = values[i - n + 1 : i + 1]
        if any(item is None for item in window):
            continue
        total = 0.0
        for weight, price in enumerate(window, start=1):
            total += weight * price  # type: ignore[operator]
        out[i] = total / denom
    return out


def _true_ranges(
    highs: Sequence[float | None],
    lows: Sequence[float | None],
    closes: Sequence[float | None],
) -> list[float | None]:
    tr = _blank(len(highs))
    if highs and highs[0] is not None and lows[0] is not None:
        tr[0] = highs[0] - lows[0]
    for i in range(1, len(highs)):
        if highs[i] is None or lows[i] is None or closes[i - 1] is None:
            continue
        tr[i] = max(
            highs[i] - lows[i],  # type: ignore[operator]
            abs(highs[i] - closes[i - 1]),  # type: ignore[operator]
            abs(lows[i] - closes[i - 1]),  # type: ignore[operator]
        )
    return tr


def _atr(
    highs: Sequence[float | None],
    lows: Sequence[float | None],
    closes: Sequence[float | None],
    n: int,
) -> list[float | None]:
    # Mean of TR[1] .. TR[n] at index n. TR[0] is not in that seed.
    tr = _true_ranges(highs, lows, closes)
    out = _blank(len(tr))
    if n < 1 or n >= len(tr):
        return out
    seed = tr[1 : n + 1]
    if any(item is None for item in seed):
        return out
    avg = sum(seed) / n  # type: ignore[arg-type]
    out[n] = avg
    for i in range(n + 1, len(tr)):
        if tr[i] is None or avg is None:
            avg = None
            continue
        avg = (avg * (n - 1) + tr[i]) / n  # type: ignore[operator]
        out[i] = avg
    return out


def _wilder(prices: Sequence[float | None], n: int) -> tuple[list[float | None], list[float | None]]:
    up = _blank(len(prices))
    down = _blank(len(prices))
    seed_at: int | None = None
    avg_up = 0.0
    avg_down = 0.0
    for i in range(n, len(prices)):
        span = prices[i - n : i + 1]
        if any(item is None for item in span):
            continue
        gains: list[float] = []
        losses: list[float] = []
        for j in range(i - n + 1, i + 1):
            change = float(prices[j]) - float(prices[j - 1])
            gains.append(change if change > 0 else 0.0)
            losses.append(-change if change < 0 else 0.0)
        avg_up = sum(gains) / n
        avg_down = sum(losses) / n
        up[i] = avg_up
        down[i] = avg_down
        seed_at = i
        break
    if seed_at is None:
        return up, down
    alive = True
    for i in range(seed_at + 1, len(prices)):
        if not alive or prices[i] is None or prices[i - 1] is None:
            alive = False
            continue
        change = float(prices[i]) - float(prices[i - 1])
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_up = (avg_up * (n - 1) + gain) / n
        avg_down = (avg_down * (n - 1) + loss) / n
        up[i] = avg_up
        down[i] = avg_down
    return up, down


def _rsi_from(up: Sequence[float | None], down: Sequence[float | None]) -> list[float | None]:
    out = _blank(len(up))
    for i, (gain, loss) in enumerate(zip(up, down)):
        if gain is None or loss is None:
            continue
        if loss == 0:
            out[i] = 100.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + gain / loss)
    return out


def _cmo_from(up: Sequence[float | None], down: Sequence[float | None]) -> list[float | None]:
    out = _blank(len(up))
    for i, (gain, loss) in enumerate(zip(up, down)):
        if gain is None or loss is None:
            continue
        denom = gain + loss
        if denom == 0:
            continue
        out[i] = 100.0 * (gain - loss) / denom
    return out


def _typical(
    highs: Sequence[float | None],
    lows: Sequence[float | None],
    closes: Sequence[float | None],
) -> list[float | None]:
    out = _blank(len(highs))
    for i in range(len(highs)):
        if highs[i] is None or lows[i] is None or closes[i] is None:
            continue
        out[i] = (highs[i] + lows[i] + closes[i]) / 3  # type: ignore[operator]
    return out


def _multiplier(high: float | None, low: float | None, close: float | None) -> float | None:
    if high is None or low is None or close is None:
        return None
    if high == low:
        return 0.0
    return ((close - low) - (high - close)) / (high - low)


def _stoch_raw(
    current: Sequence[float | None],
    highs: Sequence[float | None],
    lows: Sequence[float | None],
    n: int,
) -> list[float | None]:
    out = _blank(len(current))
    for i in range(n - 1, len(current)):
        if current[i] is None:
            continue
        high_w = highs[i - n + 1 : i + 1]
        low_w = lows[i - n + 1 : i + 1]
        if any(item is None for item in high_w) or any(item is None for item in low_w):
            continue
        highest = max(high_w)  # type: ignore[type-var]
        lowest = min(low_w)  # type: ignore[type-var]
        if highest == lowest:
            continue
        out[i] = 100.0 * (current[i] - lowest) / (highest - lowest)  # type: ignore[operator]
    return out


def _value(output: str, series: list[float | None]) -> list[float | None]:
    _output(output, frozenset({"value"}))
    return series


def _ema_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset())
    n = _length(window, None)
    _, _, _, _, _, source = _columns(bars, price)
    return _value(output, _ema(source, n))


def _wma_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset())
    n = _length(window, None)
    _, _, _, _, _, source = _columns(bars, price)
    return _value(output, _wma(source, n))


def _dema_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset())
    n = _length(window, None)
    _, _, _, _, _, source = _columns(bars, price)
    first = _ema(source, n)
    second = _ema(first, n)
    out = _blank(len(source))
    for i in range(len(source)):
        if first[i] is None or second[i] is None:
            continue
        out[i] = 2 * first[i] - second[i]  # type: ignore[operator]
    return _value(output, out)


def _tema_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset())
    n = _length(window, None)
    _, _, _, _, _, source = _columns(bars, price)
    first = _ema(source, n)
    second = _ema(first, n)
    third = _ema(second, n)
    out = _blank(len(source))
    for i in range(len(source)):
        if first[i] is None or second[i] is None or third[i] is None:
            continue
        out[i] = 3 * first[i] - 3 * second[i] + third[i]  # type: ignore[operator]
    return _value(output, out)


def _kama_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset())
    n = _length(window, None)
    _, _, _, _, _, source = _columns(bars, price)
    out = _blank(len(source))
    fast_c = 2 / 3
    slow_c = 2 / 31
    seed_at: int | None = None
    prev: float | None = None
    for i in range(n, len(source)):
        if all(item is not None for item in source[i - n : i + 1]):
            prev = source[i]
            out[i] = prev
            seed_at = i
            break
    if seed_at is None or prev is None:
        return _value(output, out)
    for i in range(seed_at + 1, len(source)):
        if source[i] is None or source[i - n] is None:
            prev = None
            continue
        if prev is None:
            continue
        volatility = 0.0
        defined = True
        for j in range(i - n + 1, i + 1):
            if source[j] is None or source[j - 1] is None:
                defined = False
                break
            volatility += abs(source[j] - source[j - 1])  # type: ignore[operator]
        if not defined or volatility == 0:
            prev = None
            continue
        efficiency = abs(source[i] - source[i - n]) / volatility  # type: ignore[operator]
        smoothing = (efficiency * (fast_c - slow_c) + slow_c) ** 2
        prev = prev + smoothing * (source[i] - prev)  # type: ignore[operator]
        out[i] = prev
    return _value(output, out)


def _hma_kind(bars, window, price, output, parsed) -> list[float | None]:
    # WMA(2*WMA(n/2) - WMA(n), floor(sqrt(n))), both lengths truncated.
    _only(parsed, frozenset())
    n = _length(window, None)
    half = n // 2
    root = math.isqrt(n)
    if half < 1 or root < 1:
        raise ValueError("window is too small for HMA")
    _, _, _, _, _, source = _columns(bars, price)
    short = _wma(source, half)
    full = _wma(source, n)
    raw = _blank(len(source))
    for i in range(len(source)):
        if short[i] is None or full[i] is None:
            continue
        raw[i] = 2 * short[i] - full[i]  # type: ignore[operator]
    return _value(output, _wma(raw, root))


def _vwma_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset())
    n = _length(window, None)
    _, _, _, _, volumes, source = _columns(bars, price)
    out = _blank(len(source))
    for i in range(n - 1, len(source)):
        prices = source[i - n + 1 : i + 1]
        vols = volumes[i - n + 1 : i + 1]
        if any(item is None for item in prices) or any(item is None for item in vols):
            continue
        denom = sum(vols)  # type: ignore[arg-type]
        if denom == 0:
            continue
        numer = 0.0
        for price_i, vol_i in zip(prices, vols):
            numer += price_i * vol_i  # type: ignore[operator]
        out[i] = numer / denom
    return _value(output, out)


def _vwap_kind(bars, window, price, output, parsed) -> list[float | None]:
    _reject_window(window)
    _only(parsed, frozenset({"anchor"}))
    anchor = _need(parsed, "anchor")
    _, highs, lows, closes, volumes, _ = _columns(bars, price)
    typical = _typical(highs, lows, closes)
    out = _blank(len(bars))
    if anchor == "cumulative":
        numer = 0.0
        denom = 0.0
        broken = False
        for i in range(len(bars)):
            if broken or typical[i] is None or volumes[i] is None:
                broken = True
                continue
            numer += typical[i] * volumes[i]  # type: ignore[operator]
            denom += volumes[i]  # type: ignore[operator]
            if denom == 0:
                continue
            out[i] = numer / denom
        return _value(output, out)
    count = _pos_int(anchor, "anchor")
    bucket = -1
    numer = 0.0
    denom = 0.0
    broken = False
    for i in range(len(bars)):
        current = i // count
        if current != bucket:
            bucket = current
            numer = 0.0
            denom = 0.0
            broken = False
        if broken or typical[i] is None or volumes[i] is None:
            broken = True
            continue
        numer += typical[i] * volumes[i]  # type: ignore[operator]
        denom += volumes[i]  # type: ignore[operator]
        if denom == 0:
            continue
        out[i] = numer / denom
    return _value(output, out)


def _supertrend_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset({"multiplier"}))
    _output(output, frozenset({"value", "direction"}))
    n = _length(window, 10)
    multiplier = _positive(parsed["multiplier"], "multiplier") if "multiplier" in parsed else 3.0
    _, highs, lows, closes, _, source = _columns(bars, price)
    atr = _atr(highs, lows, closes, n)
    line = _blank(len(bars))
    direction = _blank(len(bars))
    trend: int | None = None
    prev_upper: float | None = None
    prev_lower: float | None = None
    prev_close: float | None = None
    for i in range(len(bars)):
        if atr[i] is None or highs[i] is None or lows[i] is None:
            prev_close = source[i]
            continue
        median = (highs[i] + lows[i]) / 2  # type: ignore[operator]
        basic_upper = median + multiplier * atr[i]  # type: ignore[operator]
        basic_lower = median - multiplier * atr[i]  # type: ignore[operator]
        if trend is None:
            final_upper = basic_upper
            final_lower = basic_lower
            trend = 1
        else:
            assert prev_upper is not None and prev_lower is not None
            if basic_upper < prev_upper or (prev_close is not None and prev_close > prev_upper):
                final_upper = basic_upper
            else:
                final_upper = prev_upper
            if basic_lower > prev_lower or (prev_close is not None and prev_close < prev_lower):
                final_lower = basic_lower
            else:
                final_lower = prev_lower
            close = source[i]
            if trend == 1:
                trend = -1 if close is not None and close < final_lower else 1
            else:
                trend = 1 if close is not None and close > final_upper else -1
        prev_upper = final_upper
        prev_lower = final_lower
        prev_close = source[i]
        direction[i] = float(trend)
        line[i] = final_lower if trend == 1 else final_upper
    return line if output == "value" else direction


def _sar_kind(bars, window, price, output, parsed) -> list[float | None]:
    # The first value is the initial SAR on bar 1. Later bars advance, then clamp.
    # A strict break through that SAR flips the side and restarts the step.
    _reject_window(window)
    _only(parsed, frozenset({"step", "maximum"}))
    step = _positive(parsed["step"], "step") if "step" in parsed else 0.02
    maximum = _positive(parsed["maximum"], "maximum") if "maximum" in parsed else 0.2
    if maximum < step:
        raise ValueError("maximum must be >= step")
    _, highs, lows, _, _, _ = _columns(bars, price)
    out = _blank(len(bars))
    if len(bars) < 2:
        return _value(output, out)
    if highs[0] is None or lows[0] is None or highs[1] is None or lows[1] is None:
        return _value(output, out)
    if highs[1] > highs[0]:
        up = True
        sar = lows[0]
        extreme = highs[1]
    else:
        up = False
        sar = highs[0]
        extreme = lows[1]
    af = step
    out[1] = sar
    for i in range(2, len(bars)):
        if None in (highs[i], lows[i], highs[i - 1], lows[i - 1], highs[i - 2], lows[i - 2]):
            break
        sar = sar + af * (extreme - sar)
        if up:
            sar = min(sar, lows[i - 1], lows[i - 2])  # type: ignore[type-var]
        else:
            sar = max(sar, highs[i - 1], highs[i - 2])  # type: ignore[type-var]
        if up and lows[i] < sar:  # type: ignore[operator]
            up = False
            sar = extreme
            extreme = lows[i]
            af = step
            sar = max(sar, highs[i - 1], highs[i - 2])  # type: ignore[type-var]
        elif not up and highs[i] > sar:  # type: ignore[operator]
            up = True
            sar = extreme
            extreme = highs[i]
            af = step
            sar = min(sar, lows[i - 1], lows[i - 2])  # type: ignore[type-var]
        elif up and highs[i] > extreme:  # type: ignore[operator]
            extreme = highs[i]
            af = min(af + step, maximum)
        elif not up and lows[i] < extreme:  # type: ignore[operator]
            extreme = lows[i]
            af = min(af + step, maximum)
        out[i] = sar
    return _value(output, out)


def _donchian_mid(highs: Sequence[float | None], lows: Sequence[float | None], n: int):
    # The finished window is the previous n bars, not this bar.
    upper = _blank(len(highs))
    lower = _blank(len(highs))
    middle = _blank(len(highs))
    for i in range(n, len(highs)):
        high_w = highs[i - n : i]
        low_w = lows[i - n : i]
        if any(item is None for item in high_w) or any(item is None for item in low_w):
            continue
        upper[i] = max(high_w)  # type: ignore[type-var]
        lower[i] = min(low_w)  # type: ignore[type-var]
        middle[i] = (upper[i] + lower[i]) / 2  # type: ignore[operator]
    return {"upper": upper, "middle": middle, "lower": lower}


def _ichimoku_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset({"slow", "signal"}))
    _output(output, frozenset({"tenkan", "kijun", "span_a", "span_b"}))
    tenkan_n = _length(window, 9)
    kijun_n = _opt_int(parsed, "slow", 26)
    span_n = _opt_int(parsed, "signal", 52)
    _, highs, lows, _, _, _ = _columns(bars, price)

    def midpoint(i: int, length: int) -> float | None:
        if i < length - 1:
            return None
        high_w = highs[i - length + 1 : i + 1]
        low_w = lows[i - length + 1 : i + 1]
        if any(item is None for item in high_w) or any(item is None for item in low_w):
            return None
        return (max(high_w) + min(low_w)) / 2  # type: ignore[operator, type-var]

    tenkan = _blank(len(bars))
    kijun = _blank(len(bars))
    span_b = _blank(len(bars))
    span_a = _blank(len(bars))
    for i in range(len(bars)):
        tenkan[i] = midpoint(i, tenkan_n)
        kijun[i] = midpoint(i, kijun_n)
        span_b[i] = midpoint(i, span_n)
        if tenkan[i] is not None and kijun[i] is not None:
            span_a[i] = (tenkan[i] + kijun[i]) / 2  # type: ignore[operator]
    return {"tenkan": tenkan, "kijun": kijun, "span_a": span_a, "span_b": span_b}[output]


def _macd_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset({"fast", "slow", "signal"}))
    _output(output, frozenset({"macd", "signal", "histogram"}))
    fast = _primary(window, parsed, "fast", 12)
    slow = _opt_int(parsed, "slow", 26)
    signal_n = _opt_int(parsed, "signal", 9)
    if fast > slow:
        fast, slow = slow, fast
    _, _, _, _, _, source = _columns(bars, price)
    fast_ema = _ema(source, fast)
    slow_ema = _ema(source, slow)
    line = _blank(len(source))
    for i in range(len(source)):
        if fast_ema[i] is None or slow_ema[i] is None:
            continue
        line[i] = fast_ema[i] - slow_ema[i]  # type: ignore[operator]
    signal = _ema(line, signal_n)
    macd = _blank(len(source))
    histogram = _blank(len(source))
    for i in range(len(source)):
        if signal[i] is None or line[i] is None:
            continue
        macd[i] = line[i]
        histogram[i] = line[i] - signal[i]  # type: ignore[operator]
    return {"macd": macd, "signal": signal, "histogram": histogram}[output]


def _natr_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset())
    n = _length(window, 14)
    _, highs, lows, closes, _, _ = _columns(bars, price)
    atr = _atr(highs, lows, closes, n)
    out: list[float | None] = []
    for i, close in enumerate(closes):
        if atr[i] is None or close is None or close == 0:
            out.append(None)
        else:
            out.append(100.0 * atr[i] / close)  # type: ignore[operator]
    return _value(output, out)


def _rsi_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset())
    n = _length(window, 14)
    _, _, _, _, _, source = _columns(bars, price)
    return _value(output, _rsi_from(*_wilder(source, n)))


def _stoch_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset({"k_smooth", "d_smooth"}))
    _output(output, frozenset({"slow_k", "slow_d"}))
    n = _length(window, 14)
    k_smooth = _opt_int(parsed, "k_smooth", 3)
    d_smooth = _opt_int(parsed, "d_smooth", 3)
    _, highs, lows, _, _, source = _columns(bars, price)
    raw = _stoch_raw(source, highs, lows, n)
    slow_k = _sma(raw, k_smooth)
    slow_d = _sma(slow_k, d_smooth)
    return {"slow_k": slow_k, "slow_d": slow_d}[output]


def _stochrsi_kind(bars, window, price, output, parsed) -> list[float | None]:
    # The one length is both the RSI length and the stochastic length. No fast-K.
    _only(parsed, frozenset({"d_smooth"}))
    _output(output, frozenset({"k", "d"}))
    n = _length(window, 14)
    d_smooth = _opt_int(parsed, "d_smooth", 3)
    _, _, _, _, _, source = _columns(bars, price)
    rsi = _rsi_from(*_wilder(source, n))
    raw = _stoch_raw(rsi, rsi, rsi, n)
    return {"k": raw, "d": _sma(raw, d_smooth)}[output]


def _cci_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset())
    n = _length(window, 14)
    _, highs, lows, closes, _, _ = _columns(bars, price)
    typical = _typical(highs, lows, closes)
    means = _sma(typical, n)
    out = _blank(len(bars))
    for i in range(n - 1, len(bars)):
        window_tp = typical[i - n + 1 : i + 1]
        if means[i] is None or any(item is None for item in window_tp):
            continue
        deviation = sum(abs(item - means[i]) for item in window_tp) / n  # type: ignore[operator]
        if deviation == 0 or typical[i] is None:
            continue
        out[i] = (typical[i] - means[i]) / (0.015 * deviation)  # type: ignore[operator]
    return _value(output, out)


def _willr_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset())
    n = _length(window, 14)
    _, highs, lows, _, _, source = _columns(bars, price)
    out = _blank(len(bars))
    for i in range(n - 1, len(bars)):
        if source[i] is None:
            continue
        high_w = highs[i - n + 1 : i + 1]
        low_w = lows[i - n + 1 : i + 1]
        if any(item is None for item in high_w) or any(item is None for item in low_w):
            continue
        highest = max(high_w)  # type: ignore[type-var]
        lowest = min(low_w)  # type: ignore[type-var]
        if highest == lowest:
            continue
        out[i] = -100.0 * (highest - source[i]) / (highest - lowest)  # type: ignore[operator]
    return _value(output, out)


def _roc_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset())
    n = _length(window, 10)
    _, _, _, _, _, source = _columns(bars, price)
    out = _blank(len(source))
    for i in range(n, len(source)):
        old = source[i - n]
        if source[i] is None or old is None or old == 0:
            continue
        out[i] = (source[i] / old - 1) * 100  # type: ignore[operator]
    return _value(output, out)


def _mom_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset())
    n = _length(window, 10)
    _, _, _, _, _, source = _columns(bars, price)
    out = _blank(len(source))
    for i in range(n, len(source)):
        if source[i] is None or source[i - n] is None:
            continue
        out[i] = source[i] - source[i - n]  # type: ignore[operator]
    return _value(output, out)


def _mfi_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset())
    n = _length(window, 14)
    _, highs, lows, closes, volumes, _ = _columns(bars, price)
    typical = _typical(highs, lows, closes)
    out = _blank(len(bars))
    for i in range(n, len(bars)):
        positive = 0.0
        negative = 0.0
        defined = True
        for j in range(i - n + 1, i + 1):
            if typical[j] is None or typical[j - 1] is None or volumes[j] is None:
                defined = False
                break
            flow = typical[j] * volumes[j]  # type: ignore[operator]
            if typical[j] > typical[j - 1]:  # type: ignore[operator]
                positive += flow
            elif typical[j] < typical[j - 1]:  # type: ignore[operator]
                negative += flow
        if not defined:
            continue
        if negative == 0:
            out[i] = 100.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + positive / negative)
    return _value(output, out)


def _ao_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset({"fast", "slow"}))
    fast = _primary(window, parsed, "fast", 5)
    slow = _opt_int(parsed, "slow", 34)
    _, highs, lows, _, _, _ = _columns(bars, price)
    median = _blank(len(bars))
    for i in range(len(bars)):
        if highs[i] is None or lows[i] is None:
            continue
        median[i] = (highs[i] + lows[i]) / 2  # type: ignore[operator]
    fast_sma = _sma(median, fast)
    slow_sma = _sma(median, slow)
    out = _blank(len(bars))
    for i in range(len(bars)):
        if fast_sma[i] is None or slow_sma[i] is None:
            continue
        out[i] = fast_sma[i] - slow_sma[i]  # type: ignore[operator]
    return _value(output, out)


def _tsi_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset({"fast", "slow"}))
    long_n = _primary(window, parsed, "slow", 25)
    short_n = _opt_int(parsed, "fast", 13)
    _, _, _, _, _, source = _columns(bars, price)
    change = _blank(len(source))
    absolute = _blank(len(source))
    for i in range(1, len(source)):
        if source[i] is None or source[i - 1] is None:
            continue
        delta = source[i] - source[i - 1]  # type: ignore[operator]
        change[i] = delta
        absolute[i] = abs(delta)
    numer = _ema(_ema(change, long_n), short_n)
    denom = _ema(_ema(absolute, long_n), short_n)
    out = _blank(len(source))
    for i in range(len(source)):
        if numer[i] is None or denom[i] is None or denom[i] == 0:
            continue
        out[i] = 100.0 * numer[i] / denom[i]  # type: ignore[operator]
    return _value(output, out)


def _ultosc_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset({"fast", "slow", "signal"}))
    first = _primary(window, parsed, "fast", 7)
    second = _opt_int(parsed, "slow", 14)
    third = _opt_int(parsed, "signal", 28)
    short, middle, long = sorted((first, second, third))
    _, highs, lows, _, _, source = _columns(bars, price)
    buying = _blank(len(bars))
    true_range = _blank(len(bars))
    for i in range(1, len(bars)):
        if source[i] is None or source[i - 1] is None or highs[i] is None or lows[i] is None:
            continue
        previous = source[i - 1]
        buying[i] = source[i] - min(lows[i], previous)  # type: ignore[operator, type-var]
        true_range[i] = max(highs[i], previous) - min(lows[i], previous)  # type: ignore[operator, type-var]

    def average(i: int, length: int) -> float | None:
        if i < length:
            return None
        start = i - length + 1
        if start < 1:
            return None
        pressures = buying[start : i + 1]
        ranges = true_range[start : i + 1]
        if any(item is None for item in pressures) or any(item is None for item in ranges):
            return None
        denom = sum(ranges)  # type: ignore[arg-type]
        if denom == 0:
            return None
        return sum(pressures) / denom  # type: ignore[arg-type]

    out = _blank(len(bars))
    for i in range(len(bars)):
        short_avg = average(i, short)
        mid_avg = average(i, middle)
        long_avg = average(i, long)
        if short_avg is None or mid_avg is None or long_avg is None:
            continue
        out[i] = 100.0 * (4 * short_avg + 2 * mid_avg + long_avg) / 7
    return _value(output, out)


def _ppo_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset({"fast", "slow"}))
    fast = _primary(window, parsed, "fast", 12)
    slow = _opt_int(parsed, "slow", 26)
    _, _, _, _, _, source = _columns(bars, price)
    fast_ema = _ema(source, fast)
    slow_ema = _ema(source, slow)
    out = _blank(len(source))
    for i in range(len(source)):
        if fast_ema[i] is None or slow_ema[i] is None or slow_ema[i] == 0:
            continue
        out[i] = 100.0 * (fast_ema[i] - slow_ema[i]) / slow_ema[i]  # type: ignore[operator]
    return _value(output, out)


def _apo_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset({"fast", "slow"}))
    fast = _primary(window, parsed, "fast", 12)
    slow = _opt_int(parsed, "slow", 26)
    _, _, _, _, _, source = _columns(bars, price)
    fast_ema = _ema(source, fast)
    slow_ema = _ema(source, slow)
    out = _blank(len(source))
    for i in range(len(source)):
        if fast_ema[i] is None or slow_ema[i] is None:
            continue
        out[i] = fast_ema[i] - slow_ema[i]  # type: ignore[operator]
    return _value(output, out)


def _cmo_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset())
    n = _length(window, 14)
    _, _, _, _, _, source = _columns(bars, price)
    return _value(output, _cmo_from(*_wilder(source, n)))


def _bbands_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset({"deviation", "deviation_lower"}))
    _output(output, frozenset({"upper", "middle", "lower"}))
    n = _length(window, 20)
    upper_mult = _nonneg(parsed["deviation"], "deviation") if "deviation" in parsed else 2.0
    lower_mult = (
        _nonneg(parsed["deviation_lower"], "deviation_lower") if "deviation_lower" in parsed else 2.0
    )
    _, _, _, _, _, source = _columns(bars, price)
    upper = _blank(len(source))
    middle = _blank(len(source))
    lower = _blank(len(source))
    for i in range(n - 1, len(source)):
        window_p = source[i - n + 1 : i + 1]
        if any(item is None for item in window_p):
            continue
        mean = sum(window_p) / n  # type: ignore[arg-type]
        variance = sum((item - mean) ** 2 for item in window_p) / n  # type: ignore[operator]
        deviation = math.sqrt(variance)
        middle[i] = mean
        upper[i] = mean + upper_mult * deviation
        lower[i] = mean - lower_mult * deviation
    return {"upper": upper, "middle": middle, "lower": lower}[output]


def _keltner_kind(bars, window, price, output, parsed) -> list[float | None]:
    # `slow` is the ATR length. The window is the EMA length. Not an EMA of true range.
    _only(parsed, frozenset({"multiplier", "slow"}))
    _output(output, frozenset({"upper", "middle", "lower"}))
    n = _length(window, 20)
    atr_n = _opt_int(parsed, "slow", 20)
    multiplier = _positive(parsed["multiplier"], "multiplier") if "multiplier" in parsed else 2.0
    _, highs, lows, closes, _, source = _columns(bars, price)
    middle = _ema(source, n)
    atr = _atr(highs, lows, closes, atr_n)
    upper = _blank(len(bars))
    lower = _blank(len(bars))
    for i in range(len(bars)):
        if middle[i] is None or atr[i] is None:
            continue
        upper[i] = middle[i] + multiplier * atr[i]  # type: ignore[operator]
        lower[i] = middle[i] - multiplier * atr[i]  # type: ignore[operator]
    return {"upper": upper, "middle": middle, "lower": lower}[output]


def _donchian_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset())
    _output(output, frozenset({"upper", "middle", "lower"}))
    n = _length(window, 20)
    _, highs, lows, _, _, _ = _columns(bars, price)
    return _donchian_mid(highs, lows, n)[output]


def _obv_kind(bars, window, price, output, parsed) -> list[float | None]:
    _reject_window(window)
    _only(parsed, frozenset())
    _, _, _, _, volumes, source = _columns(bars, price)
    out = _blank(len(bars))
    if not bars or source[0] is None:
        return _value(output, out)
    # Bar 0 is 0. Later bars add, subtract, or hold volume as the chosen price moves.
    out[0] = 0.0
    previous = source[0]
    total = 0.0
    alive = True
    for i in range(1, len(bars)):
        if not alive or source[i] is None or previous is None or volumes[i] is None:
            alive = False
            continue
        if source[i] > previous:
            total += float(volumes[i])
        elif source[i] < previous:
            total -= float(volumes[i])
        out[i] = total
        previous = source[i]
    return _value(output, out)


def _ad_kind(bars, window, price, output, parsed) -> list[float | None]:
    _reject_window(window)
    _only(parsed, frozenset())
    _, highs, lows, _, volumes, source = _columns(bars, price)
    out = _blank(len(bars))
    total = 0.0
    alive = True
    for i in range(len(bars)):
        mult = _multiplier(highs[i], lows[i], source[i])
        if not alive or mult is None or volumes[i] is None:
            alive = False
            continue
        total += mult * volumes[i]  # type: ignore[operator]
        out[i] = total
    return _value(output, out)


def _cmf_kind(bars, window, price, output, parsed) -> list[float | None]:
    _only(parsed, frozenset())
    n = _length(window, 20)
    _, highs, lows, _, volumes, source = _columns(bars, price)
    out = _blank(len(bars))
    for i in range(n - 1, len(bars)):
        numer = 0.0
        denom = 0.0
        defined = True
        for j in range(i - n + 1, i + 1):
            mult = _multiplier(highs[j], lows[j], source[j])
            if mult is None or volumes[j] is None:
                defined = False
                break
            numer += mult * volumes[j]  # type: ignore[operator]
            denom += volumes[j]  # type: ignore[operator]
        if not defined or denom == 0:
            continue
        out[i] = numer / denom
    return _value(output, out)


def _pivot_kind(bars, window, price, output, parsed) -> list[float | None]:
    _reject_window(window)
    _only(parsed, frozenset({"session"}))
    _output(output, frozenset({"pivot", "r1", "s1", "r2", "s2"}))
    session = _pos_int(_need(parsed, "session"), "session")
    _, highs, lows, _, _, source = _columns(bars, price)
    pivot = _blank(len(bars))
    r1 = _blank(len(bars))
    s1 = _blank(len(bars))
    r2 = _blank(len(bars))
    s2 = _blank(len(bars))
    sessions = len(bars) // session
    for block in range(sessions):
        start = block * session
        end = start + session
        high_w = highs[start:end]
        low_w = lows[start:end]
        close = source[end - 1]
        if close is None or any(item is None for item in high_w) or any(item is None for item in low_w):
            level = None
        else:
            highest = max(high_w)  # type: ignore[type-var]
            lowest = min(low_w)  # type: ignore[type-var]
            p = (highest + lowest + close) / 3
            level = (
                p,
                2 * p - lowest,
                2 * p - highest,
                p + (highest - lowest),
                p - (highest - lowest),
            )
        pub_end = min(len(bars), end + session)
        for i in range(end, pub_end):
            if level is None:
                continue
            pivot[i], r1[i], s1[i], r2[i], s2[i] = level
    return {"pivot": pivot, "r1": r1, "s1": s1, "r2": r2, "s2": s2}[output]


def _swing_kind(bars, window, price, output, parsed) -> list[float | None]:
    _reject_window(window)
    _only(parsed, frozenset({"left", "right"}))
    _output(output, frozenset({"high", "low"}))
    left = _pos_int(_need(parsed, "left"), "left")
    right = _pos_int(_need(parsed, "right"), "right")
    _, highs, lows, _, _, _ = _columns(bars, price)

    def side(values: Sequence[float | None], want_high: bool) -> list[float | None]:
        out = _blank(len(values))
        current: float | None = None
        for i in range(len(values)):
            pivot = i - right
            if pivot >= left:
                candidate = values[pivot]
                if candidate is not None:
                    strict = True
                    for j in range(pivot - left, pivot + right + 1):
                        if j == pivot:
                            continue
                        other = values[j]
                        if other is None or not (candidate > other if want_high else candidate < other):
                            strict = False
                            break
                    if strict:
                        current = candidate
            out[i] = current
        return out

    return {"high": side(highs, True), "low": side(lows, False)}[output]


_KINDS = {
    "ema": _ema_kind,
    "wma": _wma_kind,
    "dema": _dema_kind,
    "tema": _tema_kind,
    "kama": _kama_kind,
    "hma": _hma_kind,
    "vwma": _vwma_kind,
    "vwap": _vwap_kind,
    "supertrend": _supertrend_kind,
    "sar": _sar_kind,
    "ichimoku": _ichimoku_kind,
    "macd": _macd_kind,
    "natr": _natr_kind,
    "rsi": _rsi_kind,
    "stoch": _stoch_kind,
    "stochrsi": _stochrsi_kind,
    "cci": _cci_kind,
    "willr": _willr_kind,
    "roc": _roc_kind,
    "mom": _mom_kind,
    "mfi": _mfi_kind,
    "ao": _ao_kind,
    "tsi": _tsi_kind,
    "ultosc": _ultosc_kind,
    "ppo": _ppo_kind,
    "apo": _apo_kind,
    "cmo": _cmo_kind,
    "bbands": _bbands_kind,
    "keltner": _keltner_kind,
    "donchian": _donchian_kind,
    "obv": _obv_kind,
    "ad": _ad_kind,
    "cmf": _cmf_kind,
    "pivot": _pivot_kind,
    "swing": _swing_kind,
}


def values(
    kind: str,
    bars: Sequence[_Bar],
    *,
    window: int | None,
    price: str,
    output: str,
    params: tuple[tuple[str, int | float | str], ...],
) -> list[float | None]:
    """One output series, aligned to bars. None where the line is undefined."""
    if kind not in _KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    if price not in _PRICES:
        raise ValueError(f"unknown price {price!r}")
    parsed = _parse_params(params)
    return _KINDS[kind](tuple(bars), window, price, output, parsed)

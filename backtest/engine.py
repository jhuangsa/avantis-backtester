"""Score one hypothesis on one bar series."""

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import NamedTuple

from backtest.lines import canon_kind, price_series, values as line_values

_WINDOW_REQUIRED = frozenset(
    {"sma", "ema", "wma", "dema", "tema", "kama", "hma", "vwma"}
)
_PINNED_WINDOW = {
    "atr": 14,
    "natr": 14,
    "rsi": 14,
    "cci": 14,
    "willr": 14,
    "williams r": 14,
    "mfi": 14,
    "cmo": 14,
    "roc": 10,
    "mom": 10,
    "momentum": 10,
    "bollinger": 20,
    "bbands": 20,
    "donchian": 20,
    "cmf": 20,
    "chaikin money flow": 20,
    "supertrend": 10,
    "keltner": 20,
}
_VARY_KEYS = frozenset(
    {
        "window",
        "threshold",
        "take_profit_size",
        "stop_loss_size",
        "time_exit",
        "fee",
    }
)
_PRICE_FIELDS = frozenset({"open", "high", "low", "close"})
_SIDES = frozenset({"long", "short", "both"})
_CAUSES = frozenset(
    {
        "take profit",
        "stop loss",
        "rule exit",
        "time exit",
        "action",
        "still open",
    }
)
_ACTIONS = frozenset({"long", "short", "flat"})


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Price:
    field: str = "close"

    def __post_init__(self):
        if self.field not in _PRICE_FIELDS:
            raise ValueError(self.field)


@dataclass(frozen=True)
class Threshold:
    value: float
    name: str


@dataclass(frozen=True)
class Indicator:
    kind: str
    name: str
    window: int | None = field(default=None, kw_only=True)
    price: str = field(default="close", kw_only=True)
    output: str = field(default="value", kw_only=True)
    params: tuple = field(default=(), kw_only=True)

    def __post_init__(self):
        if not isinstance(self.params, tuple):
            object.__setattr__(self, "params", tuple(self.params))


@dataclass(frozen=True)
class Cross:
    left: object
    right: object
    direction: str

    def __post_init__(self):
        if self.direction not in ("above", "below"):
            raise ValueError(self.direction)


@dataclass(frozen=True)
class Above:
    left: object
    right: object


@dataclass(frozen=True)
class Below:
    left: object
    right: object


@dataclass(frozen=True)
class All:
    parts: tuple

    def __post_init__(self):
        object.__setattr__(self, "parts", _as_parts(self.parts))


@dataclass(frozen=True)
class Any:
    parts: tuple

    def __post_init__(self):
        object.__setattr__(self, "parts", _as_parts(self.parts))


@dataclass(frozen=True)
class Hypothesis:
    name: str
    side: str | None = None
    distance_kind: str | None = field(default=None, kw_only=True)
    take_profit_size: float | None = field(default=None, kw_only=True)
    stop_loss_size: float | None = field(default=None, kw_only=True)
    long_entry: object | None = field(default=None, kw_only=True)
    short_entry: object | None = field(default=None, kw_only=True)
    long_rule_exit: object | None = field(default=None, kw_only=True)
    short_rule_exit: object | None = field(default=None, kw_only=True)
    time_exit: int | None = field(default=None, kw_only=True)
    atr_window: int | None = field(default=None, kw_only=True)
    action: object | None = field(default=None, kw_only=True)

    def __post_init__(self):
        if self.action is not None:
            _validate_action(self)
        else:
            _validate_hypothesis(self)


@dataclass(frozen=True)
class Trade:
    entry_bar: int
    exit_bar: int
    side: str
    entry_price: float
    exit_price: float
    cause: str


@dataclass(frozen=True)
class Result:
    trades: tuple[Trade, ...]
    ending_stake: float
    parameters: dict
    bar_size: object


class _Position(NamedTuple):
    entry_bar: int
    side: str
    entry_price: float
    target: float
    stop: float


def backtest(hypothesis, bars, *, fee=0.0, bar_size):
    _check_fee(fee)
    _check_bar_size(bar_size)
    if not isinstance(hypothesis, Hypothesis):
        raise ValueError("hypothesis")
    if hypothesis.action is not None:
        return _backtest_action(hypothesis, bars, fee=fee, bar_size=bar_size)
    bars = tuple(bars)
    series = _compile(hypothesis, bars)
    atr_line = None
    if hypothesis.distance_kind == "average true range":
        atr_line = _as_line(
            line_values(
                "atr",
                bars,
                _distance_atr_window(hypothesis),
                "close",
                "value",
                (),
            ),
            len(bars),
        )
    trades = []
    stake = 1.0
    pending = None
    position = None
    last = len(bars) - 1
    for i, bar in enumerate(bars):
        action = pending
        pending = None
        # A non-finite open, high, low, or close blocks every order on this bar.
        prices = _finite_bar(bar)
        if prices is None:
            continue
        open_price, high, low = prices
        # A reverse fills at this open only when the open is inside the open
        # trade's levels. A level there fills instead, and the other side does
        # not open. A plain close waits for that same open test.
        if action is not None and action[0] == "reverse":
            gap = (
                _open_through(position, open_price) if position is not None else None
            )
            if gap is not None:
                cause, price = gap
                stake = _close(trades, stake, position, i, price, cause, fee, True)
                position = None
            else:
                if position is not None:
                    stake = _close(
                        trades, stake, position, i, open_price, action[2], fee, True
                    )
                    position = None
                stake, position = _try_open(
                    hypothesis, stake, fee, i, action[1], open_price, atr_line
                )
        elif action is not None and action[0] == "entry":
            stake, position = _try_open(
                hypothesis, stake, fee, i, action[1], open_price, atr_line
            )
        if position is not None:
            gap = _open_through(position, open_price)
            if gap is not None:
                cause, price = gap
                stake = _close(trades, stake, position, i, price, cause, fee, True)
                position = None
            elif action is not None and action[0] == "close":
                stake = _close(
                    trades, stake, position, i, open_price, action[1], fee, True
                )
                position = None
            else:
                spanned = _range_through(position, high, low)
                if spanned is not None:
                    cause, price = spanned
                    stake = _close(
                        trades, stake, position, i, price, cause, fee, True
                    )
                    position = None
        if i == last:
            continue
        if position is not None:
            pending = _schedule_exit(hypothesis, position, i, series)
        else:
            pending = _schedule_entry(hypothesis, i, series)
    if position is not None:
        stake = _close(
            trades,
            stake,
            position,
            last,
            float(bars[last].close),
            "still open",
            fee,
            False,
        )
    return Result(
        trades=tuple(trades),
        ending_stake=stake,
        parameters=_parameters(hypothesis, fee),
        bar_size=bar_size,
    )


class _Held(NamedTuple):
    entry_bar: int
    side: str
    entry_price: float


def _backtest_action(hypothesis, bars, *, fee, bar_size):
    """The action at each bar sees only earlier bars, and fills at this open."""
    bars = tuple(bars)
    trades = []
    stake = 1.0
    held = None
    last = len(bars) - 1
    for i, bar in enumerate(bars):
        if i == 0:
            continue
        # A non-finite fill, or a non-finite bar just closed, blocks the action.
        if _finite_bar(bar) is None or _finite_bar(bars[i - 1]) is None:
            continue
        decision = hypothesis.action(bars[:i])
        if decision not in _ACTIONS:
            raise ValueError("action")
        open_price = float(bar.open)
        if held is not None and decision != held.side:
            stake = _close(trades, stake, held, i, open_price, "action", fee, True)
            held = None
        if held is None and decision in ("long", "short"):
            stake *= 1 - fee
            held = _Held(i, decision, open_price)
    if held is not None:
        stake = _close(
            trades,
            stake,
            held,
            last,
            float(bars[last].close),
            "still open",
            fee,
            False,
        )
    return Result(
        trades=tuple(trades),
        ending_stake=stake,
        parameters={"fee": float(fee)},
        bar_size=bar_size,
    )


def grid(hypothesis, bars, *, fee=0.0, bar_size, vary=None):
    _check_fee(fee)
    _check_bar_size(bar_size)
    if not isinstance(hypothesis, Hypothesis) or hypothesis.action is not None:
        raise ValueError("hypothesis")
    if vary is None:
        vary = {}
    if not isinstance(vary, Mapping):
        raise ValueError("vary")
    unknown = set(vary) - _VARY_KEYS
    if unknown:
        raise ValueError(f"vary keys {sorted(unknown)}")
    bars = tuple(bars)
    window_map = vary.get("window", {})
    threshold_map = vary.get("threshold", {})
    if not isinstance(window_map, Mapping) or not isinstance(threshold_map, Mapping):
        raise ValueError("vary")
    names = {item.name for item in _indicators(hypothesis)}
    threshold_names = {item.name for item in _thresholds(hypothesis)}
    for name in window_map:
        implicit_atr = (
            name == "atr" and hypothesis.distance_kind == "average true range"
        )
        if name not in names and not implicit_atr:
            raise ValueError(name)
    for name in threshold_map:
        if name not in threshold_names:
            raise ValueError(name)
    window_names = sorted(window_map)
    threshold_names_sorted = sorted(threshold_map)
    axes = (
        [_as_seq(window_map[name]) for name in window_names]
        + [_as_seq(threshold_map[name]) for name in threshold_names_sorted]
        + [
            _as_seq(vary["take_profit_size"])
            if "take_profit_size" in vary
            else [hypothesis.take_profit_size],
            _as_seq(vary["stop_loss_size"])
            if "stop_loss_size" in vary
            else [hypothesis.stop_loss_size],
            _as_seq(vary["time_exit"])
            if "time_exit" in vary
            else [hypothesis.time_exit],
            _as_seq(vary["fee"]) if "fee" in vary else [fee],
        ]
    )
    for axis in axes:
        if len(axis) == 0:
            raise ValueError("vary")
    n_windows = len(window_names)
    n_thresholds = len(threshold_names_sorted)
    cells = []
    for combo in itertools.product(*axes):
        window_values = dict(zip(window_names, combo[:n_windows]))
        threshold_values = dict(
            zip(
                threshold_names_sorted,
                combo[n_windows : n_windows + n_thresholds],
            )
        )
        take_profit, stop_loss, time_exit, cell_fee = combo[n_windows + n_thresholds :]
        for window in window_values.values():
            if not _whole(window):
                raise ValueError("window")
        _check_fee(cell_fee)
        cells.append(
            (
                _retarget(
                    hypothesis,
                    window_values,
                    threshold_values,
                    take_profit,
                    stop_loss,
                    time_exit,
                ),
                cell_fee,
            )
        )
    return tuple(
        backtest(cell, bars, fee=cell_fee, bar_size=bar_size)
        for cell, cell_fee in cells
    )


def _retarget(hypothesis, windows, thresholds, take_profit, stop_loss, time_exit):
    names = {item.name for item in _indicators(hypothesis)}
    atr_window = hypothesis.atr_window
    if "atr" in windows and "atr" not in names:
        atr_window = windows["atr"]
    return replace(
        hypothesis,
        long_entry=_map_rule(hypothesis.long_entry, windows, thresholds),
        short_entry=_map_rule(hypothesis.short_entry, windows, thresholds),
        long_rule_exit=_map_rule(hypothesis.long_rule_exit, windows, thresholds),
        short_rule_exit=_map_rule(hypothesis.short_rule_exit, windows, thresholds),
        take_profit_size=take_profit,
        stop_loss_size=stop_loss,
        time_exit=time_exit,
        atr_window=atr_window,
    )


def _map_rule(rule, windows, thresholds):
    if rule is None:
        return None
    if isinstance(rule, (Cross, Above, Below)):
        return replace(
            rule,
            left=_map_operand(rule.left, windows, thresholds),
            right=_map_operand(rule.right, windows, thresholds),
        )
    if isinstance(rule, All):
        return All(
            tuple(_map_rule(part, windows, thresholds) for part in rule.parts)
        )
    if isinstance(rule, Any):
        return Any(
            tuple(_map_rule(part, windows, thresholds) for part in rule.parts)
        )
    raise ValueError("rule")


def _map_operand(operand, windows, thresholds):
    if isinstance(operand, Threshold) and operand.name in thresholds:
        return replace(operand, value=thresholds[operand.name])
    if isinstance(operand, Indicator) and operand.name in windows:
        return replace(operand, window=windows[operand.name])
    if isinstance(operand, (Price, Threshold, Indicator)):
        return operand
    raise ValueError("operand")


def _try_open(hypothesis, stake, fee, bar_index, side, price, atr_line):
    atr = None
    if hypothesis.distance_kind == "average true range":
        signal = bar_index - 1
        if atr_line is None or signal < 0 or signal >= len(atr_line):
            return stake, None
        atr = atr_line[signal]
        if atr is None:
            return stake, None
    levels = _levels(
        side,
        price,
        hypothesis.distance_kind,
        hypothesis.take_profit_size,
        hypothesis.stop_loss_size,
        atr,
    )
    if levels is None:
        return stake, None
    target, stop = levels
    return stake * (1 - fee), _Position(bar_index, side, price, target, stop)


def _levels(side, entry, kind, take_profit, stop_loss, atr):
    if kind == "percent":
        if side == "long":
            return entry * (1 + take_profit), entry * (1 - stop_loss)
        return entry * (1 - take_profit), entry * (1 + stop_loss)
    if atr is None:
        return None
    if side == "long":
        return entry + take_profit * atr, entry - stop_loss * atr
    return entry - take_profit * atr, entry + stop_loss * atr


def _finite_bar(bar):
    open_price = _finite_price(bar.open)
    high = _finite_price(bar.high)
    low = _finite_price(bar.low)
    close = _finite_price(bar.close)
    if None in (open_price, high, low, close):
        return None
    return open_price, high, low


def _finite_price(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _open_through(position, open_price):
    stop_hit, target_hit = _beyond(position, open_price, open_price)
    if stop_hit:
        return "stop loss", open_price
    if target_hit:
        return "take profit", open_price
    return None


def _range_through(position, high, low):
    stop_hit, target_hit = _beyond(position, high, low)
    if stop_hit:
        return "stop loss", position.stop
    if target_hit:
        return "take profit", position.target
    return None


def _beyond(position, high_or_open, low_or_open):
    """Return stop-touched, target-touched. For an open, pass it as both prices."""
    if position.side == "long":
        return low_or_open <= position.stop, high_or_open >= position.target
    return high_or_open >= position.stop, low_or_open <= position.target


def _close(trades, stake, position, exit_bar, exit_price, cause, fee, charge_fee):
    if cause not in _CAUSES:
        raise ValueError(cause)
    if position.side == "long":
        stake *= exit_price / position.entry_price
    else:
        stake *= 1 + (position.entry_price - exit_price) / position.entry_price
    if charge_fee:
        stake *= 1 - fee
    trades.append(
        Trade(
            entry_bar=position.entry_bar,
            exit_bar=exit_bar,
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            cause=cause,
        )
    )
    return stake


def _schedule_exit(hypothesis, position, index, series):
    if position.side == "long":
        rule_node = hypothesis.long_rule_exit
        other_node = hypothesis.short_entry
        other_side = "short"
    else:
        rule_node = hypothesis.short_rule_exit
        other_node = hypothesis.long_entry
        other_side = "long"
    rule = rule_node is not None and _true(rule_node, index, series)
    other = other_node is not None and _true(other_node, index, series)
    held = index - position.entry_bar + 1
    timed = hypothesis.time_exit is not None and held >= hypothesis.time_exit
    if rule and other:
        return ("reverse", other_side, "rule exit")
    if rule:
        return ("close", "rule exit")
    if timed:
        return ("close", "time exit")
    return None


def _schedule_entry(hypothesis, index, series):
    long_on = hypothesis.long_entry is not None and _true(
        hypothesis.long_entry, index, series
    )
    short_on = hypothesis.short_entry is not None and _true(
        hypothesis.short_entry, index, series
    )
    if long_on and short_on:
        return None
    if long_on:
        return ("entry", "long")
    if short_on:
        return ("entry", "short")
    return None


def _true(rule, index, series):
    if isinstance(rule, Cross):
        return _cross(rule, index, series)
    if isinstance(rule, Above):
        return _compare(rule, index, series, "above")
    if isinstance(rule, Below):
        return _compare(rule, index, series, "below")
    if isinstance(rule, All):
        return all(_true(part, index, series) for part in rule.parts)
    if isinstance(rule, Any):
        return any(_true(part, index, series) for part in rule.parts)
    raise ValueError("rule")


def _cross(rule, index, series):
    if index < 1:
        return False
    left = series[id(rule.left)]
    right = series[id(rule.right)]
    previous_left, current_left = left[index - 1], left[index]
    previous_right, current_right = right[index - 1], right[index]
    if None in (previous_left, current_left, previous_right, current_right):
        return False
    if rule.direction == "above":
        return previous_left <= previous_right and current_left > current_right
    return previous_left >= previous_right and current_left < current_right


def _compare(rule, index, series, direction):
    left = series[id(rule.left)][index]
    right = series[id(rule.right)][index]
    if left is None or right is None:
        return False
    if direction == "above":
        return left > right
    return left < right


def _compile(hypothesis, bars):
    series = {}
    count = len(bars)

    def ensure(operand):
        if id(operand) in series:
            return
        if isinstance(operand, Price):
            series[id(operand)] = price_series(bars, operand.field)
            return
        if isinstance(operand, Threshold):
            value = operand.value
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                series[id(operand)] = [None] * count
            else:
                series[id(operand)] = [float(value)] * count
            return
        if isinstance(operand, Indicator):
            series[id(operand)] = _as_line(
                line_values(
                    operand.kind,
                    bars,
                    operand.window,
                    operand.price,
                    operand.output,
                    operand.params,
                ),
                count,
            )
            return
        raise ValueError("operand")

    for rule in _rules(hypothesis):
        for node in _walk(rule):
            ensure(node.left)
            ensure(node.right)
    return series


def _as_line(seq, count):
    values = list(seq)
    if len(values) != count:
        raise ValueError("indicator length")
    out = []
    for value in values:
        if (
            value is None
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            out.append(None)
        else:
            out.append(float(value))
    return out


def _parameters(hypothesis, fee):
    windows = {}
    thresholds = {}
    for operand in _operands(hypothesis):
        if isinstance(operand, Threshold):
            thresholds[operand.name] = float(operand.value)
        elif isinstance(operand, Indicator):
            resolved = _resolved_window(operand)
            if resolved is not None:
                windows[operand.name] = resolved
    if hypothesis.distance_kind == "average true range" and not any(
        canon_kind(item.kind) == "atr" for item in _indicators(hypothesis)
    ):
        windows["atr"] = _distance_atr_window(hypothesis)
    parameters = {}
    if windows:
        parameters["window"] = {name: windows[name] for name in sorted(windows)}
    if thresholds:
        parameters["threshold"] = {
            name: thresholds[name] for name in sorted(thresholds)
        }
    parameters["take_profit_size"] = float(hypothesis.take_profit_size)
    parameters["stop_loss_size"] = float(hypothesis.stop_loss_size)
    if hypothesis.time_exit is not None:
        parameters["time_exit"] = hypothesis.time_exit
    parameters["fee"] = float(fee)
    return parameters


def _resolved_window(indicator):
    if indicator.window is not None:
        return indicator.window
    return _PINNED_WINDOW.get(canon_kind(indicator.kind))


def _distance_atr_window(hypothesis):
    found = []
    for indicator in _indicators(hypothesis):
        if canon_kind(indicator.kind) == "atr":
            found.append(14 if indicator.window is None else indicator.window)
    if hypothesis.atr_window is not None:
        found.append(hypothesis.atr_window)
    if not found:
        return 14
    if any(window != found[0] for window in found):
        raise ValueError("atr window")
    return found[0]


def _validate_action(hypothesis):
    if not callable(hypothesis.action):
        raise ValueError("action")
    if hypothesis.side is not None:
        raise ValueError("side")
    if hypothesis.distance_kind is not None:
        raise ValueError("distance kind")
    if hypothesis.take_profit_size is not None or hypothesis.stop_loss_size is not None:
        raise ValueError("size")
    if hypothesis.time_exit is not None or hypothesis.atr_window is not None:
        raise ValueError("action")
    if any(
        rule is not None
        for rule in (
            hypothesis.long_entry,
            hypothesis.short_entry,
            hypothesis.long_rule_exit,
            hypothesis.short_rule_exit,
        )
    ):
        raise ValueError("action")


def _validate_hypothesis(hypothesis):
    if hypothesis.side not in _SIDES:
        raise ValueError("side")
    if hypothesis.distance_kind not in ("percent", "average true range"):
        raise ValueError("distance kind")
    if not _positive(hypothesis.take_profit_size) or not _positive(
        hypothesis.stop_loss_size
    ):
        raise ValueError("size")
    if hypothesis.time_exit is not None and not _whole(hypothesis.time_exit):
        raise ValueError("time exit")
    if hypothesis.atr_window is not None and not _whole(hypothesis.atr_window):
        raise ValueError("atr window")
    if hypothesis.side == "long":
        if hypothesis.long_entry is None or hypothesis.short_entry is not None:
            raise ValueError("entry")
        if hypothesis.short_rule_exit is not None:
            raise ValueError("rule exit")
        _validate_entry(hypothesis.long_entry)
        if hypothesis.long_rule_exit is not None:
            _validate_rule(hypothesis.long_rule_exit)
    elif hypothesis.side == "short":
        if hypothesis.short_entry is None or hypothesis.long_entry is not None:
            raise ValueError("entry")
        if hypothesis.long_rule_exit is not None:
            raise ValueError("rule exit")
        _validate_entry(hypothesis.short_entry)
        if hypothesis.short_rule_exit is not None:
            _validate_rule(hypothesis.short_rule_exit)
    else:
        if hypothesis.long_entry is None or hypothesis.short_entry is None:
            raise ValueError("entry")
        _validate_entry(hypothesis.long_entry)
        _validate_entry(hypothesis.short_entry)
        if hypothesis.long_rule_exit is not None:
            _validate_rule(hypothesis.long_rule_exit)
        if hypothesis.short_rule_exit is not None:
            _validate_rule(hypothesis.short_rule_exit)
    _check_unique_names(hypothesis)
    if hypothesis.distance_kind == "average true range":
        _distance_atr_window(hypothesis)


def _validate_entry(node):
    if isinstance(node, Cross):
        _check_comparison(node)
        return
    if isinstance(node, (Above, Below)):
        raise ValueError("entry needs a cross")
    if isinstance(node, Any):
        if not node.parts:
            raise ValueError("entry needs a cross")
        for part in node.parts:
            _validate_entry(part)
        return
    if isinstance(node, All):
        if not node.parts or not _contains_cross(node):
            raise ValueError("entry needs a cross")
        _validate_all(node)
        return
    raise ValueError("entry")


def _validate_all(node):
    for part in node.parts:
        if isinstance(part, Any):
            _validate_entry(part)
        elif isinstance(part, All):
            if not part.parts:
                raise ValueError("entry needs a cross")
            _validate_all(part)
        elif isinstance(part, (Cross, Above, Below)):
            _check_comparison(part)
        else:
            raise ValueError("entry")


def _validate_rule(node):
    if isinstance(node, (Cross, Above, Below)):
        _check_comparison(node)
        return
    if isinstance(node, (All, Any)):
        if not node.parts:
            raise ValueError("rule")
        for part in node.parts:
            _validate_rule(part)
        return
    raise ValueError("rule")


def _contains_cross(node):
    if isinstance(node, Cross):
        return True
    if isinstance(node, (All, Any)):
        return any(_contains_cross(part) for part in node.parts)
    return False


def _check_comparison(node):
    _check_operand(node.left)
    _check_operand(node.right)


def _check_operand(operand):
    if isinstance(operand, Price):
        return
    if isinstance(operand, Threshold):
        if not isinstance(operand.name, str):
            raise ValueError("threshold")
        if isinstance(operand.value, bool) or not isinstance(
            operand.value, (int, float)
        ):
            raise ValueError("threshold")
        return
    if isinstance(operand, Indicator):
        _validate_indicator(operand)
        return
    raise ValueError("operand")


def _validate_indicator(indicator):
    if not isinstance(indicator.kind, str) or not indicator.kind.strip():
        raise ValueError("indicator kind")
    if not isinstance(indicator.name, str) or indicator.name == "":
        raise ValueError("indicator name")
    if indicator.price not in _PRICE_FIELDS:
        raise ValueError("price")
    if indicator.window is not None and not _whole(indicator.window):
        raise ValueError("window")
    canon = canon_kind(indicator.kind)
    if canon in _WINDOW_REQUIRED and indicator.window is None:
        raise ValueError(f"{indicator.kind} requires a window")
    if canon == "vwap":
        anchor = _param_dict(indicator).get("anchor")
        if anchor != "cumulative" and not _whole(anchor):
            raise ValueError("vwap anchor")
    if canon in ("floor pivot", "pivot"):
        session = indicator.window
        if session is None:
            params = _param_dict(indicator)
            session = params.get(
                "session", params.get("session_length", params.get("length"))
            )
        if not _whole(session):
            raise ValueError("floor pivot session")
    if canon in ("swing", "confirmed swing"):
        params = _param_dict(indicator)
        left = params.get("left", params.get("left_bars", params.get("leftbars")))
        right = params.get("right", params.get("right_bars", params.get("rightbars")))
        if not _whole(left) or not _whole(right):
            raise ValueError("swing bars")


def _param_dict(indicator):
    found = {}
    for item in indicator.params:
        if not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], str):
            raise ValueError("params")
        found[item[0]] = item[1]
    return found


def _check_unique_names(hypothesis):
    thresholds = {}
    windows = {}
    for operand in _operands(hypothesis):
        if isinstance(operand, Threshold):
            if (
                operand.name in thresholds
                and thresholds[operand.name] != operand.value
            ):
                raise ValueError("threshold")
            thresholds[operand.name] = operand.value
        elif isinstance(operand, Indicator):
            if operand.name in windows and windows[operand.name] != operand.window:
                raise ValueError("window")
            windows[operand.name] = operand.window


def _rules(hypothesis):
    for rule in (
        hypothesis.long_entry,
        hypothesis.short_entry,
        hypothesis.long_rule_exit,
        hypothesis.short_rule_exit,
    ):
        if rule is not None:
            yield rule


def _walk(rule):
    if isinstance(rule, (Cross, Above, Below)):
        yield rule
        return
    if isinstance(rule, (All, Any)):
        for part in rule.parts:
            yield from _walk(part)
        return
    raise ValueError("rule")


def _operands(hypothesis):
    for rule in _rules(hypothesis):
        for node in _walk(rule):
            yield node.left
            yield node.right


def _indicators(hypothesis):
    for operand in _operands(hypothesis):
        if isinstance(operand, Indicator):
            yield operand


def _thresholds(hypothesis):
    for operand in _operands(hypothesis):
        if isinstance(operand, Threshold):
            yield operand


def _as_parts(parts):
    if isinstance(parts, (str, bytes)) or not isinstance(parts, Sequence):
        raise ValueError("parts")
    return tuple(parts)


def _as_seq(values):
    if isinstance(values, (str, bytes, Mapping)) or not isinstance(values, Sequence):
        raise ValueError("vary")
    return list(values)


def _check_fee(fee):
    if isinstance(fee, bool) or not isinstance(fee, (int, float)) or not math.isfinite(fee):
        raise ValueError("fee")


def _check_bar_size(bar_size):
    if bar_size is None:
        raise ValueError("bar size")


def _positive(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value > 0


def _whole(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1

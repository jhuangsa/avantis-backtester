"""Tests for clean_states in examples/clickhouse_data.py. No network, no data/."""

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PATH = Path(__file__).resolve().parents[1] / "examples" / "clickhouse_data.py"
_SPEC = importlib.util.spec_from_file_location("clickhouse_data", _PATH)
ch = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ch)


def frame(rows):
    """rows: (minute, market, trend, volatility)."""
    return pd.DataFrame(
        [(m * 60, a, b, c) for m, a, b, c in rows],
        columns=["ts", "market", "trend", "volatility"],
    )


def test_codes_follow_list_order():
    first, market, trend, vol = ch.clean_states(frame([(0, "transition", "uptrend", "shock")]))
    assert first == 0
    assert market[0] == len(ch.MARKET_STATES)
    assert trend[0] == 1
    assert vol[0] == ch.VOLATILITY_STATES.index("shock") + 1


def test_starts_at_first_complete_row_and_unknown_is_missing():
    df = frame([
        (0, "breakout", "unknown", "low"),
        (1, "", "uptrend", "low"),
        (2, "breakout", "uptrend", "low"),
        (3, "unknown", "downtrend", "high"),
    ])
    first, market, trend, vol = ch.clean_states(df)
    assert first == 120
    assert len(market) == 2
    # 'unknown' at minute 3 is filled from minute 2.
    assert market[1] == ch.MARKET_STATES.index("breakout") + 1
    assert trend[1] == ch.TREND_STATES.index("downtrend") + 1


def test_short_gap_is_filled():
    df = frame([(0, "mixed", "uptrend", "normal"), (4, "breakout", "downtrend", "high")])
    first, market, trend, vol = ch.clean_states(df)
    assert len(market) == 5
    assert list(market[:4]) == [ch.MARKET_STATES.index("mixed") + 1] * 4
    assert vol[4] == ch.VOLATILITY_STATES.index("high") + 1


def test_long_gap_becomes_unknown_after_fill_limit():
    df = frame([(0, "mixed", "uptrend", "normal"), (70, "mixed", "uptrend", "normal")])
    first, market, trend, vol = ch.clean_states(df)
    assert len(market) == 71
    assert (market[: ch.FILL_LIMIT + 1] != 0).all()
    assert (market[ch.FILL_LIMIT + 1 : 70] == 0).all()
    assert (trend[ch.FILL_LIMIT + 1 : 70] == 0).all()
    assert market[70] != 0


def test_unlisted_value_raises():
    with pytest.raises(ValueError, match="sideways"):
        ch.clean_states(frame([(0, "mixed", "sideways", "low")]))

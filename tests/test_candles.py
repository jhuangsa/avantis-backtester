"""Tests for clean_candles in examples/candles.py. Made-up candles, no data/."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from candles import clean_candles  # noqa: E402

NAN = np.nan


def frame(rows):
    """rows: (minute, open, high, low, close)."""
    return pd.DataFrame(
        [(m * 60, o, h, l, c) for m, o, h, l, c in rows],
        columns=["ts", "open", "high", "low", "close"],
    )


def test_leading_nan_rows_are_dropped():
    df, _ = clean_candles(frame([(0, NAN, NAN, NAN, NAN), (1, 1, NAN, 1, 1), (2, 1, 2, 1, 2)]))
    assert df["ts"].tolist() == [120]


def test_nan_high_in_middle_gives_flat_candle_at_previous_close():
    df, _ = clean_candles(frame([(0, 1, 3, 1, 2), (1, 5, NAN, 4, 6), (2, 2, 3, 1, 2)]))
    assert df.loc[1, ["open", "high", "low", "close"]].tolist() == [2, 2, 2, 2]
    assert df["filled"].tolist() == [False, True, False]


def test_run_of_nan_candles_is_filled_and_counted(capsys):
    rows = [(0, 1, 1, 1, 1), (1, NAN, NAN, NAN, NAN), (2, NAN, NAN, NAN, NAN),
            (3, 1, 1, 1, 1), (4, NAN, 1, 1, 1), (5, 1, 1, 1, 1)]
    df, report = clean_candles(frame(rows))
    assert report == {"filled": 3, "longest_run": 2}
    assert not df[["open", "high", "low", "close"]].isna().any().any()
    assert "warning" in capsys.readouterr().out


def test_clean_candles_come_back_unchanged(capsys):
    raw = frame([(0, 1, 3, 1, 2), (1, 2, 4, 2, 3)])
    df, report = clean_candles(raw)
    pd.testing.assert_frame_equal(df.drop(columns="filled"), raw)
    assert report == {"filled": 0, "longest_run": 0}
    assert capsys.readouterr().out == ""


def test_filled_minutes_are_not_counted_in_minutes_with_data():
    from btc_bars import resample
    df, _ = clean_candles(frame([(0, 1, 1, 1, 1), (1, NAN, 1, 1, 1), (2, 1, 1, 1, 1)]))
    bars = resample(df, "1h")
    assert bars["minutes_with_data"].tolist() == [2]


def test_dedupe_prefers_a_complete_row_over_a_broken_preferred_source():
    from btc_bars import dedupe
    df = frame([(0, 1, NAN, 1, 1), (0, 2, 3, 1, 2)])
    df["source"] = ["pyth_lazer", "benchmarks"]
    assert dedupe(df)["close"].tolist() == [2]

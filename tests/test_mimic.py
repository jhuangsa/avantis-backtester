"""examples/mimic.py: the address checksum, the cache key, and the curve grid."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))
pytest.importorskip("Crypto")
M = pytest.importorskip("mimic")


def test_checksum_matches_eip55():
    # The example address from EIP-55.
    assert M.checksum("0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed") == "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"


def test_effective_drops_knobs_whose_switch_is_off():
    a = {**M.START, "rsi_on": False, "rsi_period": 2}
    b = {**M.START, "rsi_on": False, "rsi_period": 21}
    assert M.effective(a) == M.effective(b)
    assert M.effective({**a, "rsi_on": True}) != M.effective({**b, "rsi_on": True})


def test_on_grid_carries_equity_forward():
    eq = pd.Series([10_000.0, 11_000.0], index=[0, 7200])
    out = M.on_grid(eq, pd.Index([0, 3600, 7200, 10800]))
    assert np.allclose(out, [0, 0, 0.1, 0.1])


def test_start_and_knobs_agree():
    assert set(M.START) == set(M.KNOBS)
    for k, v in M.START.items():
        assert v in M.KNOBS[k], k

"""Resample rule and the 2 bp protocol labels."""

import importlib.util
import math
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "examples" / "hf_2bps.py"
_SPEC = importlib.util.spec_from_file_location("hf_2bps", _PATH)
hf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(hf)


def test_resample_keeps_a_bucket_only_when_its_close_minute_exists():
    rows = [(i * 60, 1.0, 2.0, 0.5, 1.5) for i in range(14)]
    assert hf.resample(rows, 15) == []
    rows.append((14 * 60, 3.0, 4.0, 0.4, 3.5))
    start, open_, high, low, close = hf.resample(rows, 15)[0]
    assert start == 0
    assert open_ == 1.0
    assert high == 4.0
    assert low == 0.4
    assert close == 3.5


def test_resample_open_is_the_first_present_minute():
    rows = [(60, 8.0, 9.0, 7.0, 8.5), (14 * 60, 3.0, 4.0, 2.0, 3.5)]
    start, open_, high, low, close = hf.resample(rows, 15)[0]
    assert start == 0
    assert open_ == 8.0
    assert high == 9.0
    assert low == 2.0
    assert close == 3.5


def test_round_trip_fee_matches_the_compounded_stake():
    # Entry 10, target 12, fee 10% each fill: stake becomes 243/250.
    net = hf.trade_net_return(10, 12, "long", "take profit", 0.1)
    assert math.isclose(1 + net, 243 / 250)
    marked = hf.trade_net_return(10, 12, "long", "still open", 0.1)
    assert math.isclose(1 + marked, 27 / 25)


def test_protocol_caps_a_full_sample_confirmation():
    strong = [(1.0, 2025)] * 40 + [(1.0, 2026)] * 40
    assert hf.sample_label(strong) == "Confirmed"
    name, label = hf.protocol(strong, oos_rows=[(1.0, 2026)] * 10)
    assert name == "full"
    assert label == "Weak"


def test_weak_band_sits_between_the_published_t_values():
    # t = 1.18 is Weak in the Donchian full sample; t = 0.94 is Invalidated.
    weak = []
    # 80 returns with mean about 1 and sample std about sqrt(80)/1.18 so t ≈ 1.18
    # Build explicitly: 40 wins of +a and 40 losses of -b is unnecessary.
    # Use a constant shift of a normal-ish two-point mix.
    up, down = 10.0, -8.0
    mixed = [(up, 2026)] * 50 + [(down, 2026)] * 50
    label = hf.sample_label(mixed)
    assert label in ("Weak", "Confirmed", "Invalidated")
    mu = sum(r for r, _ in mixed) / len(mixed)
    assert mu > 0
    assert hf.t_stat([r for r, _ in mixed]) < 2
    assert label == "Weak"
    quiet = [(0.1, 2026)] * 50 + [(-0.09, 2026)] * 50
    assert hf.t_stat([r for r, _ in quiet]) < 1
    assert hf.sample_label(quiet) == "Invalidated"

"""The min position on avbt_cpp.Costs (ADR 0022).

buy_hold risks 1% of 10,000 = 100 with its stop 50 below a 100 open: 2 coins,
200 of notional. A min position above 200 refuses the open.
"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_spread import avbt, markets  # noqa: E402


def run(min_position):
    costs = {"BTC": avbt.Costs(spread=0.0, min_position=min_position), "PAD": avbt.Costs(spread=0.0)}
    return avbt.run("buy_hold", {"instrument": "BTC", "stop_loss": 0.5}, markets(), costs)


def test_default_is_none():
    assert avbt.Costs().min_position == 0.0


def test_open_at_or_above_min():
    assert len(run(0.0).trades) == 1
    assert len(run(199.0).trades) == 1


def test_open_below_min_is_refused():
    assert len(run(201.0).trades) == 0


@pytest.mark.parametrize("bad", [-1.0, math.nan, math.inf])
def test_bad_min_position(bad):
    with pytest.raises(ValueError):
        avbt.Costs(min_position=bad)

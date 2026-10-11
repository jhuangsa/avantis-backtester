"""Max drawdown is the largest fractional fall, not the largest dollar fall."""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "examples" / "veranta_top5.py"
_SPEC = importlib.util.spec_from_file_location("veranta_top5", _PATH)
veranta = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(veranta)

WALLET = {"id": "test", "address": "0x0", "blurb": "", "color": "#000"}


def _row(day, net):
    opened = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)
    return {
        "symbol": "BTC",
        "side": "long",
        "index": day,
        "open_price": 1.0,
        "opened": opened,
        "close": opened + timedelta(hours=1),
        "collateral": float(veranta.ACCOUNT),
        "net": float(net),
    }


def test_max_dd_is_the_largest_fractional_fall():
    # 10% fall on 10,000, then a 1,500 fall (5%) from a 30,000 high.
    rows = [_row(0, -1000), _row(2, 21000), _row(4, -1500)]
    book = veranta._book(WALLET, rows)
    assert book["max_dd"] == -0.1
    assert book["dd_usd"] == -1500.0

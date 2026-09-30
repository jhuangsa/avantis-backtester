"""The committed Avantis closes rebuild the five-wallet page."""

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "examples" / "veranta_top5.py"
_SPEC = importlib.util.spec_from_file_location("veranta_top5", _PATH)
veranta = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(veranta)


def test_closed_trades_rebuild_the_published_books():
    mismatch = veranta.drifts()
    assert mismatch == [], "\n".join(mismatch)

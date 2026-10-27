"""examples/mimic_frontier.py: daily risk, the kept bins, the frontier and the picks."""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))
pytest.importorskip("Crypto")
F = pytest.importorskip("mimic_frontier")

DAY = 86400


def s(risk, final):
    return {"risk": risk, "final": final, "rmse": 0.01, "params": {"leverage": 1.0}}


def book(days, cap=None):
    """`days` + 1 midnights with pnl rising 1, 3, 6, ...; a noise row at each noon."""
    t = np.arange(days + 1) * DAY + 10 * DAY
    pnl = np.cumsum(np.arange(days + 1, dtype=float))
    cap = np.full(days + 1, 100.0) if cap is None else np.asarray(cap, float)
    noon = pd.DataFrame({"time": t + DAY // 2, "pnl": 1e6, "capital": 1.0})
    mid = pd.DataFrame({"time": t, "pnl": pnl, "capital": cap})
    return pd.concat([mid, noon]).sort_values("time", ignore_index=True)


def test_daily_risk_matches_hand_computation_and_ignores_noon_rows():
    b = book(40)
    r = np.arange(1, 41) / 100.0  # pnl steps 1..40 over capital 100
    assert F.daily_risk(b) == pytest.approx(r.std(ddof=1) * math.sqrt(365))


def test_daily_risk_skips_zero_capital_days():
    cap = np.full(41, 100.0)
    cap[[5, 9]] = 0.0
    r = np.delete(np.arange(1, 41) / 100.0, [4, 8])  # return i uses capital i + 1
    assert F.daily_risk(book(40, cap)) == pytest.approx(r.std(ddof=1) * math.sqrt(365))


def test_daily_risk_is_nan_under_30_days_or_none():
    assert math.isfinite(F.daily_risk(book(30)))
    assert math.isnan(F.daily_risk(book(29)))
    cap = np.full(31, 100.0)
    cap[3] = 0.0  # 30 days, one skipped: 29 returns
    assert math.isnan(F.daily_risk(book(30, cap)))
    assert math.isnan(F.daily_risk(None))
    assert math.isnan(F.daily_risk(book(30).iloc[:0]))


def test_keep_takes_best_return_per_risk_bin():
    pool = [s(r, f) for r, f in [(0.4, 1), (0.1, 2), (0.3, 5), (0.2, 1), (0.6, 0), (0.5, 3)]]
    assert [(x["risk"], x["final"]) for x in F.keep(pool, 3)] == [(0.1, 2), (0.3, 5), (0.5, 3)]
    assert [x["risk"] for x in F.keep(pool, 10)] == [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]


def test_frontier_drops_dominated_and_keeps_higher_return_on_risk_ties():
    kept = [s(0.3, 0.5), s(0.1, 0.2), s(0.2, 0.1), s(0.1, 0.3), s(0.4, 0.4), s(0.5, 0.9)]
    assert [(x["risk"], x["final"]) for x in F.frontier(kept)] == [(0.1, 0.3), (0.3, 0.5), (0.5, 0.9)]


def test_picks_are_best_frontier_point_under_each_percentile():
    kept = [s(r / 10, f) for r, f in zip(range(1, 11), [1, 3, 2, 4, 6, 5, 7, 6, 9, 8])]
    front = F.frontier(kept)
    out = F.picks(kept, front)
    assert set(out) == {"careful", "balanced", "bold"}
    for name, p in out.items():
        level = np.percentile([x["risk"] for x in kept], p["percentile"])
        assert p["risk_level"] == pytest.approx(level)
        under = [x for x in front if x["risk"] <= level]
        assert p["risk"] <= level
        assert p["final"] == max(x["final"] for x in under)
    assert [out[n]["final"] for n in ("careful", "balanced", "bold")] == [1, 6, 9]


def test_pnl_gap_is_scale_free_and_floors_at_flat():
    w = np.array([0.0, 10, 20, 15, 30])
    assert F.pnl_gap(w / 7, w) == pytest.approx((0.0, 7.0))
    gap, k = F.pnl_gap(-w, w)  # the wrong way: k is 0, no closer than flat
    assert (gap, k) == (pytest.approx(1.0), 0.0)
    assert F.pnl_gap(np.zeros(5), w) == (1.0, 0.0)
    assert 0 < F.pnl_gap(w + np.array([0, 5, -5, 5, -5]), w)[0] < 1


def test_min_budget():
    """Parts of one position add up; the budget is the account times the largest min over notional."""
    from types import SimpleNamespace as N
    M = F.M
    costs = {"A": N(min_position=100.0), "B": N(min_position=300.0)}
    trades = [N(instrument="A", entry_time=1, size=1.0, entry_price=50.0),
              N(instrument="A", entry_time=1, size=1.0, entry_price=50.0),  # 100 in all
              N(instrument="A", entry_time=2, size=4.0, entry_price=50.0),  # 200
              N(instrument="B", entry_time=1, size=2.0, entry_price=750.0)]  # 1,500
    assert M.min_budget(N(trades=trades), costs) == pytest.approx(M.vt.ACCOUNT * 1.0)
    assert M.min_budget(N(trades=trades[2:]), costs) == pytest.approx(M.vt.ACCOUNT * 0.5)
    assert M.min_budget(N(trades=[]), costs) == 0.0


def test_score_trades_gap_for_long_trail_return():
    a, b = {"gap": 0.10, "final": 0.5}, {"gap": 0.12, "final": 1.5}
    assert F.score(a, 0) < F.score(b, 0)  # weight 0: the gap alone
    assert F.score(b, 0.05) < F.score(a, 0.05)  # 0.05 per 100% of return outweighs 0.02 of gap
    assert F.score(a, 0.01) == pytest.approx(0.095)


def test_long_end_is_the_first_of_the_month():
    assert F.long_end("2026-10-23") == "2026-10-01"
    assert F.long_end("2026-10-01") == "2026-10-01"
    assert F.long_end(pd.Timestamp("2027-01-15", tz="UTC")) == "2027-01-01"

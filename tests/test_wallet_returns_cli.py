"""examples/wallet_returns.py: checks() and the page, on hand-made tables.

Nothing reads data/ or the network: tables come from ledger() on
hand-made rows, or are edited by hand to break one identity.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))
import wallet_returns as W  # noqa: E402

H = 3600
T0 = 1_700_000_000 // H * H


def iso(t):
    return pd.Timestamp(t, unit="s", tz="UTC").isoformat()


def av_rows():
    """$100 long ETH at 2x from 100, closed at 110 two hours later: +$20.

    Fee $1, funding $0.5, so usdcSent = 100 + 20 - 1 - 0.5 = 118.5.
    """
    return [{"symbol": "ETH", "buy": True, "leverage": 2,
             "openPrice": 100.0, "openedAt": T0, "time": iso(T0 + 2 * H),
             "positionSize": 100.0, "gross": 20.0, "closingFee": 1.0,
             "funding": 0.5, "usdcSent": 118.5}]


def av_table():
    prices = {"ETH": pd.Series([105.0, 110.0], index=[T0 + H, T0 + 2 * H])}
    return W.ledger("avantis", av_rows(), prices)


def hl_fills():
    """Buy 1 BTC at 100, sell it at 110 two hours later; $0.1 fee each."""
    f = dict(coin="BTC", sz="1", fee="0.1")
    return [{**f, "px": "100", "side": "B", "time": T0 * 1000,
             "startPosition": "0", "closedPnl": "0"},
            {**f, "px": "110", "side": "A", "time": (T0 + 2 * H) * 1000,
             "startPosition": "1", "closedPnl": "10"}]


def hl_table():
    prices = {"BTC": pd.Series([105.0, 110.0], index=[T0 + H, T0 + 2 * H])}
    return W.ledger("hyperliquid", hl_fills(), prices,
                    leverage={"BTC": 5.0}, start=T0)


def by_name(res):
    return {n: (ok, d) for n, ok, d in res}


def test_avantis_identities_pass():
    res = by_name(W.checks(av_table(), "avantis", av_rows()))
    for name in ("8 P = R + U", "9 U = 0 with no margin open",
                 "10 capital never falls", "11 sign of return = sign of P",
                 "12 return >= -100%", "15 R = sum(usdcSent - positionSize)"):
        assert res[name][0] is True, (name, res[name])
    assert res["17 leaderboard"] == (None, "not in leaderboard")


def test_avantis_leaderboard_coverage_flag():
    # Leaderboard margin 1000: the history holds 10% of it.
    board = {"all_time_realized_pnl_usdc": 50.0, "collateral_used_usdc": 1000.0,
             "all_time_return_pct": 5.0}
    res = by_name(W.checks(av_table(), "avantis", av_rows(), board=board))
    assert res["17 coverage"][0] is False
    assert res["17 leaderboard return"][0] is None  # coverage too low to judge
    assert res["17 leaderboard PnL"][0] is None
    # Full coverage: the history's (118.5 - 100) / 100 = 18.5%.
    full = {**board, "collateral_used_usdc": 100.0,
            "all_time_realized_pnl_usdc": 18.5, "all_time_return_pct": 18.5}
    res = by_name(W.checks(av_table(), "avantis", av_rows(), board=full))
    assert res["17 coverage"][0] is True
    assert res["17 leaderboard PnL"][0] is True
    assert res["17 leaderboard return"][0] is True
    # The tolerance is the PnL one, max($1000, 5%), over the margin $100:
    # 1000 points. 1100% is 1081.5 points off; 1000% is 981.5.
    near = {**full, "all_time_return_pct": 1000.0}
    res = by_name(W.checks(av_table(), "avantis", av_rows(), board=near))
    assert res["17 leaderboard return"][0] is True
    off = {**full, "all_time_return_pct": 1100.0}
    res = by_name(W.checks(av_table(), "avantis", av_rows(), board=off))
    assert res["17 leaderboard return"][0] is False


def test_broken_tables_fail():
    df = av_table()
    bad = df.copy()
    bad.loc[1, "pnl"] += 5
    assert by_name(W.checks(bad, "avantis"))["8 P = R + U"][0] is False
    bad = df.copy()
    bad.loc[len(bad) - 1, "capital"] -= 10
    assert by_name(W.checks(bad, "avantis"))["10 capital never falls"][0] \
        is False
    bad = df.copy()
    bad.attrs = {**df.attrs, "breach": True}
    bad.loc[1, "return"] = -1.5
    assert by_name(W.checks(bad, "avantis"))["12 return >= -100%"][0] \
        is False


def test_rise_without_open_fails():
    # Capital rising at an hour with no open breaks check 10 on Avantis.
    bad = av_table()
    bad.loc[len(bad) - 1, "capital"] += 10
    res = by_name(W.checks(bad, "avantis", av_rows()))
    assert res["10 capital never falls"][0] is False


def test_hyperliquid_checks():
    df = hl_table()
    hist = pd.Series([0.0, 9.8], index=[T0, T0 + 2 * H])
    res = by_name(W.checks(df, "hyperliquid", hl_fills(), history=hist,
                           fallback=["BTC"]))
    assert res["16 R = closedPnl - fee + funding"][0] is True
    assert res["18 change in P vs pnlHistory"][0] is True
    assert res["19 fill limit"][0] is True
    assert res["20 gap_share"][0] is True
    assert res["leverage fallback"] == (None, "BTC")
    res = by_name(W.checks(df, "hyperliquid", hl_fills(), capped=True))
    assert res["19 fill limit"][0] is False
    assert res["18 portfolio pnlHistory"][0] is None
    gappy = df.copy()
    gappy.attrs = {**df.attrs, "gap_share": 0.05}
    assert by_name(W.checks(gappy, "hyperliquid"))["20 gap_share"][0] \
        is False


def test_empty_table():
    df = W.ledger("avantis", [], {})
    assert W.checks(df, "avantis") == [("table", None,
                                        "no trades in the window")]


def test_ok_values_are_plain():
    for ok in (r[1] for r in W.checks(hl_table(), "hyperliquid", hl_fills())):
        assert ok is None or type(ok) is bool


def test_page():
    df = av_table()
    df.loc[0, "return"] = np.nan
    view = W.wallet_view("w</script>", "avantis", df,
                         W.checks(df, "avantis", av_rows()))
    assert view["ret"][0] is None
    html = W.page([view])
    assert "/*DATA*/null" not in html and "</script>\"" not in html
    data = html.split("const D=", 1)[1].split(";\nconst esc", 1)[0]
    got = json.loads(data.replace("<\\/", "</"))
    assert got[0]["name"] == "w</script>"
    assert got[0]["t"] == df["time"].tolist()


def test_page_thins_long_tables():
    n = 5000
    df = pd.DataFrame({c: np.zeros(n) for c in W.COLUMNS})
    df["time"] = T0 + H * np.arange(n)
    df["margin_estimated"] = False
    view = W.wallet_view("w", "avantis", df, [], points=100)
    assert len(view["t"]) <= 101 and view["t"][-1] == df["time"].iloc[-1]


def test_unrealized_above_minus_margin():
    df = hl_table()
    assert by_name(W.checks(df, "hyperliquid"))["U >= -margin open"][0] \
        is True
    bad = df.copy()
    i = int(np.flatnonzero(bad["margin_open"] > 0)[0])
    m = bad.loc[i, "margin_open"]
    bad.loc[i, ["unrealized", "unrealized_price"]] = -2 * m
    bad.loc[i, "pnl"] = bad.loc[i, "realized"] - 2 * m
    ok, detail = by_name(W.checks(bad, "hyperliquid"))["U >= -margin open"]
    assert ok is False
    assert "1 hours" in detail and "-2.00" in detail
    assert "too low" in detail


class FakeMimic:
    def __init__(self, cache):
        self.CACHE = cache

    def checksum(self, a):
        return a

    def use_venue(self, venue):
        pass


def test_cli_rejects_bad_addresses(capsys):
    import pytest
    with pytest.raises(SystemExit) as e:
        W.main(["0x123"], M=FakeMimic(None))
    assert e.value.code == 2


def test_cli_skips_a_wallet_that_fails(tmp_path, monkeypatch):
    import pytest
    good, bad, empty = "0x" + "a" * 40, "0x" + "b" * 40, "0x" + "c" * 40

    def load(M, address):
        if address == bad:
            raise SystemExit("no closed trades")
        if address == empty:
            return W.ledger("avantis", [], {}), []
        return av_table(), W.checks(av_table(), "avantis", av_rows())

    monkeypatch.setattr(W, "run_avantis", load)
    monkeypatch.setattr(W, "HERE", tmp_path)
    with pytest.raises(SystemExit) as e:
        W.main([bad, good, empty], M=FakeMimic(tmp_path))
    assert e.value.code == 1
    html = (tmp_path / "wallet_returns_avantis.html").read_text()
    name = lambda a: json.dumps(W.short(a))  # as the page holds it
    assert name(good) in html and name(bad) not in html
    assert (tmp_path / f"wallet_returns_avantis_{good}.csv").exists()
    assert not (tmp_path / f"wallet_returns_avantis_{bad}.csv").exists()

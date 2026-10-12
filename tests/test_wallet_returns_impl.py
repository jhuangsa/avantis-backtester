from __future__ import annotations

import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, "examples")
import wallet_returns as W  # noqa: E402

H = 3600
T0 = 1_700_000_000 // H * H


def iso(t):
    return pd.Timestamp(t, unit="s", tz="UTC").isoformat()


def close(sym, a, c, m, g, p0=100.0, lev=1, buy=True, fee=0.0, fund=0.0):
    return dict(symbol=sym, buy=buy, leverage=lev, openPrice=p0, openedAt=a,
                time=iso(c), positionSize=m, gross=g, closingFee=fee,
                funding=fund)


def px(*pairs):
    return pd.Series([p for _, p in pairs], index=[t for t, _ in pairs])


def at(df, t):
    return df.set_index("time").loc[t]


def test_columns_and_venue():
    df = W.ledger("avantis", [], {})
    assert list(df.columns) == W.COLUMNS and len(df) == 0
    with pytest.raises(ValueError):
        W.ledger("dydx", [], {})


def test_check1_deposit_free_return():
    r = [close("ETH", T0, T0 + 10 * H, 100, 100)]
    p = {"ETH": px((T0, 100), (T0 + 2 * H, 120), (T0 + 5 * H, 200))}
    df = W.ledger("avantis", r, p)
    assert at(df, T0 + 3 * H)["return"] == pytest.approx(0.2)
    assert at(df, T0 + 6 * H)["return"] == pytest.approx(1.0)
    assert df["time"].iloc[0] == T0 and df["time"].iloc[-1] == T0 + 10 * H


def test_check2_size_weighted():
    r = [close("BTC", T0, T0 + H, 1, -1),
         close("ETH", T0 + 2 * H, T0 + 9 * H, 999, 99.9)]
    p = {"ETH": px((T0, 100), (T0 + 4 * H, 110))}
    row = at(W.ledger("avantis", r, p), T0 + 5 * H)
    assert row["capital"] == pytest.approx(1000)
    assert row["return"] == pytest.approx(0.0989)


def test_check3_reinvested_profit():
    r = [close("ETH", T0, T0 + 2 * H, 100, 100),
         close("BTC", T0 + 3 * H, T0 + 9 * H, 200, 20)]
    p = {"BTC": px((T0, 100), (T0 + 4 * H, 110))}
    row = at(W.ledger("avantis", r, p), T0 + 5 * H)
    assert row["capital"] == pytest.approx(100)
    assert row["pnl"] == pytest.approx(120)
    assert row["return"] == pytest.approx(1.2)


def test_check5_fee_lowers_pnl_not_capital():
    p = {"ETH": px((T0, 100))}
    a = W.ledger("avantis", [close("ETH", T0, T0 + 2 * H, 100, 10)], p)
    b = W.ledger("avantis", [close("ETH", T0, T0 + 2 * H, 100, 10, fee=1)], p)
    assert (a["capital"] == b["capital"]).all()
    assert b["pnl"].iloc[-1] == pytest.approx(a["pnl"].iloc[-1] - 1)
    assert b["fees"].iloc[-1] == 1


def test_check6_funding_spread():
    r = [close("ETH", T0, T0 + 4 * H, 100, 0, fund=8)]
    df = W.ledger("avantis", r, {"ETH": px((T0, 100))})
    assert at(df, T0 + 2 * H)["unrealized_funding"] == pytest.approx(-4)
    before = W.ledger("avantis", r, {"ETH": px((T0, 100))},
                      times=[T0 + 4 * H - 1e-6])
    end = at(df, T0 + 4 * H)
    assert end["unrealized_funding"] == 0 and end["realized"] == -8
    assert before["pnl"][0] == pytest.approx(end["pnl"])


def test_check7_leverage():
    r = [close("ETH", T0, T0 + 4 * H, 100, 20, lev=10)]
    p = {"ETH": px((T0, 100), (T0 + H, 102))}
    assert at(W.ledger("avantis", r, p), T0 + 2 * H)["return"] == pytest.approx(0.2)


def test_short_and_no_prices():
    r = [close("ETH", T0, T0 + 4 * H, 100, 10, buy=False),
         close("XYZ", T0, T0 + 4 * H, 50, 5)]
    df = W.ledger("avantis", r, {"ETH": px((T0, 100), (T0 + H, 90))})
    assert at(df, T0 + 2 * H)["unrealized_price"] == pytest.approx(10)
    assert df.attrs["no_prices"] == ["XYZ"]


def test_skipped_rows():
    r = [close("ETH", T0, T0 + H, 100, 1), close("ETH", T0, T0 + H, None, 1),
         close("ETH", T0, T0 + H, 1, 1, lev=0)]
    df = W.ledger("avantis", r, {})
    assert df.attrs["skipped"] == 2 and df.attrs["breach"] is False


def wallet(scale=1.0):
    rng = np.random.default_rng(1)
    rows = []
    for k in range(30):
        a = T0 + int(rng.integers(0, 200 * H))
        c = a + int(rng.integers(0, 50 * H))
        m = float(rng.uniform(1, 500)) * scale
        sym = ["ETH", "BTC"][k % 2]
        rows.append(close(sym, a, c, m, m * float(rng.uniform(-1, 1)),
                          lev=int(rng.integers(1, 6)), buy=bool(k % 3),
                          fee=m * 0.001, fund=m * 0.002))
    t = np.arange(T0 - H, T0 + 300 * H, 600)
    p = {s: pd.Series(100 * np.exp(np.cumsum(rng.normal(0, .002, len(t)))),
                      index=t) for s in ("ETH", "BTC")}
    return rows, p


def identities(df):
    assert np.allclose(df["pnl"], df["realized"] + df["unrealized"])
    flat = df["margin_open"] == 0
    assert (df.loc[flat, "unrealized"].abs() < 1e-9).all()
    assert (df["capital"].diff().dropna() >= -1e-9).all()
    k = df["capital"] > 0
    assert (np.sign(df.loc[k, "return"]) == np.sign(df.loc[k, "pnl"])).all()


def test_identities_scale_sampling_check15():
    rows, p = wallet()
    df = W.ledger("avantis", rows, p)
    identities(df)
    big = W.ledger("avantis", wallet(3.0)[0], p)
    assert np.allclose(df["return"], big["return"], equal_nan=True)
    ev = sorted({r["openedAt"] for r in rows}) + [int(df["time"].iloc[40])]
    sub = W.ledger("avantis", rows, p, times=ev)
    pd.testing.assert_frame_equal(
        sub.set_index("time").loc[[ev[-1]]],
        df.set_index("time").loc[[ev[-1]]])
    want = sum(r["gross"] - r["closingFee"] - r["funding"] for r in rows)
    assert df["realized"].iloc[-1] == pytest.approx(want)
    assert not df["margin_estimated"].any()


def test_times_before_first_open():
    r = [close("ETH", T0, T0 + H, 100, 1)]
    df = W.ledger("avantis", r, {}, times=[T0 - H, T0])
    assert np.isnan(df["return"][0]) and np.isnan(df["capital"][0])
    assert df["capital"][1] == 100


# Hyperliquid

def fill(coin, t, p, sz, side, fee=0.0, start=0.0, closed=0.0):
    return dict(coin=coin, px=str(p), sz=str(sz), side=side, time=t * 1000,
                fee=str(fee), startPosition=str(start), closedPnl=str(closed))


def test_hl_replay_cross_zero():
    f = [fill("ETH", T0 + 10, 100, 1, "B", fee=0.1),
         fill("ETH", T0 + 20, 110, 1, "B", 0.1, 1),
         fill("ETH", T0 + 2 * H, 120, 3, "A", 0.3, 2, 30)]
    fu = [dict(time=(T0 + H) * 1000, coin="ETH", usdc="-2")]
    p = {"ETH": px((T0, 100), (T0 + H, 115), (T0 + 2 * H, 125))}
    df = W.ledger("hyperliquid", f, p, funding=fu, leverage={"ETH": 5})
    one = at(df, T0 + H)
    assert one["unrealized"] == pytest.approx(2 * (115 - 105))
    assert one["margin_open"] == pytest.approx(2 * 105 / 5)
    assert one["realized"] == pytest.approx(-0.2 - 2)
    assert one["funding"] == pytest.approx(2)
    two = at(df, T0 + 2 * H)
    assert two["realized"] == pytest.approx(30 - 0.5 - 2)
    assert two["unrealized"] == pytest.approx(-1 * (125 - 120))
    assert two["margin_open"] == pytest.approx(120 / 5)
    assert df["margin_estimated"].all() and df.attrs["carried"] == []
    identities(df)
    closed = sum(float(x["closedPnl"]) - float(x["fee"]) for x in f)
    assert df["realized"].iloc[-1] == pytest.approx(closed - 2)


def test_hl_carried_and_leverage():
    f = [fill("BTC", T0 + 2 * H, 110, 1, "A", start=1)]
    p = {"BTC": px((T0 + H, 100))}
    df = W.ledger("hyperliquid", f, p, leverage={"BTC": 2}, start=T0)
    assert df.attrs["carried"] == ["BTC"]
    assert at(df, T0)["capital"] == pytest.approx(50)
    assert at(df, T0 + 2 * H)["realized"] == pytest.approx(10)
    with pytest.raises(ValueError):
        W.ledger("hyperliquid", f, p, leverage={})


def test_hl_breach_flag():
    f = [fill("ETH", T0, 100, 1, "B")]
    p = {"ETH": px((T0 + 1, 10))}
    df = W.ledger("hyperliquid", f, p, leverage={"ETH": 2},
                  times=[T0 + H])
    assert df.attrs["breach"] is True


# Round 1 decisions

def test_r1_funding_before_start_ignored():
    f = [fill("ETH", T0 + 10, 100, 1, "B")]
    fu = [dict(time=(T0 - H) * 1000, coin="ETH", usdc="5"),
          dict(time=(T0 + 20) * 1000, coin="ETH", usdc="1")]
    df = W.ledger("hyperliquid", f, {}, funding=fu, leverage={"ETH": 1},
                  start=T0)
    assert df["realized"].iloc[-1] == pytest.approx(1)


def test_r2_gap_fill():
    f = [fill("ETH", T0 + 10, 100, 2, "B"),
         fill("ETH", T0 + 20, 110, 1, "A", start=1)]
    df = W.ledger("hyperliquid", f, {}, leverage={"ETH": 1})
    # gap sells 1 at 110 (+10), then the fill sells 1 at 110 (+10)
    assert df["realized"].iloc[-1] == pytest.approx(20)
    assert df["margin_open"].iloc[-1] == 0
    assert df.attrs["gap_share"] == pytest.approx(110 / (200 + 220))
    ok = W.ledger("hyperliquid", f[:1], {}, leverage={"ETH": 1})
    assert ok.attrs["gap_share"] == 0.0


def test_r3_close_before_open_skipped():
    r = [close("ETH", T0, T0 + H, 100, 1), close("ETH", T0, T0 - 1, 100, 1)]
    df = W.ledger("avantis", r, {})
    assert df.attrs["skipped"] == 1
    assert df.attrs["breach"] is False and type(df.attrs["breach"]) is bool

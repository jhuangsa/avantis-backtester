"""examples/wallet_returns.py: ledger() against issue #15, checks 1-16.

Every row and fill is hand-made; nothing reads data/. Expected numbers are
worked out by hand in the comments.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))
import wallet_returns as W  # noqa: E402

H = 3600
T0 = 1_700_000_000 // H * H  # an hour end
COLS = ["time", "realized", "unrealized_price", "unrealized_funding",
        "unrealized", "fees", "funding", "pnl", "margin_open", "own",
        "capital", "return", "margin_estimated"]
NUM = [c for c in COLS if c not in ("time", "margin_estimated")]


# ---------- helpers ----------

def iso(t):
    return pd.Timestamp(t, unit="s", tz="UTC").isoformat()


def av(symbol, buy, lev, p0, a, c, size, gross, fee=0.0, fund=0.0):
    """One Avantis close row: open at T0+a, close at T0+c (seconds)."""
    return {"symbol": symbol, "buy": buy, "leverage": lev,
            "openPrice": p0, "openedAt": T0 + a, "time": iso(T0 + c),
            "positionSize": size, "gross": gross, "closingFee": fee,
            "funding": fund}


def series(points):
    """{offset seconds: price} -> Series indexed by unix seconds."""
    ks = sorted(points)
    return pd.Series([float(points[k]) for k in ks],
                     index=[T0 + k for k in ks])


def fill(coin, t, side, sz, px, start, fee=0.0, closed=0.0):
    """One Hyperliquid fill at T0+t seconds (time in ms)."""
    return {"coin": coin, "px": str(px), "sz": str(sz), "side": side,
            "time": (T0 + t) * 1000, "fee": str(fee),
            "startPosition": str(start), "closedPnl": str(closed)}


def fund(coin, t, usdc):
    return {"time": (T0 + t) * 1000, "coin": coin, "usdc": str(usdc)}


def row(df, t):
    """The row at T0+t."""
    hit = df[df["time"] == T0 + t]
    assert len(hit) == 1, f"no single row at offset {t}"
    return hit.iloc[0]


def approx(x):
    return pytest.approx(x, rel=1e-9, abs=1e-9)


# ---------- Avantis worked examples ----------

def eth_example1(close_c=10 * H):
    # $100 margin, 1x ETH long at 100, opened at 0.
    return [av("ETH", True, 1, 100, 0, close_c, 100, 100)]


ETH1 = {"ETH": series({0: 100, H: 120, 2 * H: 200})}


def test_check1_return_follows_price():
    df = W.ledger("avantis", eth_example1(), ETH1)
    # 1h: U = 1*100*1*(120/100-1) = 20; K = 100 -> 0.20
    assert row(df, H)["unrealized_price"] == approx(20)
    assert row(df, H)["capital"] == approx(100)
    assert row(df, H)["return"] == approx(0.20)
    # 2h: U = 100*(200/100-1) = 100 -> 1.00
    assert row(df, 2 * H)["return"] == approx(1.0)
    # close at 10h, gross +100: R = 100, M = 0, K stays 100 -> 1.00
    last = row(df, 10 * H)
    assert last["realized"] == approx(100)
    assert last["margin_open"] == approx(0)
    assert last["return"] == approx(1.0)


def test_check4_idle_market_changes_nothing():
    # Deposits are not an input; an unrelated price series must not matter.
    a = W.ledger("avantis", eth_example1(), ETH1)
    more = {**ETH1, "DOGE": series({0: 1, H: 5, 2 * H: 0.1})}
    b = W.ledger("avantis", eth_example1(), more)
    pd.testing.assert_frame_equal(a, b)


def test_check2_small_loss_then_big_win():
    rows = [
        # $1 BTC 1x long, lost entirely: gross -1, closed at 1h
        av("BTC", True, 1, 50, 0, H, 1, -1),
        # $999 ETH 1x long at 100, +10%: gross 99.9, closed at 3h
        av("ETH", True, 1, 100, 2 * H, 3 * H, 999, 99.9),
    ]
    px = {"BTC": series({0: 50, H: 0.0001}),
          "ETH": series({2 * H: 100, 3 * H: 110})}
    df = W.ledger("avantis", rows, px)
    last = row(df, 3 * H)
    # O at the ETH open: M - R = 999 - (-1) = 1000 -> K = 1000
    assert last["capital"] == approx(1000)
    # P = -1 + 99.9 = 98.9 -> 98.9 / 1000 = 0.0989
    assert last["pnl"] == approx(98.9)
    assert last["return"] == approx(0.0989)


def test_check3_reinvested_profit_is_not_new_capital():
    rows = [
        # Example 1, closed at 2h at 200: gross +100
        av("ETH", True, 1, 100, 0, 2 * H, 100, 100),
        # $200 BTC 1x long at 50 opened at 2h, +10% at 4h: gross +20
        av("BTC", True, 1, 50, 2 * H, 4 * H, 200, 20),
    ]
    px = {"ETH": series({0: 100, H: 120, 2 * H: 200}),
          "BTC": series({2 * H: 50, 3 * H: 55, 4 * H: 55})}
    df = W.ledger("avantis", rows, px)
    # at 2h: M = 200, R = 100, O = 100; K = max(100, 100) = 100
    assert row(df, 2 * H)["capital"] == approx(100)
    # 3h: R = 100, U = 200*(55/50-1) = 20, P = 120 -> 1.2
    assert row(df, 3 * H)["return"] == approx(1.2)
    last = row(df, 4 * H)
    assert last["capital"] == approx(100)
    assert last["pnl"] == approx(120)
    assert last["return"] == approx(1.2)


def test_check5_closing_fee_lowers_pnl_not_capital():
    px = {"ETH": series({0: 100, H: 110})}
    base = [av("ETH", True, 1, 100, 0, 2 * H, 100, 10)]
    paid = [av("ETH", True, 1, 100, 0, 2 * H, 100, 10, fee=2)]
    a = W.ledger("avantis", base, px)
    b = W.ledger("avantis", paid, px)
    # before the close nothing differs
    pd.testing.assert_series_equal(a.iloc[:-1]["pnl"], b.iloc[:-1]["pnl"])
    ra, rb = row(a, 2 * H), row(b, 2 * H)
    # R: 10 vs 10 - 2 = 8
    assert ra["realized"] == approx(10)
    assert rb["realized"] == approx(8)
    assert rb["fees"] == approx(2)
    assert rb["pnl"] == approx(ra["pnl"] - 2)
    assert rb["return"] < ra["return"]
    assert rb["capital"] == approx(ra["capital"]) == approx(100)


def test_check6_funding_spread_then_settled():
    # $100 1x long, flat price, funding cost 4 paid at the close at 2h.
    px = {"ETH": series({0: 100})}
    rows = [av("ETH", True, 1, 100, 0, 2 * H, 100, 0, fund=4)]
    ts = [T0, T0 + H, T0 + 2 * H - 1, T0 + 2 * H]
    df = W.ledger("avantis", rows, px, times=ts)
    half = row(df, H)
    # halfway: U_fund = -4 * 3600/7200 = -2; nothing realized
    assert half["unrealized_funding"] == approx(-2)
    assert half["realized"] == approx(0)
    assert half["funding"] == approx(0)
    assert half["pnl"] == approx(-2)
    before, at = row(df, 2 * H - 1), row(df, 2 * H)
    # 1s before: U_fund = -4 * 7199/7200
    assert before["unrealized_funding"] == approx(-4 * 7199 / 7200)
    # at close: U_fund back to 0, R = -4, funding column 4
    assert at["unrealized_funding"] == approx(0)
    assert at["realized"] == approx(-4)
    assert at["funding"] == approx(4)
    assert at["pnl"] == approx(-4)
    # no jump: P moves only by the last second's share, 4/7200
    assert at["pnl"] - before["pnl"] == approx(-4 / 7200)


def test_check7_leverage_scales_return():
    # $100 at 10x, price 100 -> 102: U = 100*10*0.02 = 20 -> 0.20
    px = {"ETH": series({0: 100, H: 102})}
    rows = [av("ETH", True, 10, 100, 0, 5 * H, 100, 20)]
    df = W.ledger("avantis", rows, px)
    assert row(df, H)["unrealized_price"] == approx(20)
    assert row(df, H)["return"] == approx(0.20)


# ---------- Avantis adversarial ----------

def test_A_intra_hour_round_trip():
    # open $500 at 00:10, close at 00:20 with gross +50
    rows = [av("ETH", True, 1, 100, 600, 1200, 500, 50)]
    df = W.ledger("avantis", rows, {"ETH": series({0: 100})})
    assert list(df["time"]) == [T0 + H]
    r = df.iloc[0]
    # K saw O = 500 at 00:10 even though no grid hour held it
    assert r["capital"] == approx(500)
    assert r["realized"] == approx(50)
    assert r["margin_open"] == approx(0)
    assert r["return"] == approx(0.10)


def test_B_close_on_hour_end_is_in_that_row():
    rows = [av("ETH", True, 1, 100, 0, 2 * H, 100, 7)]
    df = W.ledger("avantis", rows, {"ETH": series({0: 100})})
    assert row(df, H)["realized"] == approx(0)
    assert row(df, H)["margin_open"] == approx(100)
    assert row(df, 2 * H)["realized"] == approx(7)
    assert row(df, 2 * H)["margin_open"] == approx(0)


def test_C_partial_closes_each_spread_own_funding():
    # one position opened at 0: $60 closed at 1h (funding 6),
    # $40 closed at 3h (funding 3); flat price, zero gross.
    rows = [av("ETH", True, 1, 100, 0, H, 60, 0, fund=6),
            av("ETH", True, 1, 100, 0, 3 * H, 40, 0, fund=3)]
    ts = [T0 + 1800, T0 + H, T0 + 2 * H, T0 + 3 * H]
    df = W.ledger("avantis", rows, {"ETH": series({0: 100})}, times=ts)
    # 0.5h: -(6*1800/3600 + 3*1800/10800) = -(3 + 0.5) = -3.5
    assert row(df, 1800)["unrealized_funding"] == approx(-3.5)
    assert row(df, 1800)["margin_open"] == approx(100)
    # 1h: first close settled (R = -6); -3*3600/10800 = -1
    assert row(df, H)["realized"] == approx(-6)
    assert row(df, H)["unrealized_funding"] == approx(-1)
    assert row(df, H)["margin_open"] == approx(40)
    # 2h: -3*7200/10800 = -2
    assert row(df, 2 * H)["unrealized_funding"] == approx(-2)
    # 3h: all settled, R = -9
    assert row(df, 3 * H)["unrealized_funding"] == approx(0)
    assert row(df, 3 * H)["realized"] == approx(-9)
    assert row(df, 3 * H)["pnl"] == approx(-9)


def test_D_short_loses_when_price_rises():
    rows = [av("ETH", False, 1, 100, 0, 2 * H, 100, -10)]
    df = W.ledger("avantis", rows, {"ETH": series({0: 100, H: 110})})
    # U = -1 * 100 * 1 * (110/100 - 1) = -10 -> -0.10
    assert row(df, H)["unrealized_price"] == approx(-10)
    assert row(df, H)["return"] == approx(-0.10)


def test_E_market_without_prices():
    rows = [av("XYZ", True, 3, 10, 0, 2 * H, 50, 5),
            av("ETH", True, 1, 100, 0, 2 * H, 100, 10)]
    df = W.ledger("avantis", rows, {"ETH": series({0: 100, H: 110})})
    # XYZ adds nothing unrealized; ETH adds 100*(110/100-1) = 10
    assert row(df, H)["unrealized_price"] == approx(10)
    assert df.attrs["no_prices"] == ["XYZ"]


def test_F_grid_bounds():
    # first open 00:30, last close exactly 05:00
    rows = [av("ETH", True, 1, 100, 1800, 5 * H, 100, 0)]
    df = W.ledger("avantis", rows, {"ETH": series({0: 100})})
    t = list(df["time"])
    assert t[0] == T0 + H
    assert t[-1] == T0 + 5 * H
    assert all(x % H == 0 for x in t)
    assert np.all(np.diff(t) == H)


def test_G_times_before_first_open():
    rows = [av("ETH", True, 1, 100, H, 2 * H, 100, 0)]
    ts = [T0, T0 + H, T0 + 2 * H]
    df = W.ledger("avantis", rows, {"ETH": series({0: 100})}, times=ts)
    assert np.isnan(row(df, 0)["return"])
    assert row(df, H)["return"] == approx(0)


def test_H_bad_rows_skipped_and_counted():
    good = av("ETH", True, 1, 100, 0, 2 * H, 100, 10)
    zero_lev = av("ETH", True, 0, 100, 0, 2 * H, 100, 10)
    no_size = av("ETH", True, 1, 100, 0, 2 * H, 100, 10)
    del no_size["positionSize"]
    px = {"ETH": series({0: 100, H: 105})}
    a = W.ledger("avantis", [good], px)
    b = W.ledger("avantis", [good, zero_lev, no_size], px)
    pd.testing.assert_frame_equal(a, b)
    assert b.attrs["skipped"] == 2


def test_I_bad_venue_and_empty():
    with pytest.raises(ValueError):
        W.ledger("binance", [], {})
    df = W.ledger("avantis", [], {})
    assert len(df) == 0
    assert list(df.columns) == COLS


def test_J_columns_and_flags():
    df = W.ledger("avantis", eth_example1(), ETH1)
    assert list(df.columns) == COLS
    assert pd.api.types.is_integer_dtype(df["time"])
    assert not df["margin_estimated"].any()
    assert df.attrs["breach"] is False


# ---------- Hyperliquid ----------

def hl_k():
    fills = [
        fill("ETH", 0, "B", 1, 100, 0, fee=0.1),         # q=1, e=100
        fill("ETH", 600, "B", 1, 110, 1, fee=0.1),       # q=2, e=105
        fill("ETH", 1200, "A", 1, 120, 2, fee=0.1,       # +1*(120-105)=15
             closed=15),
        fill("ETH", 1800, "A", 3, 130, 1, fee=0.2,       # +1*(130-105)=25,
             closed=25),                                 # then q=-2, e=130
    ]
    funding = [fund("ETH", 2400, -0.5)]                  # 0.5 paid
    return fills, funding


def test_K_grow_reduce_flip_fees_funding():
    fills, funding = hl_k()
    px = {"ETH": series({H: 125})}
    df = W.ledger("hyperliquid", fills, px, funding=funding,
                  leverage={"ETH": 5}, start=T0)
    first = row(df, 0)
    # at 0: q=1, e=100, no price yet -> U = 0; R = -0.1; M = 100/5 = 20
    assert first["unrealized"] == approx(0)
    assert first["realized"] == approx(-0.1)
    assert first["margin_open"] == approx(20)
    r = row(df, H)
    # R = 15 + 25 - (0.1+0.1+0.1+0.2) + (-0.5) = 39.0
    assert r["realized"] == approx(39.0)
    assert r["fees"] == approx(0.5)
    assert r["funding"] == approx(0.5)
    # U = q*(p-e) = -2*(125-130) = 10
    assert r["unrealized"] == approx(10)
    assert r["unrealized_price"] == approx(10)
    assert r["unrealized_funding"] == approx(0)
    assert r["pnl"] == approx(49.0)
    # M = 2*130/5 = 52
    assert r["margin_open"] == approx(52)
    # O by event: 0: 20+0.1=20.1; 600: 42+0.2=42.2; 1200: 21-14.7=6.3;
    # 1800: 52-39.5=12.5; 2400: 52-39=13 -> K = 42.2
    assert r["own"] == approx(13)
    assert r["capital"] == approx(42.2)
    assert r["return"] == approx(49 / 42.2)


def test_L_margin_estimate_and_missing_leverage():
    fills, funding = hl_k()
    df = W.ledger("hyperliquid", fills, {"ETH": series({H: 125})},
                  funding=funding, leverage={"ETH": 5}, start=T0)
    assert df["margin_estimated"].all()
    assert list(df.columns) == COLS
    with pytest.raises(ValueError):
        W.ledger("hyperliquid", fills, {}, leverage={"BTC": 5}, start=T0)


def test_M_carried_position():
    # long 2 held before the window; sold at 1h at 110
    fills = [fill("ETH", H, "A", 2, 110, 2, closed=20)]
    px = {"ETH": series({0: 100, 1800: 105, H: 110})}
    ts = [T0, T0 + 1800, T0 + H]
    df = W.ledger("hyperliquid", fills, px, leverage={"ETH": 2},
                  start=T0, times=ts)
    assert df.attrs["carried"] == ["ETH"]
    # enters at the price at start, 100; M = 2*100/2 = 100
    assert row(df, 0)["margin_open"] == approx(100)
    # 0.5h: U = 2*(105-100) = 10
    assert row(df, 1800)["unrealized"] == approx(10)
    # 1h: R = 2*(110-100) = 20; K = 100 -> 0.20
    assert row(df, H)["realized"] == approx(20)
    assert row(df, H)["return"] == approx(0.20)


def test_M_no_carry_when_flat_at_start():
    fills, funding = hl_k()
    df = W.ledger("hyperliquid", fills, {}, funding=funding,
                  leverage={"ETH": 5}, start=T0)
    assert df.attrs["carried"] == []


def test_N_breach_flags_low_margin_estimate():
    fills = [fill("ETH", 0, "B", 1, 100, 0)]
    px = {"ETH": series({0: 100, H: 90})}
    ts = [T0, T0 + H]  # the grid would stop at the only fill
    # λ=50: M = 2, U = -10 -> return -5 < -1
    bad = W.ledger("hyperliquid", fills, px, leverage={"ETH": 50},
                    start=T0, times=ts)
    assert row(bad, H)["return"] == approx(-5)
    assert bad.attrs["breach"] is True
    # λ=1: M = 100 -> -0.10
    ok = W.ledger("hyperliquid", fills, px, leverage={"ETH": 1},
                   start=T0, times=ts)
    assert row(ok, H)["return"] == approx(-0.10)
    assert ok.attrs["breach"] is False


def test_O_check16_realized_matches_closedpnl():
    fills, funding = hl_k()
    df = W.ledger("hyperliquid", fills, {"ETH": series({H: 125})},
                  funding=funding, leverage={"ETH": 5}, start=T0)
    want = (sum(float(f["closedPnl"]) for f in fills)
            - sum(float(f["fee"]) for f in fills)
            + sum(float(x["usdc"]) for x in funding))
    # 40 - 0.5 - 0.5 = 39
    assert want == approx(39)
    assert df.iloc[-1]["realized"] == approx(want)


# ---------- random synthetic sets ----------

def price_set(rng, names, hours=60):
    out = {}
    for n in names:
        steps = np.exp(np.cumsum(rng.normal(0, 0.02, hours + 1)))
        p = 100 * steps
        out[n] = pd.Series(p, index=[T0 + k * H for k in range(hours + 1)])
    return out


def price_at(s, t):
    return float(s[s.index <= t].iloc[-1])


def avantis_set(seed=7, capped=False):
    """~30 rows over 3 markets, partial closes, shorts.

    Every close keeps g - fee - funding >= -margin, so O never rises at a
    close. capped=True: 1x longs with no fee or funding, so U >= -M too.
    """
    rng = np.random.default_rng(seed)
    px = price_set(rng, ["AAA", "BBB", "CCC"])
    rows = []
    for _ in range(15):
        m = str(rng.choice(["AAA", "BBB", "CCC"]))
        a = int(rng.integers(100, 40 * H))
        buy = True if capped else bool(rng.integers(0, 2))
        lev = 1 if capped else int(rng.choice([1, 2, 5, 10]))
        p0 = price_at(px[m], T0 + a)
        parts = int(rng.integers(1, 3))
        for _ in range(parts):
            c = a + int(rng.integers(1, 15 * H))
            size = float(rng.uniform(10, 500))
            gross = float(size * rng.uniform(-0.5, 1.0))
            fee = 0.0 if capped else float(size * rng.uniform(0, 0.05))
            f = 0.0 if capped else float(size * rng.uniform(-0.05, 0.1))
            rows.append(av(m, buy, lev, p0, a, c, size, gross, fee, f))
    return rows, px


def hyper_set(seed=11):
    rng = np.random.default_rng(seed)
    px = price_set(rng, ["XX", "YY"])
    q = {"XX": 0.0, "YY": 0.0}
    fills = []
    t = 0
    for _ in range(30):
        t += int(rng.integers(60, 2 * H))
        c = str(rng.choice(["XX", "YY"]))
        side = "B" if rng.integers(0, 2) else "A"
        sz = float(rng.choice([0.5, 1.0, 1.5, 2.0]))
        p = round(price_at(px[c], T0 + t) * rng.uniform(0.99, 1.01), 4)
        fills.append(fill(c, t, side, sz, p, q[c], fee=round(0.0005 * p * sz, 6)))
        q[c] += sz if side == "B" else -sz
    funding = [fund(str(rng.choice(["XX", "YY"])),
                    int(rng.integers(H, t)), round(float(rng.normal(0, 0.3)), 4))
               for _ in range(8)]
    return fills, funding, px


HL_LEV = {"XX": 3, "YY": 5}


def ref_closed_pnl(fills):
    """Reference closedPnl per fill: average entry, close then open on a flip."""
    q, e, out = {}, {}, []
    for f in fills:
        c, sz, p = f["coin"], float(f["sz"]), float(f["px"])
        d = sz if f["side"] == "B" else -sz
        qc, ec = q.get(c, 0.0), e.get(c, 0.0)
        pnl = 0.0
        if qc == 0 or np.sign(d) == np.sign(qc):
            ec = (abs(qc) * ec + sz * p) / (abs(qc) + sz)
            qc += d
        else:
            closed = min(sz, abs(qc))
            pnl = closed * (p - ec) * np.sign(qc)
            qc += d
            if abs(qc) < 1e-12:
                qc = 0.0
            elif np.sign(qc) == np.sign(d):
                ec = p
        q[c], e[c] = qc, ec
        out.append(pnl)
    return out


def check_common(df):
    # 8: P = R + U, U = U_price + U_fund, O = M - R
    assert np.allclose(df["pnl"], df["realized"] + df["unrealized"])
    assert np.allclose(df["unrealized"],
                       df["unrealized_price"] + df["unrealized_funding"])
    assert np.allclose(df["own"], df["margin_open"] - df["realized"])
    # 10a: K never falls
    k = df["capital"].to_numpy()
    assert np.all(np.diff(k) >= -1e-9)
    # 11: sign(return) = sign(pnl) where pnl != 0
    nz = df["pnl"].abs() > 1e-9
    assert (np.sign(df.loc[nz, "return"]) == np.sign(df.loc[nz, "pnl"])).all()
    assert (df["capital"] > 0).all()


def test_avantis_identities():
    rows, px = avantis_set()
    df = W.ledger("avantis", rows, px)
    check_common(df)
    opens = [r["openedAt"] for r in rows]
    spans = [(r["openedAt"], pd.Timestamp(r["time"]).timestamp())
             for r in rows]
    for i, r in df.iterrows():
        t = r["time"]
        # 9: nothing open -> U = 0 and M = 0
        if not any(a <= t < c for a, c in spans):
            assert r["unrealized"] == approx(0)
            assert r["margin_open"] == approx(0)
        # 10b: K rises only at a row whose hour holds an open
        if i > 0:
            prev = df.iloc[i - 1]
            if r["capital"] > prev["capital"] + 1e-9:
                assert any(prev["time"] < a <= t for a in opens)


def test_avantis_return_floor_when_losses_capped():
    rows, px = avantis_set(seed=3, capped=True)
    df = W.ledger("avantis", rows, px)
    # 12: U >= -M and every close loses <= its margin -> return >= -1
    assert (df["return"] >= -1 - 1e-9).all()
    assert df.attrs["breach"] is False


def test_avantis_scale():
    rows, px = avantis_set()
    big = [{**r, **{k: 3 * r[k] for k in
                    ("positionSize", "gross", "closingFee", "funding")}}
           for r in rows]
    a = W.ledger("avantis", rows, px)
    b = W.ledger("avantis", big, px)
    # 13: every margin x3 -> same return
    assert np.allclose(a["return"], b["return"])
    assert np.allclose(3 * a["pnl"], b["pnl"])


def test_avantis_sampling():
    rows, px = avantis_set()
    grid = W.ledger("avantis", rows, px)
    events = {r["openedAt"] for r in rows}
    events |= {int(pd.Timestamp(r["time"]).timestamp()) for r in rows}
    ts = sorted(set(grid["time"]) | events)
    dense = W.ledger("avantis", rows, px, times=ts)
    # 14: same values at every shared time
    shared = dense[dense["time"].isin(grid["time"])].reset_index(drop=True)
    pd.testing.assert_frame_equal(shared[COLS], grid[COLS].reset_index(drop=True),
                                  check_exact=False, rtol=1e-9, atol=1e-9)


def test_avantis_check15_realized_matches_usdc_sent():
    rows, px = avantis_set()
    df = W.ledger("avantis", rows, px)
    total = 0.0
    for r in rows:
        pos = r["positionSize"]
        sent = pos + r["gross"] - r["closingFee"] - r["funding"]
        total += sent - pos
    assert df.iloc[-1]["realized"] == approx(total)


def test_hyper_identities():
    fills, funding, px = hyper_set()
    df = W.ledger("hyperliquid", fills, px, funding=funding,
                  leverage=HL_LEV, start=T0)
    check_common(df)
    assert df["margin_estimated"].all()
    assert (df["unrealized_funding"] == 0).all()
    # 9: M = 0 means q = 0 everywhere, so U = 0
    flat = df["margin_open"].abs() < 1e-12
    assert np.allclose(df.loc[flat, "unrealized"], 0)


def test_hyper_scale():
    fills, funding, px = hyper_set()
    big = [{**f, "sz": str(3 * float(f["sz"])),
            "fee": str(3 * float(f["fee"])),
            "startPosition": str(3 * float(f["startPosition"]))}
           for f in fills]
    bigf = [{**x, "usdc": str(3 * float(x["usdc"]))} for x in funding]
    a = W.ledger("hyperliquid", fills, px, funding=funding,
                 leverage=HL_LEV, start=T0)
    b = W.ledger("hyperliquid", big, px, funding=bigf,
                 leverage=HL_LEV, start=T0)
    assert np.allclose(a["return"], b["return"])


def test_hyper_sampling():
    fills, funding, px = hyper_set()
    kw = dict(funding=funding, leverage=HL_LEV, start=T0)
    grid = W.ledger("hyperliquid", fills, px, **kw)
    events = {f["time"] // 1000 for f in fills} | {x["time"] // 1000 for x in funding}
    ts = sorted(set(grid["time"]) | events)
    dense = W.ledger("hyperliquid", fills, px, times=ts, **kw)
    shared = dense[dense["time"].isin(grid["time"])].reset_index(drop=True)
    pd.testing.assert_frame_equal(shared[COLS], grid[COLS].reset_index(drop=True),
                                  check_exact=False, rtol=1e-9, atol=1e-9)


def test_hyper_check16_random():
    fills, funding, px = hyper_set()
    closed = ref_closed_pnl(fills)
    fills = [{**f, "closedPnl": str(c)} for f, c in zip(fills, closed)]
    df = W.ledger("hyperliquid", fills, px, funding=funding,
                  leverage=HL_LEV, start=T0)
    want = (sum(closed) - sum(float(f["fee"]) for f in fills)
            + sum(float(x["usdc"]) for x in funding))
    assert df.iloc[-1]["realized"] == pytest.approx(want, rel=1e-9, abs=1e-6)


# ---------- round 1 decisions ----------

def test_R1_funding_before_start_ignored():
    fills = [fill("ETH", 0, "B", 1, 100, 0),
             fill("ETH", 1800, "A", 1, 100, 1)]
    kw = dict(leverage={"ETH": 1}, start=T0)
    px = {"ETH": series({0: 100})}
    base = W.ledger("hyperliquid", fills, px, **kw)
    early = W.ledger("hyperliquid", fills, px,
                     funding=[fund("ETH", -H, -5)], **kw)
    pd.testing.assert_frame_equal(base, early)
    # funding paid inside the window: R = 0 - 2 = -2, funding column 2
    late = W.ledger("hyperliquid", fills, px,
                    funding=[fund("ETH", 600, -2)], **kw)
    assert late.iloc[-1]["realized"] == approx(-2)
    assert late.iloc[-1]["funding"] == approx(2)


def test_R2_growing_gap():
    # buy 1 @100; a buy of 1 @110 is lost; sell 2 @120 from startPosition 2
    fills = [fill("ETH", 0, "B", 1, 100, 0),
             fill("ETH", 1800, "A", 2, 120, 2)]
    df = W.ledger("hyperliquid", fills, {"ETH": series({0: 100})},
                  leverage={"ETH": 1}, start=T0)
    # gap at 120: q 1 -> 2, e = (100 + 120)/2 = 110; sell: 2*(120-110) = 20
    assert df.iloc[-1]["realized"] == approx(20)
    assert df.iloc[-1]["margin_open"] == approx(0)
    # 1*120 / (1*100 + 2*120 + 1*120) = 120/460
    assert df.attrs["gap_share"] == approx(120 / 460)


def test_R2_shrinking_gap():
    # buy 2 @100; a sell of 1 is lost; sell 1 @130 from startPosition 1
    fills = [fill("ETH", 0, "B", 2, 100, 0),
             fill("ETH", 1800, "A", 1, 130, 1)]
    df = W.ledger("hyperliquid", fills, {"ETH": series({0: 100})},
                  leverage={"ETH": 1}, start=T0)
    # gap 2 -> 1 at 130 realizes 1*(130-100) = 30; sell 1 more: 30 -> 60
    assert df.iloc[-1]["realized"] == approx(60)
    assert df.iloc[-1]["margin_open"] == approx(0)
    # 1*130 / (2*100 + 1*130 + 1*130) = 130/460
    assert df.attrs["gap_share"] == approx(130 / 460)


def test_R2_clean_wallet_has_no_gap():
    fills, funding = hl_k()
    df = W.ledger("hyperliquid", fills, {}, funding=funding,
                  leverage={"ETH": 5}, start=T0)
    assert df.attrs["gap_share"] == 0.0


def test_R3_close_before_open_skipped():
    good = av("ETH", True, 1, 100, 0, 2 * H, 100, 10)
    bad = av("ETH", True, 1, 100, 2 * H, H, 100, 10)  # closes before it opens
    px = {"ETH": series({0: 100, H: 105})}
    a = W.ledger("avantis", [good], px)
    b = W.ledger("avantis", [good, bad], px)
    pd.testing.assert_frame_equal(a, b)
    assert b.attrs["skipped"] == 1


def test_avantis_two_positions_one_market():
    # $100 1x long at 100 and $50 5x long at 80, both open at once
    rows = [av("ETH", True, 1, 100, 0, 2 * H, 100, 20),
            av("ETH", True, 5, 80, 0, 2 * H, 50, 125)]
    df = W.ledger("avantis", rows, {"ETH": series({0: 100, H: 120})})
    r = row(df, H)
    # U = 100*1*(120/100-1) + 50*5*(120/80-1) = 20 + 125 = 145
    assert r["unrealized_price"] == approx(145)
    assert r["margin_open"] == approx(150)
    assert r["capital"] == approx(150)
    assert r["return"] == approx(145 / 150)


def test_hyper_two_coins_own_leverage():
    fills = [fill("XX", 0, "B", 2, 100, 0),
             fill("YY", 600, "B", 1, 200, 0)]
    px = {"XX": series({0: 100}), "YY": series({0: 200})}
    df = W.ledger("hyperliquid", fills, px,
                  leverage={"XX": 2, "YY": 4}, start=T0)
    # M = 2*100/2 + 1*200/4 = 100 + 50 = 150
    assert df.iloc[-1]["margin_open"] == approx(150)
    assert df.iloc[-1]["capital"] == approx(150)


def test_avantis_columns_are_float_when_no_trade_is_ever_live():
    rows = [av("AAA", True, 5, 100.0, 0, 0, 100.0, 1.0, 0.1)]  # opens and closes in one second
    df = W.ledger("avantis", rows, {})
    assert len(df)
    for c in NUM:
        assert df[c].dtype == np.float64, c


# ---------- Veranta v2 history ----------

def v2(open_, t, market, side, size, coll, px, coins=None, oid=None,
       of=None, net=None, fee=None, fund=None, borrow=None):
    """One Veranta v2 history row at T0+t seconds; size is notional."""
    return {"type": "open" if open_ else "close", "open": open_,
            "timestamp": T0 + t, "market": market, "side": side,
            "positionSize": size, "collateral": coll, "openPrice": px,
            "coinExposureAdded": coins, "orderId": oid,
            "orderIdOfOpenTrade": of, "netPnl": net, "closeFee": fee,
            "funding": fund, "borrowFee": borrow}


def eth_book(side="long", close=True):
    # open 1 coin at 100 ($100, $10 collateral) at 0; add 3 coins at 120
    # ($360, $72) at 1h: 4 coins, cost 460, average entry 115.
    # Full close at 3h: $460 at 115 = 4 coins; net +50, fee 2, funding
    # 1.5 + borrow 0.5.
    rows = [v2(True, 0, "ETH", side, 100, 10, 100, coins=1, oid="o1"),
            v2(True, H, "ETH", side, 360, 72, 120, coins=None, oid="o2")]
    if close:
        rows.append(v2(False, 3 * H, "ETH", side, 460, 82, 115, of="o1",
                       net=50, fee=2, fund=1.5, borrow=0.5))
    return rows


ETH_V2 = {"ETH": series({0: 100, H // 2: 110, H: 120, 2 * H: 130,
                         3 * H: 140})}
V2_TIMES = [T0, T0 + H // 2, T0 + H, T0 + 2 * H, T0 + 3 * H]


def test_veranta_open_increase_close_book():
    times, books, Q, C, Mg, net, fee, fund, carried = W.veranta_replay(eth_book())
    assert list(times) == [T0, T0 + H, T0 + 3 * H]
    assert books == [("ETH", 1.0)]
    # coins 1 -> 4 (3 = 360/120 from the price, coinExposureAdded None) -> 0
    assert Q[:, 0] == pytest.approx([1, 4, 0], abs=1e-12)
    assert C[:, 0] == pytest.approx([100, 460, 0], abs=1e-12)
    assert Q[-1, 0] == 0.0 and C[-1, 0] == 0.0  # snapped to flat
    assert list(net) == [0, 0, 50]
    assert list(fee) == [0, 0, 2]
    assert list(fund) == [0, 0, 2]
    assert carried == []


def test_veranta_ledger_realized_fees_funding_after_close():
    df = W.ledger("veranta", eth_book(), ETH_V2, times=V2_TIMES)
    last = row(df, 3 * H)
    assert last["realized"] == approx(50)  # netPnl, not netPnl - fees
    assert last["fees"] == approx(2)
    assert last["funding"] == approx(2)
    assert last["unrealized"] == approx(0)
    assert last["margin_open"] == approx(0)
    assert df["margin_estimated"].all()
    assert list(df.columns) == COLS


def test_veranta_coin_exposure_added_used_when_given():
    rows = eth_book(close=False)
    rows[1]["coinExposureAdded"] = 2.5  # not 360/120
    Q = W.veranta_replay(rows)[2]
    assert Q[-1, 0] == pytest.approx(3.5)


def test_veranta_unrealized_counts_increase_from_its_time():
    df = W.ledger("veranta", eth_book(), ETH_V2, times=V2_TIMES)
    # 0.5h: 1 coin only: 1*110 - 100 = 10
    assert row(df, H // 2)["unrealized_price"] == approx(10)
    # 1h: 4*120 - 460 = 20
    assert row(df, H)["unrealized_price"] == approx(20)
    # 2h: 4*130 - 460 = 60
    assert row(df, 2 * H)["unrealized_price"] == approx(60)
    assert row(df, 2 * H)["unrealized_funding"] == approx(0)
    assert row(df, 2 * H)["pnl"] == approx(60)


def test_veranta_short_book_flips_sign():
    df = W.ledger("veranta", eth_book("short"), ETH_V2, times=V2_TIMES)
    assert W.veranta_replay(eth_book("short"))[1] == [("ETH", -1.0)]
    assert row(df, H // 2)["unrealized_price"] == approx(-10)
    assert row(df, H)["unrealized_price"] == approx(-20)
    assert row(df, 2 * H)["unrealized_price"] == approx(-60)
    assert row(df, 3 * H)["unrealized_price"] == approx(0)


def test_veranta_margin_per_book():
    rows = eth_book(close=False) + [
        # partial close at 2h: $230 at 115 = 2 coins; cost 460 -> 230
        v2(False, 2 * H, "ETH", "long", 230, 41, 115, of="o1", net=5),
        # BTC short, $1000 notional on $250 collateral (4x) at 1h
        v2(True, H, "BTC", "short", 1000, 250, 50, coins=20, oid="o3"),
    ]
    px = {**ETH_V2, "BTC": series({0: 50})}
    df = W.ledger("veranta", rows, px, times=V2_TIMES[:4])
    # margin = cost * collateral added / notional added, so far
    # 0: ETH 100*10/100 = 10
    assert row(df, 0)["margin_open"] == approx(10)
    # 1h: ETH 460*(10+72)/(100+360) = 82; BTC 1000*250/1000 = 250
    assert row(df, H)["margin_open"] == approx(82 + 250)
    # 2h: ETH 230*82/460 = 41; BTC 250
    assert row(df, 2 * H)["margin_open"] == approx(41 + 250)
    # ETH coins 2 left: 2*130 - 230 = 30; BTC short 20 at 50, still 50: 0
    assert row(df, 2 * H)["unrealized_price"] == approx(30)
    assert row(df, 2 * H)["realized"] == approx(5)


def test_veranta_close_without_open_is_carried():
    rows = [
        v2(True, 0, "BTC", "long", 500, 50, 50, coins=10, oid="b1"),
        # ETH long opened before the history starts: 2 coins at 100
        v2(False, 2 * H, "ETH", "long", 200, 20, 100, of="gone", net=30,
           fee=1),
    ]
    px = {"BTC": series({0: 50}), "ETH": series({0: 100, H: 110})}
    ev = W.veranta_replay(rows)
    times, books, Q, C = ev[:4]
    assert ev[8] == ["ETH"]
    # a carried row at the first row's time, then the BTC open there too
    assert list(times) == [T0, T0, T0 + 2 * H]
    n = books.index(("ETH", 1.0))
    assert Q[0, n] == approx(2) and C[0, n] == approx(200)
    assert Q[-1, n] == 0.0 and C[-1, n] == 0.0
    df = W.ledger("veranta", rows, px, times=[T0, T0 + H, T0 + 2 * H])
    assert df.attrs["carried"] == ["ETH"]
    # 0: ETH 20*200/200 + BTC 50*500/500 = 70
    assert row(df, 0)["margin_open"] == approx(70)
    # 1h: ETH 2*110 - 200 = 20
    assert row(df, H)["unrealized_price"] == approx(20)
    assert row(df, 2 * H)["realized"] == approx(30)
    assert row(df, 2 * H)["fees"] == approx(1)
    assert row(df, 2 * H)["margin_open"] == approx(50)


def test_veranta_no_carry_when_every_close_has_its_open():
    df = W.ledger("veranta", eth_book(), ETH_V2, times=V2_TIMES)
    assert df.attrs["carried"] == []


def test_veranta_ledger_is_old_closes_plus_v2_rows():
    old = [av("ETH", True, 2, 100, 0, 2 * H, 100, 30, fee=1, fund=4),
           av("BTC", False, 1, 50, H, 4 * H, 200, -10, fee=0.5)]
    px = {**ETH_V2, "BTC": series({0: 50, 2 * H: 45, 4 * H: 55})}
    ts = [T0 + k * H // 2 for k in range(10)]
    both = W.ledger("veranta", old + eth_book(), px, times=ts)
    a = W.ledger("avantis", old, px, times=ts)
    b = W.ledger("veranta", eth_book(), px, times=ts)
    for c in ["realized", "unrealized_price", "unrealized_funding",
              "unrealized", "fees", "funding", "pnl", "margin_open"]:
        np.testing.assert_allclose(both[c], a[c] + b[c], rtol=1e-12,
                                   atol=1e-9, err_msg=c)
    # spot check 1h: old ETH 2*100*(120/100-1) = 40, funding spread
    # -4*1h/2h = -2; old BTC short just opened: 0; v2 ETH 20
    r = row(both, H)
    assert r["unrealized_price"] == approx(40 + 20)
    assert r["unrealized_funding"] == approx(-2)


def test_veranta_only_old_closes_matches_avantis():
    old = [av("ETH", True, 2, 100, 0, 2 * H, 100, 30, fee=1)]
    a = W.ledger("avantis", old, ETH_V2)
    b = W.ledger("veranta", old, ETH_V2)
    cols = [c for c in COLS if c != "margin_estimated"]
    pd.testing.assert_frame_equal(a[cols], b[cols])


def test_veranta_unrealized_zero_before_first_price():
    px = {"ETH": series({2 * H: 130})}
    df = W.ledger("veranta", eth_book(), px, times=V2_TIMES)
    for t in (0, H // 2, H):
        assert row(df, t)["unrealized_price"] == 0.0
    # margin still counts before the price: 460*82/460 at 1h
    assert row(df, H)["margin_open"] == approx(82)
    assert row(df, 2 * H)["unrealized_price"] == approx(4 * 130 - 460)
    assert df.attrs["no_prices"] == []
    none = W.ledger("veranta", eth_book(), {}, times=V2_TIMES)
    assert (none["unrealized_price"] == 0).all()
    assert none.attrs["no_prices"] == ["ETH"]


def test_veranta_paid_fee_is_realized_at_its_open():
    rows = eth_book()
    rows[0]["paidFee"], rows[1]["paidFee"] = 1.0, 0.5
    net, fee = W.veranta_replay(rows)[5:7]
    # open -1; increase -0.5; close +50 net, close fee 2
    assert list(net) == approx([-1, -1.5, 48.5])
    assert list(fee) == approx([1, 1.5, 3.5])
    df = W.ledger("veranta", rows, ETH_V2, times=V2_TIMES)
    assert row(df, H // 2)["realized"] == approx(-1)
    assert row(df, 3 * H)["realized"] == approx(48.5)
    assert row(df, 3 * H)["fees"] == approx(3.5)


def test_veranta_carry_fee_is_realized_at_the_first_row():
    rows = [
        v2(True, 0, "BTC", "long", 500, 50, 50, coins=10, oid="b1"),
        v2(False, 2 * H, "ETH", "long", 200, 20, 100, of="gone", net=30,
           fee=1),
    ]
    rows[1]["carryFee"] = 4.0
    net, fee = W.veranta_replay(rows)[5:7]
    # carried row, BTC open, ETH close: -4, -4, -4 + 30
    assert list(net) == approx([-4, -4, 26])
    assert list(fee) == approx([4, 4, 5])


DRAIN = {"amount": 12.0, "from": T0 + H, "to": T0 + 3 * H}


def test_veranta_drain_is_unrealized_funding_spread_evenly():
    ev = W.veranta_replay(eth_book(close=False))
    t = [T0, T0 + H, T0 + 2 * H, T0 + 3 * H, T0 + 4 * H]
    s = W.veranta_state(ev, ETH_V2, t, DRAIN)
    # 0 before "from", -12 x elapsed / 2h, then held at -12
    assert list(s["unrealized_funding"]) == approx([0, 0, -6, -12, -12])
    assert list(s["realized"]) == approx([0] * 5)  # not realized
    none = W.veranta_state(ev, ETH_V2, t)
    assert (none["unrealized_funding"] == 0).all()


@pytest.mark.parametrize("drain", [{"amount": 0.0, "from": T0, "to": T0 + H},
                                   {"amount": 5.0, "from": None, "to": T0 + H}])
def test_veranta_no_drain_when_zero_or_nothing_live(drain):
    ev = W.veranta_replay(eth_book(close=False))
    s = W.veranta_state(ev, ETH_V2, [T0, T0 + 2 * H], drain)
    assert (s["unrealized_funding"] == 0).all()


def test_veranta_ledger_takes_drain_record():
    rows = eth_book(close=False)
    df = W.ledger("veranta", rows + [{"drain": DRAIN}], ETH_V2,
                  times=V2_TIMES + [T0 + 4 * H])
    assert row(df, 2 * H)["unrealized_funding"] == approx(-6)
    # 4h: 4*140 - 460 = 100 price, less the full drain 12
    assert row(df, 4 * H)["pnl"] == approx(100 - 12)

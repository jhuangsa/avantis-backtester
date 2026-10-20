"""examples/mimic.py: the address checksum, the cache key, and the curve grid."""

import itertools
import json
import sys
import time
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


# ---- helpers for S1-S8 -------------------------------------------------------

avbt_cpp = M.avbt_cpp
TF = M.TF
D = 86400
ACCOUNT = M.vt.ACCOUNT


def ts(s):
    return int(pd.Timestamp(s, tz="UTC").timestamp())


def make_bars(tfs, t0, t1, price):
    """Bars per timeframe on [t0, t1); price(ts array) -> closes; open = close."""
    out = []
    for tf in tfs:
        step = avbt_cpp.timeframe_seconds(tf)
        t = np.arange(t0, t1, step, dtype="int64")
        c = price(t).astype(float)
        df = pd.DataFrame({"ts": t, "open": c, "high": c, "low": c, "close": c,
                           "minutes_with_data": np.full(len(t), step // 60, dtype="int32")})
        out.append(M.hd.to_bars(tf, df))
    return out


def npz_dict(t0, t1, price):
    """The old npz shape of hl_bars: '<rule>_<col>' arrays, ts = bar open time."""
    out = {}
    for rule, step in (("1h", 3600), ("4h", 14400)):
        t = np.arange(t0, t1, step, dtype="int64")
        c = price(t).astype(float)
        out.update({f"{rule}_ts": t, f"{rule}_open": c, f"{rule}_high": c, f"{rule}_low": c,
                    f"{rule}_close": c, f"{rule}_minutes_with_data": np.full(len(t), step // 60, dtype="int32")})
    return out


def fill(coin, side, px, sz, t_ms, before, tid, dir="Open Long", fee="0"):
    return {"coin": coin, "px": str(px), "sz": str(sz), "side": side, "time": int(t_ms),
            "startPosition": str(before), "fee": fee, "tid": tid, "dir": dir,
            "hash": "0x", "oid": tid, "crossed": True, "closedPnl": "0"}


class HL:
    """A fake hl_post: fills by time (pages of 2000), funding, leverage, flat candles."""

    def __init__(self, fills=(), price=100.0, empty=(), error=(), overlap=0, funding=(), leverage=None):
        self.fills = sorted(fills, key=lambda f: f["time"])
        self.price, self.empty, self.calls = price, set(empty), []
        self.error, self.overlap = set(error), overlap  # coins that answer 500; extra bars before startTime
        self.funding = sorted(funding, key=lambda x: x["time"])  # userFunding items
        self.leverage = leverage or {}  # coin -> activeAssetData response; a missing coin answers 500

    def __call__(self, body):
        self.calls.append(body)
        assert len(self.calls) < 60, "hl_post called too often"
        if body["type"] == "userFillsByTime":
            return [f for f in self.fills if f["time"] >= body["startTime"]][:2000]
        if body["type"] == "candleSnapshot":
            import urllib.error
            req = body["req"]
            if req["coin"] in self.error:
                raise urllib.error.HTTPError(M.HL, 500, "Internal Server Error", {}, None)
            if req["coin"] in self.empty or (req["coin"], req["interval"]) in self.empty:
                return []
            step = {"1h": 3600, "4h": 14400}[req["interval"]] * 1000
            lo = req["startTime"] // step * step - self.overlap * step
            p = str(self.price)
            return [{"t": t, "T": t + step - 1, "s": req["coin"], "i": req["interval"],
                     "o": p, "h": p, "l": p, "c": p, "v": "0", "n": 0}
                    for t in range(lo, req["endTime"], step)][-5000:]  # the API serves the last 5000
        if body["type"] == "userFunding":
            return [x for x in self.funding if x["time"] >= body["startTime"]][:500]
        if body["type"] == "activeAssetData":
            if body["coin"] not in self.leverage:
                import urllib.error
                raise urllib.error.HTTPError(M.HL, 500, "Internal Server Error", {}, None)
            return self.leverage[body["coin"]]
        raise AssertionError(body)


@pytest.fixture(autouse=True)
def no_io(monkeypatch, tmp_path):
    """No network, and the cache in tmp_path."""
    def off(*a, **k):
        raise AssertionError("network call")
    monkeypatch.setattr(M, "CACHE", tmp_path)
    monkeypatch.setattr(M, "hl_post", off)
    monkeypatch.setattr(M.vt, "_get", off)
    monkeypatch.setattr(M.vt, "_symbols", off)
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    return tmp_path


@pytest.fixture
def hl(monkeypatch):
    """The Hyperliquid venue, restored afterwards."""
    for k in ("VENUE", "TFS", "TF_NAMES"):
        monkeypatch.setattr(M, k, getattr(M, k))
    monkeypatch.setitem(M.KNOBS, "timeframe", M.KNOBS["timeframe"])
    M.use_venue("hyperliquid")


# ---- S1 ----------------------------------------------------------------------

def test_fee_is_a_quarter_basis_point_per_side():
    assert M.FEE == 0.00025


# ---- S3 closed_positions -----------------------------------------------------

def closed(before, after):
    out = M.closed_positions(np.array(before, float), np.array(after, float))
    return [bool(x) for x in out]


def test_closed_positions_partial_closes_are_one_long():
    assert closed([0, 1, 2, 1.2, 0.5], [1, 2, 1.2, 0.5, 0]) == [False]


def test_closed_positions_flip_closes_the_long():
    assert closed([0, 1], [1, -1]) == [False]
    assert closed([0, -1, 2], [-1, 2, 0]) == [True, False]


def test_closed_positions_short_closed_by_a_buy_is_true():
    assert closed([-1], [0]) == [True]


def test_closed_positions_float_residue_counts_as_closed():
    assert closed([1], [1e-12]) == [False]
    assert closed([-2], [-1e-10]) == [True]
    assert closed([1], [0.5]) == []


def test_closed_positions_opening_fills_are_none():
    assert closed([0, 1, 0, -1], [1, 2, -1, -3]) == []


# ---- S4 hl_curve -------------------------------------------------------------

LO, HI = "2025-01-11", "2025-02-10"
FEE = 0.00025
STATS_KEYS = {"trades", "short_share", "pnl", "gap_share"}


@pytest.fixture
def hl_curve_env(monkeypatch, hl):
    """hl_curve with flat candles at 100 and the fills the test sets."""
    def use(fills, price=100.0):
        monkeypatch.setattr(M, "hl_fills", lambda address: sorted(fills, key=lambda f: f["time"]))
        flat = price if callable(price) else (lambda t: np.full(len(t), price))
        monkeypatch.setattr(M, "hl_bars", lambda coin, start, end: make_bars(M.TFS, ts(start), ts(end), flat))
        return M.hl_curve("0xabc", LO, HI)
    return use


def test_hl_curve_position_held_from_before_the_window_has_no_pnl(hl_curve_env):
    lo = ts(LO)
    pnl, stats, counts, start, end = hl_curve_env([fill("BTC", "B", 50, 1, (lo - 10 * D) * 1000, 0, 1)])
    assert (start, end) == (LO, HI)
    assert stats["pnl"] == pytest.approx(0, abs=1e-9)
    assert np.allclose(pnl.to_numpy(), 0)
    assert pnl.index[0] == lo and len(pnl) == 30 * 24
    assert stats["trades"] == 0


def test_hl_curve_position_held_from_before_the_window_is_valued_from_the_start_price(hl_curve_env):
    lo = ts(LO)
    step = lambda t: np.where(t < lo + 2 * D, 100.0, 110.0)
    pnl, stats, *_ = hl_curve_env([fill("BTC", "B", 50, 1, (lo - 10 * D) * 1000, 0, 1)], price=step)
    assert stats["pnl"] == pytest.approx(10)  # from 100 at the window start, not from the 50 it was bought at
    assert pnl.iloc[0] == pytest.approx(0)
    assert pnl.iloc[-1] == pytest.approx(10)


def test_hl_curve_first_fill_inside_the_window_with_a_start_position_trades_the_open_at_its_price(hl_curve_env):
    lo = ts(LO)
    # The history covers the window start (a BTC round trip before it), yet the API never served the
    # fills that opened the short of 10: the open is traded at 337, the close's price, not held since
    # the window start at 250. The netted open and close pay no fee.
    step = lambda t: np.where(t < lo + 2 * D, 250.0, 300.0)
    pnl, stats, *_ = hl_curve_env([
        fill("BTC", "B", 200, 1, (lo - 10 * D) * 1000, 0, 1),
        fill("BTC", "A", 200, 1, (lo - 9 * D) * 1000, 1, 2, "Close Long"),
        fill("SOL", "B", 337, 10, (lo + D + 1800) * 1000, -10, 3, "Close Short")], price=step)
    assert stats["pnl"] == pytest.approx(0, abs=1e-9)  # no 870 loss from 250 to 337
    assert stats["gap_share"] == pytest.approx(0.5)  # the 10 x 337 open over the open plus the close
    assert pnl.iloc[0] == 0 and pnl.iloc[-1] == pytest.approx(0, abs=1e-9)
    assert stats["trades"] == 1 and stats["short_share"] == 1.0


def test_hl_curve_history_starting_inside_the_window_holds_start_positions_from_the_start_price(hl_curve_env):
    lo = ts(LO)
    # The API keeps only recent fills: the wallet's history begins inside the window, so a coin first
    # seen later with a start position was opened before the history began and is held from the start.
    step = lambda t: np.where(t < lo + 2 * D, 250.0, 300.0)
    pnl, stats, _, start, _ = hl_curve_env([
        fill("BTC", "B", 250, 1, (lo + D + 1800) * 1000, 0, 1),
        fill("SOL", "B", 337, 10, (lo + 3 * D) * 1000, -10, 2, "Close Short")], price=step)
    assert start == "2025-01-12"  # the window starts on the history's first day
    assert stats["pnl"] == pytest.approx(50 - FEE * 250 - 10 * (337 - 250) - FEE * 3370)
    assert stats["gap_share"] == 0.0
    assert pnl.iloc[0] == 0


def test_hl_curve_short_round_trip_pays_fee_on_both_notionals(hl_curve_env):
    lo = ts(LO)
    t1, t2 = lo + D + 1800, lo + 2 * D
    pnl, stats, counts, *_ = hl_curve_env([
        fill("ETH", "A", 100, 1, t1 * 1000, 0, 1, "Open Short"),
        fill("ETH", "B", 90, 1, t2 * 1000, -1, 2, "Close Short")])
    want = 10 - FEE * (100 + 90)
    assert stats["pnl"] == pytest.approx(want)
    assert pnl.iloc[-1] == pytest.approx(want)
    assert stats["trades"] == 1 and stats["short_share"] == 1.0
    # While the short is open at 100, only the open fee is lost.
    assert pnl.loc[lo + D + 7200] == pytest.approx(-FEE * 100)
    assert counts["ETH"] == 2


def test_hl_curve_flip_is_two_trades(hl_curve_env):
    lo = ts(LO)  # the first fill sits inside its day: a fill at the window's first second is not inside
    _, stats, *_ = hl_curve_env([
        fill("ETH", "A", 100, 1, (lo + D + 1800) * 1000, 0, 1, "Open Short"),
        fill("ETH", "B", 90, 2, (lo + 2 * D) * 1000, -1, 2, "Close Short"),
        fill("ETH", "A", 95, 1, (lo + 3 * D) * 1000, 1, 3, "Close Long")])
    assert stats["pnl"] == pytest.approx(15 - FEE * (100 + 180 + 95))
    assert stats["trades"] == 2 and stats["short_share"] == 0.5


def test_hl_curve_ignores_the_api_fee_field(hl_curve_env):
    lo = ts(LO)
    _, stats, *_ = hl_curve_env([
        fill("ETH", "A", 100, 1, (lo + D + 1800) * 1000, 0, 1, "Open Short", fee="1000000"),
        fill("ETH", "B", 90, 1, (lo + 2 * D) * 1000, -1, 2, "Close Short", fee="1000000")])
    assert stats["pnl"] == pytest.approx(10 - FEE * 190)


def test_hl_curve_trades_count_round_trips_not_fills(hl_curve_env):
    lo = ts(LO)
    _, stats, *_ = hl_curve_env([
        fill("ETH", "A", 100, 1, (lo + D + 1800) * 1000, 0, 1, "Open Short"),
        fill("ETH", "B", 90, 0.5, (lo + 2 * D) * 1000, -1, 2, "Close Short"),
        fill("ETH", "B", 90, 0.5, (lo + 3 * D) * 1000, -0.5, 3, "Close Short")])
    assert stats["trades"] == 1 and stats["short_share"] == 1.0
    assert stats["pnl"] == pytest.approx(100 - 45 - 45 - FEE * (100 + 45 + 45))


def test_hl_curve_gap_in_start_position_trades_at_that_fills_price(hl_curve_env):
    lo = ts(LO)
    # A missing buy of 1 (the -1 became 0) is traded at 120, the price of the fill that shows it.
    _, stats, *_ = hl_curve_env([
        fill("ETH", "A", 100, 1, (lo + D + 1800) * 1000, 0, 1, "Open Short"),
        fill("ETH", "B", 120, 2, (lo + 2 * D) * 1000, 0, 2, "Open Long"),
        fill("ETH", "A", 130, 2, (lo + 3 * D) * 1000, 2, 3, "Close Long")])
    assert stats["pnl"] == pytest.approx(100 - 3 * 120 + 260 - FEE * (100 + 360 + 260))
    assert stats["trades"] == 1 and stats["short_share"] == 0.0


def test_hl_curve_stats_keys(hl_curve_env):
    lo = ts(LO)
    _, stats, *_ = hl_curve_env([fill("BTC", "B", 100, 1, (lo + D) * 1000, 0, 1)])
    assert set(stats) == STATS_KEYS


# ---- S5 hl_fills -------------------------------------------------------------

def perp_fills(times, coin="BTC", tid0=1):
    return [fill(coin, "B", 100, 1, t, 0, tid0 + i) for i, t in enumerate(times)]


def test_hl_fills_pages_sharing_a_millisecond_lose_nothing(monkeypatch, hl):
    # Page 1 ends on one of two fills at 2000; page 2 starts there and repeats it.
    server = HL(perp_fills(list(range(1, 2000)) + [2000, 2000, 2001, 2002]))
    monkeypatch.setattr(M, "hl_post", server)
    out = M.hl_fills("0xabc")
    tids = [f["tid"] for f in out]
    assert len(tids) == 2003 and len(set(tids)) == 2003
    assert [f["time"] for f in out] == sorted(f["time"] for f in out)


def test_hl_fills_full_page_at_one_millisecond_terminates(monkeypatch, hl):
    server = HL(perp_fills([5000] * 2000 + [5001]))
    monkeypatch.setattr(M, "hl_post", server)
    out = M.hl_fills("0xabc")
    assert len(out) == 2001 and len({f["tid"] for f in out}) == 2001


def test_hl_fills_drops_spot_and_keeps_adl_settlement_liquidation(monkeypatch, hl):
    fills = [fill("BTC", "B", 100, 1, 1000, 0, 1, "Open Long"),
             fill("@1", "B", 1, 1, 1001, 0, 2, "Buy"),
             fill("PURR/USDC", "A", 1, 1, 1002, 0, 3, "Sell"),
             fill("HYPE", "B", 1, 1, 1003, 0, 4, "Buy"),
             fill("ETH", "A", 100, 1, 1004, 1, 5, "Auto-Deleveraging"),
             fill("ETH", "B", 100, 1, 1005, 0, 6, "Settlement"),
             fill("SOL", "A", 100, 1, 1006, 1, 7, "Liquidated Cross Long"),
             fill("DOGE", "A", 1, 1, 1007, 0, 8, "Spot Dust Conversion")]
    monkeypatch.setattr(M, "hl_post", HL(fills))
    out = M.hl_fills("0xabc")
    assert [f["tid"] for f in out] == [1, 5, 6, 7]
    assert {"coin", "px", "sz", "side", "time", "startPosition", "fee", "tid", "dir"} <= set(out[0])


def test_hl_fills_refresh_merges_new_fills_by_tid(monkeypatch, hl, tmp_path):
    now = int(time.time() * 1000)
    fills = perp_fills([now - 3 * 3600 * 1000, now - 2 * 3600 * 1000])
    server = HL(fills)
    monkeypatch.setattr(M, "hl_post", server)
    assert len(M.hl_fills("0xabc")) == 2
    files = list(tmp_path.glob("wallet_hl_fills_*"))
    assert [f.name for f in files] == ["wallet_hl_fills_0xabc.json.gz"], files
    cache = M.load_json_gz(files[0])
    assert isinstance(cache, dict) and "fetched" in cache, "cache keeps a fetched time"
    cache["fetched"] -= 2 * 3600 * 1000  # older than an hour
    M.save_json_gz(files[0], cache)
    server.fills.append(fill("BTC", "A", 100, 1, now - 1800 * 1000, 2, 99, "Close Long"))
    out = M.hl_fills("0xabc")
    assert [f["tid"] for f in out] == [1, 2, 99]
    assert server.calls[-1]["startTime"] >= cache["fetched"] - D * 1000
    # Fresh again: no call.
    n = len(server.calls)
    M.hl_fills("0xabc")
    assert len(server.calls) == n


def test_hl_fills_cache_without_tid_is_refetched(monkeypatch, hl, tmp_path):
    server = HL(perp_fills([1000, 2000]))
    monkeypatch.setattr(M, "hl_post", server)
    old = [{k: f[k] for k in ("coin", "px", "sz", "side", "time", "startPosition", "fee")} for f in server.fills[:1]]
    (tmp_path / "wallet_hl_fills_0xabc.json").write_text(json.dumps(old))
    out = M.hl_fills("0xabc")
    assert [f["tid"] for f in out] == [1, 2]
    assert server.calls and server.calls[0]["startTime"] == 0
    assert not (tmp_path / "wallet_hl_fills_0xabc.json").exists()


def test_hl_fills_fresh_json_cache_moves_to_gz_without_a_call(hl, tmp_path):
    fills = [{k: f[k] for k in M.HL_KEEP} for f in perp_fills([1000, 2000])]
    cache = {"fetched": int(time.time() * 1000), "keeps": M.HL_KEEPS, "fills": fills}
    old = tmp_path / "wallet_hl_fills_0xabc.json"
    old.write_text(json.dumps(cache))
    assert M.hl_fills("0xabc") == fills  # hl_post raises in no_io: no network call
    assert not old.exists()
    assert M.load_json_gz(tmp_path / "wallet_hl_fills_0xabc.json.gz") == cache
    assert M.hl_fills("0xabc") == fills


# ---- S6 hl_bars --------------------------------------------------------------

def test_hl_bars_coin_without_candles_is_cached_as_none(monkeypatch, hl):
    server = HL(empty=("ZZZ",))
    monkeypatch.setattr(M, "hl_post", server)
    assert M.hl_bars("ZZZ", LO, HI) is None
    n = len(server.calls)
    assert n >= 1
    assert M.hl_bars("ZZZ", LO, HI) is None
    assert len(server.calls) == n


def test_hl_bars_one_empty_rule_is_none_and_cached(monkeypatch, hl):
    server = HL(empty=(("HALF", "4h"),))
    monkeypatch.setattr(M, "hl_post", server)
    assert M.hl_bars("HALF", LO, HI) is None
    n = len(server.calls)
    assert M.hl_bars("HALF", LO, HI) is None
    assert len(server.calls) == n


def test_hl_bars_steps_from_timeframe_seconds_and_caches(monkeypatch, hl, tmp_path):
    server = HL(price=100.0)
    monkeypatch.setattr(M, "hl_post", server)
    b = M.hl_bars("BTC", LO, HI)
    assert [x.timeframe for x in b] == [TF.Hour1, TF.Hour4]
    assert b[0].ts[0] == ts(LO) and len(b[0].ts) == 30 * 24
    assert set(np.diff(b[0].ts)) == {3600} and set(np.diff(b[1].ts)) == {14400}
    assert np.all(b[0].close == 100.0)
    assert (tmp_path / "mimic_hl_BTC.npz").exists()
    n = len(server.calls)
    M.hl_bars("BTC", LO, HI)
    assert len(server.calls) == n


def test_hl_bars_fetches_only_what_the_cache_lacks(monkeypatch, hl):
    server = HL(price=100.0)
    monkeypatch.setattr(M, "hl_post", server)
    mid = "2025-01-25"
    M.hl_bars("BTC", LO, mid)
    n = len(server.calls)
    b = M.hl_bars("BTC", LO, HI)
    later = [c for c in server.calls[n:] if c["type"] == "candleSnapshot"]
    assert later and all(c["req"]["startTime"] > ts(LO) * 1000 for c in later)
    assert b[0].ts[0] == ts(LO) and len(b[0].ts) == 30 * 24


def test_hl_bars_colon_in_coin_names_the_file(monkeypatch, hl, tmp_path):
    monkeypatch.setattr(M, "hl_post", HL(price=1.0))
    assert M.hl_bars("xyz:ABC", LO, HI) is not None
    assert (tmp_path / "mimic_hl_xyz_ABC.npz").exists()


def test_hl_bars_seeds_from_the_old_per_range_file(monkeypatch, hl, tmp_path):
    np.savez_compressed(tmp_path / f"mimic_hl_BTC_{LO}_{HI}.npz", **npz_dict(ts(LO), ts(HI), lambda t: np.full(len(t), 7.0)))
    server = HL(price=100.0)
    monkeypatch.setattr(M, "hl_post", server)
    b = M.hl_bars("BTC", LO, HI)
    assert not server.calls
    assert np.all(b[0].close == 7.0) and len(b[0].ts) == 30 * 24


# ---- S7 avantis_curve --------------------------------------------------------

BASE = ts("2025-01-01")  # day 0
ADDR = "0xAbC"


def trade(close_at, pos, gross, buy=False, open_at=BASE + D + 12 * 3600, open_px=100.0, close_px=95.0,
          lev=2, symbol="BTC/USD", pid=1, index=0, closing_fee=0.0, funding=0.0):
    return {"time": pd.Timestamp(close_at, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "gross": gross, "pairIndex": pid, "symbol": symbol, "index": index, "buy": buy, "leverage": lev,
            "openPrice": open_px, "tp": 0, "sl": 0, "collateral": pos, "positionSize": pos,
            "closePrice": close_px, "usdcSent": pos + gross - closing_fee - funding, "openedAt": open_at,
            "closingFee": closing_fee, "funding": funding}


def price_path(t):
    """100, then 110 on day 3, then 95 from day 4."""
    return np.where(t < BASE + 3 * D, 100.0, np.where(t < BASE + 4 * D, 110.0, 95.0))


@pytest.fixture
def avantis_env(monkeypatch, tmp_path):
    """Writes the wallet's history, mocks its bars, and returns avantis_curve's (stats, counts, start, end)."""
    def use(trades):
        (tmp_path / f"wallet_{ADDR}.json").write_text(json.dumps({"address": ADDR, "count": len(trades), "trades": trades}))
        monkeypatch.setattr(M.vt, "_symbols", lambda: {1: "BTC/USD", 2: "ETH/USD"})
        monkeypatch.setattr(M, "bars", lambda pid, start, end: make_bars(M.TFS, BASE, BASE + 8 * D, price_path))
        return M.avantis_curve(ADDR)
    return use


def test_avantis_curve_stats_counts_and_dates(avantis_env):
    stats, counts, start, end = avantis_env([trade(BASE + 5 * D + 6 * 3600, 1000, 100.0)])
    assert stats == {"trades": 1, "short_share": 1.0}
    assert counts["BTC/USD"] == 1
    assert start == "2025-01-02"  # midnight of the first open


def test_avantis_curve_partial_close_is_one_trade(avantis_env):
    t1, t2 = BASE + 4 * D + 6 * 3600, BASE + 5 * D + 6 * 3600
    stats, counts, *_ = avantis_env([trade(t1, 600, 60.0), trade(t2, 400, 40.0)])
    assert stats["trades"] == 1 and counts["BTC/USD"] == 1


def test_avantis_curve_counts_orders_busiest_first(avantis_env):
    t = BASE + 5 * D + 6 * 3600
    rows = [trade(t, 100, 1.0, symbol="ETH/USD", pid=2, index=i) for i in range(3)] + [trade(t, 100, 1.0, buy=True)]
    stats, counts, *_ = avantis_env(rows)
    assert list(counts.index) == ["ETH/USD", "BTC/USD"] and list(counts) == [3, 1]
    assert stats["trades"] == 4 and stats["short_share"] == 0.75


# ---- S8 fit and settings -----------------------------------------------------

def test_settings_scale_risk_with_leverage():
    s = M.settings(0.01)
    assert s.scale_risk_with_leverage is True
    assert s.risk_per_trade == 0.01 and s.starting_balance == ACCOUNT


def test_fit_stops_after_one_pass_on_an_exact_fit(monkeypatch):
    lo = ts(LO)
    grid = pd.Index(np.arange(lo, lo + 40 * D, 3600, dtype="int64"))
    calls = itertools.count()
    monkeypatch.setattr(M, "backtest", lambda p, markets, costs: (next(calls), avbt_cpp.Result(
        np.full(len(grid), float(ACCOUNT)), TF.Hour1, (grid - 3600).to_numpy()))[1])
    monkeypatch.setattr(M, "effective", lambda p: next(calls))  # no cache: every mse is a backtest
    monkeypatch.setattr(M, "KNOBS", {"hold": [1, 2], "leverage": [1, 2], "risk": [0.01, 0.02]})
    best, mse = M.fit(None, None, np.zeros(len(grid)), grid, dict(M.START), restarts=0, passes=6, workers=2)
    assert mse == 0
    assert next(calls) <= 2 * (1 + 3 * 4)  # one pass of 3 pairs x 4 tries, plus the start; not six


# ---- S9 fills in one millisecond ---------------------------------------------

def scrambled_buy(t_ms, px=100.0):
    """One taker buy of 10 as four fills in one ms; by tid, startPositions read 6, 0, 3, 1."""
    chain = [(0, 1), (1, 2), (3, 3), (6, 4)]  # (startPosition, sz)
    tids = {6: 10, 0: 11, 3: 12, 1: 13}
    return [fill("BTC", "B", px, sz, t_ms, before, tids[before]) for before, sz in chain]


def step_price(at):
    """100 until `at`, then 110."""
    return lambda t: np.where(t < at, 100.0, 110.0)


def test_hl_fills_one_ms_follows_the_start_position_chain_not_tid(monkeypatch, hl):
    fills = scrambled_buy(5000)
    monkeypatch.setattr(M, "hl_post", HL([fills[3], fills[0], fills[2], fills[1]]))
    out = M.hl_fills("0xabc")
    assert [float(f["startPosition"]) for f in out] == [0, 1, 3, 6]


def test_hl_curve_scrambled_one_ms_group_has_no_gap_trades(hl_curve_env):
    lo = ts(LO)
    fills = scrambled_buy((lo + D + 1800) * 1000)
    fills = [fills[3], fills[0], fills[2], fills[1]]
    fills.append(fill("BTC", "A", 110, 10, (lo + 2 * D) * 1000, 10, 20, "Close Long"))
    pnl, stats, *_ = hl_curve_env(fills)
    assert stats["pnl"] == pytest.approx(100 - FEE * (1000 + 1100))
    assert stats["trades"] == 1 and pnl.iloc[0] == 0


def test_hl_curve_position_held_from_before_lo_built_by_a_scrambled_group(hl_curve_env):
    lo = ts(LO)
    fills = scrambled_buy((lo - 10 * D) * 1000)
    fills = [fills[3], fills[0], fills[2], fills[1]]
    pnl, stats, *_ = hl_curve_env(fills, price=step_price(lo + 5 * D))
    assert pnl.iloc[0] == 0
    assert pnl.loc[lo + 4 * D] == pytest.approx(0)
    # 10 held, not 4: the whole group's position rides the move.
    assert pnl.iloc[-1] == pytest.approx(10 * 10)
    assert stats["pnl"] == pytest.approx(100) and stats["trades"] == 0


def test_hl_curve_mixed_sides_in_one_ms_chain_by_start_position(hl_curve_env):
    lo = ts(LO)
    t = (lo + D + 1800) * 1000
    # By tid the sell (startPosition 5) comes first; the chain says buy 5 then sell 5.
    fills = [fill("BTC", "A", 100, 5, t, 5, 1, "Close Long"), fill("BTC", "B", 100, 5, t, 0, 2)]
    pnl, stats, *_ = hl_curve_env(fills, price=step_price(lo + 5 * D))
    assert stats["pnl"] == pytest.approx(-FEE * 1000)
    assert pnl.iloc[-1] == pytest.approx(-FEE * 1000)
    assert stats["trades"] == 1 and stats["short_share"] == 0.0


def test_hl_curve_float_noise_in_start_position_is_not_a_gap(hl_curve_env):
    lo = ts(LO)
    _, stats, *_ = hl_curve_env([
        fill("ETH", "B", 100, 1, (lo + D + 1800) * 1000, 0, 1),
        fill("ETH", "A", 110, 1, (lo + 2 * D) * 1000, "1.00000000000001", 2, "Close Long")])
    assert stats["pnl"] == pytest.approx(10 - FEE * 210, abs=1e-9)
    assert stats["trades"] == 1


# ---- S10 a coin whose candles fail -------------------------------------------

def test_hl_curve_candle_server_error_values_the_coin_at_fill_prices(monkeypatch, hl):
    lo = ts(LO)
    fills = [fill("#0", "B", 100, 1, (lo + 3600) * 1000, 0, 1, "Settlement")]  # the curve starts on LO
    server = HL(fills, price=50.0, error=("#0",))
    monkeypatch.setattr(M, "hl_post", server)
    monkeypatch.setattr(M, "hl_fills", lambda address: fills)
    _, stats, counts, *_ = M.hl_curve("0xabc", LO, HI)
    assert stats["pnl"] == pytest.approx(-FEE * 100)  # held at its fill price, not at 50
    assert counts["#0"] == 1
    n = len(server.calls)
    assert M.hl_bars("#0", LO, HI) is None
    assert len(server.calls) == n


# ---- S11 tid 0 ----------------------------------------------------------------

def test_hl_fills_keeps_two_distinct_fills_with_tid_zero(monkeypatch, hl):
    fills = [fill("BTC", "B", 100, 1, 1000, 0, 0), fill("BTC", "B", 101, 2, 2000, 1, 0)]
    monkeypatch.setattr(M, "hl_post", HL(fills))
    out = M.hl_fills("0xabc")
    assert [f["px"] for f in out] == ["100", "101"]


def test_hl_fills_tid_zero_fill_repeated_on_two_pages_is_kept_once(monkeypatch, hl):
    fills = perp_fills(list(range(1, 2000))) + [fill("BTC", "B", 100, 1, 2000, 0, 0)]
    monkeypatch.setattr(M, "hl_post", HL(fills))  # page 1 ends on the tid-0 fill; page 2 repeats it
    out = M.hl_fills("0xabc")
    assert len(out) == 2000 and sum(f["tid"] == 0 for f in out) == 1


# ---- S12 replay ---------------------------------------------------------------

def test_replay_uses_the_saved_costs_and_settings(monkeypatch, tmp_path, capsys):
    got = {}

    def run(name, params, markets, costs, settings):
        got.update(costs=costs, settings=settings)
        return avbt_cpp.Result(np.array([1.0, 1.0]), TF.Hour1, np.array([0, 3600], dtype="int64"))

    monkeypatch.setattr(M, "load_markets", lambda symbols, start, end: "markets")
    monkeypatch.setattr(M.avbt_cpp, "run", run)
    saved = {"venue": "avantis", "avbt_version": avbt_cpp.version, "strategy": "mimic", "params": dict(M.START),
             "markets": ["BTC/USD"], "start": LO, "end": HI,
             "costs": {"open_fee": 0.0005, "close_fee": 0.0005},
             "settings": {"starting_balance": ACCOUNT, "risk_per_trade": 0.01, "hard_stop": 1.0},
             "mimic": {"sharpe": 0.0, "max_drawdown": 0.0, "return": 0.0, "trades": 0}}
    path = tmp_path / "mimic_0xabc.json"
    path.write_text(json.dumps(saved))
    monkeypatch.setattr(sys, "argv", ["mimic.py", "--replay", str(path)])
    M.main()
    assert got, "replay ran no backtest"
    assert "deployed" in capsys.readouterr().out  # no "fit": the save warns it was fitted on the deployed curve
    assert got["costs"]["BTC/USD"].open_fee == 0.0005 and got["costs"]["BTC/USD"].close_fee == 0.0005
    assert got["settings"].scale_risk_with_leverage is False
    assert got["settings"].risk_per_trade == 0.01 and got["settings"].starting_balance == ACCOUNT


# ---- S13 hl_bars requests -----------------------------------------------------

def candle_calls(server):
    return [c["req"] for c in server.calls if c["type"] == "candleSnapshot"]


def test_hl_bars_asks_for_no_bar_at_hi(monkeypatch, hl):
    server = HL(price=100.0)
    monkeypatch.setattr(M, "hl_post", server)
    b = M.hl_bars("BTC", LO, HI)
    reqs = candle_calls(server)
    assert reqs and all(r["endTime"] < ts(HI) * 1000 for r in reqs)
    assert len(b[0].ts) == 30 * 24


def test_hl_bars_newer_candle_replaces_the_cached_one(monkeypatch, hl):
    server = HL(price=100.0)
    monkeypatch.setattr(M, "hl_post", server)
    mid = "2025-01-25"
    M.hl_bars("BTC", LO, mid)
    server.price, server.overlap = 7.0, 1  # the next fetch repeats the last cached bar, revised
    b = M.hl_bars("BTC", LO, HI)
    i = int(np.searchsorted(b[0].ts, ts(mid) - 3600))
    assert b[0].ts[i] == ts(mid) - 3600 and b[0].close[i] == 7.0
    assert b[0].close[i - 1] == 100.0


def test_hl_bars_wide_range_is_fetched_in_chunks_of_at_most_5000_bars(monkeypatch, hl):
    server = HL(price=100.0)
    monkeypatch.setattr(M, "hl_post", server)
    start = "2024-01-01"  # 406 days: 9744 hourly bars
    b = M.hl_bars("BTC", start, HI)
    hours = [r for r in candle_calls(server) if r["interval"] == "1h"]
    assert len(hours) >= 2
    assert all((r["endTime"] - r["startTime"]) <= 5000 * 3600 * 1000 for r in hours)
    assert b[0].ts[0] == ts(start) and len(b[0].ts) == 406 * 24
    assert np.all(b[0].close == 100.0)


# ---- S14 avantis_curve rows and grid -------------------------------------------

def test_avantis_curve_skips_a_row_without_position_size(avantis_env, capsys):
    t = BASE + 5 * D + 6 * 3600
    bad = trade(t, 100, 1.0, symbol="ETH/USD", pid=2, index=1)
    bad["positionSize"] = None
    stats, counts, *_ = avantis_env([trade(t, 1000, 100.0), bad])
    assert stats["trades"] == 1 and list(counts.index) == ["BTC/USD"]
    assert "1 rows without size" in capsys.readouterr().out


def test_avantis_curve_grid_ends_before_the_midnight_after_the_last_close(avantis_env):
    *_, start, end = avantis_env([trade(BASE + 5 * D + 6 * 3600, 1000, 100.0)])
    assert end == "2025-01-07"
    assert M.hour_grid(start, end)[-1] == BASE + 6 * D - 3600


# ---- S16 a gap at the first inside fill ----------------------------------------

def gap_fills(lo):
    """Buy 1 ten days before lo; inside, sell 2 from a start of 2: a buy of 1 is missing."""
    return [fill("ETH", "B", 100, 1, (lo - 10 * D) * 1000, 0, 1),
            fill("ETH", "A", 100, 2, (lo + 5 * D) * 1000, 2, 2, "Close Long")]


def test_hl_curve_gap_at_the_first_inside_fill_does_not_shift_the_curve(hl_curve_env):
    lo = ts(LO)
    pnl, stats, *_ = hl_curve_env(gap_fills(lo))
    assert pnl.iloc[0] == 0
    assert np.allclose(pnl.loc[: lo + 5 * D - 3600].to_numpy(), 0)
    # The missing buy of 1 and the sell of 2 net to a sell of 1 at 100: only its fee is lost.
    assert stats["pnl"] == pytest.approx(-FEE * 100)


def test_hl_curve_gap_share_is_the_missing_notional_over_the_traded_notional(hl_curve_env):
    lo = ts(LO)
    _, stats, *_ = hl_curve_env(gap_fills(lo))
    assert stats["gap_share"] == pytest.approx(100 / (200 + 100))
    _, stats, *_ = hl_curve_env([fill("ETH", "B", 100, 1, (lo + D) * 1000, 0, 1)])
    assert stats["gap_share"] == 0.0


def test_hl_curve_gap_share_counts_inside_fills_of_every_coin(hl_curve_env):
    lo = ts(LO)
    fills = gap_fills(lo) + [fill("BTC", "A", 50, 1, (lo - D) * 1000, 3, 3, "Close Long"),  # outside: not counted
                             fill("BTC", "B", 50, 4, (lo + 2 * D) * 1000, 2, 4)]
    _, stats, *_ = hl_curve_env(fills)
    assert stats["gap_share"] == pytest.approx(100 / (300 + 200))


@pytest.fixture
def setup_env(monkeypatch, hl):
    def use(gap_share):
        stats = {"trades": 1, "short_share": 0.0, "pnl": 1.0, "gap_share": gap_share}
        pnl = pd.Series(0.0, index=M.hour_grid(LO, HI))
        monkeypatch.setattr(M, "hl_curve", lambda address, since: (pnl, stats, pd.Series({"BTC": 3}), LO, HI))
        monkeypatch.setattr(M, "load_markets", lambda symbols, start, end: "markets")
        one = [fill("BTC", "B", 100, 1, (ts(LO) + 3600) * 1000, 0, 1)]  # so the return is defined
        monkeypatch.setattr(M, "hl_fills", lambda address: one)
        monkeypatch.setattr(M, "hl_funding", lambda address, since, end=None: [])
        monkeypatch.setattr(M, "hl_leverage", lambda address, coins: {c: 5.0 for c in coins})
        monkeypatch.setattr(M, "hl_bars", lambda coin, start, end: None)
        monkeypatch.setattr(M, "hl_portfolio", lambda address: {})
        return M.setup("0xabc", 8)
    return use


def test_setup_warns_when_the_api_is_missing_fills(setup_env, capsys):
    w = setup_env(0.05)
    out = capsys.readouterr().out
    assert "warning" in out and "missing fills" in out and "5%" in out
    assert w["wallet"]["gap_share"] == 0.05


def test_setup_is_quiet_when_few_fills_are_missing(setup_env, capsys):
    setup_env(0.005)
    assert "warning" not in capsys.readouterr().out


def test_setup_prints_only_the_total_pnl_and_returns_flat_wallet_scores(setup_env, capsys):
    w = setup_env(0.0)
    out = capsys.readouterr().out
    assert "total pnl $1" in out
    assert "whole account" not in out and "net deposits" not in out and "drift" not in out
    assert set(w["wallet"]) == STATS_KEYS | set(M.SCORE_KEYS)
    assert w["wallet"]["final"] == pytest.approx(w["target"].dropna().iloc[-1])
    assert np.isnan(w["wallet"]["rmse"])


# ---- S17 outcome markets -------------------------------------------------------

def outcome_fills(lo):
    """Buy 10 at 0.4 twice as "Buy", settled at 1.0."""
    return [fill("#7790", "B", 0.4, 10, (lo + D) * 1000, 0, 1, "Buy"),
            fill("#7790", "B", 0.4, 10, (lo + 2 * D) * 1000, 10, 2, "Buy"),
            fill("#7790", "A", 1.0, 20, (lo + 3 * D) * 1000, 20, 3, "Settlement")]


def test_hl_spot_keeps_every_fill_of_an_outcome_market():
    assert not any(M.hl_spot(f) for f in outcome_fills(ts(LO)))
    assert M.hl_spot(fill("HYPE", "B", 1, 1, 1000, 0, 4, "Buy"))
    assert M.hl_spot(fill("@1", "B", 1, 1, 1000, 0, 4, "Buy"))
    assert M.hl_spot(fill("PURR/USDC", "A", 1, 1, 1000, 0, 4, "Sell"))
    assert M.hl_spot(fill("DOGE", "A", 1, 1, 1000, 0, 4, "Spot Dust Conversion"))


def test_hl_fills_keeps_outcome_market_buys(monkeypatch, hl):
    monkeypatch.setattr(M, "hl_post", HL(outcome_fills(ts(LO))))
    assert [f["tid"] for f in M.hl_fills("0xabc")] == [1, 2, 3]


def test_hl_fills_cache_from_before_outcome_markets_is_refetched_in_full(monkeypatch, hl, tmp_path):
    fills = outcome_fills(ts(LO))
    server = HL(fills)
    monkeypatch.setattr(M, "hl_post", server)
    old = {"fetched": int(time.time() * 1000), "fills": [{k: fills[2][k] for k in M.HL_KEEP}]}  # fresh, no Buy fills
    (tmp_path / "wallet_hl_fills_0xabc.json").write_text(json.dumps(old))
    assert [f["tid"] for f in M.hl_fills("0xabc")] == [1, 2, 3]
    assert server.calls[0]["startTime"] == 0
    n = len(server.calls)
    M.hl_fills("0xabc")
    assert len(server.calls) == n


def test_hl_curve_outcome_market_pnl_is_the_settlement_minus_the_cost(monkeypatch, hl):
    lo = ts(LO)
    fills = outcome_fills(lo)
    monkeypatch.setattr(M, "hl_fills", lambda address: fills)
    monkeypatch.setattr(M, "hl_bars", lambda coin, start, end: None)  # no candles: fill prices
    pnl, stats, counts, *_ = M.hl_curve("0xabc", LO, HI)
    assert stats["pnl"] == pytest.approx(20 - 8 - FEE * (4 + 4 + 20))
    assert stats["trades"] == 1 and counts["#7790"] == 3
    assert pnl.iloc[-1] == pytest.approx(stats["pnl"])


# ---- S18 hl_chain --------------------------------------------------------------

def starts(fills):
    return [float(f["startPosition"]) for f in fills]


def chained(fills, prev):
    for f in fills:
        if prev is not None and not M.near(float(f["startPosition"]), prev):
            return False
        prev = float(f["startPosition"]) + M.hl_signed(f)
    return True


def test_hl_chain_starts_from_the_one_unended_start_even_when_prev_matches_another():
    g = [fill("X", "B", 1, 2, 1000, 11, 2), fill("X", "B", 1, 3, 1000, 13, 3), fill("X", "B", 1, 1, 1000, 10, 1)]
    assert starts(M.hl_chain(g, 11.0)) == [10, 11, 13]
    assert starts(M.hl_chain(g, 10.0)) == [10, 11, 13]
    assert starts(M.hl_chain(g, None)) == [10, 11, 13]


def test_hl_chain_prefers_prev_when_several_starts_are_unended():
    g = [fill("X", "B", 1, 1, 1000, 5, 1), fill("X", "B", 1, 1, 1000, 20, 2), fill("X", "B", 1, 1, 1000, 6, 3)]
    assert starts(M.hl_chain(g, 20.0))[0] == 20
    assert starts(M.hl_chain(g, 5.0)) == [5, 6, 20]


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_hl_chain_repeated_starts_choose_the_order_that_completes(order):
    g = [fill("X", "B", 1, 1, 1000, 0, 1), fill("X", "A", 1, 1, 1000, 1, 2), fill("X", "B", 1, 2, 1000, 0, 3)]
    out = M.hl_chain([g[i] for i in order], 0.0)
    assert chained(out, 0.0)
    assert [f["tid"] for f in out] == [1, 2, 3]


@pytest.mark.parametrize("order", list(itertools.permutations(range(5))))
def test_hl_chain_repeated_starts_deeper_than_one_lookahead(order):
    # 0 -> 1 -> 2 -> 1 -> 5 -> 6: at 1, the buy of 4 leads on to the buy at 5 but strands the buy of 1.
    g = [fill("X", "B", 1, 1, 1000, 0, 1), fill("X", "B", 1, 1, 1000, 1, 2), fill("X", "A", 1, 1, 1000, 2, 3),
         fill("X", "B", 1, 4, 1000, 1, 4), fill("X", "B", 1, 1, 1000, 5, 5)]
    out = M.hl_chain([g[i] for i in order], 0.0)
    assert chained(out, 0.0)
    assert [f["tid"] for f in out] == [1, 2, 3, 4, 5]


def test_hl_chain_with_no_complete_order_still_uses_every_fill():
    g = [fill("X", "B", 1, 1, 1000, 5, 1), fill("X", "A", 1, 1, 1000, 5, 2), fill("X", "B", 1, 1, 1000, 9, 3)]
    out = M.hl_chain(g, 5.0)
    assert sorted(f["tid"] for f in out) == [1, 2, 3] and out[0]["startPosition"] == "5"


def test_hl_chain_falls_back_to_the_nearest_start_when_nothing_matches():
    g = [fill("X", "B", 1, 1, 1000, 10, 1), fill("X", "B", 1, 1, 1000, 12, 2), fill("X", "B", 1, 1, 1000, 14.5, 3)]
    assert starts(M.hl_chain(g, 10.0)) == [10, 12, 14.5]


def test_hl_chain_of_two_thousand_fills_is_fast():
    rng = np.random.default_rng(7)
    pos, g = 0.0, []
    for i in range(2000):
        side, sz = ("B", "A")[rng.integers(2)], float(rng.integers(1, 20)) / 4  # exact in binary: no drift
        g.append(fill("X", side, 1, sz, 1000, pos, i + 1))
        pos += sz if side == "B" else -sz
    g = [g[i] for i in rng.permutation(2000)]
    t0 = time.perf_counter()
    out = M.hl_chain(g, 0.0)
    assert time.perf_counter() - t0 < 1.0
    assert chained(out, 0.0) and len(out) == 2000


# ---- S15 use_venue -------------------------------------------------------------

def test_use_venue_avantis_restores_the_three_timeframes(hl):
    assert len(M.TFS) == 2
    M.use_venue("avantis")
    assert M.TFS == (TF.Min15, TF.Hour1, TF.Hour4)
    assert len(M.KNOBS["timeframe"]) == 3 and set(M.TF_NAMES.values()) == set(M.TFS)
    assert M.VENUE == "avantis"


# ---- return on peak own capital (issue #16) ------------------------------------

from types import SimpleNamespace as NS  # noqa: E402

W = M.W
CLOSES4 = [100, 100, 110, 99]  # bar k opens at t0 + k h; its close is known at t0 + (k + 1) h


def mimic_env(t0, closes, trades=(), open_positions=()):
    """Hour1 bars closing at `closes` from t0, a Result-like object, and the hourly grid."""
    n = len(closes)
    opens = t0 + 3600 * np.arange(n, dtype="int64")
    price = lambda t: np.asarray(closes, float)[np.clip(np.searchsorted(opens, t, side="right") - 1, 0, n - 1)]
    markets = avbt_cpp.Markets([avbt_cpp.Market("BTC", make_bars(M.TFS, t0, t0 + 3600 * n, price))])
    r = NS(equity=np.full(n, ACCOUNT), clock=opens, timeframe=TF.Hour1, trades=list(trades),
           open_positions=list(open_positions))
    return r, markets, pd.Index(opens + 3600)


def mtrade(t0, entry_bar, exit_bar, size, entry, exit, side="Long", lev=2.0, fees=1.0, holding=0.5):
    """A Trade-like object; result is the price PnL less fees and holding costs."""
    s = 1 if side == "Long" else -1
    return NS(instrument="BTC", side=getattr(avbt_cpp.Side, side), entry_time=t0 + 3600 * entry_bar,
              exit_time=t0 + 3600 * exit_bar, entry_price=float(entry), exit_price=float(exit), size=float(size),
              leverage=lev, fees=fees, holding_costs=holding, result=s * size * (exit - entry) - fees - holding)


def test_trade_becomes_an_avantis_row():
    t0 = ts(LO)
    r, _, grid = mimic_env(t0, CLOSES4, [mtrade(t0, 1, 3, 10, 100, 99)])
    [row] = M.mimic_rows(r, grid)
    assert row["symbol"] == "BTC" and row["buy"] is True and row["leverage"] == 2.0
    assert row["openPrice"] == 100 and row["openedAt"] == t0 + 2 * 3600  # the fill shows at the bar's close
    assert pd.Timestamp(row["time"]).timestamp() == t0 + 4 * 3600
    assert row["positionSize"] == pytest.approx(500)  # 10 x 100 / 2
    assert row["gross"] == pytest.approx(-10)  # result + fees + holding costs
    assert row["closingFee"] == 1.0 and row["funding"] == 0.5


@pytest.mark.parametrize("side, want", [
    # Hours t0+1h..t0+4h: before the open; open at 100; 110 with half the funding; closed at 99.
    ("Long", [np.nan, 0.0, (100 - 0.25) / 500, (-10 - 1.5) / 500]),
    ("Short", [np.nan, 0.0, (-100 - 0.25) / 500, (10 - 1.5) / 500]),
])
def test_mimic_own_long_and_short_by_hand(hl, side, want):
    t0 = ts(LO)
    r, markets, grid = mimic_env(t0, CLOSES4, [mtrade(t0, 1, 3, 10, 100, 99, side)])
    [row] = M.mimic_rows(r, grid)
    assert row["buy"] is (side == "Long") and row["positionSize"] == pytest.approx(500)
    rho = M.mimic_own(r, markets, grid)
    assert isinstance(rho, pd.Series) and list(rho.index) == list(grid)
    np.testing.assert_allclose(rho.to_numpy(), want)


def test_open_position_at_the_end_is_marked_not_realized(hl):
    t0 = ts(LO)
    pos = NS(instrument="BTC", side=avbt_cpp.Side.Long, entry_price=100.0, size=10.0, entry_time=t0 + 3600)
    r, markets, grid = mimic_env(t0, CLOSES4, open_positions=[pos])
    [row] = M.mimic_rows(r, grid, 4)
    assert pd.Timestamp(row["time"]).timestamp() == grid[-1] + 3600  # after the grid: never realized
    assert row["gross"] == 0 and row["closingFee"] == 0 and row["funding"] == 0
    assert row["positionSize"] == pytest.approx(250)
    book = W.ledger("avantis", [row], M.market_prices(markets), times=grid)
    # margin 10 x 100 / 4 = 250; U = 250 x 4 x (99 / 100 - 1) = -10
    assert (book["realized"] == 0).all()
    assert book["unrealized"].iloc[-1] == pytest.approx(-10)
    assert M.mimic_own(r, markets, grid, 4).iloc[-1] == pytest.approx(-10 / 250)


def test_market_prices_are_the_hour_closes_known_at_the_bar_end(hl):
    t0 = ts(LO)
    _, markets, grid = mimic_env(t0, CLOSES4)
    s = M.market_prices(markets)["BTC"]
    assert list(s.index) == list(grid) and list(s) == CLOSES4


def test_identical_trades_give_mse_zero(hl):
    t0 = ts(LO)
    r, markets, grid = mimic_env(t0, CLOSES4, [mtrade(t0, 1, 3, 10, 100, 99, fees=0.0, holding=0.0)])
    # The same position as an Avantis wallet's close: 500 margin at 2x, open at 100, closed at 99.
    row = {"symbol": "BTC", "buy": True, "leverage": 2, "openPrice": 100, "openedAt": t0 + 7200,
           "time": "2025-01-11T04:00:00Z", "positionSize": 500, "gross": -10, "closingFee": 0, "funding": 0}
    target = W.ledger("avantis", [row], M.market_prices(markets), times=grid)["return"]
    assert np.isnan(target.iloc[0]) and target.iloc[-1] == pytest.approx(-0.02)
    assert M.own_mse(M.mimic_own(r, markets, grid), target.to_numpy()) == pytest.approx(0, abs=1e-12)


def test_own_mse_counts_the_mimic_as_zero_before_its_first_open_and_skips_hours_without_a_target():
    mimic = [np.nan, np.nan, 0.1, 0.2]
    target = [np.nan, 0.1, 0.1, 0.1]
    assert M.own_mse(mimic, target) == pytest.approx((0.01 + 0 + 0.01) / 3)
    assert np.isnan(M.own_mse(mimic, [np.nan] * 4))


def book(rho, pnl=None, capital=None, t0=None):
    """A ledger-like table on hourly times from t0 (default LO): return, and pnl and capital when given."""
    n = len(rho)
    t0 = ts(LO) if t0 is None else t0
    return pd.DataFrame({"time": t0 + 3600 * np.arange(n, dtype="int64"), "return": np.asarray(rho, float),
                         "pnl": np.zeros(n) if pnl is None else np.asarray(pnl, float),
                         "capital": np.ones(n) if capital is None else np.asarray(capital, float)})


def test_own_scores_final_peak_lowest_skip_undefined_hours():
    s = M.own_scores(book([np.nan, 0.1, -0.3, 0.5, 0.2]))
    assert set(s) == set(M.SCORE_KEYS)
    assert (s["final"], s["peak"], s["lowest"]) == (0.2, 0.5, -0.3)
    assert np.isnan(s["rmse"]) and np.isnan(s["sharpe"])  # no target; under 30 days


def test_own_scores_drawdown_is_the_largest_fraction_not_the_largest_dollar_fall():
    # 1 + return: 1, 0.5, 10, 8: falls of 50% (0.5) and 20% (2).
    assert M.own_scores(book([0, -0.5, 9, 7]))["max_drawdown"] == pytest.approx(0.5)
    assert M.own_scores(book([0.1, 0.2, 0.3]))["max_drawdown"] == 0


def test_own_scores_rmse_counts_the_mimic_as_zero_before_its_first_open():
    s = M.own_scores(book([np.nan, 0.1, -0.3, 0.5, 0.2]), [np.nan, 0.1, -0.3, 0.5, 0.0])
    assert s["rmse"] == pytest.approx((0.04 / 4) ** 0.5)
    s = M.own_scores(book([np.nan, np.nan, 0.2]), [0.1, 0.1, 0.2])
    assert s["rmse"] == pytest.approx(((0.01 + 0.01) / 3) ** 0.5)


@pytest.mark.parametrize("empty", [None, book([])])
def test_own_scores_without_a_book_are_nan_but_the_rmse(empty):
    s = M.own_scores(empty)
    assert set(s) == set(M.SCORE_KEYS) and all(np.isnan(v) for v in s.values())
    s = M.own_scores(empty, np.array([0.1, np.nan, -0.2]))
    assert s["rmse"] == pytest.approx(((0.01 + 0.04) / 2) ** 0.5)
    assert np.isnan(s["final"]) and np.isnan(s["sharpe"])


def long_book(days, capital=None):
    """Hourly from 05:00 on LO for `days` days and 12 hours; pnl a wobbling climb, capital 1000."""
    t0 = ts(LO) + 5 * 3600
    n = days * 24 + 12
    k = np.arange(n)
    pnl = 20 * np.sin(k / 5.0) + 0.3 * k
    cap = np.full(n, 1000.0) if capital is None else capital(t0 + 3600 * k)
    return book(pnl / 1000, pnl, cap, t0)


def by_hand(b):
    day = b[b["time"] % D == 0]
    pnl, cap = day["pnl"].to_numpy(), day["capital"].to_numpy()
    r = np.diff(pnl)[cap[1:] > 0] / cap[1:][cap[1:] > 0]
    return r.mean() / r.std(ddof=1) * np.sqrt(365)


def test_daily_sharpe_matches_a_hand_count_at_midnights():
    b = long_book(60)
    naive = b["pnl"].to_numpy()[::24]  # every 24th hour from 05:00: not midnights
    rn = np.diff(naive) / 1000
    want = by_hand(b)
    assert not np.isclose(rn.mean() / rn.std(ddof=1) * np.sqrt(365), want)  # the fixture discriminates
    assert M.daily_sharpe(b) == pytest.approx(want, rel=1e-9)
    assert M.own_scores(b)["sharpe"] == M.daily_sharpe(b)


def test_daily_sharpe_under_30_days_or_with_no_spread_is_nan():
    assert np.isnan(M.daily_sharpe(long_book(20)))
    flat = long_book(60)
    flat["pnl"] = (flat["time"] - flat["time"].iloc[0]) // D  # the same gain every day, exactly
    assert np.isnan(M.daily_sharpe(flat))


def test_daily_sharpe_leaves_out_days_without_capital():
    first = ts(LO) + 10 * D
    b = long_book(60, lambda t: np.where(t < first, 0.0, 1000.0))
    day = b[b["time"] % D == 0]
    r = np.diff(day["pnl"].to_numpy()) / 1000
    every = r.mean() / r.std(ddof=1) * np.sqrt(365)
    want = by_hand(b)
    assert not np.isclose(every, want)  # the skipped days matter
    assert M.daily_sharpe(b) == pytest.approx(want, rel=1e-9)


def test_doubling_the_leverage_doubles_the_return(hl):
    t0 = ts(LO)
    one = mimic_env(t0, CLOSES4, [mtrade(t0, 1, 3, 10, 100, 99, lev=1.0)])
    two = mimic_env(t0, CLOSES4, [mtrade(t0, 1, 3, 10, 100, 99, lev=2.0)])
    a, b = M.mimic_own(*one), M.mimic_own(*two)
    # 1x: margin 1000; t0+3h U = 100 less half the funding; closed -10 - 1.5.
    np.testing.assert_allclose(a.to_numpy()[1:], [0, 99.75 / 1000, -11.5 / 1000])
    np.testing.assert_allclose(b.to_numpy()[1:], 2 * a.to_numpy()[1:])


def sine_markets(t0, n=24 * 40):
    price = lambda t: 100 + 5 * np.sin((np.asarray(t) - t0) / 3600 / 7.0)
    markets = avbt_cpp.Markets([avbt_cpp.Market("BTC", make_bars(M.TFS, t0, t0 + 3600 * n, price))])
    return markets, M.hour_grid(LO, pd.Timestamp(t0 + 3600 * n, unit="s").strftime("%Y-%m-%d"))


P_LONG = {**M.START, "side": 1, "hold": 4, "stop_atrs": 8, "tp_atrs": 12, "timeframe": "1 hour"}


def test_fit_moves_leverage_toward_the_wallets(monkeypatch, hl):
    markets, grid = sine_markets(ts(LO))
    costs = {"BTC": avbt_cpp.Costs(FEE, FEE, spread=0)}
    target = M.mimic_own(M.backtest({**P_LONG, "leverage": 10}, markets, costs), markets, grid, 10)
    monkeypatch.setattr(M, "KNOBS", {"leverage": [1, 3, 10, 30], "hold": [4]})
    best, mse = M.fit(markets, costs, target, grid, {**P_LONG, "leverage": 1}, restarts=0, passes=2, workers=1)
    assert best["leverage"] == 10 and mse == pytest.approx(0, abs=1e-12)


def test_knobs_hold_leverage_and_not_risk():
    assert "risk" not in M.KNOBS and "risk" not in M.START
    assert M.START["leverage"] in M.KNOBS["leverage"]
    assert min(M.KNOBS["leverage"]) >= 1 and max(M.KNOBS["leverage"]) <= 100


def test_backtest_fixes_risk_and_scale_and_takes_the_leverage(monkeypatch):
    got = {}

    def run(name, params, markets, costs, settings):
        got.update(name=name, params=dict(params), settings=settings)
        return avbt_cpp.Result(np.array([1.0, 1.0]), TF.Hour1, np.array([0, 3600], dtype="int64"))
    monkeypatch.setattr(M.avbt_cpp, "run", run)
    M.backtest({**M.START, "leverage": 15}, "markets", "costs")
    assert got["name"] == "mimic" and got["params"]["leverage"] == 15
    assert got["settings"].risk_per_trade == 0.002
    assert got["settings"].scale_risk_with_leverage is True
    assert got["settings"].starting_balance == ACCOUNT


def test_score_sets_of_a_new_save_pass_through():
    new = {**dict.fromkeys(M.SCORE_KEYS, 0.1), "trades": 4}
    assert M.score_sets(new) is new


def test_score_sets_of_an_old_save_carry_own_over_flat():
    old = {"own": {"final": 0.25, "peak": 0.5, "lowest": -0.1, "rmse": 0.02},
           "account": {"sharpe": 2.0, "max_drawdown": 0.2, "return": 0.3},
           "deployed": {"sharpe": 1.0, "max_drawdown": 0.1, "return": 0.2},
           "return": 0.3, "sharpe": 2.0, "max_drawdown": 0.2, "trades": 4, "short_share": 0.5}
    s = M.score_sets(old)
    assert set(s) == set(M.SCORE_KEYS) | {"trades", "short_share"}
    assert (s["final"], s["peak"], s["lowest"], s["rmse"]) == (0.25, 0.5, -0.1, 0.02)
    assert np.isnan(s["max_drawdown"]) and np.isnan(s["sharpe"])  # the account's, not own's: dropped
    assert s["trades"] == 4 and s["short_share"] == 0.5


def test_score_sets_of_the_oldest_save_are_nan_but_trades():
    s = M.score_sets({"sharpe": 1.0, "max_drawdown": 0.1, "return": 0.2, "trades": 2})
    assert s["trades"] == 2 and set(s) == set(M.SCORE_KEYS) | {"trades"}
    assert all(np.isnan(s[k]) for k in M.SCORE_KEYS)


# setup builds the wallet's curve from ledger()

STEP = ts(LO) + 2 * D  # prices step from 100 to 110 here


def hl_setup_env(monkeypatch, fills, leverage):
    stats = {"trades": 1, "short_share": 0.0, "pnl": 1.0, "gap_share": 0.0}
    pnl = pd.Series(0.0, index=M.hour_grid(LO, HI))
    counts = pd.Series({f["coin"]: 1 for f in fills})
    price = lambda t: np.where(t < STEP, 100.0, 110.0)
    monkeypatch.setattr(M, "hl_curve", lambda address, since: (pnl, stats, counts, LO, HI))
    monkeypatch.setattr(M, "hl_fills", lambda address: fills)
    monkeypatch.setattr(M, "hl_funding", lambda address, since, end=None: [])
    monkeypatch.setattr(M, "hl_leverage", lambda address, coins: {c: leverage.get(c) for c in coins})
    monkeypatch.setattr(M, "hl_bars", lambda coin, start, end: make_bars(M.TFS, ts(start), ts(end), price))
    monkeypatch.setattr(M, "hl_portfolio", lambda address: {})
    monkeypatch.setattr(M, "load_markets", lambda symbols, start, end: "markets")
    return M.setup("0xabc", 8)


def test_setup_builds_the_hyperliquid_target_with_the_median_leverage_fallback(monkeypatch, hl):
    t = (ts(LO) + D + 1800) * 1000
    fills = [fill(c, "B", 100, 1, t, 0, i + 1) for i, c in enumerate(("BTC", "SOL", "ETH"))]
    w = hl_setup_env(monkeypatch, fills, {"BTC": 5.0, "SOL": 15.0})
    target = w["target"]
    assert list(target.index) == list(M.hour_grid(LO, HI))
    assert target.loc[: ts(LO) + D].isna().all()  # before the first fill
    margin = 100 / 5 + 100 / 15 + 100 / 10  # ETH takes the median of 5 and 15
    assert target.iloc[-1] == pytest.approx(30 / margin)
    assert w["leverage"] == 10.0 and w["capital"] == pytest.approx(margin)
    assert w["book"]["return"].iloc[-1] == pytest.approx(target.iloc[-1])
    assert set(w["wallet"]) == STATS_KEYS | set(M.SCORE_KEYS)
    assert w["wallet"]["final"] == pytest.approx(30 / margin) and w["wallet"]["lowest"] == pytest.approx(0)
    assert all(len(c) == 3 for c in w["checks"])
    names = [n for n, *_ in w["checks"]]
    assert "U >= -margin open" in names and "12 return >= -100%" in names


def test_setup_builds_the_avantis_target_from_its_rows(monkeypatch, avantis_env):
    monkeypatch.setattr(M.W, "board_row", lambda address: None)
    monkeypatch.setattr(M, "load_markets", lambda symbols, start, end: "markets")
    avantis_env([trade(BASE + 5 * D + 6 * 3600, 1000, 100.0)])  # writes the history, mocks the bars
    w = M.setup(ADDR, 8)
    rho = w["target"]
    # Short 1000 margin at 2x from 100, 110 on day 3, closed on day 5 for +100.
    assert rho.loc[BASE + D + 12 * 3600] == 0 and np.isnan(rho.loc[BASE + D + 11 * 3600])
    assert rho.loc[BASE + 3 * D + 3600] == pytest.approx(-0.2)
    assert rho.iloc[-1] == pytest.approx(0.1)
    assert w["leverage"] == 2 and w["capital"] == 1000
    assert set(w["wallet"]) == {"trades", "short_share", *M.SCORE_KEYS}
    assert w["wallet"]["final"] == pytest.approx(0.1) and w["wallet"]["lowest"] == pytest.approx(-0.2)
    assert w["wallet"]["max_drawdown"] == pytest.approx(0.2)


def test_wallet_note_prints_the_return_capital_and_hyperliquid_checks(hl, capsys):
    w = {"target": pd.Series([np.nan, 0.25]), "capital": 1234.0,
         "checks": [("8 P = R + U", True, "fine"), ("U >= -margin open", True, "holds"),
                    ("12 return >= -100%", True, "ok"), ("10 capital never falls", False, "fell")]}
    M.wallet_note(w)
    out = capsys.readouterr().out
    assert "25.0%" in out and "$1,234" in out
    assert "U >= -margin open" in out and "12 return" in out and "10 capital" in out and "8 P" not in out


def own_main(monkeypatch, tmp_path, leverage=7.0):
    markets, grid = sine_markets(ts(LO))
    costs = {"BTC": avbt_cpp.Costs(FEE, FEE, spread=0)}
    target = M.mimic_own(M.backtest({**P_LONG, "leverage": 10}, markets, costs), markets, grid, 10) * 1.1
    rho = target.to_numpy()
    scores = M.own_scores(book(rho, 100 * np.nan_to_num(rho), np.full(len(rho), 100.0), int(grid[0])))
    wallet = {"trades": 3, "short_share": 0.0, "pnl": 10.0, "gap_share": 0.0, **scores}
    end = pd.Timestamp(grid[-1] + 3600, unit="s").strftime("%Y-%m-%d")
    monkeypatch.setattr(M, "setup", lambda address, top: {
        "wallet": wallet, "symbols": ["BTC"], "start": LO, "end": end, "markets": markets,
        "covered_share": 1.0, "target": target, "capital": 100.0, "leverage": leverage, "checks": []})
    got = {}

    def fake_fit(markets, costs, target, grid, first, *a, knobs, **k):
        got.update(first=first, knobs=list(knobs["leverage"]))
        return {**first, "side": 1, "hold": 4, "stop_atrs": 8, "tp_atrs": 12}, 0.5
    monkeypatch.setattr(M, "fit", fake_fit)
    monkeypatch.setattr(M, "load_markets", lambda symbols, start, end: markets)
    monkeypatch.setattr(sys, "argv", ["mimic.py", "--venue", "hyperliquid", "0x" + "ab" * 20])
    M.main()
    path = next(tmp_path.glob("mimic_hl_*.json"))
    return path, got, markets, grid


def test_main_saves_flat_scores_and_the_wallet_curve(monkeypatch, tmp_path, hl):
    path, got, *_ = own_main(monkeypatch, tmp_path)
    saved = json.loads(path.read_text())
    assert saved["fit"] == "own" and "deployed" not in json.dumps(saved)
    assert "wallet_costs" not in saved
    assert got["first"]["leverage"] == 7.0 and 7.0 in got["knobs"] and got["knobs"] == sorted(got["knobs"])
    assert saved["params"]["leverage"] == 7.0
    assert set(saved["mimic"]) == set(M.SCORE_KEYS) | {"trades"}
    assert set(saved["wallet"]) == STATS_KEYS | set(M.SCORE_KEYS)
    assert np.isnan(saved["wallet"]["rmse"]) and saved["mimic"]["rmse"] > 0
    assert np.isfinite(saved["mimic"]["sharpe"]) and saved["mimic"]["trades"] > 0
    z = np.load(M.target_path(path))
    assert set(z.files) == {"t", "rho"}
    rho = M.load_target(path, M.hour_grid(saved["start"], saved["end"])).dropna()
    assert saved["wallet"]["final"] == pytest.approx(rho.iloc[-1])
    assert "risk" not in saved["params"] and saved["settings"]["risk_per_trade"] == 0.002
    assert saved["mse"] == 0.5


def test_main_has_no_fee_or_funding_flags(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["mimic.py", "--fees", "real", "0x" + "ab" * 20])
    with pytest.raises(SystemExit):
        M.main()
    assert "unrecognized" in capsys.readouterr().err


def test_replay_of_a_new_save_reproduces_its_scores(monkeypatch, tmp_path, hl, capsys):
    path, _, markets, grid = own_main(monkeypatch, tmp_path)
    saved = json.loads(path.read_text())
    target = M.load_target(path, grid)
    again = M.mimic_scores(M.rerun(saved, markets), markets, grid, target, saved["params"]["leverage"])
    assert again == pytest.approx(saved["mimic"], nan_ok=True)
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["mimic.py", "--replay", str(path)])
    M.main()
    assert "deployed" not in capsys.readouterr().out


def test_main_leaves_the_module_knobs_alone(monkeypatch, tmp_path, hl):
    before = list(M.KNOBS["leverage"])
    assert 7.0 not in before and 13.0 not in before
    _, one, *_ = own_main(monkeypatch, tmp_path, 7.0)
    _, two, *_ = own_main(monkeypatch, tmp_path, 13.0)
    assert M.KNOBS["leverage"] == before
    assert 7.0 in one["knobs"] and 13.0 not in one["knobs"]
    assert 13.0 in two["knobs"] and 7.0 not in two["knobs"]


def test_replay_without_the_wallet_curve_says_the_rmse_is_lost(monkeypatch, tmp_path, hl, capsys):
    path, *_ = own_main(monkeypatch, tmp_path)
    M.target_path(path).unlink()
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["mimic.py", "--replay", str(path)])
    M.main()
    out = capsys.readouterr().out
    assert "wallet curve" in out and "missing" in out and "RMSE" in out


def test_table_prints_one_flat_row_with_a_dash_for_a_nan_score(capsys):
    nan = float("nan")
    s = {"final": 0.25, "peak": 0.5, "lowest": -0.1, "max_drawdown": 0.2, "sharpe": nan, "rmse": nan, "trades": 3}
    M.table([("wallet", s)])
    row = capsys.readouterr().out.splitlines()[-1]
    assert "nan" not in row
    assert row.split() == ["wallet", "25.0%", "50.0%", "-10.0%", "20.0%", "-", "-", "3"]


def test_table_reads_an_old_save(capsys):
    old = {"own": {"final": 0.25, "peak": 0.5, "lowest": -0.1, "rmse": 0.02},
           "account": {"sharpe": 1.0, "max_drawdown": 0.1, "return": 0.2}, "trades": 3}
    M.table([("saved", old)])
    assert capsys.readouterr().out.splitlines()[-1].split() == ["saved", "25.0%", "50.0%", "-10.0%", "-", "-",
                                                               "2.0%", "3"]


# ---- funding, leverage, the fill limit ---------------------------------------

def funding_item(t_ms, coin, usdc):
    """A userFunding item; usdc is a string as the API sends it."""
    return {"time": t_ms, "hash": "0x0",
            "delta": {"type": "funding", "coin": coin, "usdc": usdc, "szi": "1.0",
                      "fundingRate": "0.0001", "nSamples": None}}


def test_hl_fills_keep_closed_pnl(monkeypatch, hl):
    fs = perp_fills([1000, 2000])
    fs[1]["closedPnl"] = "12.5"
    monkeypatch.setattr(M, "hl_post", HL(fs))
    assert [f["closedPnl"] for f in M.hl_fills("0xabc")] == ["0", "12.5"]


def test_hl_fills_cache_without_closed_pnl_is_refetched_in_full(monkeypatch, hl, tmp_path):
    server = HL(perp_fills([1000, 2000]))
    monkeypatch.setattr(M, "hl_post", server)
    keep = [k for k in M.HL_KEEP if k != "closedPnl"]
    old = {"fetched": int(time.time() * 1000), "keeps": "perps and outcome markets",
           "fills": [{k: f[k] for k in keep} for f in server.fills]}
    M.save_json_gz(tmp_path / "wallet_hl_fills_0xabc.json.gz", old)
    out = M.hl_fills("0xabc")
    assert server.calls[0]["startTime"] == 0
    assert all("closedPnl" in f for f in out)


def test_hl_fills_capped_at_the_limit():
    assert M.HL_FILL_LIMIT == 10_000
    assert not M.hl_fills_capped([{}] * 9_999)
    assert M.hl_fills_capped([{}] * 10_000)


def test_hl_fills_capped_with_a_window():
    # Over the limit, but the oldest fill is at or before the window start: nothing lost.
    fills = [{"time": 1000 + i} for i in range(10_001)]
    assert not M.hl_fills_capped(fills, since_ms=1000)
    assert M.hl_fills_capped(fills, since_ms=999)
    assert not M.hl_fills_capped(fills[:9_999], since_ms=999)


def test_hl_funding_pages_flattens_and_keeps_the_sign(monkeypatch):
    items = [funding_item(1000 + i, "BTC", "-0.5") for i in range(500)]
    items += [funding_item(5000, "ETH", "2.25"), funding_item(5000, "BTC", "-1")]
    server = HL(funding=items)
    monkeypatch.setattr(M, "hl_post", server)
    out = M.hl_funding("0xABC", 0)
    assert len(out) == 502
    assert len(server.calls) == 2 and server.calls[1]["startTime"] == 1499
    assert out[0] == {"time": 1000, "coin": "BTC", "usdc": -0.5}
    assert {(x["coin"], x["usdc"]) for x in out[-2:]} == {("ETH", 2.25), ("BTC", -1.0)}
    assert server.calls[0]["user"] == "0xabc"


def test_hl_funding_window_and_cache(monkeypatch, tmp_path):
    now = int(time.time() * 1000)
    h = 3600 * 1000
    server = HL(funding=[funding_item(now - 3 * h, "BTC", "1"), funding_item(now - 2 * h, "BTC", "2")])
    monkeypatch.setattr(M, "hl_post", server)
    assert [x["usdc"] for x in M.hl_funding("0xabc", now - 3 * h, now - 2 * h)] == [1.0]
    path = tmp_path / "wallet_hl_funding_0xabc.json.gz"
    assert path.exists()
    n = len(server.calls)
    assert [x["usdc"] for x in M.hl_funding("0xabc", now - 3 * h)] == [1.0, 2.0]
    assert len(server.calls) == n, "fresh cache: no call"
    # Stale: refetch from a day before the fetch and merge.
    cache = M.load_json_gz(path)
    cache["fetched"] -= 2 * h
    M.save_json_gz(path, cache)
    server.funding.append(funding_item(now - h // 2, "ETH", "-3"))
    out = M.hl_funding("0xabc", now - 3 * h)
    assert [x["usdc"] for x in out] == [1.0, 2.0, -3.0]
    assert server.calls[-1]["startTime"] == cache["fetched"] - D * 1000
    # An earlier since than the cache covers: a full refetch from it.
    M.hl_funding("0xabc", 0)
    assert server.calls[-1]["startTime"] == 0


def test_hl_leverage_parses_value_and_records_none(monkeypatch, tmp_path):
    lev = {"BTC": {"coin": "BTC", "leverage": {"type": "cross", "value": 20}},
           "ETH": {"coin": "ETH", "leverage": {"type": "isolated", "value": 5, "rawUsd": "-10"}},
           "SOL": {"coin": "SOL"}}
    server = HL(leverage=lev)
    monkeypatch.setattr(M, "hl_post", server)
    out = M.hl_leverage("0xABC", ["BTC", "ETH", "SOL", "DOGE"])
    assert out == {"BTC": 20.0, "ETH": 5.0, "SOL": None, "DOGE": None}
    assert (tmp_path / "wallet_hl_leverage_0xabc.json").exists()
    n = len(server.calls)
    assert M.hl_leverage("0xabc", ["ETH"]) == {"ETH": 5.0}
    assert len(server.calls) == n, "fresh cache: no call"
    lev["XRP"] = {"leverage": {"value": 3}}
    assert M.hl_leverage("0xabc", ["BTC", "XRP"]) == {"BTC": 20.0, "XRP": 3.0}
    assert len(server.calls) == n + 1, "only the new coin is fetched"


# ---- the ledger's sums, the 15-minute base, empty curves ---------------------

CLOSES8 = [100, 100, 110, 99, 99, 99, 99, 99]


def env8(trades=(), timeframe=TF.Hour1):
    """mimic_env on CLOSES8 and its first six grid hours, t0 + 1h .. t0 + 6h."""
    r, markets, grid = mimic_env(ts(LO), CLOSES8, trades)
    r.timeframe = timeframe
    return r, markets, grid[:6]


def ledger8(r, markets, grid):
    return W.ledger("avantis", M.mimic_rows(r, grid), M.market_prices(markets), times=np.asarray(grid))


def test_rows_on_a_15_minute_base_shift_by_15_minutes(hl):
    t0 = ts(LO)
    t = mtrade(t0, 0, 0, 4, 100, 100, fees=0.0, holding=0.0)
    t.entry_time, t.exit_time = t0 + 900, t0 + 1800
    r, _, grid = env8([t], TF.Min15)
    [row] = M.mimic_rows(r, grid)
    assert row["openedAt"] == t0 + 1800
    assert W.unix(row["time"])[0] == t0 + 2700


def test_realized_at_the_end_is_the_sum_of_results(hl):
    t0 = ts(LO)
    trades = [mtrade(t0, 1, 2, 10, 100, 110, fees=1.0, holding=0.25),
              mtrade(t0, 3, 4, 3, 99, 99, "Short", lev=3.0, fees=0.5, holding=-0.75)]
    # Results: 10 * 10 - 1 - 0.25 = 98.75 and 0 - 0.5 + 0.75 = 0.25.
    df = ledger8(*env8(trades))
    assert df["realized"].iloc[-1] == pytest.approx(98.75 + 0.25)


def test_partial_close_counts_the_margin_once(hl):
    t0 = ts(LO)
    # One 10-unit long at 100, 2x (margin 500), closed as 6 at t0+3h open and 4 at t0+5h open.
    a = mtrade(t0, 1, 3, 6, 100, 110, fees=0.0, holding=0.0)  # result 60
    b = mtrade(t0, 1, 5, 4, 100, 99, fees=0.0, holding=0.0)  # result -4
    rho = M.mimic_own(*env8([a, b]))
    # t0+3h: price 110, U = 600*0.1 + 400*0.1 = 100; K = 300 + 200 = 500: 0.2.
    # t0+4h: A closed (+60); B at 99: -4; P = 56: 0.112. t0+6h: both closed, 56 / 500.
    np.testing.assert_allclose(rho.iloc[2:], [0.2, 0.112, 0.112, 0.112])
    assert ledger8(*env8([a, b]))["capital"].iloc[-1] == pytest.approx(500)


def test_exit_on_the_last_grid_hour_is_realized(hl):
    t0 = ts(LO)
    t = mtrade(t0, 1, 5, 10, 100, 99, fees=0.0, holding=0.0)  # exit at t0+5h open, shows at t0+6h, the last hour
    df = ledger8(*env8([t]))
    assert df["realized"].iloc[-1] == pytest.approx(-10)
    assert df["unrealized"].iloc[-1] == pytest.approx(0)


def test_mimic_with_no_trades_is_nan_and_mse_is_the_target_squared(hl):
    r, markets, grid = env8()
    rho = M.mimic_own(r, markets, grid)
    assert rho.isna().all()
    target = pd.Series([np.nan, 0.1, -0.2, 0.3, np.nan, 0.0], index=grid)
    m = M.own_mse(rho, target)
    assert np.isfinite(m) and m == pytest.approx((0.01 + 0.04 + 0.09 + 0) / 4)


def test_mse_with_no_target_hour_is_nan():
    mimic = pd.Series([0.1, 0.2])
    assert np.isnan(M.own_mse(mimic, pd.Series([np.nan, np.nan])))


@pytest.mark.parametrize("name", ["deployed_curve", "dropped_pnl", "warn_dropped",
                                  "mimic_deployed", "mimic_dropped"])
def test_deployed_helpers_are_gone(name):
    assert not hasattr(M, name)


@pytest.mark.parametrize("name", ["curve_stats", "mimic_equity", "OWN_KEYS", "HL_MAX_LEVERAGE", "hl_account",
                                  "hl_snapshots", "hl_history", "hl_ledger", "hl_flows", "hl_flow", "net_flows"])
def test_account_helpers_are_gone(name):
    assert not hasattr(M, name)
    assert callable(M.hl_portfolio)  # wallet_returns still uses it


def test_mimic_book_without_trades_is_none(hl):
    r, markets, grid = env8()
    assert M.mimic_book(r, markets, grid) is None


def test_mimic_book_is_the_ledger_of_the_mimic_rows(hl):
    r, markets, grid = env8([mtrade(ts(LO), 1, 3, 10, 100, 99)])
    got = M.mimic_book(r, markets, grid)
    pd.testing.assert_frame_equal(got, ledger8(r, markets, grid))
    np.testing.assert_allclose(M.mimic_own(r, markets, grid).to_numpy(), got["return"].to_numpy())


def test_mimic_scores_are_flat_own_scores_and_trades(hl):
    markets, grid = sine_markets(ts(LO))
    r = M.backtest({**P_LONG, "leverage": 10}, markets, {"BTC": avbt_cpp.Costs(FEE, FEE, spread=0)})
    target = M.mimic_own(r, markets, grid, 10)
    s = M.mimic_scores(r, markets, grid, target.to_numpy(), 10)
    assert set(s) == set(M.SCORE_KEYS) | {"trades"}
    assert s["trades"] == avbt_cpp.summary(r)["trades"] > 0
    assert s["rmse"] == pytest.approx(0, abs=1e-12)
    want = M.own_scores(M.mimic_book(r, markets, grid, 10), target.to_numpy())
    assert {k: s[k] for k in M.SCORE_KEYS} == pytest.approx(want, nan_ok=True)


def test_chart_ends_on_the_last_grid_hour_at_the_saved_final(monkeypatch, tmp_path, hl):
    chart = pytest.importorskip("mimic_chart")
    path, _, _, grid = own_main(monkeypatch, tmp_path)
    saved = json.loads(path.read_text())
    out = chart.curves(saved)
    assert len(grid) % 4 != 1  # every 4th hour alone would miss the last
    assert out["t"][-1] == grid[-1] and out["t"][:2] == [grid[0], grid[4]]
    assert out["mimic"][-1] == pytest.approx(saved["mimic"]["final"], abs=1e-4)
    assert out["wallet"][-1] == pytest.approx(saved["wallet"]["final"], abs=1e-4)


def test_hl_all_takes_addresses_and_an_output_name():
    import mimic_hl_all as A
    a = A.parse(["--out", "top.html", "0xAB", "0xcd"])
    assert (a.out, a.addresses) == ("top.html", ["0xAB", "0xcd"])
    a = A.parse([])
    assert (a.out, a.addresses) == ("mimic_hl_all.html", [])
    saves = [{"address": "0xab"}, {"address": "0xCD"}, {"address": "0xef"}]
    assert A.pick(saves, ["0xAB", "0xcd"]) == saves[:2]
    assert A.pick(saves, []) == saves

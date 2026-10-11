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


def hourly(t0, n, values):
    """Equity on close times t0, t0 + 1h, ... (n hours)."""
    return pd.Series(np.asarray(values, float), index=t0 + 3600 * np.arange(n, dtype="int64"))


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
    """A fake hl_post: fills by time (pages of 2000), a portfolio, flat candles."""

    def __init__(self, fills=(), price=100.0, empty=(), perp="1000.0", all_time="500.0", error=(), overlap=0,
                 portfolio=None, ledger=()):
        self.fills = sorted(fills, key=lambda f: f["time"])
        self.ledger = sorted(ledger, key=lambda x: x["time"])  # userNonFundingLedgerUpdates items
        self.price, self.empty, self.calls = price, set(empty), []
        self.perp, self.all_time = perp, all_time
        self.error, self.overlap = set(error), overlap  # coins that answer 500; extra bars before startTime
        self.portfolio = portfolio  # a full portfolio response ([name, obj] pairs), else one point per history

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
        if body["type"] == "userNonFundingLedgerUpdates":
            return [x for x in self.ledger if x["time"] >= body["startTime"]][:2000]
        if body["type"] == "portfolio":
            if self.portfolio is not None:
                return self.portfolio
            return [["allTime",{"accountValueHistory": [[0, self.perp]], "pnlHistory": [], "vlm": "0"}],
                    ["allTime", {"accountValueHistory": [[0, self.all_time]], "pnlHistory": [], "vlm": "0"}]]
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


# ---- S2 curve_stats ----------------------------------------------------------

def test_curve_stats_flat_equity_returns_zero():
    s = M.curve_stats(hourly(ts("2025-01-01"), 40 * 24, [ACCOUNT] * (40 * 24)))
    assert s["return"] == 0 and s["max_drawdown"] == 0
    assert set(s) == {"sharpe", "max_drawdown", "return"}


def test_curve_stats_drawdown_is_the_largest_fraction_not_the_largest_dollar_fall():
    eq = hourly(ts("2025-01-01"), 5, [100, 90, 200, 1000, 950])
    s = M.curve_stats(eq)
    assert s["max_drawdown"] == pytest.approx(0.10)
    assert s["return"] == pytest.approx(8.5)
    assert np.isnan(s["sharpe"])  # under 30 days


def test_curve_stats_zero_equity_is_minus_inf_sharpe_and_full_drawdown():
    s = M.curve_stats(hourly(ts("2025-01-01"), 3, [100, 0, 0]))
    assert s["sharpe"] == -np.inf and s["max_drawdown"] == 1.0


def test_curve_stats_sharpe_matches_a_hand_count_dropping_part_days():
    t0 = ts("2025-01-01") + 5 * 3600  # the first value closes at 05:00
    n = 60 * 24 + 12                  # the last at 17:00
    k = np.arange(n)
    eq = hourly(t0, n, 10_000 * (1 + 0.002 * np.sin(k / 5.0)) + 0.3 * k)
    mid = np.arange(-(-t0 // D) * D, eq.index[-1] + 1, D)
    days = eq.loc[mid].to_numpy()
    r = days[1:] / days[:-1] - 1
    want = r.mean() / r.std(ddof=1) * np.sqrt(365)
    naive = eq.iloc[::24].to_numpy()
    rn = naive[1:] / naive[:-1] - 1
    assert not np.isclose(rn.mean() / rn.std(ddof=1) * np.sqrt(365), want)  # the fixture discriminates
    assert M.curve_stats(eq)["sharpe"] == pytest.approx(want, rel=1e-9)


# ---- S2 mimic_equity ---------------------------------------------------------

@pytest.mark.parametrize("tf", [TF.Hour1, TF.Hour4])
def test_mimic_equity_index_is_clock_plus_timeframe(tf):
    step = avbt_cpp.timeframe_seconds(tf)
    r = avbt_cpp.Result(np.array([1.0, 2.0, 3.0]), tf, np.array([0, step, 2 * step], dtype="int64"))
    eq = M.mimic_equity(r)
    assert isinstance(eq, pd.Series)
    assert list(eq.index) == [step, 2 * step, 3 * step]
    assert list(eq) == [1.0, 2.0, 3.0]


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
STATS_KEYS = {"deployed", "account", "trades", "short_share", "pnl", "start_account", "net_flows",
              "account_drift", "gap_share", "dropped_pnl", "dropped_share"}


@pytest.fixture
def hl_curve_env(monkeypatch, hl):
    """hl_curve with flat candles at 100, a $1000 account, and the fills the test sets."""
    def use(fills, price=100.0):
        lo, hi = ts(LO), ts(HI)
        monkeypatch.setattr(M, "hl_fills", lambda address: sorted(fills, key=lambda f: f["time"]))
        monkeypatch.setattr(M, "hl_snapshots", lambda address: (pd.Series({0: 1000.0}), pd.Series(dtype=float)))
        monkeypatch.setattr(M, "hl_ledger", lambda address: [])
        flat = price if callable(price) else (lambda t: np.full(len(t), price))
        monkeypatch.setattr(M, "hl_bars", lambda coin, start, end: make_bars(M.TFS, ts(start), ts(end), flat))
        monkeypatch.setattr(M, "hl_npz", lambda coin, start, end: npz_dict(ts(start), ts(end), flat), raising=False)
        return M.hl_curve("0xabc", LO, HI)
    return use


def test_hl_curve_position_held_from_before_the_window_has_no_pnl(hl_curve_env):
    lo = ts(LO)
    _, stats, counts, start, end, eq = hl_curve_env([fill("BTC", "B", 50, 1, (lo - 10 * D) * 1000, 0, 1)])
    assert (start, end) == (LO, HI)
    assert stats["pnl"] == pytest.approx(0, abs=1e-9)
    assert np.allclose(eq.to_numpy(), ACCOUNT)
    assert eq.index[0] == lo and len(eq) == 30 * 24
    assert stats["trades"] == 0 and stats["start_account"] == 1000.0


def test_hl_curve_position_held_from_before_the_window_is_valued_from_the_start_price(hl_curve_env):
    lo = ts(LO)
    step = lambda t: np.where(t < lo + 2 * D, 100.0, 110.0)
    _, stats, *_, eq = hl_curve_env([fill("BTC", "B", 50, 1, (lo - 10 * D) * 1000, 0, 1)], price=step)
    assert stats["pnl"] == pytest.approx(10)  # from 100 at the window start, not from the 50 it was bought at
    assert eq.iloc[0] == pytest.approx(ACCOUNT)
    assert eq.iloc[-1] == pytest.approx(ACCOUNT + 10 * ACCOUNT / 1000)


def test_hl_curve_first_fill_inside_the_window_with_a_start_position_trades_the_open_at_its_price(hl_curve_env):
    lo = ts(LO)
    # The history covers the window start (a BTC round trip before it), yet the API never served the
    # fills that opened the short of 10: the open is traded at 337, the close's price, not held since
    # the window start at 250. The netted open and close pay no fee.
    step = lambda t: np.where(t < lo + 2 * D, 250.0, 300.0)
    eq, stats, *_ = hl_curve_env([
        fill("BTC", "B", 200, 1, (lo - 10 * D) * 1000, 0, 1),
        fill("BTC", "A", 200, 1, (lo - 9 * D) * 1000, 1, 2, "Close Long"),
        fill("SOL", "B", 337, 10, (lo + D + 1800) * 1000, -10, 3, "Close Short")], price=step)
    assert stats["pnl"] == pytest.approx(0, abs=1e-9)  # no 870 loss from 250 to 337
    assert stats["gap_share"] == pytest.approx(0.5)  # the 10 x 337 open over the open plus the close
    assert eq.iloc[0] == ACCOUNT and eq.iloc[-1] == pytest.approx(ACCOUNT)
    assert stats["trades"] == 1 and stats["short_share"] == 1.0


def test_hl_curve_history_starting_inside_the_window_holds_start_positions_from_the_start_price(hl_curve_env):
    lo = ts(LO)
    # The API keeps only recent fills: the wallet's history begins inside the window, so a coin first
    # seen later with a start position was opened before the history began and is held from the start.
    step = lambda t: np.where(t < lo + 2 * D, 250.0, 300.0)
    _, stats, _, start, _, eq = hl_curve_env([
        fill("BTC", "B", 250, 1, (lo + D + 1800) * 1000, 0, 1),
        fill("SOL", "B", 337, 10, (lo + 3 * D) * 1000, -10, 2, "Close Short")], price=step)
    assert start == "2025-01-12"  # the window starts on the history's first day
    assert stats["pnl"] == pytest.approx(50 - FEE * 250 - 10 * (337 - 250) - FEE * 3370)
    assert stats["gap_share"] == 0.0
    assert eq.iloc[0] == ACCOUNT


def test_hl_curve_short_round_trip_pays_fee_on_both_notionals(hl_curve_env):
    lo = ts(LO)
    t1, t2 = lo + D + 1800, lo + 2 * D
    _, stats, counts, start, end, eq = hl_curve_env([
        fill("ETH", "A", 100, 1, t1 * 1000, 0, 1, "Open Short"),
        fill("ETH", "B", 90, 1, t2 * 1000, -1, 2, "Close Short")])
    pnl = 10 - FEE * (100 + 90)
    assert stats["pnl"] == pytest.approx(pnl)
    assert eq.iloc[-1] == pytest.approx(ACCOUNT + pnl * ACCOUNT / 1000)
    assert stats["trades"] == 1 and stats["short_share"] == 1.0
    # While the short is open at 100, only the open fee is lost.
    assert eq.loc[lo + D + 7200] == pytest.approx(ACCOUNT - FEE * 100 * ACCOUNT / 1000)
    assert counts["ETH"] == 2


def test_hl_curve_flip_is_two_trades(hl_curve_env):
    lo = ts(LO)  # the first fill sits inside its day: a fill at the window's first second is not inside
    eq, stats, *_ = hl_curve_env([
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


def test_hl_curve_account_is_at_least_peak_notional_over_max_leverage(hl_curve_env):
    lo = ts(LO)
    # 1000 of notional at 100, held at the start, needs 100_000 / 50 = 2000 > the $1000 account.
    _, stats, *_ = hl_curve_env([fill("BTC", "B", 100, 1000, (lo - D) * 1000, 0, 1)])
    assert stats["start_account"] == pytest.approx(100_000 / M.HL_MAX_LEVERAGE)


def test_hl_account_falls_back_to_all_time_when_perp_is_zero(monkeypatch, hl):
    monkeypatch.setattr(M, "hl_post", HL(perp="0.0", all_time="500.0"))
    assert M.hl_account("0xabc", ts(LO)) == 500.0


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
          lev=2, symbol="BTC/USD", pid=1, index=0):
    return {"time": pd.Timestamp(close_at, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "gross": gross, "pairIndex": pid, "symbol": symbol, "index": index, "buy": buy, "leverage": lev,
            "openPrice": open_px, "tp": 0, "sl": 0, "collateral": pos, "positionSize": pos,
            "closePrice": close_px, "usdcSent": pos + gross, "openedAt": open_at, "closingFee": 0, "funding": 0}


def price_path(t):
    """100, then 110 on day 3, then 95 from day 4."""
    return np.where(t < BASE + 3 * D, 100.0, np.where(t < BASE + 4 * D, 110.0, 95.0))


@pytest.fixture
def avantis_env(monkeypatch, tmp_path):
    def use(trades, candles=True):
        (tmp_path / f"wallet_{ADDR}.json").write_text(json.dumps({"address": ADDR, "count": len(trades), "trades": trades}))
        monkeypatch.setattr(M.vt, "_symbols", lambda: {1: "BTC/USD", 2: "ETH/USD"})
        monkeypatch.setattr(M, "bars", lambda pid, start, end:
                            make_bars(M.TFS, BASE, BASE + 8 * D, price_path) if candles else None)
        return M.avantis_curve(ADDR)
    return use


def test_avantis_curve_marks_an_open_short_to_market(avantis_env):
    # Short 1000 margin at 2x (notional 2000) at 100; price 110 on day 3; closed day 5 at 95.
    _, stats, counts, start, end, eq = avantis_env([trade(BASE + 5 * D + 6 * 3600, 1000, 100.0)])
    scale = ACCOUNT / 1000
    open_fee = close_fee = FEE * 2000
    assert eq.index[0] == BASE + D  # midnight of the first open
    assert BASE + 6 * D - 3600 <= eq.index[-1] <= BASE + 6 * D
    assert set(np.diff(eq.index)) == {3600}
    assert eq.loc[BASE + 3 * D + 12 * 3600] == pytest.approx(ACCOUNT + scale * (-200 - open_fee))
    assert eq.min() < ACCOUNT
    assert eq.iloc[-1] == pytest.approx(ACCOUNT + scale * (100 - open_fee - close_fee))
    assert stats["account"]["return"] == pytest.approx(0.099)
    assert stats["trades"] == 1 and stats["short_share"] == 1.0
    assert set(stats) == STATS_KEYS - {"pnl", "start_account", "net_flows", "account_drift", "gap_share"}
    assert stats["dropped_pnl"] == 0 and stats["dropped_share"] == 0
    assert counts["BTC/USD"] == 1
    assert start < end


def test_avantis_curve_before_the_open_is_the_account(avantis_env):
    eq, *_ = avantis_env([trade(BASE + 5 * D + 6 * 3600, 1000, 100.0)])
    assert np.allclose(eq.loc[: BASE + D + 11 * 3600].to_numpy(), ACCOUNT)


def test_avantis_curve_partial_close_is_one_trade_and_frees_its_margin(avantis_env):
    t1, t2 = BASE + 4 * D + 6 * 3600, BASE + 5 * D + 6 * 3600
    _, stats, counts, *_, eq = avantis_env([trade(t1, 600, 60.0), trade(t2, 400, 40.0)])
    scale = ACCOUNT / 1000
    fees_by_t1 = FEE * 2000 + FEE * 1200
    # After the first close: 60 realized, 800 notional still short at 95 from 100.
    assert eq.loc[BASE + 4 * D + 12 * 3600] == pytest.approx(ACCOUNT + scale * (60 - fees_by_t1 + 800 * 0.05))
    assert eq.iloc[-1] == pytest.approx(ACCOUNT + scale * (100 - FEE * 2000 - FEE * 2000))
    assert stats["trades"] == 1 and counts["BTC/USD"] == 1


def test_avantis_curve_symbol_without_candles_is_realized_only(avantis_env, capsys):
    _, stats, *_, eq = avantis_env([trade(BASE + 5 * D + 6 * 3600, 1000, 100.0)], candles=False)
    scale = ACCOUNT / 1000
    assert eq.min() == pytest.approx(ACCOUNT - scale * FEE * 2000)
    assert eq.iloc[-1] == pytest.approx(ACCOUNT + scale * (100 - FEE * 4000))
    assert stats["trades"] == 1
    assert "BTC/USD" in capsys.readouterr().out


def test_avantis_curve_fees_use_notional_at_open_and_at_close(avantis_env):
    # A long at 5x on 200 margin (notional 1000) closed flat: only the fees show.
    _, stats, *_, eq = avantis_env([trade(BASE + 5 * D + 6 * 3600, 200, 0.0, buy=True, open_px=95.0, close_px=95.0, lev=5)])
    assert eq.iloc[-1] == pytest.approx(ACCOUNT - (ACCOUNT / 200) * FEE * 2000)


def test_avantis_curve_counts_orders_busiest_first(avantis_env):
    t = BASE + 5 * D + 6 * 3600
    rows = [trade(t, 100, 1.0, symbol="ETH/USD", pid=2, index=i) for i in range(3)] + [trade(t, 100, 1.0)]
    _, stats, counts, *_ = avantis_env(rows)
    assert list(counts.index) == ["ETH/USD", "BTC/USD"] and list(counts) == [3, 1]
    assert stats["trades"] == 4


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
    eq, stats, *_ = hl_curve_env(fills)
    assert stats["pnl"] == pytest.approx(100 - FEE * (1000 + 1100))
    assert stats["trades"] == 1 and eq.iloc[0] == ACCOUNT


def test_hl_curve_position_held_from_before_lo_built_by_a_scrambled_group(hl_curve_env):
    lo = ts(LO)
    fills = scrambled_buy((lo - 10 * D) * 1000)
    fills = [fills[3], fills[0], fills[2], fills[1]]
    eq, stats, *_ = hl_curve_env(fills, price=step_price(lo + 5 * D))
    assert eq.iloc[0] == ACCOUNT
    assert eq.loc[lo + 4 * D] == pytest.approx(ACCOUNT)
    # 10 held, not 4: the whole group's position rides the move.
    assert eq.iloc[-1] == pytest.approx(ACCOUNT + 10 * 10 * ACCOUNT / 1000)
    assert stats["pnl"] == pytest.approx(100) and stats["trades"] == 0


def test_hl_curve_mixed_sides_in_one_ms_chain_by_start_position(hl_curve_env):
    lo = ts(LO)
    t = (lo + D + 1800) * 1000
    # By tid the sell (startPosition 5) comes first; the chain says buy 5 then sell 5.
    fills = [fill("BTC", "A", 100, 5, t, 5, 1, "Close Long"), fill("BTC", "B", 100, 5, t, 0, 2)]
    _, stats, *_, eq = hl_curve_env(fills, price=step_price(lo + 5 * D))
    assert stats["pnl"] == pytest.approx(-FEE * 1000)
    assert eq.iloc[-1] == pytest.approx(ACCOUNT - FEE * 1000 * ACCOUNT / 1000)
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
    monkeypatch.setattr(M, "hl_snapshots", lambda address: (pd.Series({0: 1000.0}), pd.Series(dtype=float)))
    eq, stats, counts, *_ = M.hl_curve("0xabc", LO, HI)
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

def test_avantis_curve_skips_a_row_without_position_size(avantis_env):
    t = BASE + 5 * D + 6 * 3600
    bad = trade(t, 100, 1.0, symbol="ETH/USD", pid=2, index=1)
    bad["positionSize"] = None
    _, stats, counts, *_, eq = avantis_env([trade(t, 1000, 100.0), bad])
    assert stats["trades"] == 1 and counts["BTC/USD"] == 1
    assert eq.iloc[-1] == pytest.approx(ACCOUNT + (ACCOUNT / 1000) * (100 - FEE * 4000))


def test_avantis_curve_grid_ends_before_the_midnight_after_the_last_close(avantis_env):
    eq, *_ = avantis_env([trade(BASE + 5 * D + 6 * 3600, 1000, 100.0)])
    assert eq.index[-1] == BASE + 6 * D - 3600


# ---- S16 a gap at the first inside fill ----------------------------------------

def gap_fills(lo):
    """Buy 1 ten days before lo; inside, sell 2 from a start of 2: a buy of 1 is missing."""
    return [fill("ETH", "B", 100, 1, (lo - 10 * D) * 1000, 0, 1),
            fill("ETH", "A", 100, 2, (lo + 5 * D) * 1000, 2, 2, "Close Long")]


def test_hl_curve_gap_at_the_first_inside_fill_does_not_shift_the_curve(hl_curve_env):
    lo = ts(LO)
    eq, stats, *_ = hl_curve_env(gap_fills(lo))
    assert eq.iloc[0] == ACCOUNT
    assert np.allclose(eq.loc[: lo + 5 * D - 3600].to_numpy(), ACCOUNT)
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
        scores = {"sharpe": 0.0, "max_drawdown": 0.0, "return": 0.0}
        stats = {"deployed": scores, "account": scores, "trades": 1, "short_share": 0.0,
                 "pnl": 1.0, "start_account": 1000.0, "net_flows": 0.0, "account_drift": 0.0, "gap_share": gap_share}
        eq = hourly(ts(LO), 24, [ACCOUNT] * 24)
        monkeypatch.setattr(M, "hl_curve", lambda address, since: (eq, stats, pd.Series({"BTC": 3}), LO, HI, eq))
        monkeypatch.setattr(M, "load_markets", lambda symbols, start, end: "markets")
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
    monkeypatch.setattr(M, "hl_snapshots", lambda address: (pd.Series({0: 1000.0}), pd.Series(dtype=float)))
    monkeypatch.setattr(M, "hl_ledger", lambda address: [])
    monkeypatch.setattr(M, "hl_bars", lambda coin, start, end: None)  # no candles: fill prices
    _, stats, counts, *_, eq = M.hl_curve("0xabc", LO, HI)
    assert stats["pnl"] == pytest.approx(20 - 8 - FEE * (4 + 4 + 20))
    assert stats["trades"] == 1 and counts["#7790"] == 3
    assert eq.iloc[-1] == pytest.approx(ACCOUNT + stats["pnl"] * ACCOUNT / 1000)


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


# ---- S12 Hyperliquid curve as a time-weighted return ---------------------------

def steps(*points):
    """A price per grid hour: (hour, price) pairs, carried forward; as a bar-open function for make_bars."""
    at = np.array([t for t, _ in points], dtype="int64")
    px = np.array([p for _, p in points], float)

    def price(t):  # t: bar open times; the bar ending at t + 1h is the hour's price
        return px[np.searchsorted(at, np.asarray(t) + 3600, side="right") - 1]
    return price


def history(*points, pnl=None):
    """A portfolio history: (unix second, account value) pairs, and pnlHistory from `pnl` per point."""
    values = [[t * 1000, str(float(v))] for t, v in points]
    pnls = [[t * 1000, str(float(p))] for (t, _), p in zip(points, pnl)] if pnl is not None else []
    return {"accountValueHistory": values, "pnlHistory": pnls, "vlm": "0"}


@pytest.fixture
def tw_env(monkeypatch, hl):
    """hl_curve on a portfolio response: fills, a price per hour, and [name, history] pairs."""
    def use(fills, price, portfolio, ledger=()):
        monkeypatch.setattr(M, "hl_fills", lambda address: sorted(fills, key=lambda f: f["time"]))
        monkeypatch.setattr(M, "hl_post", HL(portfolio=portfolio, ledger=ledger))
        monkeypatch.setattr(M, "hl_bars", lambda coin, start, end: make_bars(M.TFS, ts(start), ts(end), price))
        monkeypatch.setattr(M, "hl_npz", lambda coin, start, end: npz_dict(ts(start), ts(end), price), raising=False)
        return M.hl_curve("0xabc", LO, HI)
    return use


def flow(t, type, **delta):
    """A userNonFundingLedgerUpdates item at unix second t."""
    return {"time": t * 1000, "hash": "0x", "delta": {"type": type, **delta}}


def held(size, side="B"):
    """One position of `size` opened ten days before the window: no fee, no gap, no trade inside it."""
    return [fill("BTC", side, 50, size, (ts(LO) - 10 * D) * 1000, 0, 1, "Open Long" if side == "B" else "Open Short")]


def test_tw_no_flows_compounds_on_the_running_account(tw_env):
    lo = ts(LO)
    # 1 BTC held from before; the price goes 100 -> 200 -> 150: PnL +100 then -50 (0, +100, +50).
    price = steps((0, 100), (lo + 2 * D, 200), (lo + 4 * D, 150))
    snaps = history((lo, 1000), (lo + 3 * D, 1100), (lo + 6 * D, 1050), pnl=[0, 100, 50])
    _, stats, *_, eq = tw_env(held(1), price, [["allTime",snaps]])
    assert eq.iloc[0] == ACCOUNT
    assert eq.loc[lo + 2 * D] == pytest.approx(ACCOUNT * 1.1)
    assert eq.iloc[-1] == pytest.approx(ACCOUNT * 1.1 * (1 - 50 / 1100))  # the -50 is on 1100, not 1000
    assert stats["pnl"] == pytest.approx(50) and stats["start_account"] == pytest.approx(1000)
    assert stats["net_flows"] == pytest.approx(0)


def test_tw_withdrawal_makes_a_later_loss_a_bigger_share(tw_env):
    lo = ts(LO)
    # +1000 on 1000 (eq doubles), 1500 withdrawn (account 500), then -400: 80% of 500, not 40% of 1000.
    price = steps((0, 100), (lo + 2 * D, 1100), (lo + 4 * D, 700))
    snaps = history((lo, 1000), (lo + 6 * D, 100), pnl=[0, 600])
    ledger = [flow(lo + 3 * D, "withdraw", usdc="1499.0", fee="1.0")]
    _, stats, *_, eq = tw_env(held(1), price, [["allTime",snaps]], ledger)
    assert eq.loc[lo + 3 * D] == pytest.approx(2 * ACCOUNT)
    assert eq.iloc[-1] == pytest.approx(2 * ACCOUNT * 0.2)
    assert (eq > 0).all()
    assert stats["pnl"] == pytest.approx(600) and stats["net_flows"] == pytest.approx(-1500)
    assert stats["account_drift"] == pytest.approx(0)  # the snapshot at day 6 agrees: 1000 + 600 - 1500


def test_tw_deposit_after_a_loss_keeps_the_curve_alive(tw_env):
    lo = ts(LO)
    # Short 10 at 100: -900 (account 100), 5000 deposited (5100), -2000 more (cumulative -2900,
    # below the 1000 start), then +1000. No hour loses 100%: the curve never dies.
    price = steps((0, 100), (lo + 2 * D, 190), (lo + 4 * D, 390), (lo + 6 * D, 290))
    snaps = history((lo, 1000), pnl=[0])
    ledger = [flow(lo + 3 * D, "deposit", usdc="5000.0")]
    _, stats, *_, eq = tw_env(held(10, "A"), price, [["allTime",snaps]], ledger)
    assert eq.loc[lo + 3 * D] == pytest.approx(ACCOUNT * 0.1)
    assert eq.loc[lo + 5 * D] == pytest.approx(ACCOUNT * 0.1 * (1 - 2000 / 5100))
    assert eq.iloc[-1] == pytest.approx(ACCOUNT * 0.1 * (1 - 2000 / 5100) * (1 + 1000 / 3100))
    assert (eq > 0).all()
    assert stats["pnl"] == pytest.approx(-1900) and stats["net_flows"] == pytest.approx(5000)


def test_tw_a_full_loss_in_one_hour_stays_at_zero(tw_env):
    lo = ts(LO)
    # Short 10 at 100 on a 1000 account; the price doubles in one hour: -1000, all of it. The
    # recovery that follows (the floor keeps A > 0) never lifts a dead curve.
    price = steps((0, 100), (lo + 2 * D, 200), (lo + 4 * D, 100))
    snaps = history((lo, 1000), pnl=[0])
    _, stats, *_, eq = tw_env(held(10, "A"), price, [["allTime",snaps]])
    assert eq.loc[lo + 2 * D] == 0.0 and eq.iloc[-1] == 0.0
    assert stats["pnl"] == pytest.approx(0) and stats["account"]["sharpe"] == -np.inf


def test_tw_floor_is_the_notional_over_max_leverage(tw_env):
    lo = ts(LO)
    # The account says 0 while 10 BTC (1000 of notional) is held: the account is 1000 / 50 = 20,
    # so +100 is a return of 5. The later snapshot agrees with the 0 (0 + 100).
    price = steps((0, 100), (lo + 2 * D, 110))
    whole = history((lo, 0), (lo + 5 * D, 100), pnl=[0, 100])
    _, stats, *_, eq = tw_env(held(10), price, [["allTime", whole]])
    assert stats["start_account"] == pytest.approx(1000 / M.HL_MAX_LEVERAGE)
    assert eq.iloc[-1] == pytest.approx(ACCOUNT * (1 + 100 * M.HL_MAX_LEVERAGE / 1000))


def test_tw_floor_lifts_a_small_positive_snapshot(tw_env):
    lo = ts(LO)
    # A $10 account cannot hold 1000 of notional: the account is 20.
    price = steps((0, 100), (lo + 2 * D, 110))
    _, stats, *_, eq = tw_env(held(10), price, [["allTime",history((lo, 10), pnl=[0])]])
    assert stats["start_account"] == pytest.approx(20)
    assert eq.iloc[-1] == pytest.approx(ACCOUNT * 6)


def test_tw_snapshots_only_check_the_account(tw_env):
    lo = ts(LO)
    # A snapshot inside the window does not re-anchor: a withdrawal the ledger lacks shows as drift.
    price = steps((0, 100), (lo + 2 * D, 1100), (lo + 4 * D, 700))
    all_time = history((lo, 1000), (lo + 6 * D, 100), pnl=[0, 600])
    week = history((lo + 3 * D, 500), pnl=[1000])
    _, stats, *_, eq = tw_env(held(1), price, [["allTime",all_time], ["week",week]])
    assert eq.iloc[-1] == pytest.approx(2 * ACCOUNT * 0.8)  # -400 on an un-withdrawn 2000
    assert stats["net_flows"] == pytest.approx(0)
    assert stats["account_drift"] == pytest.approx(1500 / 1600)  # day 6: snapshot 100 against 1600


def test_tw_ledger_failure_falls_back_to_snapshot_re_anchoring(tw_env, monkeypatch, capsys):
    lo = ts(LO)
    price = steps((0, 100), (lo + 2 * D, 1100), (lo + 4 * D, 700))
    snaps = history((lo, 1000), (lo + 3 * D, 500), (lo + 6 * D, 100), pnl=[0, 1000, 600])

    def down(address):
        raise OSError("ledger down")
    monkeypatch.setattr(M, "hl_ledger", down)
    _, stats, *_, eq = tw_env(held(1), price, [["allTime",snaps]])
    assert eq.iloc[-1] == pytest.approx(2 * ACCOUNT * 0.2)  # the day-3 snapshot re-anchors to 500
    assert stats["net_flows"] == pytest.approx(-1500) and stats["account_drift"] is None
    assert "ledger" in capsys.readouterr().out


def test_tw_flow_in_the_hour_counts_from_that_hours_end(tw_env):
    lo = ts(LO)
    # A deposit of 1000 twenty minutes into the hour that loses 100: the loss is on 1000, the
    # next hour's loss of 100 on 1900.
    price = steps((0, 100), (lo + 2 * D, 0), (lo + 2 * D + 3600, -100))
    ledger = [flow(lo + 2 * D - 2400, "deposit", usdc="1000.0")]
    _, stats, *_, eq = tw_env(held(1), price, [["allTime",history((lo, 1000), pnl=[0])]], ledger)
    assert eq.loc[lo + 2 * D] == pytest.approx(ACCOUNT * 0.9)
    assert eq.loc[lo + 2 * D + 3600] == pytest.approx(ACCOUNT * 0.9 * (1 - 100 / 1900))


def test_hl_flow_signs_each_ledger_type():
    me = "0xabc"
    other = "0xdef"
    cases = [({"type": "deposit", "usdc": "10"}, 10),
             ({"type": "withdraw", "usdc": "9", "fee": "1"}, -10),
             ({"type": "accountClassTransfer", "usdc": "10", "toPerp": True}, 0),
             ({"type": "accountClassTransfer", "usdc": "10", "toPerp": False}, 0),
             ({"type": "internalTransfer", "usdc": "10", "user": me, "destination": other, "fee": "0"}, -10),
             ({"type": "internalTransfer", "usdc": "10", "user": other, "destination": me, "fee": "0"}, 10),
             ({"type": "subAccountTransfer", "usdc": "10", "user": me, "destination": other}, -10),
             ({"type": "vaultCreate", "usdc": "10", "fee": "1"}, -11),
             ({"type": "vaultDeposit", "usdc": "10"}, -10),
             ({"type": "vaultWithdraw", "requestedUsd": "12", "netWithdrawnUsd": "10"}, 10),
             ({"type": "vaultDistribution", "usdc": "10"}, 10),
             ({"type": "send", "token": "USDC", "amount": "10", "fee": "1", "user": me, "destination": other,
               "sourceDex": "", "destinationDex": "spot"}, -11),
             ({"type": "send", "token": "USDC", "amount": "10", "fee": "0", "user": other, "destination": me,
               "sourceDex": "spot", "destinationDex": "xyz"}, 10),
             ({"type": "send", "token": "USDC", "amount": "10", "fee": "0", "user": me, "destination": me,
               "sourceDex": "", "destinationDex": "xyz"}, 0),  # between the wallet's own dexes
             ({"type": "send", "token": "HYPE", "amount": "10", "fee": "0", "user": me, "destination": other,
               "sourceDex": "", "destinationDex": ""}, 0),
             ({"type": "spotTransfer", "token": "USDC", "amount": "10", "usdcValue": "10", "fee": "0",
               "user": me, "destination": other}, -10),
             ({"type": "spotTransfer", "token": "USDC", "amount": "10", "usdcValue": "10", "fee": "0",
               "user": other, "destination": me}, 10),
             ({"type": "spotTransfer", "token": "HYPE", "amount": "10", "usdcValue": "100", "fee": "0",
               "user": me, "destination": other}, 0),
             ({"type": "rewardsClaim", "amount": "3", "token": ""}, 0)]
    for delta, want in cases:
        assert M.hl_flow(delta, me) == pytest.approx(want), delta
    assert [M.hl_token_transfer(d) for d, _ in cases].count(True) == 2  # the HYPE send and spotTransfer


def test_tw_same_time_in_two_histories_is_one_snapshot(tw_env):
    lo = ts(LO)
    price = steps((0, 100), (lo + 2 * D, 200))
    snaps = history((lo, 1000), pnl=[0])
    _, stats, *_, eq = tw_env(held(1), price, [["allTime",snaps], ["week",snaps], ["day",snaps]])
    assert eq.iloc[-1] == pytest.approx(ACCOUNT * 1.1)
    assert stats["net_flows"] == pytest.approx(0)


def test_tw_the_whole_account_is_the_account_not_the_perp_value(tw_env):
    lo = ts(LO)
    # The perp account holds 100 and the spot account 900: the +100 is 10% of the whole account.
    price = steps((0, 100), (lo + 2 * D, 200))
    perp = history((lo, 100), pnl=[0])
    whole = history((lo, 1000), pnl=[0])
    _, stats, *_, eq = tw_env(held(1), price, [["perpAllTime", perp], ["allTime", whole]])
    assert stats["start_account"] == pytest.approx(1000)
    assert eq.iloc[-1] == pytest.approx(ACCOUNT * 1.1)


def test_tw_no_whole_account_history_uses_the_perp_one(tw_env):
    lo = ts(LO)
    price = steps((0, 100), (lo + 2 * D, 200))
    _, stats, *_, eq = tw_env(held(1), price, [["perpAllTime", history((lo, 1000), pnl=[0])]])
    assert stats["start_account"] == pytest.approx(1000)
    assert eq.iloc[-1] == pytest.approx(ACCOUNT * 1.1)


def test_tw_a_move_from_perp_to_spot_is_not_a_flow(tw_env):
    lo = ts(LO)
    # 900 moved to spot a day in: the whole account is still 1000, so the +100 is 10%.
    price = steps((0, 100), (lo + 2 * D, 200))
    ledger = [flow(lo + D, "accountClassTransfer", usdc="900.0", toPerp=False)]
    _, stats, *_, eq = tw_env(held(1), price, [["allTime", history((lo, 1000), pnl=[0])]], ledger)
    assert eq.iloc[-1] == pytest.approx(ACCOUNT * 1.1)
    assert stats["net_flows"] == pytest.approx(0)


def test_tw_before_the_first_snapshot_the_account_is_anchored_on_its_pnl(tw_env):
    lo = ts(LO)
    # The first snapshot (1100) is three days in, after the +100: the account at lo is 1000, so the
    # +100 is 10%, not 100 / 1100. The -50 after it is on 1100.
    price = steps((0, 100), (lo + 2 * D, 200), (lo + 4 * D, 150))
    snaps = history((lo + 3 * D, 1100), pnl=[100])
    _, stats, *_, eq = tw_env(held(1), price, [["allTime",snaps]])
    assert stats["start_account"] == pytest.approx(1000)
    assert eq.loc[lo + 3 * D] == pytest.approx(ACCOUNT * 1.1)
    assert eq.iloc[-1] == pytest.approx(ACCOUNT * 1.1 * (1 - 50 / 1100))


def test_tw_a_fill_inside_the_window_moves_the_account_with_its_pnl(tw_env):
    lo = ts(LO)
    # An inside round trip: buy 1 at 100, sell at 200 two days later, on a 1000 account that is
    # snapshotted once at lo. The open fee is a loss on 1000; the +100 less the close fee is a gain
    # on 1000 - fee: the product telescopes to (1000 + pnl) / 1000.
    price = steps((0, 100), (lo + 2 * D, 200))
    fills = [fill("BTC", "B", 50, 1, (lo - 10 * D) * 1000, 0, 1),  # a round trip before: the history covers lo
             fill("BTC", "A", 50, 1, (lo - 9 * D) * 1000, 1, 2, "Close Long"),
             fill("BTC", "B", 100, 1, (lo + D) * 1000, 0, 3),
             fill("BTC", "A", 200, 1, (lo + 3 * D) * 1000, 1, 4, "Close Long")]
    _, stats, *_, eq = tw_env(fills, price, [["allTime",history((lo, 1000), pnl=[0])]])
    pnl = 100 - FEE * 300
    assert stats["pnl"] == pytest.approx(pnl) and stats["trades"] == 1
    assert eq.iloc[-1] == pytest.approx(ACCOUNT * (1000 + pnl) / 1000)


def test_tw_stats_come_from_the_new_curve(tw_env):
    lo = ts(LO)
    price = steps((0, 100), (lo + 2 * D, 1100), (lo + 4 * D, 700))
    snaps = history((lo - 5 * D, 1000), (lo, 1000), (lo + 6 * D, 100), pnl=[0, 0, 600])
    ledger = [flow(lo - 2 * D, "withdraw", usdc="99.0", fee="1.0"),  # before the window: not counted
              flow(lo + 3 * D, "withdraw", usdc="1499.0", fee="1.0")]
    _, stats, *_, eq = tw_env(held(1), price, [["allTime",snaps]], ledger)
    assert set(stats) == STATS_KEYS
    assert stats["start_account"] == pytest.approx(1000) and stats["pnl"] == pytest.approx(600)
    assert stats["net_flows"] == pytest.approx(-1500)
    s = M.curve_stats(eq)
    np.testing.assert_equal([stats["account"][k] for k in s], list(s.values()))  # nan == nan here
    assert stats["account"]["return"] == pytest.approx(0.4 - 1)
    assert stats["account"]["max_drawdown"] == pytest.approx(0.8)


# ---- S13 return on deployed capital --------------------------------------------

from types import SimpleNamespace as NS  # noqa: E402


def series(t0, *values):
    return hourly(t0, len(values), values)


def hourly_returns(eq):
    return (eq / eq.shift(1) - 1).dropna().to_numpy()


def test_deployed_curve_long_held_three_hours_by_hand():
    # Long 10 at 100, held; the price goes 100 -> 110 -> 99: +10% on the 1000 opened, then
    # -110 on the 1100 held the hour before, -10%.
    t0 = ts(LO)
    P = series(t0, 0.0, 100.0, -10.0)
    N = series(t0, 1000.0, 1100.0, 990.0)
    T = series(t0, 1000.0, 0.0, 0.0)
    eq = M.deployed_curve(P, N, T)
    assert isinstance(eq, pd.Series) and list(eq.index) == list(P.index)
    assert eq.iloc[0] == ACCOUNT
    assert np.allclose(eq.to_numpy(), ACCOUNT * np.array([1.0, 1.1, 1.1 * 0.9]))


def test_deployed_curve_no_position_is_flat_even_when_pnl_moves():
    t0 = ts(LO)
    P = series(t0, 0.0, 5.0, 10.0, 3.0)
    zero = series(t0, 0.0, 0.0, 0.0, 0.0)
    eq = M.deployed_curve(P, zero, zero)
    assert np.allclose(eq.to_numpy(), ACCOUNT)


def test_deployed_curve_open_and_close_inside_one_hour_divides_by_the_opening_notional():
    # Nothing held the hour before, 100 opened and closed inside the hour, 5 made: 5%.
    t0 = ts(LO)
    P = series(t0, 0.0, 0.0, 5.0, 5.0)
    N = series(t0, 0.0, 0.0, 0.0, 0.0)
    T = series(t0, 0.0, 0.0, 100.0, 0.0)
    eq = M.deployed_curve(P, N, T)
    assert np.allclose(eq.to_numpy(), ACCOUNT * np.array([1.0, 1.0, 1.05, 1.05]))


def test_deployed_curve_uses_the_larger_of_held_and_opened():
    # 1000 held the hour before and 400 more opened this hour: the +50 is on 1000, not 400 or 1400.
    t0 = ts(LO)
    P = series(t0, 0.0, 0.0, 50.0)
    N = series(t0, 0.0, 1000.0, 1450.0)
    T = series(t0, 0.0, 1000.0, 400.0)
    eq = M.deployed_curve(P, N, T)
    assert eq.iloc[-1] == pytest.approx(ACCOUNT * 1.05)
    # 300 held and 1000 opened: the +50 is on 1000.
    N = series(t0, 0.0, 300.0, 1350.0)
    T = series(t0, 0.0, 300.0, 1000.0)
    assert M.deployed_curve(P, N, T).iloc[-1] == pytest.approx(ACCOUNT * 1.05)


def test_deployed_curve_short_by_hand():
    # Short 1 at 100 (gross notional 100); the price falls to 90: +10 on 100.
    t0 = ts(LO)
    P = series(t0, 0.0, 10.0)
    N = series(t0, 100.0, 90.0)
    T = series(t0, 100.0, 0.0)
    assert M.deployed_curve(P, N, T).iloc[-1] == pytest.approx(ACCOUNT * 1.1)


def test_deployed_curve_is_scale_free():
    t0 = ts(LO)
    P = series(t0, 0.0, 3.0, -2.0, 7.0)
    N = series(t0, 50.0, 55.0, 60.0, 0.0)
    T = series(t0, 50.0, 0.0, 10.0, 0.0)
    a = M.deployed_curve(P, N, T)
    b = M.deployed_curve(P * 1000, N * 1000, T * 1000)
    assert np.allclose(a.to_numpy(), b.to_numpy())


def step_at(at, before, after):
    """A bar-open price function: `before` for bars opening before `at`, `after` from it on."""
    return lambda t: np.where(t < at, float(before), float(after))


def test_hl_deployed_long_round_trip_matches_a_hand_count(hl_curve_env):
    lo = ts(LO)
    s = lo + 2 * D  # the bar opening at s closes at s + 1h at 110
    fills = [fill("ETH", "B", 100, 1, (lo + D + 1800) * 1000, 0, 1),
             fill("ETH", "A", 110, 1, (lo + 3 * D) * 1000, 1, 2, "Close Long")]
    eq, *_ = hl_curve_env(fills, price=step_at(s, 100, 110))
    assert eq.iloc[0] == ACCOUNT
    assert eq.loc[lo + D + 3600] == pytest.approx(ACCOUNT * (1 - FEE))  # the open fee on the 100 opened
    assert eq.loc[s + 3600] / eq.loc[s] == pytest.approx(1.1)  # +10 on the 100 held the hour before
    assert eq.loc[lo + 3 * D] / eq.loc[lo + 3 * D - 3600] == pytest.approx(1 - FEE)  # the close fee on 110 held
    assert eq.iloc[-1] == pytest.approx(ACCOUNT * (1 - FEE) * 1.1 * (1 - FEE))


def test_hl_deployed_is_the_same_at_ten_times_the_size(hl_curve_env):
    lo = ts(LO)
    price = step_at(lo + 2 * D, 100, 110)

    def fills(sz):
        return [fill("ETH", "B", 100, sz, (lo + D + 1800) * 1000, 0, 1),
                fill("ETH", "A", 110, sz, (lo + 3 * D) * 1000, sz, 2, "Close Long")]
    one, *_ = hl_curve_env(fills(1), price=price)
    ten, *_ = hl_curve_env(fills(10), price=price)
    assert not np.allclose(one.to_numpy(), ACCOUNT)  # the fixture discriminates
    assert np.allclose(one.to_numpy(), ten.to_numpy())


def test_avantis_deployed_is_the_same_at_ten_times_the_size_or_the_leverage(avantis_env):
    t = BASE + 5 * D + 6 * 3600
    base, *_ = avantis_env([trade(t, 1000, 100.0)])
    size, *_ = avantis_env([trade(t, 10_000, 1000.0)])
    lever, *_ = avantis_env([trade(t, 1000, 1000.0, lev=20)])
    assert not np.allclose(base.to_numpy(), ACCOUNT)
    assert np.allclose(base.to_numpy(), size.to_numpy())
    assert np.allclose(base.to_numpy(), lever.to_numpy())


def test_avantis_deployed_short_matches_a_hand_count(avantis_env):
    # Short 2000 notional at 100; 110 on day 3 (-200 on 2000), 95 from day 4 (+300 on 2200); closed day 5.
    t = BASE + 5 * D + 6 * 3600
    eq, stats, *_ = avantis_env([trade(t, 1000, 100.0)])
    day3, day4 = BASE + 3 * D + 3600, BASE + 4 * D + 3600  # the first hours priced 110 and 95
    assert eq.loc[day3] / eq.loc[day3 - 3600] == pytest.approx(1 - 200 / 2000)
    assert eq.loc[day4] / eq.loc[day4 - 3600] == pytest.approx(1 + 300 / 2200)
    open_hour = BASE + D + 13 * 3600  # the first hour after the open at 12:00
    assert eq.loc[open_hour] == pytest.approx(ACCOUNT * (1 - FEE))  # the open fee on the 2000 opened
    assert stats["deployed"]["return"] == pytest.approx(eq.iloc[-1] / ACCOUNT - 1)


def test_hl_deployed_ignores_idle_cash_and_deposits(tw_env):
    lo = ts(LO)
    price = steps((0, 100), (lo + 2 * D, 200))
    small, *_ = tw_env(held(1), price, [["allTime", history((lo, 1000), pnl=[0])]])
    big, *_ = tw_env(held(1), price, [["allTime", history((lo, 1_000_000), pnl=[0])]],
                     [flow(lo + D, "deposit", usdc="5000000.0")])
    assert np.allclose(small.to_numpy(), big.to_numpy())
    assert small.iloc[-1] == pytest.approx(2 * ACCOUNT)  # +100 on the 100 held: the account plays no part


def test_hl_deployed_is_bounded_by_the_price_move_on_a_tiny_account(tw_env):
    lo = ts(LO)
    # 1 BTC held, the price moves 5%; the account snapshot is $1: the old account return would be
    # +250% on the $2 floor. The deployed return is 5%.
    price = steps((0, 100), (lo + 2 * D, 105))
    eq, stats, *_ = tw_env(held(1), price, [["allTime", history((lo, 1), pnl=[0])]])
    r = hourly_returns(eq)
    assert np.abs(r).max() <= 0.05 + 2 * FEE + 1e-12
    assert eq.iloc[-1] == pytest.approx(ACCOUNT * 1.05)


def test_hl_deployed_open_and_close_inside_one_hour(hl_curve_env):
    lo = ts(LO)
    h = lo + D + 3600  # the hour the two fills land in
    fills = [fill("ETH", "B", 100, 1, (lo + D + 600) * 1000, 0, 1),
             fill("ETH", "A", 105, 1, (lo + D + 1200) * 1000, 1, 2, "Close Long")]
    eq, stats, *_ = hl_curve_env(fills)
    r = (5 - FEE * 205) / 100  # the opening notional is the capital
    assert np.allclose(eq.loc[:h - 3600].to_numpy(), ACCOUNT)
    assert eq.loc[h] == pytest.approx(ACCOUNT * (1 + r))
    assert np.allclose(eq.loc[h:].to_numpy(), ACCOUNT * (1 + r))
    assert stats["trades"] == 1


def test_hl_deployed_short_gains_on_its_notional(hl_curve_env):
    lo = ts(LO)
    s = lo + 2 * D
    eq, *_ = hl_curve_env([fill("ETH", "A", 100, 1, (lo + D + 1800) * 1000, 0, 1, "Open Short")],
                          price=step_at(s, 100, 90))
    assert eq.loc[lo + D + 3600] == pytest.approx(ACCOUNT * (1 - FEE))
    assert eq.loc[s + 3600] / eq.loc[s] == pytest.approx(1.1)  # +10 on the 100 short
    assert eq.iloc[-1] == pytest.approx(ACCOUNT * (1 - FEE) * 1.1)


def test_hl_deployed_after_a_flip_the_new_side_earns_on_its_notional(hl_curve_env):
    lo = ts(LO)
    s = lo + 3 * D
    # Short 1 at 100, then buy 2 at 100 (long 1); the price goes to 110 a day later: +10 on 100.
    fills = [fill("ETH", "A", 100, 1, (lo + D + 1800) * 1000, 0, 1, "Open Short"),
             fill("ETH", "B", 100, 2, (lo + 2 * D) * 1000, -1, 2, "Close Short")]
    eq, stats, *_ = hl_curve_env(fills, price=step_at(s, 100, 110))
    assert eq.loc[s + 3600] / eq.loc[s] == pytest.approx(1.1)
    assert stats["trades"] == 1 and stats["short_share"] == 1.0


def test_hl_deployed_no_fill_inside_and_no_position_is_flat(hl_curve_env):
    lo = ts(LO)
    fills = [fill("BTC", "B", 50, 1, (lo - 10 * D) * 1000, 0, 1),
             fill("BTC", "A", 60, 1, (lo - 9 * D) * 1000, 1, 2, "Close Long")]
    eq, stats, *_ = hl_curve_env(fills, price=step_at(lo + 2 * D, 100, 150))
    assert np.allclose(eq.to_numpy(), ACCOUNT)
    assert stats["deployed"]["return"] == 0


# mimic_deployed: a fake Result (equity at each bar's close, clock = the bars' opens) on fake bars.

def mimic_env(t0, closes, equity, trades):
    """Hour1 bars closing at `closes` from t0, a Result-like object, and the hourly grid."""
    n = len(closes)
    opens = t0 + 3600 * np.arange(n, dtype="int64")
    price = lambda t: np.asarray(closes, float)[np.clip(np.searchsorted(opens, t, side="right") - 1, 0, n - 1)]
    markets = avbt_cpp.Markets([avbt_cpp.Market("BTC", make_bars(M.TFS, t0, t0 + 3600 * n, price))])
    r = NS(equity=np.asarray(equity, float), clock=opens, timeframe=TF.Hour1, trades=list(trades),
           open_positions=[], ending_balance=float(equity[-1]), version=avbt_cpp.version)
    return r, markets, pd.Index(opens + 3600)


def mtrade(entry_bar, exit_bar, clock, size, entry_price, exit_price, side=None):
    return NS(instrument="BTC", entry_bar=entry_bar, exit_bar=exit_bar, entry_time=int(clock[entry_bar]),
              exit_time=int(clock[exit_bar]), side=side or avbt_cpp.Side.Long, entry_price=float(entry_price),
              exit_price=float(exit_price), size=float(size), leverage=1.0, fees=0.0, holding_costs=0.0,
              result=0.0, cause=avbt_cpp.Cause.Order)


def test_mimic_deployed_matches_a_hand_count(hl):
    t0 = ts(LO)
    closes = [100, 100, 110, 99]
    # Entered at the open of bar 1 (100), marked at the closes of bars 1 and 2, out at the open of bar 3 (99).
    r, markets, grid = mimic_env(t0, closes, [ACCOUNT, ACCOUNT, ACCOUNT + 100, ACCOUNT - 10],
                                 [mtrade(1, 3, t0 + 3600 * np.arange(4), 10, 100, 99)])
    eq = M.mimic_deployed(r, markets, grid)
    assert isinstance(eq, pd.Series) and list(eq.index) == list(grid)
    # Nothing on the entry's close (no PnL yet); +100 on the 1000 held; -110 on the 1100 held at
    # the close before the exit, which is still open at that close.
    assert np.allclose(eq.to_numpy(), ACCOUNT * np.array([1.0, 1.0, 1.1, 1.1 * 0.9]))


def test_mimic_deployed_does_not_change_with_the_risk(hl):
    t0 = ts(LO)
    closes = [100, 100, 110, 99]
    clock = t0 + 3600 * np.arange(4)
    small, markets, grid = mimic_env(t0, closes, [ACCOUNT, ACCOUNT, ACCOUNT + 100, ACCOUNT - 10],
                                     [mtrade(1, 3, clock, 10, 100, 99)])
    large, *_ = mimic_env(t0, closes, [ACCOUNT, ACCOUNT, ACCOUNT + 1000, ACCOUNT - 100],
                          [mtrade(1, 3, clock, 100, 100, 99)])
    a, b = M.mimic_deployed(small, markets, grid), M.mimic_deployed(large, markets, grid)
    assert np.allclose(a.to_numpy(), b.to_numpy())


def test_mimic_deployed_short_and_no_trades(hl):
    t0 = ts(LO)
    closes = [100, 100, 90, 90]
    clock = t0 + 3600 * np.arange(4)
    r, markets, grid = mimic_env(t0, closes, [ACCOUNT, ACCOUNT, ACCOUNT + 100, ACCOUNT + 100],
                                 [mtrade(1, 3, clock, 10, 100, 90, side=avbt_cpp.Side.Short)])
    eq = M.mimic_deployed(r, markets, grid)
    assert np.allclose(eq.to_numpy(), ACCOUNT * np.array([1.0, 1.0, 1.1, 1.1]))  # +100 on the 1000 short
    flat, markets, grid = mimic_env(t0, closes, [ACCOUNT] * 4, [])
    assert np.allclose(M.mimic_deployed(flat, markets, grid).to_numpy(), ACCOUNT)


def test_mimic_deployed_on_a_real_backtest_is_scale_free(hl):
    # The library strategy with a long on every bar: twice the risk, the same deployed curve.
    t0 = ts(LO)
    n = 24 * 40
    k = np.arange(n)
    price = lambda t: 100 + 5 * np.sin((np.asarray(t) - t0) / 3600 / 7.0)
    bars = make_bars(M.TFS, t0, t0 + 3600 * n, price)
    markets = avbt_cpp.Markets([avbt_cpp.Market("BTC", bars)])
    grid = pd.Index(t0 + 3600 * (k + 1))
    costs = {"BTC": avbt_cpp.Costs(FEE, FEE)}
    p = {**M.START, "side": 1, "hold": 4, "stop_atrs": 8, "tp_atrs": 12, "timeframe": "1 hour"}
    params = {k: v for k, v in p.items() if k not in ("risk", "leverage")}
    params["timeframe"] = TF.Hour1
    curves = []
    for risk in (0.002, 0.02):
        s = avbt_cpp.PortfolioSettings()
        s.starting_balance, s.risk_per_trade, s.hard_stop, s.scale_risk_with_leverage = ACCOUNT, risk, 1.0, True
        r = avbt_cpp.run("mimic", {**params, "leverage": 3}, markets, costs, s)
        assert len(r.trades) > 2
        curves.append(M.mimic_deployed(r, markets, grid))
    assert not np.allclose(curves[0].to_numpy(), ACCOUNT)
    assert np.allclose(curves[0].to_numpy(), curves[1].to_numpy(), rtol=1e-6)


def test_mimic_deployed_on_a_4_hour_base_clock_places_fills_at_the_bar_close(hl):
    # A 4-hour base clock: a fill at a bar's open first shows in the equity 4 hours later.
    t0 = ts(LO)
    closes = [100] * 7 + [105] * 4 + [100]  # 12 hourly closes; 105 on the hours the trade is held
    opens = t0 + 3600 * np.arange(12, dtype="int64")
    price = lambda t: np.asarray(closes, float)[np.clip(np.searchsorted(opens, t, side="right") - 1, 0, 11)]
    markets = avbt_cpp.Markets([avbt_cpp.Market("BTC", make_bars((TF.Hour1, TF.Hour4), t0, t0 + 12 * 3600, price))])
    clock = t0 + 4 * 3600 * np.arange(3, dtype="int64")
    # In at the open of 4-hour bar 1 (shows at t0 + 8h), out at the open of bar 2 (shows at t0 + 12h).
    r = NS(equity=np.array([ACCOUNT, ACCOUNT, ACCOUNT + 100.0]), clock=clock, timeframe=TF.Hour4,
           trades=[mtrade(1, 2, clock, 10, 100, 110)], open_positions=[], ending_balance=ACCOUNT + 100.0,
           version=avbt_cpp.version)
    grid = pd.Index(opens + 3600)
    eq = M.mimic_deployed(r, markets, grid)
    # +100 at the last hour, on the 1050 held the hour before; nothing is dropped.
    assert np.allclose(eq.to_numpy(), ACCOUNT * np.array([1.0] * 11 + [1 + 100 / 1050]))
    assert M.mimic_dropped(r, markets, grid) == (0.0, 0.0)


def test_mimic_deployed_4_hour_signal_on_a_real_backtest_drops_no_pnl(hl):
    # Signals on 4-hour bars, fills on the 1-hour base clock: every exit's PnL lands on a held hour,
    # and the curve compounds each long's exit over entry less the fee at each end.
    t0 = ts(LO)
    n = 24 * 40
    price = lambda t: 100 + 5 * np.sin((np.asarray(t) - t0) / 3600 / 7.0)
    markets = avbt_cpp.Markets([avbt_cpp.Market("BTC", make_bars((TF.Hour1, TF.Hour4), t0, t0 + 3600 * n, price))])
    grid = pd.Index(t0 + 3600 * (np.arange(n) + 1))
    p = {**M.START, "side": 1, "hold": 4, "stop_atrs": 8, "tp_atrs": 12, "timeframe": "4 hours"}
    r = M.backtest(p, markets, {"BTC": avbt_cpp.Costs(FEE, FEE)})
    assert r.timeframe == TF.Hour1 and len(r.trades) > 2
    assert M.mimic_dropped(r, markets, grid) == (0.0, 0.0)
    eq = M.mimic_deployed(r, markets, grid)
    hand = np.prod([(1 - FEE) ** 2 * t.exit_price / t.entry_price for t in r.trades])
    assert eq.iloc[-1] / ACCOUNT == pytest.approx(hand, rel=1e-9)


def test_dropped_pnl_counts_pnl_on_hours_with_no_deployed_capital():
    P = pd.Series([0.0, 0.0, 5.0, 5.0, -3.0])  # moves: 0, +5, 0, -8
    assert M.dropped_pnl(P, np.zeros(5), np.zeros(5)) == (13.0, 1.0)
    assert M.dropped_pnl(P, [0, 1, 1, 0, 0], np.zeros(5)) == (8.0, 8 / 13)  # the +5 lands on the 1 held
    assert M.dropped_pnl(P, [0, 1, 1, 1, 0], np.zeros(5)) == (0.0, 0.0)
    assert M.dropped_pnl(P, np.zeros(5), [0, 0, 1, 0, 1]) == (0.0, 0.0)  # or on the 1 opened in the hour
    assert M.dropped_pnl(pd.Series(np.zeros(5)), np.zeros(5), np.zeros(5)) == (0.0, 0.0)


def test_mimic_deployed_counts_a_position_still_open_at_the_end(hl):
    # In at the open of bar 1 (100) and never closed: its mark is in the equity, it is not in trades.
    t0 = ts(LO)
    closes = [100, 100, 110, 99]
    r, markets, grid = mimic_env(t0, closes, [ACCOUNT, ACCOUNT, ACCOUNT + 100, ACCOUNT - 10], [])
    r.open_positions = [avbt_cpp.Position("BTC", avbt_cpp.Side.Long, 100.0, 10.0, int(r.clock[1]))]
    P, N, T = M.mimic_pnl(r, markets, grid)
    assert list(T) == [0.0, 1000.0, 0.0, 0.0]  # opened at the hour its fill first shows
    assert list(N) == [0.0, 1000.0, 1100.0, 990.0]  # held to the end, at each hour's close
    assert M.mimic_dropped(r, markets, grid) == (0.0, 0.0)
    eq = M.mimic_deployed(r, markets, grid)
    assert np.allclose(eq.to_numpy(), ACCOUNT * np.array([1.0, 1.0, 1.1, 1.1 * (1 - 110 / 1100)]))


def test_mimic_scores_on_a_real_backtest_ending_with_an_open_position_drop_no_pnl(hl):
    # The last long is still open when the data ends: not a trade, but its PnL moves the equity.
    t0 = ts(LO)
    n = 24 * 10
    price = lambda t: 100 + 5 * np.sin((np.asarray(t) - t0) / 3600 / 7.0)
    markets = avbt_cpp.Markets([avbt_cpp.Market("BTC", make_bars((TF.Hour1, TF.Hour4), t0, t0 + 3600 * n, price))])
    grid = pd.Index(t0 + 3600 * (np.arange(n) + 1))
    p = {**M.START, "side": 1, "hold": 168, "stop_atrs": 8, "tp_atrs": 12, "timeframe": "1 hour"}
    r = M.backtest(p, markets, {"BTC": avbt_cpp.Costs(FEE, FEE)})
    assert len(r.open_positions) == 1 and r.equity[-1] != r.ending_balance
    last = r.open_positions[0]
    assert last.entry_time > max(t.exit_time for t in r.trades)
    s = M.mimic_scores(r, markets, grid)
    assert (s["dropped_pnl"], s["dropped_share"]) == (0.0, 0.0)
    # The open position's mark is the last long's move at the closes since its entry.
    P, N, T = M.mimic_pnl(r, markets, grid)
    a = np.searchsorted(grid, last.entry_time + 3600)
    assert np.allclose(N[a:], last.size * M.market_closes(markets, grid)["BTC"][a:])
    assert T[a] == last.size * last.entry_price


def test_mimic_scores_and_report_warn_on_dropped_pnl(hl, capsys):
    t0 = ts(LO)
    n = 24 * 10
    price = lambda t: 100 + 5 * np.sin((np.asarray(t) - t0) / 3600 / 7.0)
    markets = avbt_cpp.Markets([avbt_cpp.Market("BTC", make_bars((TF.Hour1, TF.Hour4), t0, t0 + 3600 * n, price))])
    grid = pd.Index(t0 + 3600 * (np.arange(n) + 1))
    p = {**M.START, "side": 1, "hold": 4, "stop_atrs": 8, "tp_atrs": 12, "timeframe": "1 hour"}
    r = M.backtest(p, markets, {"BTC": avbt_cpp.Costs(FEE, FEE)})
    s = M.mimic_scores(r, markets, grid)
    assert len(r.trades) > 0
    assert s["dropped_pnl"] == 0 and s["dropped_share"] == 0
    M.warn_dropped("mimic", s)
    assert capsys.readouterr().out == ""
    M.warn_dropped("mimic", {**s, "dropped_pnl": 7.0, "dropped_share": 0.02})
    assert "warning" in capsys.readouterr().out
    M.warn_dropped("mimic", {})  # an old file's scores
    assert capsys.readouterr().out == ""


# the fit's fixed account, and the score layout

def test_knobs_no_longer_hold_risk_or_leverage():
    assert "risk" not in M.KNOBS and "leverage" not in M.KNOBS
    assert "risk" not in M.START and "leverage" not in M.START


def test_backtest_fixes_risk_leverage_and_scale(monkeypatch):
    got = {}

    def run(name, params, markets, costs, settings):
        got.update(name=name, params=dict(params), settings=settings)
        return avbt_cpp.Result(np.array([1.0, 1.0]), TF.Hour1, np.array([0, 3600], dtype="int64"))
    monkeypatch.setattr(M.avbt_cpp, "run", run)
    M.backtest(dict(M.START), "markets", "costs")
    assert got["name"] == "mimic" and got["params"]["leverage"] == 3
    assert got["settings"].risk_per_trade == 0.002
    assert got["settings"].scale_risk_with_leverage is True
    assert got["settings"].starting_balance == ACCOUNT


SCORE_KEYS = {"sharpe", "max_drawdown", "return"}


def test_hl_curve_stats_hold_deployed_and_account_scores(hl_curve_env):
    lo = ts(LO)
    eq, stats, *_ = hl_curve_env([fill("BTC", "B", 100, 1, (lo + D + 1800) * 1000, 0, 1)],
                                 price=step_at(lo + 2 * D, 100, 120))
    assert SCORE_KEYS <= set(stats["deployed"]) and SCORE_KEYS <= set(stats["account"])
    for k in ("trades", "short_share", "pnl", "net_flows", "account_drift", "gap_share"):
        assert k in stats, k
    s = M.curve_stats(eq)
    np.testing.assert_equal([stats["deployed"][k] for k in SCORE_KEYS], [s[k] for k in SCORE_KEYS])
    # The $1000 account and the 100 deployed: the +20 is 2% of one, 20% of the other.
    assert stats["deployed"]["return"] == pytest.approx((1 - FEE) * 1.2 - 1)
    assert stats["account"]["return"] == pytest.approx((1 - FEE * 100 / 1000) * (1 + 20 / (1000 - FEE * 100)) - 1)


def test_avantis_curve_stats_hold_deployed_and_account_scores(avantis_env):
    eq, stats, *_ = avantis_env([trade(BASE + 5 * D + 6 * 3600, 1000, 100.0)])
    assert SCORE_KEYS <= set(stats["deployed"]) and SCORE_KEYS <= set(stats["account"])
    assert "trades" in stats and "short_share" in stats
    assert stats["account"]["return"] == pytest.approx(0.099)  # the peak-margin curve, as before
    s = M.curve_stats(eq)
    np.testing.assert_equal([stats["deployed"][k] for k in SCORE_KEYS], [s[k] for k in SCORE_KEYS])


def test_main_saves_deployed_and_account_scores_per_side(monkeypatch, tmp_path, hl):
    t0 = ts(LO)
    n = 24 * 40
    closes = 100 + 5 * np.sin(np.arange(n) / 7.0)
    opens = t0 + 3600 * np.arange(n, dtype="int64")
    price = lambda t: closes[np.clip(np.searchsorted(opens, t, side="right") - 1, 0, n - 1)]
    markets = avbt_cpp.Markets([avbt_cpp.Market("BTC", make_bars(M.TFS, t0, t0 + 3600 * n, price))])
    eq = hourly(t0 + 3600, n, ACCOUNT * (1 + 0.001 * np.arange(n)))
    wallet = {"deployed": M.curve_stats(eq), "account": M.curve_stats(eq), "trades": 3, "short_share": 1.0,
              "pnl": 10.0, "net_flows": 0.0, "account_drift": 0.0, "gap_share": 0.0}
    monkeypatch.setattr(M, "setup", lambda address, top: {
        "eq": eq, "wallet": wallet, "symbols": ["BTC"], "start": LO, "end": "2025-02-20",
        "markets": markets, "covered_share": 1.0})
    monkeypatch.setattr(M, "fit", lambda *a, **k: (dict(a[4]), 0.5))
    monkeypatch.setattr(sys, "argv", ["mimic.py", "--venue", "hyperliquid", "0x" + "ab" * 20])
    M.main()
    saved = json.loads(next(tmp_path.glob("mimic_hl_*.json")).read_text())
    for side in ("wallet", "mimic"):
        assert SCORE_KEYS <= set(saved[side]["deployed"]), side
        assert SCORE_KEYS <= set(saved[side]["account"]), side
        assert "trades" in saved[side], side
    assert "risk" not in saved["params"] and saved["settings"]["risk_per_trade"] == 0.002
    assert saved["settings"]["scale_risk_with_leverage"] is True
    assert saved["mse"] == 0.5

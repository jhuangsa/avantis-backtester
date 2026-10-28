"""examples/veranta_history.py: API rows to records, and the Veranta Copy check."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))
pytest.importorskip("Crypto")
V = pytest.importorskip("veranta_history")
import wallet_returns as W  # noqa: E402

T0 = 1_760_000_000  # unix seconds


def row(type_, oid, of=None, *, t=0, open_=False, coll=100.0, side="long", **kw):
    r = {
        "type": type_, "open": open_, "orderId": oid, "orderIdOfOpenTrade": of,
        "timestamp": T0 + t, "time": f"2025-10-09T{8 + t // 3600:02d}:53:20.000Z",
        "market": "BTC/USD", "pairIndex": 1, "side": side, "leverage": 10.0,
        "collateral": coll, "positionSize": coll * 10, "openPrice": 60000.0,
        "closePrice": 61000.0, "openFee": 0.5, "closeFee": 0.7, "borrowFee": 0.2,
        "funding": -0.1, "grossPnl": 16.0, "netPnl": 15.0, "usdcSentToTrader": 115.0,
        "tp": 70000.0, "sl": 55000.0, "isPartialClose": False, "txHash": f"0x{oid}",
    }
    r.update(kw)
    return r


def rows():
    """Newest first: a close of order 1, an increase, the open of order 1."""
    return [
        row("MARKET_CLOSE", 3, 1, t=7200, coll=150.0, openFee=None, borrowFee=None),
        row("POSITION_INCREASE", 2, t=3600, open_=True, coll=50.0),
        row("MARKET_OPEN", 1, t=0, open_=True, coll=100.0),
    ]


def test_close_maps_to_record():
    recs, added, orphans = V.records(rows())
    assert len(recs) == 1 and orphans == []
    r = recs[0]
    assert r["openedAt"] == T0  # its MARKET_OPEN's timestamp, not the close's
    assert r["positionSize"] == r["collateral"] == 150.0
    assert r["closingFee"] == pytest.approx(0.7)  # openFee None counts 0
    assert r["funding"] == pytest.approx(-0.1)  # borrowFee None counts 0
    assert r["buy"] is True and r["index"] == 1 and r["partial"] is False
    assert added == pytest.approx(50 / 150)


def test_fees_and_funding_sum_and_short_side():
    rs = rows()
    rs[0].update(openFee=0.5, borrowFee=0.2, side="short")
    r = V.records(rs)[0][0]
    assert r["closingFee"] == pytest.approx(1.2)
    assert r["funding"] == pytest.approx(0.1)
    assert r["buy"] is False


def test_no_rows_has_zero_increase_share():
    assert V.records([]) == ([], 0.0, [])


def test_close_without_open_is_dated_from_the_first_row():
    rs = rows()[:2]
    recs, _, orphans = V.records(rs)
    assert [r["txHash"] for r in orphans] == [rs[0]["txHash"]]
    assert recs[0]["openedAt"] == min(r["timestamp"] for r in rs)


def test_limit_open_counts_as_open():
    rs = rows()
    rs[2]["type"] = "LIMIT_OPEN"
    assert len(V.records(rs)[0]) == 1


def test_records_pass_wallet_returns_kept():
    recs = V.records(rows())[0]
    assert all(W.kept(r) for r in recs)


def page(rs, n=1, pnl=15.0):
    keys = ("txHash", "type", "market", "side", "collateral", "openPrice", "closePrice", "grossPnl")
    return {"stats": {"n_closes": n, "realized_pnl": pnl},
            "recent_trades": [{k: r[k] for k in keys} for r in rs[:2]]}


def run_check(monkeypatch, pg, rs):
    monkeypatch.setattr(V, "get", lambda url: pg)
    V.check("0xABC", rs, V.records(rs)[0])


def test_check_passes_on_match(monkeypatch, capsys):
    rs = rows()
    run_check(monkeypatch, page(rs), rs)
    assert "pass" in capsys.readouterr().out


@pytest.mark.parametrize("change, msg", [
    (lambda p: p["stats"].update(n_closes=2), "closes"),
    (lambda p: p["stats"].update(realized_pnl=16.0), "net PnL"),
    (lambda p: p["recent_trades"][0].update(grossPnl=0.0), "recent trades"),
])
def test_check_exits_on_mismatch(monkeypatch, change, msg):
    rs = rows()
    pg = page(rs)
    change(pg)
    with pytest.raises(SystemExit, match=msg):
        run_check(monkeypatch, pg, rs)


# ---------- costs and onchain_total ----------

def flow(tx, t, dir_, amt, who=None, method="Transfer"):
    """One USDC transfer at T0+t; who defaults to Trading Storage."""
    return {"tx": f"0x{tx}", "t": T0 + t, "dir": dir_, "who": who or V.STORAGE,
            "amt": amt, "method": method}


def opn(oid, t, coll, **kw):
    return row("MARKET_OPEN", oid, t=t, open_=True, coll=coll, **kw)


def close(oid, of, t, coll, sent, **kw):
    return row("MARKET_CLOSE", oid, of, t=t, coll=coll, usdcSentToTrader=sent, **kw)


def live(coll, at, **kw):
    return {"collateral": coll, "opened_at": at, **kw}


def run_costs(rs, flows, old=(), pos=()):
    old = list(old)
    out = V.costs(rs, old, V.records(rs)[2], flows, list(pos))
    return (*out, old)


def test_paid_fee_is_paid_in_its_tx_less_collateral():
    # open 1: $100 collateral; paid 100 + 1.25 in two transfers of its tx
    rs = [close(3, 1, 7200, 100, 115.0), opn(1, 0, 100)]
    fl = [flow(1, 0, "OUT", 100.0), flow(1, 0, "OUT", 1.25),
          flow(3, 7200, "IN", 115.0, V.VAULT)]
    drain, unmatched, problems, _ = run_costs(rs, fl)
    assert rs[1]["paidFee"] == pytest.approx(1.25)
    assert problems == [] and unmatched == pytest.approx(0)
    assert drain["amount"] == 0  # all closed, nothing live


def test_unpaid_increase_gets_median_rate_of_paid_opens():
    # positionSize = 10 x collateral; fees 1, 2, 9 on $1000: rates .001 .002 .009
    rs = [opn(1, 0, 100), opn(2, 1, 100), opn(3, 2, 100), opn(4, 3, 50, type="POSITION_INCREASE")]
    fl = [flow(1, 0, "OUT", 101.0), flow(2, 1, "OUT", 102.0), flow(3, 2, "OUT", 109.0)]
    _, unmatched, _, _ = run_costs(rs, fl)
    assert [r["paidFee"] for r in rs[:3]] == pytest.approx([1, 2, 9])
    assert rs[3]["paidFee"] == pytest.approx(0.002 * 500)
    # paid by an earlier order: its collateral and fee count against unmatched
    assert unmatched == pytest.approx(-51.0)


def test_open_paid_from_another_tx_has_the_history_open_fee():
    # a smart wallet pays $100.5 in its own tx; the open's tx moves no USDC
    rs = [opn(1, 0, 100, openFee=0.5)]
    _, unmatched, _, _ = run_costs(rs, [flow(9, 0, "OUT", 100.5)])
    assert rs[0]["paidFee"] == pytest.approx(0.5)
    assert unmatched == pytest.approx(0)


@pytest.mark.parametrize("got, bad", [(115.0, False), (115.009, False), (114.0, True), (None, True)])
def test_close_payout_must_match_the_chain(got, bad):
    rs = [close(3, 1, 7200, 100, 115.0), opn(1, 0, 100)]
    fl = [flow(1, 0, "OUT", 101.0)] + ([flow(3, 7200, "IN", got, V.VAULT)] if got else [])
    problems = run_costs(rs, fl)[2]
    assert len(problems) == bad
    if bad:
        assert problems[0].startswith("close 0x3 sent 115.00")


def test_update_margin_in_v2_lowers_what_is_left_open():
    # opens $100 and $20, $30 taken back by Update Margin; live holds $70:
    # drain 120 - 30 - 70
    rs = [opn(2, 600, 20), opn(1, 0, 100)]
    fl = [flow(1, 0, "OUT", 101.0), flow(2, 600, "OUT", 20.2),
          flow(9, 300, "IN", 30.0, V.VAULT, "Update Margin")]
    drain, unmatched, problems, _ = run_costs(rs, fl, pos=[live(70.0, T0)])
    assert drain == {"amount": pytest.approx(20.0), "from": T0, "to": T0 + 600}
    assert unmatched == pytest.approx(0)  # Update Margin is not an unmatched transfer
    assert problems == []
    # without it, 120 - 70 is drained
    fl.pop()
    assert run_costs(rs, fl, pos=[live(70.0, T0)])[0]["amount"] == pytest.approx(50.0)


def test_before_v2_old_fees_spread_by_margin_and_payouts_credited():
    # old closes: margin 100 and 300, sent 110 and 290; orphan carried in: $100.
    # before v2: paid in 525 = 500 margin + 25 fees; Vault paid 405 = 400 + 5 extra.
    # fee = 25 - 5 = 20, spread over margin 500.
    old = [{"positionSize": 100.0, "usdcSent": 110.0, "closingFee": 0.5},
           {"positionSize": 300.0, "usdcSent": 290.0}]
    rs = [close(3, 0, 7200, 100, 115.0)]  # its open is not in the history
    fl = [flow(7, -9000, "OUT", 525.0), flow(8, -5000, "IN", 405.0, V.VAULT),
          flow(3, 7200, "IN", 115.0, V.VAULT)]
    drain, unmatched, problems, old = run_costs(rs, fl, old)
    assert old[0]["closingFee"] == pytest.approx(0.5 + 20 * 100 / 500)
    assert old[1]["closingFee"] == pytest.approx(20 * 300 / 500)
    assert rs[0]["carryFee"] == pytest.approx(20 * 100 / 500)
    assert problems == [] and unmatched == pytest.approx(0)
    assert drain["amount"] == 0  # the orphan's close is not counted against v2 opens


def test_drain_from_oldest_live_to_last_row_floored_at_zero():
    # open 100 + increase 50 - partial close 60 = 90 left open
    rs = [close(3, 1, 7200, 60, 70.0), row("POSITION_INCREASE", 2, t=3600, open_=True, coll=50.0),
          opn(1, 0, 100)]
    fl = [flow(1, 0, "OUT", 101.0), flow(2, 3600, "OUT", 50.5), flow(3, 7200, "IN", 70.0, V.VAULT)]
    pos = [live(40.0, T0 + 5), live(30.0, T0 + 2)]
    drain = run_costs(rs, fl, pos=pos)[0]
    assert drain == {"amount": pytest.approx(20.0), "from": T0 + 2, "to": T0 + 7200}
    assert run_costs(rs, fl, pos=[live(120.0, T0)])[0]["amount"] == 0.0
    assert run_costs(rs, fl)[0]["from"] is None


def test_onchain_total_is_net_usdc_plus_open_margin_and_price_pnl():
    pd = pytest.importorskip("pandas")
    fl = [flow(1, 0, "OUT", 101.0), flow(2, 10, "IN", 50.0, V.VAULT),
          flow(3, 20, "OUT", 999.0, "0xelse")]  # not Veranta: left out
    px = {"BTC": pd.Series([90.0, 110.0], index=[T0, T0 + 100]),
          "ETH": pd.Series([180.0], index=[T0])}
    pos = [{"symbol": "BTC", "side": "long", "open_price": 100.0, "notional": 1000.0, "collateral": 100.0},
           {"symbol": "ETH", "side": "short", "open_price": 200.0, "notional": 500.0, "collateral": 50.0}]
    # -101 + 50 + (100 + 50) + 1000 * 0.1 + 500 * 0.1
    assert V.onchain_total(fl, pos, T0 + 100, px) == pytest.approx(249.0)
    # at T0 BTC is 90: 1000 * -0.1
    assert V.onchain_total(fl, pos, T0, px) == pytest.approx(49.0)


def test_ledger_total_at_the_end_equals_onchain_total():
    # BTC long: open 10 coins at 100 ($1000, $100, fee 1); increase 4 coins at 125
    # ($500, $50, fee 0.5): 14 coins, cost 1500. Partial close of 7 at the average
    # entry 1500/14 and 150: gross 300, close fee 0.7, funding 0.2 + 0.1, net 299;
    # sent 75 + 299. Left: 7 coins, cost 750, $75 open; live holds $60: drain 15.
    pd = pytest.importorskip("pandas")
    avg = 1500 / 14
    common = dict(openFee=None, market="BTC")
    rs = [close(3, 1, 7200, 75, 374.0, positionSize=750.0, openPrice=avg, closePrice=150.0,
                grossPnl=300.0, closeFee=0.7, funding=0.2, borrowFee=0.1, netPnl=299.0,
                isPartialClose=True, **common),
          row("POSITION_INCREASE", 2, t=3600, open_=True, coll=50.0, positionSize=500.0,
              openPrice=125.0, **common),
          opn(1, 0, 100, positionSize=1000.0, openPrice=100.0, **common)]
    fl = [flow(1, 0, "OUT", 101.0), flow(2, 3600, "OUT", 50.5), flow(3, 7200, "IN", 374.0, V.VAULT)]
    pos = [{"symbol": "BTC", "side": "long", "open_price": avg, "notional": 750.0,
            "collateral": 60.0, "opened_at": T0}]
    drain, unmatched, problems, _ = run_costs(rs, fl, pos=pos)
    assert problems == [] and unmatched == pytest.approx(0)
    assert drain["amount"] == pytest.approx(15.0)
    px = {"BTC": pd.Series([100.0, 125.0, 150.0], index=[T0, T0 + 3600, T0 + 7200])}
    ts = [T0, T0 + 3600, T0 + 7200]
    df = W.ledger("veranta", rs + [{"drain": drain}], px, times=ts)
    end = df.iloc[-1]
    # realized -1 - 0.5 + 299; price 7 * 150 - 750; drain -15
    assert end["realized"] == pytest.approx(297.5)
    assert end["unrealized_price"] == pytest.approx(300.0)
    assert end["unrealized_funding"] == pytest.approx(-15.0)
    assert end["fees"] == pytest.approx(1.5 + 0.7)
    chain = V.onchain_total(fl, pos, T0 + 7200, px)
    assert chain == pytest.approx(582.5)
    assert end["pnl"] == pytest.approx(chain)


def test_old_liquidation_fee_is_not_counted_twice():
    # gross is the whole $33.33 margin; closingFee 9.999 is the liquidation fee
    liq = {"gross": -33.33, "positionSize": 33.33, "usdcSent": 0, "closingFee": 9.999, "funding": 0}
    assert V.M.vt.cap_fee(liq)["closingFee"] == pytest.approx(0)
    # a close that adds up is left alone
    ok = {"gross": -18.145, "positionSize": 21.379, "usdcSent": 0, "closingFee": 3.207, "funding": 0.027}
    assert V.M.vt.cap_fee(ok)["closingFee"] == pytest.approx(3.207)


def test_record_caps_an_old_liquidation():
    item = {"timeStamp": "2026-05-23T18:33:49.000Z", "_grossPnl": -33.33, "event": {"args": {
        "t": {"pairIndex": 1, "buy": True, "leverage": 10, "initialPosToken": 33.33, "timestamp": 1},
        "positionSizeUSDC": 33.33, "usdcSentToTrader": 0,
        "feeInfo": {"closingFee": 9.999, "r": 0}}}}
    r = V.M.vt._record(item, {1: "SOL/USD"})
    assert r["gross"] - r["closingFee"] - r["funding"] == pytest.approx(r["usdcSent"] - r["positionSize"])


def test_old_closes_caps_a_cached_liquidation(monkeypatch, tmp_path):
    monkeypatch.setattr(V.M, "CACHE", tmp_path)
    row = {"gross": -33.33, "positionSize": 33.33, "usdcSent": 0, "closingFee": 9.999, "funding": 0}
    V.M.save_json(tmp_path / "wallet_0xA_v1.json", {"trades": [row]})
    assert V.old_closes("0xA")[0]["closingFee"] == pytest.approx(0)


@pytest.mark.parametrize("text, want", [("1,234.5", 1234.5), ("0.0₅1", 0.000001), ("0.0₁₂3", 3e-13)])
def test_basescan_amount_reads_subscript_zeros(text, want):
    assert V.amount(text) == pytest.approx(want)

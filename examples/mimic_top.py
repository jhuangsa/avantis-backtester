"""The leaderboard's top Avantis wallets and their saved mimic fits on one page.

Reads the top N rows of the leaderboard CSV, each wallet's saved fit
(data/candles/mimic_<address>.json) and its downloaded trade history, and
sets the history beside the CSV: trades, margin, PnL and last trade. A
wallet whose history holds under 90% of the CSV's margin is flagged: its
curve, and so its fit, leaves out much of its trading.

Run from the repository root, after examples/mimic.py on each wallet:

    python3 examples/mimic_top.py data/verantacopy-traders-all-time-returns.csv 5

Writes examples/mimic_top<N>.html.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mimic_chart as C  # noqa: E402
import mimic_hl_all as A  # noqa: E402

M = C.M


def history(address):
    """The downloaded history's closes, positions, margin, net PnL and last close."""
    df = pd.DataFrame(json.loads((M.CACHE / f"wallet_{address}.json").read_text())["trades"])
    size, sent = (pd.to_numeric(df[k], errors="coerce") for k in ("positionSize", "usdcSent"))
    positions = df.groupby(["symbol", "buy", "index", "openPrice", df.openedAt // 60]).ngroups
    return {"closes": len(df), "positions": positions, "margin": float(size.sum()),
            "pnl": float((sent - size).sum()), "last": df.time.max()[:10]}


def main():
    csv, top = Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 5
    rows = []
    for _, c in pd.read_csv(csv).head(top).iterrows():
        address = M.checksum(c.wallet_address)
        path = M.CACHE / f"mimic_{address}.json"
        if not path.exists():
            print(f"{address}: no saved fit, left out")
            continue
        saved = json.loads(path.read_text())
        curves = C.curves(saved)
        rows.append({"rank": int(c["rank"]), "address": address, "markets": saved["markets"],
                     "covered": saved.get("covered_share"), "mse": saved["mse"], "params": saved["params"],
                     "wallet_stats": C.clean(M.score_sets(saved["wallet"])),
                     "mimic_stats": C.clean(M.score_sets(saved["mimic"])),
                     "csv": {"trades": int(c.closed_trades), "margin": float(c.collateral_used_usdc),
                             "pnl": float(c.all_time_realized_pnl_usdc), "last": c.last_trade},
                     "api": history(address), "t": curves["t"], "wallet": curves["wallet"], "mimic": curves["mimic"]})
    out = HERE / f"mimic_top{top}.html"
    out.write_text(PAGE.replace("/*DATA*/null", json.dumps(rows)))
    print(f"wrote {out}, {len(rows)} wallets")


PAGE = (A.PAGE.split("<script>")[0]
        .replace("<title>Hyperliquid Wallet Mimics</title>", "<title>Avantis Top Wallet Mimics</title>")
        .replace("<h1>Hyperliquid wallet mimics</h1>", "<h1>Avantis top wallet mimics</h1>")
        .replace('<div class="scroll"><table id="stats"></table></div>',
                 '<div class="scroll"><table id="stats"></table></div>\n'
                 '<h2>Trade history against the leaderboard</h2>\n<div class="scroll"><table id="check"></table></div>')
        ) + r"""<script>
const D=/*DATA*/null;
document.getElementById("sub").textContent=`The leaderboard's top ${D.length} wallets on Avantis, each fitted with the mimic strategy over its whole downloaded history. `+
"Every number is on the return on peak own capital: total PnL, realized plus unrealized, over the most of the trader's own money ever in positions at once, not compounded. "+
"The fit makes the mimic's curve match the wallet's. "+
"The wallet pays its real closing fees and funding from its trade history; the mimic pays 0.05% of notional per round trip and no funding. "+
"Copy error is the root of the MSE between the two curves. "+
"A flagged wallet's downloaded history holds under 90% of the margin the leaderboard counts: its curve leaves out much of its trading.";
""" + A.COMMON + r"""const share=r=>r.api.margin/r.csv.margin;
const flagged=r=>share(r)<0.9;
const name=r=>`#${r.rank} <a href="#w${r.address}">${short(r.address)}</a>${flagged(r)?' <span class="flag" title="the history holds under 90% of the leaderboard margin">!</span>':""}`;
let h=`<tr><th>Wallet</th><th>Copy error</th>${HEAD}<th>Covered</th></tr>`;
for(const r of D)h+=`<tr><td>${name(r)}</td><td>${(Math.sqrt(r.mse)*100).toFixed(1)}%</td>${cells(r)}<td>${r.covered==null?"–":dd(r.covered)}</td></tr>`;
document.getElementById("stats").innerHTML=h;
let k=`<tr><th>Wallet</th><th>Trades, leaderboard</th><th>Closes, history</th><th>Positions, history</th>`+
`<th>Margin, leaderboard</th><th>Margin, history</th><th>Share</th><th>PnL, leaderboard</th><th>PnL, history</th>`+
`<th>Last trade, leaderboard</th><th>Last close, history</th></tr>`;
for(const r of D){const c=r.csv,a=r.api;
k+=`<tr><td>${name(r)}</td><td>${c.trades}</td><td>${a.closes}</td><td>${a.positions}</td><td>${usd(c.margin)}</td><td>${usd(a.margin)}</td>
<td${flagged(r)?' class="flag"':""}>${dd(share(r))}</td><td${cls(c.pnl)}>${usd(c.pnl)}</td><td${cls(a.pnl)}>${usd(a.pnl)}</td><td>${c.last}</td><td>${a.last}</td></tr>`}
document.getElementById("check").innerHTML=k;
""" + A.CARDS.replace("<h3>${short(r.address)}</h3>", "<h3>#${r.rank} ${short(r.address)}</h3>") + "</script></body></html>\n"


if __name__ == "__main__":
    main()

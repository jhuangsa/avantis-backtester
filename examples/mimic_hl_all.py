"""All saved Hyperliquid mimic fits on one page: stats and both returns on peak own capital.

The table shows the scores on peak own capital first, on the account
second, and flags a wallet whose account snapshots drift over 25% from
the computed account.

Run from the repository root, after examples/mimic.py --venue hyperliquid:

    python3 examples/mimic_hl_all.py

Writes examples/mimic_hl_all.html. Only some wallets, to another page:

    python3 examples/mimic_hl_all.py --out mimic_hl_top5.html 0xabc... 0xdef...

The page is written in examples/; the table is sorted by the wallet's
final return on peak own capital.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mimic_chart as C  # noqa: E402


def parse(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("addresses", nargs="*", help="wallets to show; default all saved")
    ap.add_argument("--out", default="mimic_hl_all.html", help="page name in examples/")
    return ap.parse_args(argv)


def pick(saves, addresses):
    """The saves whose address is in addresses (any case); all if none given."""
    want = {a.lower() for a in addresses}
    return [s for s in saves if not want or s["address"].lower() in want]


def main(argv=None):
    args = parse(argv)
    saves = [json.loads(p.read_text()) for p in sorted(C.M.CACHE.glob("mimic_hl_0x*.json"))]
    saves = pick(saves, args.addresses)
    if len(saves) < len(set(a.lower() for a in args.addresses)):
        raise SystemExit("some addresses have no saved fit")
    rows = []
    for saved in saves:
        c = C.curves(saved)
        rows.append({"address": saved["address"], "markets": saved["markets"], "start": saved["start"],
                     "end": saved["end"], "mse": saved["mse"], "wallet_stats": C.clean(C.M.score_sets(saved["wallet"])),
                     "mimic_stats": C.clean(C.M.score_sets(saved["mimic"])), "params": saved["params"], "fit": saved.get("fit"),
                     "t": c["t"], "wallet": c["wallet"], "mimic": c["mimic"]})
    rows.sort(key=lambda r: -(r["wallet_stats"]["own"]["final"] or 0))
    out = HERE / args.out
    out.write_text(PAGE.replace("/*DATA*/null", json.dumps(rows)))
    print(f"wrote {out}, {len(rows)} wallets")


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hyperliquid Wallet Mimics</title>
<style>
:root{--surface:#fcfcfb;--panel:#f4f3f0;--line:#e3e2de;--ink:#0b0b0b;--ink2:#52514e;--ink3:#8a8984;
--s1:#2a78d6;--s2:#eb6834;--neg:#c23a3a}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--surface:#1a1a19;--panel:#232321;--line:#383835;
--ink:#fff;--ink2:#c3c2b7;--ink3:#8a8984;--s1:#3987e5;--s2:#d95926;--neg:#e06666}}
:root[data-theme="dark"]{--surface:#1a1a19;--panel:#232321;--line:#383835;--ink:#fff;--ink2:#c3c2b7;
--ink3:#8a8984;--s1:#3987e5;--s2:#d95926;--neg:#e06666}
*{box-sizing:border-box}
body{margin:0;background:var(--surface);color:var(--ink);font:15px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:1200px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:36px 0 8px}
.sub{color:var(--ink2);margin:0 0 20px}
.legend{display:flex;gap:18px;color:var(--ink2);font-size:13px;margin:4px 0 12px}
.legend i{display:inline-block;width:14px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
td,th{text-align:right;padding:6px 8px;white-space:nowrap;border-bottom:1px solid var(--line)}
th{color:var(--ink2);font-weight:500}td:first-child,th:first-child{text-align:left}
.neg{color:var(--neg)}a{color:inherit}.flag{color:var(--neg);font-weight:600}
th.set{text-align:center;border-bottom:none;padding-bottom:0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px}
.card{background:var(--panel);border-radius:10px;padding:12px 14px}
.card h3{font-size:14px;margin:0;font-family:ui-monospace,monospace}
.card .m{color:var(--ink3);font-size:12px;margin-bottom:2px;overflow-wrap:anywhere}
.card .legend{font-size:12px;margin:0 0 4px}
.card .s{display:grid;grid-template-columns:auto 1fr 1fr;gap:0 10px;font-size:12px;
font-variant-numeric:tabular-nums;margin-top:6px}
.card .s span:nth-child(3n+1){color:var(--ink3)}
svg{display:block;width:100%;height:auto}svg text{fill:var(--ink3);font-size:10px}
</style></head><body><main>
<h1>Hyperliquid wallet mimics</h1>
<p class="sub" id="sub"></p>
<div class="legend"><span><i style="background:var(--s1)"></i>Wallet</span>
<span><i style="background:var(--s2)"></i>Mimic</span></div>
<h2>Stats</h2>
<div class="scroll"><table id="stats"></table></div>
<h2>PnL curves</h2>
<div class="grid" id="cards"></div>
</main>
<script>
const D=/*DATA*/null;
document.getElementById("sub").textContent=`${D.length} wallets from OptimistFi, each fitted with the mimic strategy over its last 200 days. `+
"Both curves are the return on peak own capital: total PnL over the most of the trader's own money ever in positions at once, not compounded: the curve the fit minimizes. "+
"The wallet's margin is estimated from its current leverage setting, so its return can read high. "+
"The account scores are a time-weighted return on the wallet's whole Hyperliquid account, perp plus spot, with deposits and withdrawals taken out, and the mimic's $10,000 account. "+
"Both curves count PnL realized plus unrealized, open positions valued at each hour's close. "+
"A flagged wallet's account snapshots drift over 25% from the computed account: its account scores are unreliable. "+
"The mimic pays 0.05% of notional per round trip and no funding; the wallet its real fees and funding. "+
"Copy error is the root of the MSE between the two curves.";
const pct=(x,d=0)=>x==null?"–":(x>0?"+":"")+(x*100).toFixed(d)+"%";
const fix=(x,d)=>x==null?"–":x.toFixed(d);
const dd=x=>x==null?"–":(x*100).toFixed(0)+"%";
const cls=x=>x<0?' class="neg"':"";
const short=a=>a.slice(0,6)+"…"+a.slice(-4);
const cols=s=>`<td${cls(s.sharpe)}>${fix(s.sharpe,2)}</td><td${cls(s.return)}>${pct(s.return)}</td><td>${dd(s.max_drawdown)}</td>`;
const owns=s=>`<td${cls(s.final)}>${pct(s.final)}</td><td${cls(s.peak)}>${pct(s.peak)}</td><td${cls(s.lowest)}>${pct(s.lowest)}</td>`;
const six="<th>Wallet Sharpe</th><th>Wallet return</th><th>Wallet max DD</th><th>Mimic Sharpe</th><th>Mimic return</th><th>Mimic max DD</th>";
const sixo="<th>Wallet final</th><th>Wallet peak</th><th>Wallet lowest</th><th>Mimic final</th><th>Mimic peak</th><th>Mimic lowest</th>";
let h=`<tr><th></th><th></th><th class="set" colspan="6">On peak own capital</th><th class="set" colspan="6">On the account</th><th></th><th></th><th></th></tr>`+
`<tr><th>Wallet</th><th>Copy error</th>${sixo}${six}<th>Wallet trades</th><th>Mimic trades</th><th>Account drift</th></tr>`;
for(const r of D){const w=r.wallet_stats,m=r.mimic_stats,drift=w.account_drift;
const flag=drift!=null&&drift>0.25;
h+=`<tr><td><a href="#w${r.address}">${short(r.address)}</a>${flag?' <span class="flag" title="the account snapshots drift over 25% from the computed account">!</span>':""}</td>
<td>${(Math.sqrt(r.mse)*100).toFixed(1)}%</td>${owns(w.own)}${owns(m.own)}${cols(w.account)}${cols(m.account)}
<td>${w.trades}</td><td>${m.trades}</td><td${flag?' class="flag"':""}>${drift==null?"–":(drift*100).toFixed(0)+"%"}</td></tr>`}
document.getElementById("stats").innerHTML=h;
function chart(r){const W=320,H=150,L=34,B=16,T=6;
const ys=r.wallet.concat(r.mimic).filter(v=>v!=null),lo=Math.min(0,...ys),hi=Math.max(0,...ys),n=r.t.length-1;
const x=i=>L+(W-L-4)*i/n,y=v=>T+(H-T-B)*(1-(v-lo)/((hi-lo)||1));
const path=a=>{let d="",up=false;a.forEach((v,i)=>{if(v==null){up=false;return}d+=(up?"L":"M")+x(i).toFixed(1)+","+y(v).toFixed(1);up=true});return d};
const d0=new Date(r.t[0]*1e3).toISOString().slice(0,10),d1=new Date(r.t[n]*1e3).toISOString().slice(0,10);
return `<svg viewBox="0 0 ${W} ${H}"><line x1="${L}" x2="${W-4}" y1="${y(0)}" y2="${y(0)}" stroke="var(--line)"/>
<text x="${L-4}" y="${y(hi)+8}" text-anchor="end">${pct(hi)}</text><text x="${L-4}" y="${y(lo)}" text-anchor="end">${pct(lo)}</text>
<text x="${L}" y="${H-2}">${d0}</text><text x="${W-4}" y="${H-2}" text-anchor="end">${d1}</text>
<path d="${path(r.wallet)}" fill="none" stroke="var(--s1)" stroke-width="1.6"/>
<path d="${path(r.mimic)}" fill="none" stroke="var(--s2)" stroke-width="1.6"/></svg>`}
document.getElementById("cards").innerHTML=D.map(r=>{const w=r.wallet_stats,m=r.mimic_stats;
return `<div class="card" id="w${r.address}"><h3>${short(r.address)}</h3>
<div class="m">${r.markets.join(", ")} · ${r.params.timeframe}</div>
<div class="legend"><span><i style="background:var(--s1)"></i>Wallet</span>
<span><i style="background:var(--s2)"></i>Mimic</span></div>${chart(r)}
<div class="s"><span></span><span style="color:var(--s1)">Wallet</span><span style="color:var(--s2)">Mimic</span>
<span>Return (own, final)</span><span${cls(w.own.final)}>${pct(w.own.final)}</span><span${cls(m.own.final)}>${pct(m.own.final)}</span>
<span>Peak (own)</span><span>${pct(w.own.peak)}</span><span>${pct(m.own.peak)}</span>
<span>Lowest (own)</span><span>${pct(w.own.lowest)}</span><span>${pct(m.own.lowest)}</span>
<span>Return (account)</span><span${cls(w.account.return)}>${pct(w.account.return)}</span><span${cls(m.account.return)}>${pct(m.account.return)}</span>
<span>Sharpe (account)</span><span>${fix(w.account.sharpe,2)}</span><span>${fix(m.account.sharpe,2)}</span>
<span>Max DD (account)</span><span>${dd(w.account.max_drawdown)}</span><span>${dd(m.account.max_drawdown)}</span>
<span>Trades</span><span>${w.trades}</span><span>${m.trades}</span></div></div>`}).join("");
</script></body></html>
"""

if __name__ == "__main__":
    main()

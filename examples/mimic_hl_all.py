"""All saved Hyperliquid mimic fits on one page: stats and both PnL curves.

Run from the repository root, after examples/mimic.py --venue hyperliquid:

    python3 examples/mimic_hl_all.py

Writes examples/mimic_hl_all.html.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mimic_chart as C  # noqa: E402


def main():
    rows = []
    for path in sorted(C.M.CACHE.glob("mimic_hl_0x*.json")):
        saved = json.loads(path.read_text())
        c = C.curves(saved)
        rows.append({"address": saved["address"], "markets": saved["markets"], "start": saved["start"],
                     "end": saved["end"], "mse": saved["mse"], "wallet_stats": saved["wallet"],
                     "mimic_stats": saved["mimic"], "params": saved["params"],
                     "t": c["t"], "wallet": c["wallet"], "mimic": c["mimic"]})
    rows.sort(key=lambda r: -r["wallet_stats"]["return"])
    out = HERE / "mimic_hl_all.html"
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
.neg{color:var(--neg)}a{color:inherit}
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
<p class="sub">19 wallets from OptimistFi, each fitted with the <code>mimic</code> strategy over its last 200 days.
PnL is a return on a $10,000 account (wallet trades sized at an assumed 5x leverage).
Copy error is the root of the MSE between the two curves.</p>
<div class="legend"><span><i style="background:var(--s1)"></i>Wallet</span>
<span><i style="background:var(--s2)"></i>Mimic</span></div>
<h2>Stats</h2>
<div class="scroll"><table id="stats"></table></div>
<h2>PnL curves</h2>
<div class="grid" id="cards"></div>
</main>
<script>
const D=/*DATA*/null;
const pct=(x,d=0)=>(x>0?"+":"")+(x*100).toFixed(d)+"%";
const cls=x=>x<0?' class="neg"':"";
const short=a=>a.slice(0,6)+"…"+a.slice(-4);
let h="<tr><th>Wallet</th><th>Copy error</th><th>Wallet Sharpe</th><th>Mimic Sharpe</th><th>Wallet return</th>"+
"<th>Mimic return</th><th>Wallet max DD</th><th>Mimic max DD</th><th>Wallet trades</th><th>Mimic trades</th></tr>";
for(const r of D){const w=r.wallet_stats,m=r.mimic_stats;
h+=`<tr><td><a href="#w${r.address}">${short(r.address)}</a></td><td>${(Math.sqrt(r.mse)*100).toFixed(1)}%</td>
<td${cls(w.sharpe)}>${w.sharpe.toFixed(2)}</td><td${cls(m.sharpe)}>${m.sharpe.toFixed(2)}</td>
<td${cls(w.return)}>${pct(w.return)}</td><td${cls(m.return)}>${pct(m.return)}</td>
<td>${(w.max_drawdown*100).toFixed(0)}%</td><td>${(m.max_drawdown*100).toFixed(0)}%</td>
<td>${w.trades}</td><td>${m.trades}</td></tr>`}
document.getElementById("stats").innerHTML=h;
function chart(r){const W=320,H=150,L=34,B=16,T=6;
const ys=r.wallet.concat(r.mimic),lo=Math.min(0,...ys),hi=Math.max(0,...ys),n=r.t.length-1;
const x=i=>L+(W-L-4)*i/n,y=v=>T+(H-T-B)*(1-(v-lo)/((hi-lo)||1));
const path=a=>a.map((v,i)=>(i?"L":"M")+x(i).toFixed(1)+","+y(v).toFixed(1)).join("");
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
<span>Return</span><span${cls(w.return)}>${pct(w.return)}</span><span${cls(m.return)}>${pct(m.return)}</span>
<span>Sharpe</span><span>${w.sharpe.toFixed(2)}</span><span>${m.sharpe.toFixed(2)}</span>
<span>Max DD</span><span>${(w.max_drawdown*100).toFixed(0)}%</span><span>${(m.max_drawdown*100).toFixed(0)}%</span>
<span>Trades</span><span>${w.trades}</span><span>${m.trades}</span></div></div>`}).join("");
</script></body></html>
"""

if __name__ == "__main__":
    main()

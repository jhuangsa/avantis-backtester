"""Chart a saved mimic fit: the wallet's and the mimic's equity and drawdown.

Run from the repository root, after examples/mimic.py:

    python3 examples/mimic_chart.py data/candles/mimic_<address>.json

Writes examples/mimic_<first 6 of the address>.html, one self-contained page.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mimic as M  # noqa: E402


def curves(saved):
    """Both equity curves as returns on a 4-hour grid, and the mimic's trades."""
    hl = saved.get("venue") == "hyperliquid"
    M.use_venue(saved.get("venue", "avantis"))
    if hl:
        eq = M.hl_curve(saved["address"], saved["start"], saved["end"])[0]
    else:
        _, eq, _ = M.wallet_curve(M.CACHE / f"wallet_{saved['address']}.json")
    symbols = list(saved["markets"])
    markets = M.load_markets(symbols, saved["start"], saved["end"])
    costs = {s: M.avbt_cpp.Costs(M.FEE, M.FEE) for s in symbols}
    r = M.backtest(saved["params"], markets, costs)
    lo, hi = (pd.Timestamp(saved[k], tz="UTC").timestamp() for k in ("start", "end"))
    grid = pd.Index(np.arange(lo, hi, 4 * 3600, dtype="int64"))
    wallet = M.on_grid(eq, grid)
    mimic = M.on_grid(pd.Series(r.equity, index=r.clock), grid)
    trades = [{"m": t.instrument.split("/")[0], "side": t.side.name.lower(), "in": t.entry_time,
               "out": t.exit_time, "r": round(t.result / M.vt.ACCOUNT, 4)} for t in r.trades]
    return {"t": grid.tolist(), "wallet": np.round(wallet, 4).tolist(),
            "mimic": np.round(mimic, 4).tolist(), "trades": trades}


def main():
    path = Path(sys.argv[1])
    saved = json.loads(path.read_text())
    data = {**curves(saved), "saved": saved}
    tag = "hl_" if saved.get("venue") == "hyperliquid" else ""
    out = HERE / f"mimic_{tag}{saved['address'][2:8].lower()}.html"
    page = PAGE.replace("/*DATA*/null", json.dumps(data))
    if tag:  # a Hyperliquid wallet's curve is total PnL, realized plus unrealized
        page = page.replace("<h2>Equity,", "<h2>Total PnL, realized plus unrealized,")
    out.write_text(page)
    print(f"wrote {out}")


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wallet Mimic Fit</title>
<style>
:root{--surface:#fcfcfb;--panel:#f4f3f0;--line:#e3e2de;--ink:#0b0b0b;--ink2:#52514e;--ink3:#8a8984;
--s1:#2a78d6;--s2:#eb6834}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--surface:#1a1a19;--panel:#232321;--line:#383835;
--ink:#fff;--ink2:#c3c2b7;--ink3:#8a8984;--s1:#3987e5;--s2:#d95926}}
:root[data-theme="dark"]{--surface:#1a1a19;--panel:#232321;--line:#383835;--ink:#fff;--ink2:#c3c2b7;--ink3:#8a8984;
--s1:#3987e5;--s2:#d95926}
*{box-sizing:border-box}
body{margin:0;background:var(--surface);color:var(--ink);font:15px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:1000px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:36px 0 8px}
.sub{color:var(--ink2);margin:0 0 24px;overflow-wrap:anywhere}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.tile{background:var(--panel);border-radius:10px;padding:12px 14px}
.tile .k{color:var(--ink2);font-size:13px}.tile .v{font-size:22px;font-variant-numeric:tabular-nums}
.tile .w{color:var(--ink3);font-size:13px;font-variant-numeric:tabular-nums}
.legend{display:flex;gap:18px;color:var(--ink2);font-size:13px;margin:4px 0}
.legend i{display:inline-block;width:14px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}
.chart{position:relative}svg{display:block;width:100%;height:auto;overflow:visible}
.grid line{stroke:var(--line)}.axis text{fill:var(--ink3);font-size:11px}
.tip{position:absolute;pointer-events:none;background:var(--panel);border:1px solid var(--line);border-radius:8px;
padding:6px 10px;font-size:13px;font-variant-numeric:tabular-nums;display:none;white-space:nowrap}
table{border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}
td,th{text-align:left;padding:6px 8px;white-space:nowrap;border-bottom:1px solid var(--line)}th{color:var(--ink2);font-weight:500}
.off{color:var(--ink3)}.scroll{overflow-x:auto}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:24px}
</style></head><body><main>
<h1>Wallet mimic fit</h1>
<p class="sub" id="sub"></p>
<div class="tiles" id="tiles"></div>
<h2>Equity, return on a $10,000 account</h2>
<div class="legend"><span><i style="background:var(--s1)"></i>Wallet</span><span><i style="background:var(--s2)"></i>Mimic</span></div>
<div class="chart" id="eq"></div>
<h2>Drawdown from peak</h2>
<div class="chart" id="dd"></div>
<div class="cols">
<div><h2>Strategy params</h2><p class="sub" style="margin:0 0 8px">Grey rows belong to a filter that is off.</p>
<table id="params"></table></div>
<div><h2>Mimic trades</h2><div class="scroll"><table id="trades"></table></div></div>
</div>
</main>
<script>
const D=/*DATA*/null, S=D.saved;
const pct=(x,d=1)=>(x*100).toFixed(d)+"%", day=t=>new Date(t*1000).toISOString().slice(0,10);
document.getElementById("sub").textContent=`${S.address} · ${S.markets.join(", ")} · ${S.start} to ${S.end} · `+
  `MSE ${S.mse.toFixed(5)} (RMSE ${pct(Math.sqrt(S.mse))} of the account)`;
const tiles=[["Sharpe","sharpe",x=>x.toFixed(2)],["Max drawdown","max_drawdown",pct],["Return","return",pct],["Trades","trades",x=>x]];
document.getElementById("tiles").innerHTML=tiles.map(([k,f,fmt])=>
  `<div class="tile"><div class="k">${k}</div><div class="v">${fmt(S.mimic[f])}</div><div class="w">wallet ${fmt(S.wallet[f])}</div></div>`).join("");
const dd=a=>{let p=-1e9;return a.map(r=>{const e=1+r;p=Math.max(p,e);return e/p-1})};
const NS="http://www.w3.org/2000/svg";
function chart(id,series,fmt){
  const W=960,H=300,L=52,R=12,T=10,B=26,el=document.getElementById(id),t=D.t;
  const all=series.flatMap(s=>s.v),lo=Math.min(0,...all),hi=Math.max(0,...all),pad=(hi-lo)*.06||.01;
  const y0=lo-pad,y1=hi+pad,x=i=>L+(W-L-R)*i/(t.length-1),y=v=>T+(H-T-B)*(1-(v-y0)/(y1-y0));
  let g=`<g class="grid">`,ax=`<g class="axis">`;
  for(let k=0;k<=4;k++){const v=y0+(y1-y0)*k/4;g+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}"/>`;
    ax+=`<text x="${L-6}" y="${y(v)+4}" text-anchor="end">${fmt(v)}</text>`}
  for(let k=0;k<=5;k++){const i=Math.round((t.length-1)*k/5);ax+=`<text x="${x(i)}" y="${H-6}" text-anchor="middle">${day(t[i])}</text>`}
  let p=g+`</g>`+ax+`</g>`;
  for(const s of series)p+=`<path d="${s.v.map((v,i)=>(i?"L":"M")+x(i).toFixed(1)+","+y(v).toFixed(1)).join("")}" fill="none" stroke="var(${s.c})" stroke-width="2" stroke-linejoin="round"/>`;
  p+=`<line id="${id}x" y1="${T}" y2="${H-B}" stroke="var(--ink3)" stroke-dasharray="3 3" visibility="hidden"/>`;
  p+=series.map((s,j)=>`<circle id="${id}d${j}" r="4" fill="var(${s.c})" stroke="var(--surface)" stroke-width="2" visibility="hidden"/>`).join("");
  el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${id}">${p}<rect x="${L}" y="0" width="${W-L-R}" height="${H}" fill="transparent"/></svg><div class="tip"></div>`;
  const svg=el.querySelector("svg"),tip=el.querySelector(".tip"),cx=el.querySelector(`#${id}x`);
  svg.addEventListener("pointermove",ev=>{
    const b=svg.getBoundingClientRect(),sx=(ev.clientX-b.left)*W/b.width;
    const i=Math.max(0,Math.min(t.length-1,Math.round((sx-L)/(W-L-R)*(t.length-1))));
    cx.setAttribute("x1",x(i));cx.setAttribute("x2",x(i));cx.setAttribute("visibility","visible");
    series.forEach((s,j)=>{const d=el.querySelector(`#${id}d${j}`);d.setAttribute("cx",x(i));d.setAttribute("cy",y(s.v[i]));d.setAttribute("visibility","visible")});
    tip.innerHTML=`<b>${day(t[i])}</b><br>`+series.map(s=>`<span style="color:var(${s.c})">●</span> ${s.n} ${fmt(s.v[i])}`).join("<br>");
    tip.style.display="block";const px=x(i)*b.width/W;
    tip.style.left=(px>b.width*.6?px-tip.offsetWidth-12:px+12)+"px";tip.style.top="8px";
  });
  svg.addEventListener("pointerleave",()=>{tip.style.display="none";el.querySelectorAll("[visibility]").forEach(n=>n.setAttribute("visibility","hidden"))});
}
chart("eq",[{n:"Wallet",v:D.wallet,c:"--s1"},{n:"Mimic",v:D.mimic,c:"--s2"}],v=>pct(v,0));
chart("dd",[{n:"Wallet",v:dd(D.wallet),c:"--s1"},{n:"Mimic",v:dd(D.mimic),c:"--s2"}],v=>pct(v,0));
const groups={rsi_:"rsi_on",trend_:"trend_on",move_:"move_on",break_:"break_on",hour_:"hours_on"};
document.getElementById("params").innerHTML="<tr><th>Param</th><th>Value</th></tr>"+Object.entries(S.params).map(([k,v])=>{
  const g=Object.keys(groups).find(p=>k.startsWith(p)),off=(g&&k!==groups[g]&&!S.params[groups[g]])||(k.endsWith("_on")&&!v);
  return `<tr class="${off?"off":""}"><td>${k}</td><td>${v}</td></tr>`}).join("");
document.getElementById("trades").innerHTML="<tr><th>Market</th><th>Side</th><th>In</th><th>Out</th><th>Result</th></tr>"+
  D.trades.map(r=>`<tr><td>${r.m}</td><td>${r.side}</td><td>${day(r.in)}</td><td>${day(r.out)}</td><td>${pct(r.r,2)}</td></tr>`).join("");
</script></body></html>
"""

if __name__ == "__main__":
    main()

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Play, RefreshCw, Square, Wifi, WifiOff } from "lucide-react";
import { appConfig } from "@/lib/config";

type Row = Record<string, unknown>;
type Chain = { symbol?: string; expiry_type?: string; option_feed?: Row; sampler?: Row; universe_tokens?: number; error?: string | null; signals?: Row };
type Runtime = {
  status?: string;
  refresh_seconds?: number;
  signal_evaluation_seconds?: number;
  spot_feed?: Row;
  option_chains?: Record<string, Chain>;
  signals?: Record<string, Row>;
};
const n = (v: unknown) => typeof v === "number" && Number.isFinite(v) ? v : null;
const fmt = (v: unknown, digits = 2) => n(v) === null ? "—" : (v as number).toLocaleString("en-IN", { maximumFractionDigits: digits });
const age = (v: unknown) => n(v) === null ? "—" : n(v)! < 1000 ? `${Math.round(n(v)!)} ms` : `${(n(v)! / 1000).toFixed(1)} s`;
const stamp = (v: unknown) => n(v) === null ? "—" : new Date((v as number) / 1_000_000).toLocaleTimeString("en-IN", { hour12: false });
const apiError = (body: Row, fallback: string) => {
  const detail = body.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const info = detail as Row;
    return [typeof info.message === "string" ? info.message : "", typeof info.reason === "string" ? info.reason : ""].filter(Boolean).join(": ") || fallback;
  }
  return fallback;
};
function Metric({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return <div className="rounded-xl border theme-border theme-surface p-3 min-w-0"><div className="text-[11px] uppercase tracking-wide theme-muted">{label}</div><div className="mt-1 text-lg font-bold theme-text truncate">{value}</div>{sub && <div className="mt-1 text-[11px] theme-muted">{sub}</div>}</div>;
}
function SignalSummary({ title, chain, spot }: { title: string; chain: Chain; spot: Row }) {
  const feed = chain.option_feed ?? {};
  const sampler = chain.sampler ?? {};
  const metrics = Array.isArray(sampler.live_metrics) ? sampler.live_metrics as Row[] : [];
  const calls = metrics.filter(x => x.option_type === "CE" && n(x.iv) !== null);
  const puts = metrics.filter(x => x.option_type === "PE" && n(x.iv) !== null);
  const avg = (rows: Row[]) => rows.length ? rows.reduce((s, x) => s + Number(x.iv), 0) / rows.length : null;
  const callIv = avg(calls), putIv = avg(puts);
  const skew = callIv !== null && putIv !== null ? callIv - putIv : null;
  const spotPrice = n(spot.price);
  const strikes = metrics.map(x => n(x.strike)).filter((x): x is number => x !== null);
  const atm = spotPrice !== null && strikes.length ? strikes.reduce((best, x) => Math.abs(x - spotPrice) < Math.abs(best - spotPrice) ? x : best, strikes[0]) : null;
  const atmPair = metrics.filter(x => x.strike === atm);
  const atmIv = atmPair.length ? avg(atmPair) : null;
  const signals = chain.signals && typeof chain.signals === "object" ? chain.signals as Row : {};
  const strategies = Array.isArray(signals.strategies) ? signals.strategies as Row[] : [];
  const status = (name: string) => String(strategies.find(x => x.strategy === name)?.status ?? "WAITING");
  return <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
    <Metric label={`${title} Spot`} value={fmt(spotPrice)} sub={`Tick age ${age(spot.age_ms)} • ${spot.fresh ? "FRESH" : "WAITING"}`} />
    <Metric label="ATM Strike" value={fmt(atm, 0)} />
    <Metric label="ATM IV" value={atmIv === null ? "—" : `${fmt(atmIv, 2)}%`} />
    <Metric label="IV Skew (CE−PE)" value={skew === null ? "—" : `${skew > 0 ? "+" : ""}${fmt(skew, 2)} pt`} />
    <Metric label="IV Flip" value={status("IV_FLIP")} />
    <Metric label="Gamma Blast" value={status("GAMMA_BLAST")} sub={String(feed.status ?? "WAITING")} />
  </div>;
}
function OptionChain({ title, chain }: { title: string; chain: Chain }) {
  const feed = chain.option_feed ?? {};
  const sampler = chain.sampler ?? {};
  const quotes = Array.isArray(feed.quotes) ? feed.quotes as Row[] : [];
  const metrics = Array.isArray(sampler.live_metrics) ? sampler.live_metrics as Row[] : [];
  const byToken = new Map(metrics.map(x => [String(x.token ?? ""), x]));
  const merged = quotes.map(q => ({ ...q, ...(byToken.get(String(q.token ?? "")) ?? {}) }));
  const strikes = [...new Set(merged.map(x => n(x.strike)).filter((x): x is number => x !== null))].sort((a,b)=>a-b);
  const byKey = new Map(merged.map(x => [`${x.strike}:${x.option_type}`, x]));
  const spotFeed = feed.status ?? "WAITING";
  return <section className="overflow-hidden rounded-2xl border theme-border theme-surface">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b theme-border p-3 sm:p-4">
      <div><h2 className="text-lg font-bold theme-text">{title} Option Chain</h2><p className="text-xs theme-muted mt-1">{String(feed.expiry_type ?? chain.expiry_type ?? "—")} • Expiry {String(feed.expiry ?? "—")} • ATM ±3 strikes</p></div>
      <div className="flex flex-wrap gap-2 text-xs"><span className="rounded-lg border theme-border px-2 py-1">Feed: {String(spotFeed)}</span><span className="rounded-lg border theme-border px-2 py-1">Contracts: {fmt(feed.received_contracts,0)}/{fmt(feed.expected_contracts,0)}</span><span className="rounded-lg border theme-border px-2 py-1">IV: {fmt(sampler.iv_live_count,0)} • OI: {fmt(sampler.oi_live_count,0)}</span></div>
    </div>
    {chain.error && <div className="m-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-600">{chain.error}</div>}
    <div className="overflow-x-auto">
      <table className="w-full min-w-[1050px] border-collapse text-center text-xs">
        <thead><tr><th colSpan={6} className="bg-emerald-500/10 py-2 text-emerald-700 text-sm">Calls (CE)</th><th rowSpan={2} className="theme-surface-2 px-3 font-bold theme-text">Strike</th><th colSpan={6} className="bg-rose-500/10 py-2 text-rose-700 text-sm">Puts (PE)</th></tr>
          <tr className="theme-surface-2 theme-muted">{["OI (Lots)","OI Chg","IV %","LTP","Bid","Ask","Bid","Ask","LTP","IV %","OI Chg","OI (Lots)"].map((x,i)=><th key={i} className="border-y theme-border px-2 py-2 font-semibold">{x}</th>)}</tr></thead>
        <tbody>{strikes.length === 0 ? <tr><td colSpan={13} className="p-10 theme-muted">Waiting for fresh option quotes. No placeholder market values are displayed.</td></tr> : strikes.map(strike=>{
          const ce=byKey.get(`${strike}:CE`) ?? {}, pe=byKey.get(`${strike}:PE`) ?? {};
          const ceFresh=ce.fresh===true, peFresh=pe.fresh===true;
          const cell=(v:unknown,d=2)=>n(v)===null?"—":fmt(v,d);
          return <tr key={strike} className="border-b theme-border hover:theme-surface-2">
            <td className="px-2 py-2">{cell(ce.oi,0)}</td><td className="px-2 py-2 text-emerald-600">—</td><td className="px-2 py-2 font-semibold">{ce.iv_live?cell(ce.iv,2):"—"}</td><td className="px-2 py-2">{cell(ce.ltp)}</td><td className="px-2 py-2 text-emerald-600">{cell(ce.bid)}</td><td className="px-2 py-2 text-rose-600">{cell(ce.ask)}</td>
            <td className={`px-3 py-2 font-bold ${strike===strikes[Math.floor(strikes.length/2)]?"bg-blue-500/15 text-blue-700":"theme-surface-2"}`}>{fmt(strike,0)}</td>
            <td className="px-2 py-2 text-emerald-600">{cell(pe.bid)}</td><td className="px-2 py-2 text-rose-600">{cell(pe.ask)}</td><td className="px-2 py-2">{cell(pe.ltp)}</td><td className="px-2 py-2 font-semibold">{pe.iv_live?cell(pe.iv,2):"—"}</td><td className="px-2 py-2 text-emerald-600">—</td><td className="px-2 py-2">{cell(pe.oi,0)}</td>
          </tr>;
        })}</tbody>
      </table>
    </div>
    <div className="border-t theme-border px-3 py-2 text-[11px] theme-muted">IV is modeled from live bid/ask midpoint (LTP fallback). OI is the broker tick's open interest. OI Change is shown as — until a verified exchange/broker OI-change field is available.</div>
  </section>;
}
export default function IVSkewFlipPage() {
  const [runtime,setRuntime]=useState<Runtime|null>(null);
  const [selectedNifty,setSelectedNifty]=useState<"NIFTY_WEEKLY"|"NIFTY_MONTHLY">("NIFTY_WEEKLY");
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const base=appConfig.apiBaseUrl.replace(/\/$/,"");
  const refresh=useCallback(async()=>{
    const response=await fetch(`${base}/api/v1/scanner/iv-skew-flip/status`,{cache:"no-store"});
    const body=await response.json();
    if(!response.ok) throw new Error(apiError(body,`Status HTTP ${response.status}`));
    setRuntime(body.runtime??null);
  },[base]);
  useEffect(()=>{
    let active=true;
    const poll=async()=>{try{const response=await fetch(`${base}/api/v1/scanner/iv-skew-flip/status`,{cache:"no-store"});const body=await response.json();if(active&&response.ok)setRuntime(body.runtime??null);}catch{/* keep last known state */}};
    void poll();const timer=window.setInterval(()=>{if(active)void poll();},1000);
    return()=>{active=false;window.clearInterval(timer);};
  },[base]);
  async function control(action:"start"|"stop"){
    setBusy(true);setError("");
    try{const response=await fetch(`${base}/api/v1/scanner/iv-skew-flip/${action}`,{method:"POST",cache:"no-store"});const body=await response.json();if(!response.ok)throw new Error(apiError(body,`Could not ${action} scanner`));setRuntime(body);}
    catch(e){setError(e instanceof Error?e.message:"Scanner control failed");}finally{setBusy(false);}
  }
  const chains=runtime?.option_chains??{};
  const nifty=chains[selectedNifty]??{};
  const bank=chains.BANKNIFTY_MONTHLY??{};
  const spot=runtime?.spot_feed?.spot && typeof runtime.spot_feed.spot==="object" ? runtime.spot_feed.spot as Row : {};
  const niftySpot=(spot.NIFTY&&typeof spot.NIFTY==="object"?spot.NIFTY:{}) as Row;
  const bankSpot=(spot.BANKNIFTY&&typeof spot.BANKNIFTY==="object"?spot.BANKNIFTY:{}) as Row;
  const running=runtime?.status==="RUNNING";
  const niftyExpiry=String((nifty.option_feed??{}).expiry_type??"");
  const bankExpiry=String((bank.option_feed??{}).expiry_type??"");
  return <main className="space-y-4 p-3 sm:p-5 theme-text">
    <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div><div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] theme-accent"><Activity size={15}/> Live Options Analytics</div><h1 className="mt-1 text-2xl font-bold sm:text-3xl">IV Skew / IV Flip + Gamma Blast</h1><p className="mt-1 text-sm theme-muted">NIFTY Weekly + Monthly • BANKNIFTY Monthly • Live OI and modeled IV</p></div>
      <div className="flex gap-2"><button onClick={()=>void refresh().catch(e=>setError(String(e)))} disabled={busy} className="inline-flex items-center gap-2 rounded-xl border theme-border theme-surface px-3 py-2 text-sm"><RefreshCw size={15}/> Refresh</button><button onClick={()=>void control(running?"stop":"start")} disabled={busy} className={`inline-flex items-center gap-2 rounded-xl border px-3 py-2 text-sm ${running?"border-rose-500/40 bg-rose-500/10 text-rose-600":"border-emerald-500/40 bg-emerald-500/10 text-emerald-600"}`}>{running?<Square size={15}/>:<Play size={15}/>} {running?"Stop Live Feed":"Start Live Feed"}</button></div>
    </header>
    <div className="flex flex-wrap gap-2 text-xs"><span className="rounded-full border theme-border px-3 py-2">{running?<Wifi size={13} className="inline"/>:<WifiOff size={13} className="inline"/>} {runtime?.status??"NOT STARTED"}</span><span className="rounded-full border theme-border px-3 py-2">Universe refresh {fmt(runtime?.refresh_seconds,0)}s</span><span className="rounded-full border theme-border px-3 py-2">Signal evaluation {fmt(runtime?.signal_evaluation_seconds,0)}s</span><span className="rounded-full border theme-border px-3 py-2">Orders OFF</span></div>
    {error&&<div role="alert" className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-600">{error}</div>}
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-xl font-bold theme-text">NIFTY Option Chain</h2><div className="inline-flex rounded-xl border theme-border p-1"><button onClick={()=>setSelectedNifty("NIFTY_WEEKLY")} className={`rounded-lg px-4 py-2 text-sm font-semibold ${selectedNifty==="NIFTY_WEEKLY"?"bg-blue-600 text-white":"theme-muted"}`}>Weekly</button><button onClick={()=>setSelectedNifty("NIFTY_MONTHLY")} className={`rounded-lg px-4 py-2 text-sm font-semibold ${selectedNifty==="NIFTY_MONTHLY"?"bg-blue-600 text-white":"theme-muted"}`}>Monthly</button></div></div>
      <OptionChain title={`NIFTY ${niftyExpiry|| (selectedNifty==="NIFTY_WEEKLY"?"Weekly":"Monthly")}`} chain={nifty}/>
      <SignalSummary title="NIFTY" chain={nifty} spot={niftySpot}/>
    </section>
    <section className="space-y-3 pt-2"><div className="flex items-center justify-between gap-2"><h2 className="text-xl font-bold theme-text">BANKNIFTY Option Chain</h2><span className="rounded-lg border theme-border px-3 py-2 text-sm font-semibold">Monthly</span></div><OptionChain title="BANKNIFTY Monthly" chain={bank}/><SignalSummary title="BANKNIFTY" chain={bank} spot={bankSpot}/></section>
    <section className="rounded-2xl border theme-border theme-surface p-4"><h2 className="font-semibold theme-text">Live Signal Monitor</h2><p className="mt-1 text-xs theme-muted">Each expiry stream is evaluated independently. WAITING means required fresh quotes or history are not available; it is not a trade signal.</p><div className="mt-3 grid gap-2 sm:grid-cols-3">{Object.entries(runtime?.signals??{}).map(([key,value])=><div key={key} className="rounded-xl border theme-border p-3"><b className="text-sm">{key.replaceAll("_"," ")}</b>{(Array.isArray(value.strategies)?value.strategies as Row[]:[]).map((s,i)=><div key={i} className="flex justify-between gap-2 border-t theme-border py-2 text-xs"><span>{String(s.strategy??"Signal")}</span><b>{String(s.direction??"—")} • {String(s.status??"WAITING")}</b></div>)}</div>)}</div></section>
    <p className="text-xs theme-muted">Live data appears only after fresh Angel One ticks arrive. No broker orders are placed. OI Change is intentionally not fabricated when the provider does not supply a verified field.</p>
  </main>;
}

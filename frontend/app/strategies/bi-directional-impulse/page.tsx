"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Activity, ArrowDownRight, ArrowUpRight, Clock3, Layers3, RefreshCw, ShieldAlert, Signal, Waves, Zap } from "lucide-react";
import { appConfig } from "@/lib/config";

type Signal = { underlying?: string; direction?: string; score?: number; grade?: string; timestamp?: string; entry?: number; invalidation?: number; target_1?: number; reason?: string };
type Status = { status?: string; feed_connected?: boolean; scanner_active?: boolean; feed_status?: string; feed_epoch?: number; fresh_quote_count?: number; stale_quote_count?: number; observer_errors?: number; market_regime?: string; signal_count?: number };
type History = { signals?: Signal[]; signal_count?: number; capacity?: number; suppressed_duplicate_count?: number; cooldown_seconds?: number; order_routing_enabled?: boolean };
const engines = [
  ["Liquidity & Trap","LOCATION","PDH/PDL, session extremes, equal highs/lows, sweep depth, reclaim quality and failed acceptance."],
  ["Absorption & Aggressor Flow","ORDER FLOW","Aggressive flow versus price progress, replenishment evidence, delta divergence and imbalance clusters."],
  ["Futures Lead–Lag","EARLY WARNING","Timestamp-aligned Spot/Futures returns, basis shock/acceleration, futures range break and OI context."],
  ["Options Pressure","DERIVATIVES","Near-ATM OI velocity, volume/OI context, IV repricing and strike migration; dealer gamma is never assumed."],
  ["Structure & Execution","TRIGGER","Anchored VWAP, impulse quality, structural break/retest, executable spread and invalidation."],
  ["Cross-Market Context","CONFIRMATION","Independent index participation and conflict detection with correlated-evidence discounting."],
] as const;
const stages = [["01","PRECONDITION","Regime and liquidity context"],["02","PRESSURE BUILD","Absorption or lead-lag evidence"],["03","PRE-IMPULSE","Confluence; trigger still pending"],["04","TRIGGERED","Structure, displacement and execution gates"]] as const;
const num=(v?:number|null)=>typeof v==="number"&&Number.isFinite(v)?v.toLocaleString("en-IN",{maximumFractionDigits:2}):"—";
const time=(v?:string)=>v?new Date(v).toLocaleTimeString("en-IN",{timeZone:"Asia/Kolkata",hour:"2-digit",minute:"2-digit",second:"2-digit"})+" IST":"—";
function Pill({children,tone="neutral"}:{children:ReactNode;tone?:"neutral"|"green"|"red"|"amber"}) {
 const cls=tone==="green"?"border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300":tone==="red"?"border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-300":tone==="amber"?"border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300":"border theme-border theme-surface-2 theme-muted";
 return <span className={`inline-flex rounded-full border px-2 py-1 text-[10px] font-semibold ${cls}`}>{children}</span>;
}
function Stat({label,value,sub}:{label:string;value:string;sub?:string}) {
 return <div className="min-w-0 rounded-xl border theme-border theme-surface-2 p-3"><div className="text-[10px] font-semibold uppercase tracking-wide theme-muted">{label}</div><div className="mt-1 truncate text-lg font-extrabold theme-text">{value}</div>{sub&&<div className="mt-1 text-[10px] theme-muted">{sub}</div>}</div>;
}
function SignalHistoryPanel({title,buy,signals}:{title:string;buy:boolean;signals:Signal[]}) {
 const dir=buy?"BUY":"SELL", list=signals.filter(s=>(s.direction??"").toUpperCase()===dir);
 return <section className="overflow-hidden rounded-2xl border theme-border theme-surface">
  <header className={`border-b theme-border p-4 ${buy?"bg-emerald-500/5":"bg-rose-500/5"}`}>
   <div className="flex items-center justify-between gap-2"><h2 className="flex items-center gap-2 text-sm font-bold theme-text">{buy?<ArrowUpRight size={18} className="text-emerald-600"/>:<ArrowDownRight size={18} className="text-rose-600"/>}{title}</h2><Pill tone={buy?"green":"red"}>{dir}</Pill></div>
   <p className="mt-2 text-xs theme-muted">Existing Early Impulse signal history only; dedicated microstructure sequence scoring is not yet connected.</p>
  </header>
  <div className="space-y-2 p-3">{list.length?list.slice(0,5).map((s,i)=><div key={s.timestamp??i} className="rounded-xl border theme-border p-3">
   <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-xs font-bold theme-text">{s.underlying??"Instrument"}</span><Pill>{s.grade??"SIGNAL"} · {s.score??"—"}</Pill></div>
   <div className="mt-2 grid grid-cols-2 gap-2 text-[11px] theme-muted"><div>Time {time(s.timestamp)}</div><div>Entry {num(s.entry)}</div><div>Invalidation {num(s.invalidation)}</div><div>Target {num(s.target_1)}</div></div>{s.reason&&<p className="mt-2 text-xs theme-muted">{s.reason}</p>}
  </div>):<div className="rounded-xl border border-dashed theme-border p-4 text-xs theme-muted">No {dir} signals in the available history. No sample/demo signals are generated.</div>}</div>
 </section>;
}
export default function BiDirectionalImpulsePage(){
 const [status,setStatus]=useState<Status|null>(null),[history,setHistory]=useState<History|null>(null),[updated,setUpdated]=useState<string|null>(null),[loading,setLoading]=useState(false),[error,setError]=useState<string|null>(null);
 const lock=useRef(false);
 const refresh=useCallback(async()=>{
  if(lock.current)return;lock.current=true;setLoading(true);
  const base=appConfig.apiBaseUrl.replace(/\/$/,"");
  try{
   const [sr,hr]=await Promise.all([fetch(base+"/api/v1/early-impulse/status",{cache:"no-store",signal:AbortSignal.timeout(6500)}),fetch(base+"/api/v1/early-impulse/signals",{cache:"no-store",signal:AbortSignal.timeout(6500)})]);
   if(!sr.ok)throw new Error("Status endpoint HTTP "+sr.status);
   setStatus(await sr.json() as Status);if(hr.ok)setHistory(await hr.json() as History);
   setUpdated(new Date().toISOString());setError(null);
  }catch(e){setError(e instanceof Error?e.message:"Live status unavailable");}
  finally{lock.current=false;setLoading(false);}
 },[]);
 useEffect(()=>{void refresh();const timer=window.setInterval(()=>void refresh(),20000);return()=>window.clearInterval(timer);},[refresh]);
 const live=status?.feed_connected===true,signals=history?.signals??[];
 return <main className="space-y-4">
  <section className="overflow-hidden rounded-2xl border theme-border theme-surface">
   <div className="p-4 sm:p-6"><div className="flex flex-wrap items-start justify-between gap-3">
    <div className="min-w-0"><div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.15em] theme-accent"><Activity size={14}/> Independent scanner workspace</div><h1 className="mt-1 text-2xl font-black sm:text-3xl">BI-DIRECTIONAL <span className="theme-accent">IMPULSE</span></h1><p className="mt-2 max-w-3xl text-sm theme-muted">Short-covering and long-unwinding microstructure workspace: liquidity traps, absorption, futures lead, options pressure and sequential triggers. Scanner-only; no broker orders.</p></div>
    <button onClick={()=>void refresh()} disabled={loading} className="inline-flex min-h-10 items-center gap-2 rounded-xl border theme-border theme-surface-2 px-3 text-xs font-bold theme-text disabled:opacity-60"><RefreshCw size={14} className={loading?"animate-spin":""}/> Refresh</button>
   </div>
   <div className="mt-4 flex flex-wrap gap-2"><Pill tone={live?"green":"amber"}>{live?"FEED CONNECTED":"FEED WAITING"}</Pill><Pill tone={status?.scanner_active?"green":"amber"}>{status?.scanner_active?"RUNNER ACTIVE":"RUNNER INACTIVE"}</Pill><Pill>{status?.market_regime??"REGIME UNKNOWN"}</Pill><Pill>ORDER ROUTING OFF</Pill><Pill>IV SCANNER ISOLATED</Pill></div></div>
   {error&&<div className="mx-3 mb-3 flex gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-300"><ShieldAlert size={15}/><span>Live status unavailable: {error}. Last valid state is retained.</span></div>}
   {!live&&!error&&<div className="mx-3 mb-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-300">Feed is not live. Live scorecards remain blank until valid backend observations arrive.</div>}
   <div className="grid grid-cols-2 gap-2 border-t theme-border p-3 sm:grid-cols-4"><Stat label="Fresh quotes" value={num(status?.fresh_quote_count)} sub="observer snapshot"/><Stat label="Stale quotes" value={num(status?.stale_quote_count)} sub="freshness gate"/><Stat label="Signal records" value={num(history?.signal_count??status?.signal_count)} sub="bounded history"/><Stat label="Feed epoch" value={num(status?.feed_epoch)} sub="reconnect generation"/></div>
   <div className="px-3 pb-3 text-[10px] theme-muted">Updated {time(updated??undefined)} · Auto-refresh 20s · Observer errors {num(status?.observer_errors)}</section>
  <section className="grid gap-3 sm:grid-cols-2"><SignalHistoryPanel title="BUY Signal History" buy signals={signals}/><SignalHistoryPanel title="SELL Signal History" buy={false} signals={signals}/></section>
  <section className="rounded-2xl border theme-border theme-surface p-3 sm:p-4"><div className="flex items-start justify-between gap-3"><div><h2 className="text-base font-bold">Event-driven pipeline</h2><p className="mt-1 text-xs theme-muted">Conceptual lifecycle. Backend event-state integration is pending.</p></div><Layers3 size={18} className="theme-accent"/></div>
   <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">{stages.map(([n,t,d])=><div key={n} className="rounded-xl border theme-border theme-surface-2 p-3"><div className="flex items-center justify-between"><span className="text-[10px] font-bold theme-muted">{n}</span><Signal size={15} className="theme-muted"/></div><div className="mt-2 text-xs font-bold theme-text">{t}</div><div className="mt-1 text-[11px] theme-muted">{d}</div><div className="mt-2"><Pill>Awaiting event feed</Pill></div></div>)}</div>
  </section>
  <section className="rounded-2xl border theme-border theme-surface"><div className="border-b theme-border p-3 sm:p-4"><h2 className="text-base font-bold">Six microstructure engines</h2><p className="mt-1 text-xs theme-muted">Independent evidence groups; correlated signals discounted, missing data fails closed.</p></div>
   <div className="grid gap-2 p-3 sm:grid-cols-2 lg:grid-cols-3">{engines.map(([name,tag,detail],i)=><article key={name} className="rounded-xl border theme-border theme-surface-2 p-3"><div className="flex items-center justify-between gap-2"><span className="text-[10px] font-bold theme-accent">{String(i+1).padStart(2,"0")} · {tag}</span><Pill>DATA GATED</Pill></div><h3 className="mt-2 text-sm font-bold theme-text">{name}</h3><p className="mt-1 text-xs leading-relaxed theme-muted">{detail}</p></article>)}</div>
  </section>
  <section className="grid gap-3 sm:grid-cols-2"><div className="rounded-2xl border theme-border theme-surface p-3 sm:p-4"><h2 className="flex items-center gap-2 text-sm font-bold"><ShieldCheck size={16} className="text-emerald-500"/>Fail-closed quality gates</h2><ul className="mt-3 space-y-2 text-xs theme-muted"><li>• Quote freshness and timestamp skew</li><li>• Spread, depth and liquidity checks</li><li>• Correlated evidence not double-counted</li><li>• Signal expiry and duplicate suppression</li><li>• Opposite-direction conflict blocks trigger</li><li>• Structural invalidation required</li><li>• Broker routing disabled</li></ul></div>
  <div className="rounded-2xl border theme-border theme-surface p-3 sm:p-4"><h2 className="flex items-center gap-2 text-sm font-bold"><Clock3 size={16} className="theme-accent"/>Replay calibration</h2><div className="mt-3 grid grid-cols-2 gap-2"><Stat label="Cooldown" value={num(history?.cooldown_seconds)+"s"} sub="backend reported"/><Stat label="History capacity" value={num(history?.capacity)} sub="bounded store"/><Stat label="Duplicates suppressed" value={num(history?.suppressed_duplicate_count)} sub="backend reported"/><Stat label="Routing" value="OFF" sub="scanner only"/></div><p className="mt-3 text-xs theme-muted">Precision, lead-time, MFE/MAE and calibrated probability are not displayed until measured from replay/outcome data.</p></div></section>
  <section className="rounded-2xl border theme-border theme-surface p-3 sm:p-4"><div className="flex items-center gap-2 text-sm font-bold"><Waves size={16} className="theme-accent"/>Accuracy-first roadmap</div><p className="mt-2 text-xs leading-relaxed theme-muted">Adaptive median/MAD baselines, regime-conditioned thresholds, event decay, correlation penalties, contradiction handling and feed-quality gates are proposed design features—not yet claimed as implemented live.</p></section>
 </main>;
}

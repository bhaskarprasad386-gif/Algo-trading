"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  Activity, ArrowDownRight, ArrowUpRight, BellRing, CheckCircle2, CircleDot,
  Clock3, Layers3, RefreshCw, ShieldAlert, ShieldCheck, Signal, Waves, Zap,
} from "lucide-react";
import { appConfig } from "@/lib/config";

type Signal = {
  underlying?: string; direction?: string; score?: number; grade?: string;
  timestamp?: string; entry?: number; invalidation?: number; target_1?: number;
  reason?: string; scanner_only?: boolean;
};
type Status = {
  status?: string; feed_connected?: boolean; scanner_active?: boolean;
  feed_status?: string; feed_epoch?: number; fresh_quote_count?: number;
  stale_quote_count?: number; observer_errors?: number; market_regime?: string;
  signal_count?: number; latest_signal?: Signal | null;
  order_routing_enabled?: boolean; iv_scanner_dependency?: boolean;
};
type History = { signals?: Signal[]; signal_count?: number; capacity?: number; suppressed_duplicate_count?: number; cooldown_seconds?: number; order_routing_enabled?: boolean; scanner_only?: boolean };

const modules = [
  {name:"Liquidity & Trap",tag:"LOCATION",detail:"PDH / PDL, session extremes, equal highs/lows, sweep depth, reclaim quality and failed acceptance."},
  {name:"Absorption & Aggressor Flow",tag:"ORDER FLOW",detail:"Aggressive flow versus price progress, replenishment evidence, delta divergence and imbalance clusters."},
  {name:"Futures Lead–Lag",tag:"EARLY WARNING",detail:"Timestamp-aligned spot/futures returns, basis shock and acceleration, futures range break and OI context."},
  {name:"Options Pressure",tag:"DERIVATIVES",detail:"Near-ATM OI velocity, volume/OI context, IV repricing and strike migration; dealer gamma is not assumed."},
  {name:"Structure & Execution",tag:"TRIGGER",detail:"Anchored VWAP, impulse quality, structural break/retest, executable spread and invalidation."},
  {name:"Cross-Market Context",tag:"CONFIRMATION",detail:"Independent index participation and conflict detection with correlated evidence discounting."},
];

const fmt = (v?: number | null) => typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("en-IN",{maximumFractionDigits:2}) : "—";
const time = (v?: string) => v ? new Date(v).toLocaleTimeString("en-IN",{timeZone:"Asia/Kolkata",hour:"2-digit",minute:"2-digit",second:"2-digit"})+" IST" : "—";
const direction = (d?: string) => (d ?? "—").toUpperCase();

function Pill({children,tone="neutral"}:{children:ReactNode;tone?: "neutral"|"green"|"red"|"amber"}) {
  const cls = tone==="green" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300" : tone==="red" ? "border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-300" : tone==="amber" ? "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300" : "border theme-border theme-surface-2 theme-muted";
  return <span className={`inline-flex items-center rounded-full border px-2 py-1 text-[10px] font-semibold ${cls}`}>{children}</span>;
}
function Stat({label,value,sub}:{label:string;value:string;sub?:string}) {
  return <div className="min-w-0 rounded-xl border theme-border theme-surface-2 p-3"><div className="text-[10px] font-semibold uppercase tracking-wide theme-muted">{label}</div><div className="mt-1 truncate text-lg font-extrabold theme-text">{value}</div>{sub&&<div className="mt-1 text-[10px] theme-muted">{sub}</div>}</div>;
}
function DirectionPanel({title,direction,description,signals}:{title:string;direction:"BUY" | "SELL";description:string;signals:Signal[]}) {
  const bullish=direction==="BUY";
  const list=signals.filter(s=>String(s.direction??"").toUpperCase()===direction);
  return <section className="rounded-2xl border theme-border theme-surface overflow-hidden">
    <div className={`border-b theme-border p-4 ${bullish?"bg-emerald-500/5":"bg-rose-500/5"}`}>
      <div className="flex items-center justify-between gap-2"><div className="flex items-center gap-2 text-sm font-bold"><span className={`rounded-lg p-2 ${bullish?"bg-emerald-500/10 text-emerald-600":"bg-rose-500/10 text-rose-600"}`}>{bullish?<ArrowUpRight size={18}/>:<ArrowDownRight size={18}/>}</span>{title}</div><Pill tone={bullish?"green":"red"}>{bullish?"BULLISH":"BEARISH"}</Pill></div>
      <p className="px-4 pt-3 text-xs theme-muted">{description}</p>
    </div>
    <div className="p-3 space-y-2">
      {list.length?list.slice(0,5).map((s,i)=><div key={s.timestamp??i} className="rounded-xl border theme-border p-3">
        <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-xs font-bold theme-text">{s.underlying??"Underlying"} · {direction(s.direction)}</span><Pill tone="neutral">{s.grade??"SIGNAL"} · {s.score??"—"}</Pill></div>
        <div className="mt-2 grid grid-cols-2 gap-2 text-[11px] theme-muted"><div>Time: {time(s.timestamp)}</div><div>Entry: {fmt(s.entry)}</div><div>Invalidation: {fmt(s.invalidation)}</div><div>Target: {fmt(s.target_1)}</div></div>
        {s.reason&&<p className="mt-2 text-xs theme-muted">{s.reason}</p>}
      </div>):<div className="rounded-xl border border-dashed theme-border p-4 text-xs theme-muted">No validated {bullish?"bullish":"bearish"} signals in current history. No demo signals are shown.</div>}
    </div>
  </section>;
}

export default function BiDirectionalImpulsePage() {
  const [status,setStatus]=useState<Status|null>(null);
  const [history,setHistory]=useState<History|null>(null);
  const [updated,setUpdated]=useState<string|null>(null);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState<string|null>(null);

  const refresh=useCallback(async()=>{
    if(loading)return;
    setLoading(true);
    const base=appConfig.apiBaseUrl.replace(/\/$/,"");
    try{
      const [sr,hr]=await Promise.all([
        fetch(base+"/api/v1/early-impulse/status",{cache:"no-store",signal:AbortSignal.timeout(6500)}),
        fetch(base+"/api/v1/early-impulse/signals",{cache:"no-store",signal:AbortSignal.timeout(6500)}),
      ]);
      if(!sr.ok)throw new Error("Status endpoint HTTP "+sr.status);
      const s=await sr.json() as Status;
      setStatus(s);
      if(hr.ok)setHistory(await hr.json() as History);
      setUpdated(new Date().toISOString());setError(null);
    }catch(e){setError(e instanceof Error?e.message:"Unable to read live scanner status");}
    finally{setLoading(false);}
  },[loading]);

  useEffect(()=>{void refresh();const id=window.setInterval(()=>void refresh(),20000);return()=>window.clearInterval(id);},[refresh]);

  const feed=status?.feed_connected===true;
  const signals=history?.signals??[];
  const bulls=signals.filter(s=>String(s.direction??"").toUpperCase()==="BUY");
  const bears=signals.filter(s=>String(s.direction??"").toUpperCase()==="SELL");
  const feedTone=feed?"green":"amber";

  return <main className="space-y-4">
    <section className="overflow-hidden rounded-2xl border theme-border theme-surface">
      <div className="relative p-4 sm:p-6">
        <div className="absolute right-0 top-0 h-32 w-32 rounded-full bg-cyan-500/5 blur-3xl"/>
        <div className="relative flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0"><div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.16em] theme-accent"><Activity size={14}/> Independent signal workspace</div>
          <h1 className="mt-1 text-2xl font-black tracking-tight sm:text-3xl">BI-DIRECTIONAL <span className="theme-accent">IMPULSE</span></h1>
          <p className="mt-2 max-w-3xl text-sm theme-muted">Short-covering and long-unwinding intelligence — event sequence, liquidity traps, absorption, futures lead and derivatives context. Scanner-only; no broker execution.</p></div>
          <button onClick={()=>void refresh()} disabled={loading} className="inline-flex min-h-10 items-center gap-2 rounded-xl border theme-border theme-surface-2 px-3 text-xs font-bold theme-text disabled:opacity-60"><RefreshCw size={14} className={loading?"animate-spin":""}/> Refresh</button>
        </div>
        <div className="mt-4 flex flex-wrap gap-2"><Pill tone={feedTone}>{feed?"FEED CONNECTED":"FEED WAITING"}</Pill><Pill tone={status?.scanner_active?"green":"amber"}>{status?.scanner_active?"RUNNER ACTIVE":"RUNNER INACTIVE"}</Pill><Pill>{status?.market_regime??"REGIME UNKNOWN"}</Pill><Pill>ROUTING OFF</Pill><Pill>IV SCANNER INDEPENDENT</Pill></div>
      </div>
      {error&&<div className="mx-3 mb-3 flex gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-300"><ShieldAlert size={15}/><span>Live status unavailable: {error}. Last valid snapshot retained.</span></div>}
      {!feed&&!error&&<div className="mx-3 mb-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-300">Feed not live. Scorecard remains blank until valid backend observations arrive. No synthetic market data or simulated scores.</div>}
      <div className="grid grid-cols-2 gap-2 border-t theme-border p-3 sm:grid-cols-4">
        <Stat label="Feed quotes" value={fmt(status?.fresh_quote_count)} sub="fresh observations"/>
        <Stat label="Stale quotes" value={fmt(status?.stale_quote_count)} sub="quality gate"/>
        <Stat label="Signal records" value={fmt(history?.signal_count??status?.signal_count)} sub="validated history"/>
        <Stat label="Feed epoch" value={fmt(status?.feed_epoch)} sub="reconnect generation"/>
      </div>
      <div className="px-3 pb-3 text-[10px] theme-muted">Updated: {time(updated??undefined)} · Auto-refresh 20s · {status?.observer_errors??0} observer errors</div>
    </section>

    <section className="grid gap-3 sm:grid-cols-2">
      <DirectionPanel title="BUY Signal History" direction="BUY" description="Existing scanner BUY records only. The dedicated short-covering sequence engine is not yet wired to live feature data." signals={signals}/>
      <DirectionPanel title="SELL Signal History" direction="SELL" description="Existing scanner SELL records only. The dedicated long-unwinding sequence engine is not yet wired to live feature data." signals={signals}/>
    </section>

    <section className="rounded-2xl border theme-border theme-surface p-3 sm:p-4">
      <div className="flex items-start justify-between gap-3"><div><h2 className="text-base font-bold">Signal pipeline</h2><p className="mt-1 text-xs theme-muted">Conceptual stages only; the current backend does not emit these dedicated event states yet.</p></div><Layers3 size={18} className="theme-accent"/></div>
      <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ["01","PRECONDITION","Regime + liquidity context",CircleDot],
          ["02","PRESSURE BUILD","Absorption / lead-lag evidence",Waves],
          ["03","PRE-IMPULSE","Confluence; trigger pending",BellRing],
          ["04","TRIGGERED","Structure + displacement + gates",Zap],
        ].map(([n,t,d,Icon])=><div key={String(n)} className="rounded-xl border theme-border theme-surface-2 p-3"><div className="flex items-center justify-between"><span className="text-[10px] font-bold theme-muted">{n}</span><Icon size={16} className="theme-muted"/></div><div className="mt-2 text-xs font-bold theme-text">{String(t)}</div><div className="mt-1 text-[11px] theme-muted">{String(d)}</div><div className="mt-2"><Pill>Awaiting live event</Pill></div></div>)}
      </div>
    </section>

    <section className="rounded-2xl border theme-border theme-surface">
      <div className="border-b theme-border p-3 sm:p-4"><div className="flex items-center gap-2"><Signal size={17} className="theme-accent"/><h2 className="text-base font-bold">6 Intelligence Engines</h2></div><p className="mt-1 text-xs theme-muted">Independent evidence groups · correlated evidence discount · missing-data fail-closed</p></div>
      <div className="grid gap-2 p-3 sm:grid-cols-2 lg:grid-cols-3">
        {modules.map((m,i)=><article key={m.name} className="rounded-xl border theme-border theme-surface-2 p-3"><div className="flex items-center justify-between gap-2"><span className="text-[10px] font-bold theme-accent">{String(i+1).padStart(2,"0")} · {m.tag}</span><Pill>DATA GATED</Pill></div><h3 className="mt-2 text-sm font-bold theme-text">{m.name}</h3><p className="mt-1 text-xs leading-relaxed theme-muted">{m.detail}</p></article>)}
      </div>
    </section>

    <section className="grid gap-3 sm:grid-cols-2">
      <div className="rounded-2xl border theme-border theme-surface p-3 sm:p-4"><div className="flex items-center gap-2 text-sm font-bold"><ShieldCheck size={16} className="text-emerald-500"/> Quality Gates</div><ul className="mt-3 space-y-2 text-xs theme-muted"><li>• Quote freshness + timestamp skew</li><li>• Spread / depth / liquidity validation</li><li>• Correlated evidence is not double-counted</li><li>• Signal expiry and duplicate suppression</li><li>• Opposite-direction conflict blocks triggers</li><li>• Structure-based invalidation required</li><li>• Scanner-only; order routing disabled</li></ul></div>
      <div className="rounded-2xl border theme-border theme-surface p-3 sm:p-4"><div className="flex items-center gap-2 text-sm font-bold"><Clock3 size={16} className="theme-accent"/> Validation & Calibration</div><div className="mt-3 grid grid-cols-2 gap-2"><Stat label="Cooldown" value={String(history?.cooldown_seconds??"—")+"s"} sub="API reported"/><Stat label="History capacity" value={fmt(history?.capacity)} sub="bounded records"/><Stat label="Duplicates blocked" value={fmt(history?.suppressed_duplicate_count)} sub="API reported"/><Stat label="Execution" value="DISABLED" sub="paper/live not wired"/></div><p className="mt-3 text-xs theme-muted">Accuracy, precision, lead time, MFE/MAE and calibration will remain unreported until outcomes are measured from replay/live observations.</p></div>
    </section>

    <section className="rounded-2xl border theme-border theme-surface p-3 sm:p-4"><div className="flex items-center gap-2 text-sm font-bold"><CheckCircle2 size={17} className="theme-accent"/> Accuracy-first design</div><p className="mt-2 text-xs leading-relaxed theme-muted">Adaptive baselines (median/MAD), regime-specific thresholds, event sequence decay, independent evidence groups, contradiction penalty and feed-quality gates are the intended next backend steps. The current page deliberately displays no invented confidence or institutional-flow claims.</p></section>
  </main>;
}

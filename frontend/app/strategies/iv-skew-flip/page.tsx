"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, RefreshCw, Wifi, WifiOff } from "lucide-react";
import { appConfig } from "@/lib/config";

type Row = Record<string, unknown>;
type Chain = { symbol?: string; expiry_type?: string; option_feed?: Row; sampler?: Row; universe_tokens?: number; error?: string | null; signals?: Row };
type BasketEntry = {
  status?: string;
  signals_allowed?: boolean;
  reason?: string;
  effective_date?: string;
  age_days?: number;
  constituent_count?: number;
  mapped_token_count?: number;
  weight_sum?: number;
  source_name?: string;
  source_url?: string;
  imported_at?: string | null;
};
type BasketRegistry = {
  as_of?: string;
  max_age_days?: number;
  activation_enabled?: boolean;
  signals_allowed?: boolean;
  baskets?: Record<string, BasketEntry>;
};
type Runtime = {
  status?: string;
  desired_state?: "ON" | "OFF";
  saved_enabled?: boolean;
  runner_status?: "RUNNING" | "STOPPED";
  refresh_seconds?: number;
  signal_evaluation_seconds?: number;
  spot_feed?: Row;
  option_chains?: Record<string, Chain>;
  signals?: Record<string, Row>;
  early_move?: Record<string, Row>;
  equity_pressure?: Row;
};
const n = (v: unknown) => typeof v === "number" && Number.isFinite(v) ? v : null;
const fmt = (v: unknown, digits = 2) => n(v) === null ? "—" : (v as number).toLocaleString("en-IN", { maximumFractionDigits: digits });
const delta = (v: unknown, suffix = "", digits = 2) => n(v) === null ? "—" : `${Number(v) > 0 ? "+" : ""}${fmt(v, digits)}${suffix}`;
const age = (v: unknown) => n(v) === null ? "—" : n(v)! < 1000 ? `${Math.round(n(v)!)} ms` : `${(n(v)! / 1000).toFixed(1)} s`;
const apiError = (body: Row, fallback: string) => {
  const detail = body.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const info = detail as Row;
    return [typeof info.message === "string" ? info.message : "", typeof info.reason === "string" ? info.reason : ""].filter(Boolean).join(": ") || fallback;
  }
  return fallback;
};
function DeltaCell({ value, pct, suffix = "" }: { value: unknown; pct?: unknown; suffix?: string }) {
  const amount = n(value);
  if (amount === null) return <span className="theme-muted">—</span>;
  const color = amount > 0 ? "text-emerald-600" : amount < 0 ? "text-rose-600" : "theme-muted";
  return <span className={color}><span>{delta(amount, suffix, suffix === " pt" ? 4 : 2)}</span>{n(pct) !== null && <span className="block text-[10px]">{delta(pct, "%", 2)}</span>}</span>;
}
function Metric({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return <div className="rounded-xl border theme-border theme-surface p-3 min-w-0"><div className="text-[11px] uppercase tracking-wide theme-muted">{label}</div><div className="mt-1 text-lg font-bold theme-text truncate">{value}</div>{sub && <div className="mt-1 text-[11px] theme-muted">{sub}</div>}</div>;
}
function SignalSummary({ title, chain, spot }: { title: string; chain: Chain; spot: Row }) {
  const feed = chain.option_feed ?? {};
  const sampler = chain.sampler ?? {};
  const metrics = Array.isArray(sampler.live_metrics) ? sampler.live_metrics as Row[] : [];
  const avg = (rows: Row[]) => rows.length ? rows.reduce((s, x) => s + Number(x.iv), 0) / rows.length : null;
  const spotPrice = n(spot.price);
  const resolvedStrikes = Array.isArray(feed.strikes) ? feed.strikes.map(n).filter((x): x is number => x !== null) : [];
  const strikes = (resolvedStrikes.length ? resolvedStrikes : metrics.map(x => n(x.strike)).filter((x): x is number => x !== null)).sort((a,b)=>a-b);
  const atmIndex = spotPrice !== null && strikes.length ? strikes.reduce((best, x, index) => Math.abs(x - spotPrice) < Math.abs(strikes[best] - spotPrice) ? index : best, 0) : -1;
  const atm = atmIndex >= 0 ? strikes[atmIndex] : null;
  const byContract = new Map(metrics.filter(x => n(x.iv) !== null).map(x => [`${n(x.strike)}:${String(x.option_type)}`, x]));
  const matchedSkewPairs: number[] = [];
  if (atmIndex >= 0) {
    for (const offset of [0, 1, 2]) {
      const call = byContract.get(`${strikes[atmIndex + offset]}:CE`);
      const put = byContract.get(`${strikes[atmIndex - offset]}:PE`);
      if (call && put) matchedSkewPairs.push(Number(call.iv) - Number(put.iv));
    }
  }
  // Match the backend engine: ATM and up to two equidistant OTM CE/PE pairs.
  // Do not average the whole call and put wings, which can bias the displayed skew.
  const skew = matchedSkewPairs.length >= 2
    ? matchedSkewPairs.reduce((sum, value) => sum + value, 0) / matchedSkewPairs.length
    : null;
  const atmPair = metrics.filter(x => x.strike === atm && n(x.iv) !== null);
  const atmIv = atmPair.length ? avg(atmPair) : null;
  const signals = chain.signals && typeof chain.signals === "object" ? chain.signals as Row : {};
  const strategies = Array.isArray(signals.strategies) ? signals.strategies as Row[] : [];
  const status = (name: string) => String(strategies.find(x => x.strategy === name)?.status ?? "WAITING");
  return <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
    <Metric label={`${title} Spot`} value={fmt(spotPrice)} sub={`${spot.last_trade_time ? new Date(String(spot.last_trade_time)).toLocaleString("en-IN",{timeZone:"Asia/Kolkata",hour12:true}) : "Time unavailable"} • ${spot.fresh === true ? "LIVE" : spot.quote_status === "LAST_KNOWN_STALE" ? `LAST KNOWN • STALE • ${age(spot.age_ms)} old` : "WAITING"}`} />
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
  const resolvedStrikes = Array.isArray(feed.strikes) ? feed.strikes.map(n).filter((x): x is number => x !== null) : [];
  const strikes = resolvedStrikes.length ? resolvedStrikes : [...new Set(merged.map(x => n(x.strike)).filter((x): x is number => x !== null))].sort((a,b)=>a-b);
  const byKey = new Map(merged.map(x => [`${x.strike}:${x.option_type}`, x]));
  const spotFeed = feed.status ?? "WAITING";
  return <section className="overflow-hidden rounded-2xl border theme-border theme-surface">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b theme-border p-3 sm:p-4">
      <div><h2 className="text-lg font-bold theme-text">{title} Option Chain</h2><p className="text-xs theme-muted mt-1">{String(feed.expiry_type ?? chain.expiry_type ?? "—")} • Expiry {String(feed.expiry ?? "—")} • ATM ±3 strikes</p></div>
      <div className="flex flex-wrap gap-2 text-xs"><span className="rounded-lg border theme-border px-2 py-1">Feed: {String(spotFeed)}</span><span className="rounded-lg border theme-border px-2 py-1">Contracts: {fmt(feed.received_contracts,0)}/{fmt(feed.expected_contracts,0)}</span><span className="rounded-lg border theme-border px-2 py-1">IV: {fmt(sampler.iv_live_count,0)} • OI: {fmt(sampler.oi_live_count,0)}</span></div>
    </div>
    {chain.error && <div className="m-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-600">{chain.error}</div>}
    <div className="overflow-x-auto">
      <table className="w-full min-w-[1350px] border-collapse border border theme-border text-center text-xs">
        <thead><tr><th colSpan={8} className="border border theme-border bg-emerald-500/10 py-2 text-emerald-700 text-sm">Calls (CE)</th><th rowSpan={2} className="border border theme-border theme-surface-2 px-3 font-bold theme-text">Strike</th><th colSpan={8} className="border border theme-border bg-rose-500/10 py-2 text-rose-700 text-sm">Puts (PE)</th></tr>
          <tr className="theme-surface-2 theme-muted">{["OI","OI Δ 1s","IV %","IV Δ 1s","LTP","LTP Δ 1s","Bid","Ask","Bid","Ask","LTP","LTP Δ 1s","IV %","IV Δ 1s","OI Δ 1s","OI"].map((x,i)=><th key={i} className="border border theme-border px-2 py-2 font-semibold">{x}</th>)}</tr></thead>
        <tbody>{strikes.length === 0 ? <tr><td colSpan={17} className="border border theme-border p-10 theme-muted">Waiting for fresh option quotes. No placeholder market values are displayed.</td></tr> : strikes.map(strike=>{
          const ce=byKey.get(`${strike}:CE`) ?? {}, pe=byKey.get(`${strike}:PE`) ?? {};
          const cell=(v:unknown,d=2)=>n(v)===null?"—":fmt(v,d);
          return <tr key={strike} className="border-b border theme-border hover:theme-surface-2">
            <td className="border border theme-border px-2 py-2">{cell(ce.oi,0)}</td><td className="border border theme-border px-2 py-2"><DeltaCell value={ce.oi_change_1s} pct={ce.oi_change_pct_1s}/></td><td className="border border theme-border px-2 py-2 font-semibold">{ce.iv_live?cell(ce.iv,2):"—"}</td><td className="border border theme-border px-2 py-2"><DeltaCell value={ce.iv_change_1s} suffix=" pt"/></td><td className="border border theme-border px-2 py-2" title={n(ce.ltp) !== null ? `Last broker-traded price • ${ce.fresh === true ? `live ${age(ce.age_ms)} ago` : `last received ${age(ce.age_ms)} ago; may be stale after market close`}` : "No broker LTP received yet"}>{cell(ce.ltp)}</td><td className="border border theme-border px-2 py-2"><DeltaCell value={ce.ltp_change_1s} pct={ce.ltp_change_pct_1s}/></td><td className="border border theme-border px-2 py-2 text-emerald-600">{cell(ce.bid)}</td><td className="border border theme-border px-2 py-2 text-rose-600">{cell(ce.ask)}</td>
            <td className={`border border theme-border px-3 py-2 font-bold ${strike===strikes[Math.floor(strikes.length/2)]?"bg-blue-500/15 text-blue-700":"theme-surface-2"}`}>{fmt(strike,0)}</td>
            <td className="border border theme-border px-2 py-2 text-emerald-600">{cell(pe.bid)}</td><td className="border border theme-border px-2 py-2 text-rose-600">{cell(pe.ask)}</td><td className="border border theme-border px-2 py-2" title={n(pe.ltp) !== null ? `Last broker-traded price • ${pe.fresh === true ? `live ${age(pe.age_ms)} ago` : `last received ${age(pe.age_ms)} ago; may be stale after market close`}` : "No broker LTP received yet"}>{cell(pe.ltp)}</td><td className="border border theme-border px-2 py-2"><DeltaCell value={pe.ltp_change_1s} pct={pe.ltp_change_pct_1s}/></td><td className="border border theme-border px-2 py-2 font-semibold">{pe.iv_live?cell(pe.iv,2):"—"}</td><td className="border border theme-border px-2 py-2"><DeltaCell value={pe.iv_change_1s} suffix=" pt"/></td><td className="border border theme-border px-2 py-2"><DeltaCell value={pe.oi_change_1s} pct={pe.oi_change_pct_1s}/></td><td className="border border theme-border px-2 py-2">{cell(pe.oi,0)}</td>
          </tr>;
        })}</tbody>
      </table>
    </div>
    <div className="border-t theme-border px-3 py-2 text-[11px] theme-muted">LTP is the actual broker last-traded price (never the IV pricing mark). The last received LTP remains visible after market close or when the feed becomes stale, with its quote age in the tooltip; it is not represented as a live price. IV, OI and LTP Δ update only from valid fresh broker ticks; stale ticks do not create artificial changes. IV Δ is in percentage points. OI is broker tick open interest.</div>
  </section>;
}
export default function IVSkewFlipPage() {
  const [runtime,setRuntime]=useState<Runtime|null>(null);
  const [basketRegistry,setBasketRegistry]=useState<BasketRegistry|null>(null);
  const [selectedNifty,setSelectedNifty]=useState<"NIFTY_WEEKLY"|"NIFTY_MONTHLY">("NIFTY_WEEKLY");
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [selectedEnabled,setSelectedEnabled]=useState(false);
  const [savedEnabled,setSavedEnabled]=useState(false);
  const [settingDirty,setSettingDirty]=useState(false);
  const base=appConfig.apiBaseUrl.replace(/\/$/,"");
  useEffect(()=>{
    let active=true;
    const poll=async()=>{
      try{
        const response=await fetch(`${base}/api/v1/scanner/iv-skew-flip/baskets`,{cache:"no-store"});
        const body=await response.json();
        if(active&&response.ok)setBasketRegistry(body as BasketRegistry);
      }catch{/* keep last known basket registry state */}
    };
    void poll();
    const timer=window.setInterval(()=>{if(active)void poll();},10000);
    return()=>{active=false;window.clearInterval(timer);};
  },[base]);
  const refresh=useCallback(async()=>{
    const response=await fetch(`${base}/api/v1/scanner/iv-skew-flip/status`,{cache:"no-store"});
    const body=await response.json();
    if(!response.ok) throw new Error(apiError(body,`Status HTTP ${response.status}`));
    setRuntime(body.runtime??null);
    setSelectedEnabled(body.saved_enabled===true);
    setSavedEnabled(body.saved_enabled===true);
    setSettingDirty(false);
  },[base]);
  useEffect(()=>{
    let active=true;
    const poll=async()=>{try{const response=await fetch(`${base}/api/v1/scanner/iv-skew-flip/status`,{cache:"no-store"});const body=await response.json();if(active&&response.ok){setRuntime(body.runtime??null);setSavedEnabled(body.saved_enabled===true);if(!settingDirty)setSelectedEnabled(body.saved_enabled===true);}}catch{/* keep last known state */}};
    void poll();const timer=window.setInterval(()=>{if(active)void poll();},1000);
    return()=>{active=false;window.clearInterval(timer);};
  },[base,settingDirty]);
  async function saveRunnerSetting(){
    setBusy(true);setError("");
    try{
      const response=await fetch(`${base}/api/v1/scanner/iv-skew-flip/config`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:selectedEnabled}),cache:"no-store"});
      const body=await response.json();
      if(!response.ok)throw new Error(apiError(body,"Could not save IV Scanner runner setting"));
      setRuntime(body.runtime??null);
      setSavedEnabled(body.saved_enabled===true);
      setSelectedEnabled(body.saved_enabled===true);
      setSettingDirty(false);
    }catch(e){setError(e instanceof Error?e.message:"IV Scanner setting save failed");}
    finally{setBusy(false);}
  }
  const chains=runtime?.option_chains??{};
  const nifty=chains[selectedNifty]??{};
  const bank=chains.BANKNIFTY_MONTHLY??{};
  const spotFeed = runtime?.spot_feed && typeof runtime.spot_feed === "object" ? runtime.spot_feed as Row : {};
  const spot = spotFeed.spot && typeof spotFeed.spot === "object" ? spotFeed.spot as Row : {};
  const niftySpot=(spot.NIFTY&&typeof spot.NIFTY==="object"?spot.NIFTY:{}) as Row;
  const bankSpot=(spot.BANKNIFTY&&typeof spot.BANKNIFTY==="object"?spot.BANKNIFTY:{}) as Row;
  const running=runtime?.status==="RUNNING";
  const niftyExpiry=String((nifty.option_feed??{}).expiry_type??"");
  const bankExpiry=String((bank.option_feed??{}).expiry_type??"");
  return <main className="space-y-4 p-3 sm:p-5 theme-text">
    <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div><div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] theme-accent"><Activity size={15}/> Live Options Analytics</div><h1 className="mt-1 text-2xl font-bold sm:text-3xl">IV Skew / IV Flip + Gamma Blast</h1><p className="mt-1 text-sm theme-muted">NIFTY Weekly + Monthly • BANKNIFTY Monthly • Live OI and modeled IV</p></div>
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={()=>void refresh().catch(e=>setError(String(e)))} disabled={busy} className="inline-flex items-center gap-2 rounded-xl border theme-border theme-surface px-3 py-2 text-sm"><RefreshCw size={15}/> Refresh</button>
        <div className="inline-flex rounded-xl border theme-border p-1" aria-label="IV Scanner desired runner state">
          <button type="button" onClick={()=>{setSelectedEnabled(true);setSettingDirty(true);}} disabled={busy} aria-pressed={selectedEnabled} className={`rounded-lg px-3 py-2 text-sm font-semibold ${selectedEnabled?"bg-emerald-600 text-white":"theme-muted"}`}>ON</button>
          <button type="button" onClick={()=>{setSelectedEnabled(false);setSettingDirty(true);}} disabled={busy} aria-pressed={!selectedEnabled} className={`rounded-lg px-3 py-2 text-sm font-semibold ${!selectedEnabled?"bg-rose-600 text-white":"theme-muted"}`}>OFF</button>
        </div>
        <button onClick={()=>void saveRunnerSetting()} disabled={busy||selectedEnabled===savedEnabled} className="rounded-xl border theme-border theme-surface px-4 py-2 text-sm font-semibold disabled:opacity-50">{busy?"Saving…":"Save"}</button>
      </div>
    </header>
    <div className="flex flex-wrap gap-2 text-xs"><span className="rounded-full border theme-border px-3 py-2">{running?<Wifi size={13} className="inline"/>:<WifiOff size={13} className="inline"/>} {`Runner ${runtime?.runner_status??runtime?.status??"NOT STARTED"} • Saved ${savedEnabled?"ON":"OFF"}`}</span><span className="rounded-full border theme-border px-3 py-2">Universe refresh {fmt(runtime?.refresh_seconds,0)}s</span><span className="rounded-full border theme-border px-3 py-2">Signal evaluation {fmt(runtime?.signal_evaluation_seconds,0)}s</span><span className="rounded-full border theme-border px-3 py-2">Orders OFF</span></div>
    {error&&<div role="alert" className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-600">{error}</div>}
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-xl font-bold theme-text">NIFTY Option Chain</h2><div className="inline-flex rounded-xl border theme-border p-1"><button onClick={()=>setSelectedNifty("NIFTY_WEEKLY")} className={`rounded-lg px-4 py-2 text-sm font-semibold ${selectedNifty==="NIFTY_WEEKLY"?"bg-blue-600 text-white":"theme-muted"}`}>Weekly</button><button onClick={()=>setSelectedNifty("NIFTY_MONTHLY")} className={`rounded-lg px-4 py-2 text-sm font-semibold ${selectedNifty==="NIFTY_MONTHLY"?"bg-blue-600 text-white":"theme-muted"}`}>Monthly</button></div></div>
      <OptionChain title={`NIFTY ${niftyExpiry|| (selectedNifty==="NIFTY_WEEKLY"?"Weekly":"Monthly")}`} chain={nifty}/>
      <SignalSummary title="NIFTY" chain={nifty} spot={niftySpot}/>
    </section>
    <section className="space-y-3 pt-2"><div className="flex items-center justify-between gap-2"><h2 className="text-xl font-bold theme-text">BANKNIFTY Option Chain</h2><span className="rounded-lg border theme-border px-3 py-2 text-sm font-semibold">Monthly</span></div><OptionChain title="BANKNIFTY Monthly" chain={bank}/><SignalSummary title="BANKNIFTY" chain={bank} spot={bankSpot}/></section>
    <section className="rounded-2xl border theme-border theme-surface p-4">
      <h2 className="font-semibold theme-text">Early Move Monitor</h2>
      <p className="mt-1 text-xs theme-muted">1-second refresh • rolling 10-second volume buckets • IV/premium/spot confluence. Alert-only; no orders.</p>
      <div className="mt-3 grid gap-3 md:grid-cols-3">
        {Object.entries(runtime?.early_move??{}).map(([key,value])=><div key={key} className="rounded-xl border theme-border p-3">
          <div className="flex items-center justify-between gap-2"><b className="text-sm">{key.replaceAll("_"," ")}</b><span className={`rounded-full px-2 py-1 text-xs font-bold ${value.state==="PRE_MOVE"?"bg-amber-500/15 text-amber-700":value.state==="CONFIRMED"?"bg-emerald-500/15 text-emerald-700":"theme-surface-2 theme-muted"}`}>{String(value.state??"WATCH")}</span></div>
          <div className="mt-2 text-lg font-bold">{String(value.direction??"—")}</div>
          <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
            <div><span className="theme-muted">Volume ratio</span><b className="block">{fmt(value.volume_ratio)}x</b></div>
            <div><span className="theme-muted">Premium Δ</span><b className="block">{delta(value.premium_change_pct,"%")}</b></div>
            <div><span className="theme-muted">IV Δ</span><b className="block">{delta(value.iv_change_points," pt",4)}</b></div>
            <div><span className="theme-muted">Spot Δ 10s</span><b className="block">{delta(value.spot_change_pct,"%",4)}</b></div>
          </div>
          <p className="mt-2 text-xs theme-muted">{String(value.reason??"Waiting for fresh ticks")}</p>
          <p className="mt-1 text-[10px] theme-muted">Baseline {String(value.baseline_status??"WARMING")} • contracts {fmt(value.contracts_confirming,0)}</p>
        </div>)}
      </div>
    </section>
    <section className="rounded-2xl border theme-border theme-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><h2 className="font-semibold theme-text">Weighted Constituents Pressure</h2><p className="mt-1 text-xs theme-muted">Research feed • 5s / 10s / 30s weighted returns • no order execution</p></div>
        <span className="rounded-full border theme-border px-3 py-1 text-xs font-semibold">{String(runtime?.equity_pressure?.status ?? "DISABLED")}</span>
      </div>
      {runtime?.equity_pressure?.enabled !== true ? <p className="mt-3 text-sm theme-muted">DISABLED — verified official index-weight basket is not configured. This module will not subscribe to equity constituents or generate directional pressure until a verified basket is injected.</p> : <>
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Metric label="Feed" value={String((runtime.equity_pressure.feed as Row | undefined)?.status ?? "WAITING")} />
          <Metric label="Fresh Components" value={`${fmt(runtime.equity_pressure.fresh_components,0)} / ${fmt(runtime.equity_pressure.expected_components,0)}`} />
          <Metric label="Weight Coverage" value={`${fmt(runtime.equity_pressure.weight_coverage_pct)}%`} />
          <Metric label="Direction" value={String(runtime.equity_pressure.direction ?? "WAITING")} sub={runtime.equity_pressure.signals_allowed === true ? "Timeframes agree" : "Directional output suppressed"} />
        </div>
        <div className="mt-3 grid gap-2 md:grid-cols-3">
          {["5s","10s","30s"].map((windowKey)=>{const w=(runtime.equity_pressure?.windows as Row | undefined)?.[windowKey] as Row | undefined;return <div key={windowKey} className="rounded-xl border theme-border p-3"><div className="flex justify-between gap-2 text-sm"><b>{windowKey}</b><b>{String(w?.direction ?? "WAITING")}</b></div><div className="mt-2 text-xs theme-muted">Weighted return <b className="theme-text">{delta(w?.weighted_return_pct,"% ",4)}</b></div><div className="mt-1 text-xs theme-muted">Coverage {fmt(w?.coverage_pct)}% • Advancing {fmt(w?.advancing_weight_pct)}%</div><div className="mt-2 text-xs theme-muted">Top contributors: {Array.isArray(w?.top_contributors)?(w!.top_contributors as Row[]).slice(0,3).map(x=>String(x.symbol??"")).join(", ")||"—":"—"}</div></div>;})}
        </div>
      </>}
    </section>
    <section className="rounded-2xl border theme-border theme-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><h2 className="font-semibold theme-text">Official Index Weight Registry</h2><p className="mt-1 text-xs theme-muted">NIFTY and BANKNIFTY • read-only provenance and freshness • no automatic activation</p></div>
        <span className="rounded-full border theme-border px-3 py-1 text-xs font-semibold">As of {basketRegistry?.as_of ?? "—"}</span>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        {(["NIFTY","BANKNIFTY"] as const).map((symbol)=>{
          const item=basketRegistry?.baskets?.[symbol]??{};
          const verified=item.status==="VERIFIED_FRESH";
          return <div key={symbol} className="rounded-xl border theme-border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2"><b className="text-sm theme-text">{symbol}</b><span className={`rounded-full px-2 py-1 text-xs font-semibold ${verified?"bg-emerald-500/10 text-emerald-700":"bg-amber-500/10 text-amber-700"}`}>{item.status??"LOADING"}</span></div>
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
              <div><span className="theme-muted">Effective date</span><b className="block theme-text">{item.effective_date??"—"}</b></div>
              <div><span className="theme-muted">Age</span><b className="block theme-text">{item.age_days===undefined?"—":`${item.age_days} days`}</b></div>
              <div><span className="theme-muted">Constituents</span><b className="block theme-text">{fmt(item.constituent_count,0)}</b></div>
              <div><span className="theme-muted">Mapped tokens</span><b className="block theme-text">{fmt(item.mapped_token_count,0)}</b></div>
              <div><span className="theme-muted">Weight total</span><b className="block theme-text">{item.weight_sum===undefined?"—":`${fmt(item.weight_sum,4)}%`}</b></div>
              <div><span className="theme-muted">Pressure signals</span><b className="block text-rose-600">BLOCKED</b></div>
            </div>
            <p className="mt-3 text-[11px] theme-muted">{item.reason??(verified?"Basket passed stored-data integrity checks. Runtime activation remains separately disabled.":"No verified current basket available.")}</p>
            {item.source_name&&<p className="mt-1 text-[11px] theme-muted">Source: {item.source_name}</p>}
          </div>;
        })}
      </div>
      <p className="mt-3 text-[11px] theme-muted">Registry max age {basketRegistry?.max_age_days??45} days • Activation {basketRegistry?.activation_enabled===true?"ON":"OFF"} • Signals {basketRegistry?.signals_allowed===true?"ALLOWED":"BLOCKED"}. A green verification state is provenance validation only; it does not turn on weighted pressure.</p>
    </section>
    <section className="rounded-2xl border theme-border theme-surface p-4"><h2 className="font-semibold theme-text">Live Signal Monitor</h2><p className="mt-1 text-xs theme-muted">Each expiry stream is evaluated independently. WAITING means required fresh quotes or history are not available; it is not a trade signal.</p><div className="mt-3 grid gap-2 sm:grid-cols-3">{Object.entries(runtime?.signals??{}).map(([key,value])=>{const finalSignal=value.confluence&&typeof value.confluence==="object"?value.confluence as Row:{};return <div key={key} className="rounded-xl border theme-border p-3"><b className="text-sm">{key.replaceAll("_"," ")}</b><div className="mt-2 rounded-lg border theme-border bg-blue-500/5 p-2"><div className="flex justify-between gap-2 text-xs"><span className="font-semibold">FINAL CONFLUENCE</span><b>{String(finalSignal.direction??"—")} • {String(finalSignal.status??"WAITING")}</b></div><p className="mt-1 text-[11px] theme-muted">{String(finalSignal.reason??"Waiting for independent confirmations")}</p><p className="mt-1 text-[10px] theme-muted">Signal gate: {value.signals_allowed===true?"ALLOWED":"BLOCKED"} • Data: {value.data_ready===true?"READY":"WAITING"}</p></div>{(Array.isArray(value.strategies)?value.strategies as Row[]:[]).map((s,i)=><div key={i} className="flex justify-between gap-2 border-t theme-border py-2 text-xs"><span>{String(s.strategy??"Signal")}</span><b>{String(s.direction??"—")} • {String(s.status??"WAITING")}</b></div>)}</div>})}</div></section>
    <p className="text-xs theme-muted">Live data appears only after fresh Angel One ticks arrive. Select ON or OFF and press Save to control the backend runner. Runner status is reported by the backend; no broker orders are placed.</p>
  </main>;
}

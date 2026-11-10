"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, RefreshCw, Wifi, WifiOff } from "lucide-react";
import { appConfig } from "@/lib/config";

type Row = Record<string, unknown>;
type Chain = { symbol?: string; expiry_type?: string; option_feed?: Row; sampler?: Row; universe_tokens?: number; expiry_selection?: Row; error?: string | null; signals?: Row };
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
  equity_pressure_by_underlying?: Record<string, Row>;
  index_future_feed?: Row;
  spot_future_basis?: Record<string, Row>;
};
const n = (v: unknown) => typeof v === "number" && Number.isFinite(v) ? v : null;
const fmt = (v: unknown, digits = 2) => n(v) === null ? "—" : (v as number).toLocaleString("en-IN", { maximumFractionDigits: digits });
const delta = (v: unknown, suffix = "", digits = 2) => n(v) === null ? "—" : `${Number(v) > 0 ? "+" : ""}${fmt(v, digits)}${suffix}`;
const age = (v: unknown) => n(v) === null ? "—" : n(v)! < 1000 ? `${Math.round(n(v)!)} ms` : `${(n(v)! / 1000).toFixed(1)} s`;
const sampleTime = (value: unknown) => {
  const ns = n(value);
  if (ns === null || ns <= 0) return "Time unavailable";
  const date = new Date(ns / 1_000_000);
  return Number.isNaN(date.getTime()) ? "Time unavailable" : date.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour12: true });
};
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
function compactIndian(value: unknown) {
  const amount = n(value);
  if (amount === null) return "—";
  const abs = Math.abs(amount);
  const sign = amount < 0 ? "-" : "";
  const raw = Math.round(abs).toLocaleString("en-IN");
  const unit = abs >= 10_000_000 ? `${sign}${(abs / 10_000_000).toFixed(2)} Cr` : abs >= 100_000 ? `${sign}${(abs / 100_000).toFixed(2)} Lakh` : null;
  return unit ? `${raw} (${unit})` : `${sign}${raw}`;
}
function StackedMetric({ value, change, pct, suffix = "", digits = 0, compact = false }: { value: unknown; change: unknown; pct?: unknown; suffix?: string; digits?: number; compact?: boolean }) {
  const amount = n(change);
  const changeColor = amount === null || amount === 0 ? "theme-muted" : amount > 0 ? "text-emerald-600" : "text-rose-600";
  return <div className="font-medium">{compact ? compactIndian(value) : n(value) === null ? "—" : fmt(value, digits)}<div className={`mt-0.5 text-[10px] font-normal ${compact ? changeColor : ""}`}>{compact ? <>{amount === null ? "—" : `${amount > 0 ? "+" : ""}${compactIndian(amount)}`}{n(pct) !== null && <span className="block">{delta(pct, "%", 2)}</span>}</> : <DeltaCell value={change} pct={pct} suffix={suffix}/>}</div></div>;
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
  const atmIndex = spotPrice !== null && strikes.length ? strikes.reduce((best, x, index) => { const distance = Math.abs(x - spotPrice); const bestDistance = Math.abs(strikes[best] - spotPrice); return distance < bestDistance || (distance === bestDistance && x > strikes[best]) ? index : best; }, 0) : -1;
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
  // Match the backend engine: live-spot ATM (midpoint ties round upward) and up to two equidistant OTM CE/PE pairs.
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
function OptionChain({ title, chain, spot }: { title: string; chain: Chain; spot: Row }) {
  const feed = chain.option_feed ?? {};
  const sampler = chain.sampler ?? {};
  const quotes = Array.isArray(feed.quotes) ? feed.quotes as Row[] : [];
  const metrics = Array.isArray(sampler.live_metrics) ? sampler.live_metrics as Row[] : [];
  const byToken = new Map(metrics.map(x => [String(x.token ?? ""), x]));
  const quoteTokens = new Set(quotes.map(q => String(q.token ?? "")).filter(Boolean));
  // Keep sampler-only rows too: a quote can disappear from the fresh feed while
  // its last valid IV is intentionally retained and must still render as LAST.
  const merged = [
    ...quotes.map(q => ({ ...q, ...(byToken.get(String(q.token ?? "")) ?? {}) })),
    ...metrics.filter(x => !quoteTokens.has(String(x.token ?? ""))),
  ];
  const resolvedStrikes = Array.isArray(feed.strikes) ? feed.strikes.map(n).filter((x): x is number => x !== null) : [];
  const allStrikes = (resolvedStrikes.length ? resolvedStrikes : [...new Set(merged.map(x => n(x.strike)).filter((x): x is number => x !== null))]).sort((a,b)=>a-b);
  const spotPrice = n(spot.price);
  const feedAtm = n(feed.atm_strike);
  const atmTarget = spotPrice ?? feedAtm;
  const atmIndex = allStrikes.length ? atmTarget === null ? Math.floor(allStrikes.length / 2) : allStrikes.reduce((best,x,i)=>Math.abs(x-atmTarget)<Math.abs(allStrikes[best]-atmTarget)||(Math.abs(x-atmTarget)===Math.abs(allStrikes[best]-atmTarget)&&x>allStrikes[best])?i:best,0) : -1;
  const strikes = atmIndex < 0 ? [] : allStrikes.slice(Math.max(0,atmIndex-5),atmIndex+6);
  const byKey = new Map(merged.map(x => [`${x.strike}:${x.option_type}`, x]));
  const spotFeed = feed.status ?? "WAITING";
  const cell=(v:unknown,d=2)=>n(v)===null?"—":fmt(v,d);
  const quoteTitle=(x:Row,field:string)=>n(x[field])===null?"No broker quote received":`Broker ${field.toUpperCase()} • ${x.fresh===true?"LIVE":`last received ${age(x.age_ms)} ago; may be stale`}`;
  return <section className="overflow-hidden rounded-2xl border theme-border theme-surface">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b theme-border p-3 sm:p-4">
      <div><h2 className="text-lg font-bold theme-text">{title} Option Chain</h2><p className="text-xs theme-muted mt-1">{String(feed.expiry_type ?? chain.expiry_type ?? "—")} • Expiry {String(feed.expiry ?? "—")} • ATM {fmt(strikes[5] ?? (atmIndex>=0?allStrikes[atmIndex]:null),0)} • −5 to +5 strikes ({strikes.length}/11)</p><p className="mt-1 text-[11px] theme-muted">Sampler: {String(sampler.status ?? "WAITING")} • Last sample: {sampleTime(sampler.sampled_at_ns)} • Persisted history: {String(sampler.history_persistence ?? "UNKNOWN")} ({fmt(sampler.history_persisted_count,0)} rows)</p>{Boolean(sampler.history_persistence_error) && <p className="mt-1 text-[11px] text-amber-600">History save error: {String(sampler.history_persistence_error)}</p>}{Boolean(sampler.reason) && <p className="mt-1 text-[11px] text-amber-600">Sampler reason: {String(sampler.reason)}</p>}</div>
      <div className="flex flex-wrap gap-2 text-xs"><span className="rounded-lg border theme-border px-2 py-1">Feed: {String(spotFeed)}</span><span className="rounded-lg border theme-border px-2 py-1">Contracts: {fmt(feed.received_contracts,0)}/{fmt(feed.expected_contracts,0)}</span><span className="rounded-lg border theme-border px-2 py-1">IV LIVE: {fmt(sampler.iv_live_count,0)} • OI LIVE: {fmt(sampler.oi_live_count,0)} • OI LAST: {fmt(sampler.oi_stale_count,0)}</span></div>
    </div>
    {chain.error && <div className="m-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-600">{chain.error}</div>}
    <div className="overflow-x-auto">
      <table className="w-full min-w-[900px] border-collapse border theme-border text-center text-xs">
        <thead><tr><th colSpan={4} className="border theme-border bg-emerald-500/10 py-2 text-emerald-700 text-sm">Calls (CE)</th><th rowSpan={2} className="border theme-border theme-surface-2 px-3 font-bold theme-text">Strike</th><th colSpan={4} className="border theme-border bg-rose-500/10 py-2 text-rose-700 text-sm">Puts (PE)</th></tr>
          <tr className="theme-surface-2 theme-muted">{["Volume","OI","IV %","LTP","LTP","IV %","OI","Volume"].map((x,i)=><th key={i} className="border theme-border px-2 py-2 font-semibold">{x}<span className="block text-[9px] font-normal opacity-70">{["Δ / %","Δ / %","Δ pt","Δ / %","Δ / %","Δ pt","Δ / %","Δ / %"][i]}</span></th>)}</tr></thead>
        <tbody>{strikes.length === 0 ? <tr><td colSpan={9} className="border theme-border p-10 theme-muted">Waiting for strike universe and broker quotes. No placeholder market values are displayed.</td></tr> : strikes.map(strike=>{
          const ce=byKey.get(`${strike}:CE`) ?? {}, pe=byKey.get(`${strike}:PE`) ?? {};
          const ivStatus=(x:Row)=>n(x.iv)===null?"IV not received":`${x.iv_live===true||x.iv_status==="LIVE"?"LIVE IV":"LAST valid IV"} • sample ${sampleTime(x.sampled_at_ns)} • option tick ${sampleTime(x.source_timestamp_ns)} • spot tick ${sampleTime(x.spot_timestamp_ns)}`;
          const ivLabel=(x:Row)=>n(x.iv)===null?"—":x.iv_live===true||x.iv_status==="LIVE"?"LIVE":"LAST";
          return <tr key={strike} className="border-b theme-border hover:theme-surface-2">
            <td className="border theme-border px-2 py-2"><StackedMetric value={ce.volume} change={ce.volume_change_1s} pct={ce.volume_change_pct_1s} compact/></td>
            <td className="border theme-border px-2 py-2" title={ce.oi===null||ce.oi===undefined?"OI not received":`OI ${String(ce.oi_status??(ce.oi_live===true?"LIVE":"LAST"))} • source ${sampleTime(ce.oi_timestamp_ns)} • age ${age(ce.oi_age_ms)}`}><StackedMetric value={ce.oi} change={ce.oi_change_1s} pct={ce.oi_change_pct_1s} compact/><span className="block text-[9px] theme-muted">{ce.oi===null||ce.oi===undefined?"—":String(ce.oi_status??(ce.oi_live===true?"LIVE":"LAST"))}</span></td>
            <td className="border theme-border px-2 py-2 font-semibold" title={ivStatus(ce)}><StackedMetric value={ce.iv} change={ce.iv_change_1s} suffix=" pt" digits={2}/><span className="block text-[9px] theme-muted">{ivLabel(ce)}</span></td>
            <td className="border theme-border px-2 py-2" title={quoteTitle(ce,"ltp")}><StackedMetric value={ce.ltp} change={ce.ltp_change_1s} pct={ce.ltp_change_pct_1s} digits={2}/></td>
            <td className={`border theme-border px-3 py-2 font-bold ${strike===allStrikes[atmIndex]?"bg-blue-500/15 text-blue-700":"theme-surface-2"}`}>{fmt(strike,0)}{strike===allStrikes[atmIndex]&&<span className="block text-[9px]">ATM</span>}</td>
            <td className="border theme-border px-2 py-2" title={quoteTitle(pe,"ltp")}><StackedMetric value={pe.ltp} change={pe.ltp_change_1s} pct={pe.ltp_change_pct_1s} digits={2}/></td>
            <td className="border theme-border px-2 py-2 font-semibold" title={ivStatus(pe)}><StackedMetric value={pe.iv} change={pe.iv_change_1s} suffix=" pt" digits={2}/><span className="block text-[9px] theme-muted">{ivLabel(pe)}</span></td>
            <td className="border theme-border px-2 py-2" title={pe.oi===null||pe.oi===undefined?"OI not received":`OI ${String(pe.oi_status??(pe.oi_live===true?"LIVE":"LAST"))} • source ${sampleTime(pe.oi_timestamp_ns)} • age ${age(pe.oi_age_ms)}`}><StackedMetric value={pe.oi} change={pe.oi_change_1s} pct={pe.oi_change_pct_1s} compact/><span className="block text-[9px] theme-muted">{pe.oi===null||pe.oi===undefined?"—":String(pe.oi_status??(pe.oi_live===true?"LIVE":"LAST"))}</span></td>
            <td className="border theme-border px-2 py-2"><StackedMetric value={pe.volume} change={pe.volume_change_1s} pct={pe.volume_change_pct_1s} compact/></td>
          </tr>;
        })}</tbody>
      </table>
    </div>
    <div className="border-t theme-border px-3 py-2 text-[11px] theme-muted">LTP is the actual broker last-traded price. Bid/Ask columns are intentionally hidden. IV retains the last received value when a fresh tick is unavailable and labels it LAST rather than hiding it; new fresh quotes update the IV and its percentage-point change. Volume/OI show the full Indian-formatted number alongside the Cr/Lakh equivalent; OI is explicitly marked LIVE only when its source timestamp is within five minutes, otherwise LAST. Green/red changes indicate positive/negative movement; stale or repeated ticks do not create artificial deltas.</div>
  </section>;
}

type OiVolumeRow = { strike: number; pe?: Row; ce?: Row };
function NiftyWeeklyOiVolumeChart({ chain, spot }: { chain: Chain; spot: Row }) {
  const feed = chain.option_feed ?? {};
  const sampler = chain.sampler ?? {};
  const metrics = Array.isArray(sampler.live_metrics) ? sampler.live_metrics as Row[] : [];
  const expiry = String(feed.expiry ?? "");
  const eligible = metrics.filter(item =>
    String(item.underlying ?? "").toUpperCase() === "NIFTY"
    && String(item.option_type ?? "").toUpperCase() in { CE: 1, PE: 1 }
    && (!expiry || String(item.expiry ?? "") === expiry)
    && n(item.strike) !== null
  );
  const listed = Array.isArray(feed.strikes) ? feed.strikes.map(n).filter((x): x is number => x !== null) : [];
  const allStrikes = (listed.length ? listed : [...new Set(eligible.map(x => n(x.strike)).filter((x): x is number => x !== null))]).sort((a,b)=>a-b);
  const spotPrice = n(spot.price);
  const atmTarget = spotPrice ?? n(feed.atm_strike);
  const atmIndex = allStrikes.length ? atmTarget === null ? -1 : allStrikes.reduce((best,x,i)=>Math.abs(x-atmTarget)<Math.abs(allStrikes[best]-atmTarget) || (Math.abs(x-atmTarget)===Math.abs(allStrikes[best]-atmTarget)&&x>allStrikes[best])?i:best,0) : -1;
  const strikes = atmIndex < 0 ? [] : allStrikes.slice(Math.max(0,atmIndex-5),atmIndex+6);
  const byKey = new Map(eligible.map(item=>[`${item.strike}:${String(item.option_type).toUpperCase()}`,item]));
  const rows: OiVolumeRow[] = strikes.map(strike=>({strike,pe:byKey.get(`${strike}:PE`),ce:byKey.get(`${strike}:CE`)}));
  const oiValues = eligible.map(item=>n(item.oi)).filter((v):v is number=>v!==null);
  const volumeValues = eligible.map(item=>n(item.volume)).filter((v):v is number=>v!==null);
  const maxOi = Math.max(1,...oiValues);
  const maxVolume = Math.max(1,...volumeValues);
  const value = (item:Row|undefined,key:string) => item ? n(item[key]) : null;
  const side = (kind:"PE"|"CE") => rows.map(row => {
    const item = kind==="PE" ? row.pe : row.ce;
    const oi = value(item,"oi");
    const volume = value(item,"volume");
    const oiStatus = oi===null ? "MISSING" : String(item?.oi_status ?? (item?.oi_live===true?"LIVE":"LAST"));
    const volumeFresh = n(item?.source_age_ms)!==null && n(item?.source_age_ms)! <= 5000;
    const volumeStatus = volume===null ? "MISSING" : volumeFresh ? "LIVE" : "LAST";
    const tone = kind==="PE" ? "bg-pink-500" : "bg-emerald-500";
    const volumeTone = kind==="PE" ? "bg-pink-300" : "bg-emerald-300";
    return <div key={row.strike} className="grid grid-cols-[42px_minmax(0,1fr)] items-center gap-2 border-b theme-border py-1.5">
      <div className="text-[10px] font-semibold tabular-nums leading-4"><span className={oiStatus==="LIVE"?"theme-text":oiStatus==="LAST"?"text-amber-600":"theme-muted"}>{oi===null?"—":compactIndian(oi)}</span><br/><span className={volumeStatus==="LIVE"?"theme-text":volumeStatus==="LAST"?"text-amber-600":"theme-muted"}>{volume===null?"—":compactIndian(volume)}</span></div>
      <div className="space-y-1">
        <div className="h-3 overflow-hidden rounded-sm theme-surface-2" title={`OI ${oiStatus} • ${item?.oi_timestamp_ns?sampleTime(item.oi_timestamp_ns):"timestamp unavailable"}`}><div className={`h-full rounded-sm ${tone}`} style={{width:oi===null?"0%":`${oi/maxOi*100}%`}}/></div>
        <div className="h-2.5 overflow-hidden rounded-sm theme-surface-2" title={`Volume ${volumeStatus} • option tick ${item?.source_timestamp_ns?sampleTime(item.source_timestamp_ns):"timestamp unavailable"}`}><div className={`h-full rounded-sm ${volumeTone}`} style={{width:volume===null?"0%":`${volume/maxVolume*100}%`}}/></div>
      </div>
    </div>;
  });
  const oiLive = eligible.filter(item=>item.oi!==null&&item.oi!==undefined&&item.oi_live===true).length;
  const oiLast = eligible.filter(item=>item.oi!==null&&item.oi!==undefined&&item.oi_status==="LAST").length;
  const volumeLive = eligible.filter(item=>n(item.volume)!==null&&n(item.source_age_ms)!==null&&Number(item.source_age_ms)<=5000).length;
  const oiTotal = (kind:"PE"|"CE") => eligible.filter(item=>String(item.option_type).toUpperCase()===kind).reduce((sum,item)=>sum+(n(item.oi)??0),0);
  const volumeTotal = (kind:"PE"|"CE") => eligible.filter(item=>String(item.option_type).toUpperCase()===kind).reduce((sum,item)=>sum+(n(item.volume)??0),0);
  return <section className="space-y-3 rounded-2xl border theme-border theme-surface p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div><h2 className="text-lg font-bold theme-text">NIFTY Weekly OI &amp; Volume Profile</h2><p className="mt-1 text-xs theme-muted">ATM-centred strikes • paired OI and Volume bars • actual sampler data</p></div>
      <span className="rounded-full border theme-border px-3 py-1 text-[10px] theme-muted">{String(sampler.status??"WAITING")} • 1s sampler</span>
    </div>
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <Metric label="PE Total OI" value={compactIndian(oiTotal("PE"))}/>
      <Metric label="CE Total OI" value={compactIndian(oiTotal("CE"))}/>
      <Metric label="PE Total Volume" value={compactIndian(volumeTotal("PE"))}/>
      <Metric label="CE Total Volume" value={compactIndian(volumeTotal("CE"))}/>
    </div>
    <div className="flex flex-wrap gap-2 text-[10px] theme-muted"><span className="rounded border theme-border px-2 py-1">OI LIVE {oiLive}</span><span className="rounded border theme-border px-2 py-1">OI LAST {oiLast}</span><span className="rounded border theme-border px-2 py-1">Volume fresh {volumeLive}/{eligible.length}</span><span className="rounded border theme-border px-2 py-1">Expiry {expiry||"WAITING"}</span></div>
    <div className="grid grid-cols-[minmax(0,1fr)_76px_minmax(0,1fr)] gap-2">
      <div className="min-w-0 rounded-xl border theme-border p-2">
        <div className="mb-2 flex items-center justify-between"><b className="text-xs text-rose-600">PE • PUT</b><span className="text-[9px] theme-muted">OI / Volume</span></div>
        {side("PE")}
      </div>
      <div className="rounded-xl border theme-border p-2">
        <div className="mb-2 text-center text-xs font-bold theme-text">Strike</div>
        {strikes.map(strike=><div key={strike} className={`flex min-h-[42px] items-center justify-center border-b theme-border text-[10px] font-semibold tabular-nums ${strike===allStrikes[atmIndex]?"rounded bg-amber-400/20 text-amber-700":"theme-text"}`}>{fmt(strike,0)}{strike===allStrikes[atmIndex]&&<span className="ml-1 text-[8px]">ATM</span>}</div>)}
      </div>
      <div className="min-w-0 rounded-xl border theme-border p-2">
        <div className="mb-2 flex items-center justify-between"><b className="text-xs text-emerald-600">CE • CALL</b><span className="text-[9px] theme-muted">OI / Volume</span></div>
        {side("CE")}
      </div>
    </div>
    <div className="flex flex-wrap gap-4 text-[10px] theme-muted"><span><span className="mr-1 inline-block h-2 w-4 rounded bg-pink-500"/> PE OI</span><span><span className="mr-1 inline-block h-2 w-4 rounded bg-pink-300"/> PE Volume</span><span><span className="mr-1 inline-block h-2 w-4 rounded bg-emerald-500"/> CE OI</span><span><span className="mr-1 inline-block h-2 w-4 rounded bg-emerald-300"/> CE Volume</span></div>
    <p className="text-[11px] theme-muted">{atmIndex<0?"Waiting for a fresh NIFTY spot and strike universe. No sample values are substituted.":`ATM ${fmt(allStrikes[atmIndex],0)} • ${strikes.length} strikes • OI freshness uses broker OI timestamp (5 min limit); Volume freshness uses option tick age (5 sec limit). Hover bars for source timestamps.`}</p>
  </section>;
}

type IvChartObservation = { token?: string; strike?: number; option_type?: string; timestamp_ns?: number; spot_timestamp_ns?: number; spot_price?: number | null; iv?: number | null };
function NiftyWeeklySpotIvChart({ history, spot, chain }: { history: Row | null; spot: Row; chain: Chain }) {
  const observations=Array.isArray(history?.observations)?history!.observations as IvChartObservation[]:[];
  const width=1100, height=340, left=62, right=64, top=18, bottom=34;
  const plotW=width-left-right, plotH=height-top-bottom;
  const allValid=observations.filter(x=>n(x.timestamp_ns)!==null&&n(x.timestamp_ns)!>0);
  const feed=chain.option_feed??{};
  const feedStrikes=Array.isArray(feed.strikes)?feed.strikes.map(n).filter((v):v is number=>v!==null):[];
  const persistedStrikes=[...new Set(allValid.map(item=>n(item.strike)).filter((v):v is number=>v!==null))];
  // The live option feed can be stopped or temporarily incomplete after market
  // close. Keep the chart useful from durable broker observations in that case,
  // and union both sources so missing live strikes do not hide saved history.
  const listed=[...new Set([...feedStrikes,...persistedStrikes])].sort((a,b)=>a-b);
  const spotPrice=n(spot.price)??n(feed.atm_strike);
  const atmIndex=listed.length?(spotPrice!==null?listed.reduce((best,value,index)=>Math.abs(value-spotPrice)<Math.abs(listed[best]-spotPrice)||(Math.abs(value-spotPrice)===Math.abs(listed[best]-spotPrice)&&value>listed[best])?index:best,0):Math.floor(listed.length/2)):-1;
  const selectedStrikes=atmIndex<0?[]:listed.slice(Math.max(0,atmIndex-5),atmIndex+6);
  const selectedSet=new Set(selectedStrikes);
  const feedExpiry=String(feed.expiry??"").trim();
  const historyExpiry=String(history?.expiry??"").trim();
  const expiry=feedExpiry||historyExpiry;
  const expiryMismatch=Boolean(feedExpiry&&historyExpiry&&feedExpiry!==historyExpiry);
  const valid=allValid.filter(item=>!expiryMismatch&&selectedSet.has(Number(item.strike)));
  const spotByTime=new Map<number,number>();
  for(const item of allValid)if(n(item.spot_timestamp_ns)!==null&&n(item.spot_price)!==null)spotByTime.set(Number(item.spot_timestamp_ns),Number(item.spot_price));
  const spotPoints=[...spotByTime.entries()].sort((a,b)=>a[0]-b[0]).map(([t,v])=>({t,v}));
  const groups=new Map<string,{strike:number;kind:string;points:{t:number;v:number}[]}>();
  for(const item of valid){
    if(n(item.strike)===null||n(item.iv)===null)continue;
    const kind=String(item.option_type??"").toUpperCase();
    if(kind!=="CE"&&kind!=="PE")continue;
    const key=`${Number(item.strike)}:${kind}`;
    if(!groups.has(key))groups.set(key,{strike:Number(item.strike),kind,points:[]});
    groups.get(key)!.points.push({t:Number(item.timestamp_ns),v:Number(item.iv)});
  }
  for(const series of groups.values())series.points.sort((a,b)=>a.t-b.t);
  const allTimes=[...valid.map(x=>Number(x.timestamp_ns)),...spotPoints.map(x=>x.t)];
  const minT=allTimes.length?Math.min(...allTimes):0, maxT=allTimes.length?Math.max(...allTimes):1;
  const spotValues=spotPoints.map(x=>x.v);
  const ivValues=[...groups.values()].flatMap(x=>x.points.map(p=>p.v));
  const bounds=(values:number[])=>{if(!values.length)return [0,1] as [number,number];const lo=Math.min(...values),hi=Math.max(...values);const pad=Math.max((hi-lo)*0.12,0.5);return [lo-pad,hi+pad] as [number,number]};
  const [spotMin,spotMax]=bounds(spotValues),[ivMin,ivMax]=bounds(ivValues);
  const x=(t:number)=>left+(t-minT)/Math.max(1,maxT-minT)*plotW;
  const ySpot=(v:number)=>top+(spotMax-v)/Math.max(0.0001,spotMax-spotMin)*plotH;
  const yIv=(v:number)=>top+(ivMax-v)/Math.max(0.0001,ivMax-ivMin)*plotH;
  const decimate=(points:{t:number;v:number}[])=>{const step=Math.max(1,Math.ceil(points.length/220));return points.filter((_,i)=>i%step===0||i===points.length-1)};
  const path=(points:{t:number;v:number}[],scale:(v:number)=>number)=>decimate(points).map(p=>`${x(p.t).toFixed(1)},${scale(p.v).toFixed(1)}`).join(" ");
  const strikes=selectedStrikes;
  const atm=atmIndex>=0?listed[atmIndex]:null;
  const ordered=[...groups.entries()].sort((a,b)=>a[1].strike-b[1].strike||a[1].kind.localeCompare(b[1].kind));
  const latest=valid.length?Math.max(...valid.map(x=>Number(x.timestamp_ns))):0;
  const live=latest>0&&Date.now()*1_000_000-latest<=5_000_000_000&&history?.data_status==="LIVE";
  const latestAgeSeconds=latest>0?Math.max(0,Date.now()*1_000_000-latest)/1_000_000_000:null;
  const palette=["#0f766e","#2563eb","#9333ea","#c2410c","#0891b2","#be123c","#4d7c0f","#7c3aed"];
  const colorFor=(strike:number,kind:string)=>{const idx=strikes.indexOf(strike);return palette[(idx*2+(kind==="PE"?1:0))%palette.length]};
  return <section className="space-y-3 rounded-2xl border theme-border theme-surface p-4">
    <div className="flex flex-wrap items-center justify-between gap-2"><div><h2 className="text-lg font-bold theme-text">NIFTY Weekly • Spot + IV Time Series</h2><p className="mt-1 text-xs theme-muted">ATM ±5 strikes • live strikes + durable-history fallback • dual Y-axis</p></div><span className={`rounded-full border px-3 py-1 text-[10px] ${live?"border-emerald-500/40 text-emerald-700":"theme-border theme-muted"}`}>{live?"LIVE • tick ≤ 5s":valid.length?"LAST • persisted broker data":"NO SAVED DATA"} • {valid.length} observations</span></div>
    <p className="text-[11px] theme-muted">Expiry {expiry||"—"} • Saved rows {allValid.length} • Displayed strikes {selectedStrikes.length}/11 • Latest observation {latest?sampleTime(latest):"—"} • Age {latestAgeSeconds===null?"—":latestAgeSeconds<60?`${latestAgeSeconds.toFixed(1)}s`:latestAgeSeconds<3600?`${(latestAgeSeconds/60).toFixed(1)}m`:`${(latestAgeSeconds/3600).toFixed(1)}h`} • {String(history?.data_status??"UNKNOWN")}{expiryMismatch?" • EXPIRY MISMATCH — chart suppressed":""}</p>
    {valid.length===0?<div className="rounded-xl border theme-border p-8 text-center text-sm theme-muted">{expiryMismatch? `Feed expiry ${feedExpiry} does not match saved-history expiry ${historyExpiry}; cross-expiry plotting is blocked.` : allValid.length? "Saved observations exist, but none match the selected strike window." : `No persisted observations are available for expiry ${expiry||"selected weekly"}. The chart will populate from real broker IV/spot ticks when this expiry is first sampled; no synthetic values are shown.`}</div>:<>
      <div className="overflow-x-auto"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="NIFTY spot price and all strike CE and PE implied volatility time series" className="w-full min-w-[760px]">
        {[0,0.25,0.5,0.75,1].map((fraction,i)=>{const yy=top+plotH*fraction;const sv=spotMax-(spotMax-spotMin)*fraction;const iv=ivMax-(ivMax-ivMin)*fraction;return <g key={i}><line x1={left} x2={width-right} y1={yy} y2={yy} stroke="currentColor" opacity=".13" strokeDasharray="3 4"/><text x={left-8} y={yy+4} textAnchor="end" fontSize="10" fill="currentColor">{sv.toFixed(1)}</text><text x={width-right+8} y={yy+4} textAnchor="start" fontSize="10" fill="currentColor">{iv.toFixed(1)}%</text></g>})}
        <line x1={left} x2={left} y1={top} y2={top+plotH} stroke="currentColor" opacity=".45"/><line x1={width-right} x2={width-right} y1={top} y2={top+plotH} stroke="currentColor" opacity=".45"/><line x1={left} x2={width-right} y1={top+plotH} y2={top+plotH} stroke="currentColor" opacity=".45"/>
        <polyline points={path(spotPoints,ySpot)} fill="none" stroke="#111827" strokeWidth="3" strokeLinejoin="round" strokeLinecap="round"/>
        {ordered.map(([key,series])=><polyline key={key} points={path(series.points,yIv)} fill="none" stroke={colorFor(series.strike,series.kind)} strokeWidth={series.kind==="CE"&&series.strike===atm?2.8:1.35} opacity={series.kind==="CE"&&series.strike===atm?1:0.72} strokeLinejoin="round" strokeLinecap="round"/>)}
        <text x={left} y={height-8} fontSize="10" fill="currentColor">{new Date(minT/1e6).toLocaleTimeString("en-IN",{timeZone:"Asia/Kolkata"})}</text><text x={width-right} y={height-8} textAnchor="end" fontSize="10" fill="currentColor">{new Date(maxT/1e6).toLocaleTimeString("en-IN",{timeZone:"Asia/Kolkata"})}</text>
      </svg></div>
      <div className="flex flex-wrap gap-x-4 gap-y-2 text-[10px] theme-muted"><span><i className="mr-1 inline-block h-[3px] w-5 align-middle bg-gray-900"/>Spot (left axis)</span><span><i className="mr-1 inline-block h-[3px] w-5 align-middle bg-blue-600"/>IV % (right axis)</span><span>ATM CE {atm===null?"—":fmt(atm,0)} • selected {selectedStrikes.length} strikes</span><span>Expiry {String(history?.expiry??"—")}</span></div>
      <div className="flex flex-wrap gap-2">{ordered.map(([key,series])=><span key={key} className="rounded border theme-border px-2 py-1 text-[10px]" style={{borderLeftWidth:3,borderLeftColor:colorFor(series.strike,series.kind)}}>{series.strike} {series.kind} • {series.points.length}</span>)}</div>
      <p className="text-[11px] theme-muted">Spot scale is on the left; IV percentage scale is on the right. Each strike/type has its own colour. Missing IV observations create gaps, not zero values. ATM CE is emphasized using the current spot-derived ATM strike. The chart refreshes from durable broker observations every 5 seconds. When ATM moves, the strike window recentres automatically to the current ATM ±5; newly selected strikes use their own historical observations from the rolling window.</p>
    </>}
  </section>;
}

export default function IVSkewFlipPage() {
  const [runtime,setRuntime]=useState<Runtime|null>(null);
  const [basketRegistry,setBasketRegistry]=useState<BasketRegistry|null>(null);
  const [durableHistory,setDurableHistory]=useState<Row|null>(null);
  const [chartHistory,setChartHistory]=useState<Row|null>(null);
  const selectedNifty="NIFTY_WEEKLY" as const;
  const [busy,setBusy]=useState(false);
  const [snapshotBusy,setSnapshotBusy]=useState(false);\n  const [snapshotNotice,setSnapshotNotice]=useState("");
  const [error,setError]=useState("");
  const [selectedEnabled,setSelectedEnabled]=useState(false);
  const [savedEnabled,setSavedEnabled]=useState(false);
  const [settingDirty,setSettingDirty]=useState(false);
  const base=appConfig.apiBaseUrl.replace(/\/$/,"");
  const requestedExpiry=String(runtime?.option_chains?.NIFTY_WEEKLY?.option_feed?.expiry??"");
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
  useEffect(()=>{
    let active=true;
    const poll=async()=>{
      try{
        const response=await fetch(`${base}/api/v1/scanner/iv-skew-flip/history?underlying=NIFTY${requestedExpiry?`&expiry=${encodeURIComponent(requestedExpiry)}`:""}`,{cache:"no-store"});
        const body=await response.json();
        if(active&&response.ok&&body.status==="ok")setDurableHistory(body as Row);
      }catch{/* keep the last successful durable history response */}
    };
    void poll();
    const timer=window.setInterval(()=>{if(active)void poll();},10000);
    return()=>{active=false;window.clearInterval(timer);};
  },[base,requestedExpiry]);
  const activeWeeklyExpiry=String(runtime?.option_chains?.NIFTY_WEEKLY?.option_feed?.expiry??"");
  useEffect(()=>{
    let active=true;
    const poll=async()=>{
      try{
        const expiry=activeWeeklyExpiry;
        const query=expiry?`&expiry=${encodeURIComponent(expiry)}`:"";
        const response=await fetch(`${base}/api/v1/scanner/iv-skew-flip/timeseries?underlying=NIFTY&window_seconds=600&limit=20000${query}`,{cache:"no-store"});
        const body=await response.json();
        if(active&&response.ok&&body.status==="ok")setChartHistory(body as Row);
      }catch{/* keep last successful chart window */}
    };
    void poll();
    const timer=window.setInterval(()=>{if(active)void poll();},5000);
    return()=>{active=false;window.clearInterval(timer);};
  },[base,requestedExpiry,activeWeeklyExpiry]);
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
  async function fetchBrokerSnapshot(){
    setSnapshotBusy(true);setSnapshotNotice("");setError("");
    try{
      const response=await fetch(`${base}/api/v1/scanner/iv-skew-flip/snapshot`,{method:"POST",cache:"no-store"});
      const body=await response.json();
      if(!response.ok)throw new Error(apiError(body,"Angel One broker snapshot failed"));
      setSnapshotNotice(`Angel One saved ${fmt(body.saved_contracts,0)}/${fmt(body.expected_contracts,0)} contracts • IV received ${fmt(body.iv_received_contracts,0)} • ${String(body.expiry??"")}`);
      const expiry=String(body.expiry??requestedExpiry);
      const history=await fetch(`${base}/api/v1/scanner/iv-skew-flip/history?underlying=NIFTY${expiry?`&expiry=${encodeURIComponent(expiry)}`:""}`,{cache:"no-store"});
      const saved=await history.json();
      if(history.ok&&saved.status==="ok")setDurableHistory(saved as Row);
    }catch(e){setError(e instanceof Error?e.message:"Angel One broker snapshot failed");}
    finally{setSnapshotBusy(false);}
  }
  const chains=runtime?.option_chains??{};
  const liveNifty=chains[selectedNifty]??{};
  const liveNiftySampler=liveNifty.sampler&&typeof liveNifty.sampler==="object"?liveNifty.sampler as Row:{};
  const liveNiftyMetrics=Array.isArray(liveNiftySampler.live_metrics)?liveNiftySampler.live_metrics as Row[]:[];
  const savedMetricsRaw=Array.isArray(durableHistory?.metrics)?durableHistory.metrics as Row[]:[];
  const savedExpiry=String(durableHistory?.expiry??"");
  const liveFeed=liveNifty.option_feed&&typeof liveNifty.option_feed==="object"?liveNifty.option_feed as Row:{};
  const historyMatchesLiveExpiry=!liveFeed.expiry||String(liveFeed.expiry)===savedExpiry;
  const savedMetrics=historyMatchesLiveExpiry?savedMetricsRaw:[];
  const metricsByToken=new Map<string,Row>();
  for(const item of savedMetrics)metricsByToken.set(String(item.token??""),item);
  for(const item of liveNiftyMetrics)metricsByToken.set(String(item.token??""),item);
  const mergedNiftyMetrics=[...metricsByToken.values()].filter(item=>String(item.token??""));
  const nifty={
    ...liveNifty,
    option_feed:{
      ...liveFeed,
      expiry:liveFeed.expiry??(liveNiftyMetrics.length?undefined:savedExpiry||undefined),
      expiry_type:liveFeed.expiry_type??(liveNiftyMetrics.length?undefined:durableHistory?.expiry_type),
      strikes:Array.isArray(liveFeed.strikes)&&liveFeed.strikes.length?liveFeed.strikes:[...new Set(savedMetrics.map(item=>n(item.strike)).filter((value):value is number=>value!==null))].sort((a,b)=>a-b),
    },
    sampler:{
      ...liveNiftySampler,
      live_metrics:mergedNiftyMetrics,
      status:liveNiftySampler.status??(savedMetrics.length?"LAST":"WAITING"),
      history_persistence:liveNiftySampler.history_persistence??(savedMetrics.length?"DATABASE":"UNKNOWN"),
      history_persisted_count:liveNiftySampler.history_persisted_count??savedMetrics.length,
      iv_live_count:liveNiftySampler.iv_live_count??0,
      oi_live_count:liveNiftySampler.oi_live_count??0,
      reason:liveNiftySampler.reason??(savedMetrics.length?"Showing latest durable broker observations; these are LAST values, not live ticks.":undefined),
    },
  } as Chain;
  const bank=chains.BANKNIFTY_MONTHLY??{};
  const spotFeed = runtime?.spot_feed && typeof runtime.spot_feed === "object" ? runtime.spot_feed as Row : {};
  const spot = spotFeed.spot && typeof spotFeed.spot === "object" ? spotFeed.spot as Row : {};
  const niftySpot=(spot.NIFTY&&typeof spot.NIFTY==="object"?spot.NIFTY:{}) as Row;
  const bankSpot=(spot.BANKNIFTY&&typeof spot.BANKNIFTY==="object"?spot.BANKNIFTY:{}) as Row;
  const spotTransport=(spotFeed.transport&&typeof spotFeed.transport==="object"?spotFeed.transport:{}) as Row;
  const spotValidation=(spotFeed.record_validation&&typeof spotFeed.record_validation==="object"?spotFeed.record_validation:{}) as Row;
  const niftyExchangeAge=spotTransport.last_exchange_tick_received_age_seconds_by_exchange_type;
  const niftyRawFrameAge=spotTransport.last_raw_frame_age_seconds_by_exchange_group;
  const running=runtime?.status==="RUNNING";
  const niftyExpiry=String((nifty.option_feed??{}).expiry_type??"");
  const bankExpiry=String((bank.option_feed??{}).expiry_type??"");
  return <main className="space-y-4 p-3 sm:p-5 theme-text">
    <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div><div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] theme-accent"><Activity size={15}/> Live Options Analytics</div><h1 className="mt-1 text-2xl font-bold sm:text-3xl">IV Skew / IV Flip + Gamma Blast</h1><p className="mt-1 text-sm theme-muted">NIFTY Weekly only • other chains temporarily disabled • Live OI and modeled IV</p></div>
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={()=>void refresh().catch(e=>setError(String(e)))} disabled={busy} className="inline-flex items-center gap-2 rounded-xl border theme-border theme-surface px-3 py-2 text-sm"><RefreshCw size={15}/> Refresh</button>
        <button onClick={()=>void fetchBrokerSnapshot()} disabled={!running||snapshotBusy} className="rounded-xl border theme-border theme-surface px-3 py-2 text-sm font-semibold disabled:opacity-50">{snapshotBusy?"Fetching broker…":"Fetch Broker IV + Chain"}</button>
        <div className="inline-flex rounded-xl border theme-border p-1" aria-label="IV Scanner desired runner state">
          <button type="button" onClick={()=>{setSelectedEnabled(true);setSettingDirty(true);}} disabled={busy} aria-pressed={selectedEnabled} className={`rounded-lg px-3 py-2 text-sm font-semibold ${selectedEnabled?"bg-emerald-600 text-white":"theme-muted"}`}>ON</button>
          <button type="button" onClick={()=>{setSelectedEnabled(false);setSettingDirty(true);}} disabled={busy} aria-pressed={!selectedEnabled} className={`rounded-lg px-3 py-2 text-sm font-semibold ${!selectedEnabled?"bg-rose-600 text-white":"theme-muted"}`}>OFF</button>
        </div>
        <button onClick={()=>void saveRunnerSetting()} disabled={busy||selectedEnabled===savedEnabled} className="rounded-xl border theme-border theme-surface px-4 py-2 text-sm font-semibold disabled:opacity-50">{busy?"Saving…":"Save"}</button>
      </div>
    </header>
    <div className="flex flex-wrap gap-2 text-xs"><span className="rounded-full border theme-border px-3 py-2">{running?<Wifi size={13} className="inline"/>:<WifiOff size={13} className="inline"/>} {`Runner ${runtime?.runner_status??runtime?.status??"NOT STARTED"} • Saved ${savedEnabled?"ON":"OFF"}`}</span><span className="rounded-full border theme-border px-3 py-2">Universe refresh {fmt(runtime?.refresh_seconds,0)}s</span><span className="rounded-full border theme-border px-3 py-2">Signal evaluation {fmt(runtime?.signal_evaluation_seconds,0)}s</span><span className="rounded-full border theme-border px-3 py-2">Orders OFF</span></div>
    {snapshotNotice&&<div role="status" className="rounded-xl border theme-border p-3 text-sm theme-muted">{snapshotNotice} • broker snapshot saved as LAST data</div>}
    {error&&<div role="alert" className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-600">{error}</div>}
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-xl font-bold theme-text">NIFTY Weekly Option Chain</h2><span className="rounded-lg border theme-border px-3 py-2 text-xs font-semibold">ACTIVE • ONLY ENABLED CHAIN</span></div>
      <div className="rounded-lg border theme-border px-3 py-2 text-xs theme-muted">Expiry selection date: {String(nifty.expiry_selection?.selection_date??"—")} • Selected: {String(nifty.expiry_selection?.selected_expiry??(liveFeed.expiry??"—"))} • Master candidates: {Array.isArray(nifty.expiry_selection?.available_expiries)?(nifty.expiry_selection!.available_expiries as string[]).join(", "):"Waiting for instrument-master diagnostics"}</div>
      <OptionChain title={`NIFTY ${niftyExpiry|| (selectedNifty==="NIFTY_WEEKLY"?"Weekly":"Monthly")}`} chain={nifty} spot={niftySpot}/>
      <NiftyWeeklyOiVolumeChart chain={nifty} spot={niftySpot}/>
      <NiftyWeeklySpotIvChart history={chartHistory} spot={niftySpot} chain={nifty}/>
      <SignalSummary title="NIFTY" chain={nifty} spot={niftySpot}/>
    </section>
    <section className="rounded-2xl border theme-border theme-surface p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold theme-text">NIFTY Spot Feed Diagnostics</h3><span className="rounded-full border theme-border px-3 py-1 text-xs">{String(spotFeed.status??"WAITING")}</span></div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Metric label="NIFTY Receive Age" value={age(niftySpot.age_ms)} sub="Local callback receive time"/>
        <Metric label="NIFTY Source Age" value={age(niftySpot.source_age_ms)} sub="Broker exchange timestamp"/>
        <Metric label="NSE Exchange Tick Age" value={JSON.stringify(niftyExchangeAge??{})} sub="Age by exchange type, seconds"/>
        <Metric label="NSE Raw Frame Age" value={JSON.stringify(niftyRawFrameAge??{})} sub="Age by exchange group, seconds"/>
        <Metric label="IV Callbacks" value={fmt(spotValidation.callbacks_received,0)}/>
        <Metric label="IV Accepted" value={fmt(spotValidation.accepted_records,0)}/>
        <Metric label="IV Rejected" value={fmt(spotValidation.rejected_records,0)} sub={Object.entries((spotValidation.rejected_by_reason&&typeof spotValidation.rejected_by_reason==="object"?spotValidation.rejected_by_reason:{}) as Row).map(([key,value])=>`${key}: ${String(value)}`).join(" • ") || "No rejections reported"}/>
        <Metric label="Normalized Delivered" value={fmt(spotTransport.normalized_records_delivered,0)} sub="Shared manager process-wide"/>
      </div>
      <p className="text-[11px] theme-muted">Raw frame / exchange tick counters are shared-manager diagnostics, not NIFTY-only proof. IV Accepted confirms this consumer received a usable NIFTY spot record. Weekly and Monthly use the same NIFTY spot gate.</p>
    </section>
    <section className="rounded-2xl border theme-border p-3 text-sm theme-muted">NIFTY Monthly और BANKNIFTY chains फिलहाल बंद हैं। केवल NIFTY Weekly feed, sampler और signals सक्रिय हैं।</section>
    <section className="rounded-2xl border theme-border theme-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><h2 className="font-semibold theme-text">Live Index Spot–Future Basis</h2><p className="mt-1 text-xs theme-muted">Future − Spot • nearest and next expiry • paired fresh broker timestamps required</p></div>
        <span className="rounded-full border theme-border px-3 py-1 text-xs">Future feed: {String(runtime?.index_future_feed?.status ?? "WAITING")}</span>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        {(["NIFTY","BANKNIFTY"] as const).map((symbol)=>{
          const analysis=runtime?.spot_future_basis?.[symbol]??{};
          const contracts=Array.isArray(analysis.expiry_contracts)?analysis.expiry_contracts as Row[]:[];
          return <div key={symbol} className="rounded-xl border theme-border p-3">
            <div className="flex items-center justify-between gap-2"><b className="text-sm theme-text">{symbol}</b><span className="rounded-full border theme-border px-2 py-1 text-xs">{String(analysis.status??"WAITING")}</span></div>
            {contracts.length===0?<p className="mt-3 text-xs theme-muted">{String(analysis.error??"Waiting for future contracts and synchronized spot/future ticks.")}</p>:<div className="mt-3 space-y-2">
              {contracts.map((item,index)=>{
                const history=item.history&&typeof item.history==="object"?item.history as Row:{};
                const windows=history.windows&&typeof history.windows==="object"?history.windows as Row:{};
                const one=windows["1m"]&&typeof windows["1m"]==="object"?windows["1m"] as Row:{};
                const five=windows["5m"]&&typeof windows["5m"]==="object"?windows["5m"] as Row:{};
                const fifteen=windows["15m"]&&typeof windows["15m"]==="object"?windows["15m"] as Row:{};
                return <div key={String(item.expiry)+"-"+index} className="rounded-lg border theme-border p-2">
                  <div className="flex flex-wrap items-center justify-between gap-2"><b className="text-xs">{index===0?"NEAR EXPIRY":"NEXT EXPIRY"} • {String(item.expiry??"—")}</b><span className="text-[11px] theme-muted">{String(item.status??"WAITING")}</span></div>
                  <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                    <Metric label="Spot" value={fmt(item.spot)} />
                    <Metric label="Future" value={fmt(item.future)} />
                    <Metric label="Basis" value={delta(item.basis_points," pt",2)} sub={n(item.basis_pct)===null?"—":delta(item.basis_pct,"% ",4)+" of spot"} />
                    <Metric label="Trend" value={String(history.trend??"WARMING")} sub="Basis direction, not market direction" />
                  </div>
                  <div className="mt-2 grid grid-cols-3 gap-2 text-[11px] theme-muted"><div>1m Δ <b className="theme-text">{delta(one.basis_change_points," pt",2)}</b></div><div>5m Δ <b className="theme-text">{delta(five.basis_change_points," pt",2)}</b></div><div>15m Δ <b className="theme-text">{delta(fifteen.basis_change_points," pt",2)}</b></div></div>
                  <p className="mt-1 text-[10px] theme-muted">Spot age {age(item.spot_age_ms)} • Future age {age(item.future_age_ms)} • Pair skew {age(item.pair_skew_ms)} • {item.fresh===true?"Fresh paired quotes":"Not eligible for live context"}</p>
                </div>;
              })}
            </div>}
          </div>;
        })}
      </div>
      <p className="mt-3 text-[11px] theme-muted">Basis is informational context only. It does not independently generate or authorize a signal or order. If either quote is stale or timestamp skew exceeds the limit, the pair is withheld.</p>
    </section>
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
        <div><h2 className="font-semibold theme-text">NIFTY + BANKNIFTY Top 10 Heavyweight Pressure</h2><p className="mt-1 text-xs theme-muted">Dono indices ke independently ranked highest-weight 10 constituents • 5s / 10s / 30s weighted returns • no order execution</p></div>
        <span className="rounded-full border theme-border px-3 py-1 text-xs font-semibold">{String(runtime?.equity_pressure?.status ?? "DISABLED")}</span>
      </div>
      {Object.keys(runtime?.equity_pressure_by_underlying ?? {}).length > 0 ? (
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          {Object.entries(runtime?.equity_pressure_by_underlying ?? {}).map(([symbol, pressure]) => (
            <div key={symbol} className="rounded-xl border theme-border p-3">
              <div className="flex items-center justify-between gap-2"><b className="text-sm theme-text">{symbol}</b><span className="rounded-full border theme-border px-2 py-1 text-xs">{String(pressure.status ?? "WAITING")}</span></div>
              <div className="mt-3 grid grid-cols-2 gap-2">
                <Metric label="Feed" value={String((pressure.feed as Row | undefined)?.status ?? "WAITING")} />
                <Metric label="Fresh Components" value={`${fmt(pressure.fresh_components,0)} / ${fmt(pressure.expected_components,0)}`} />
                <Metric label="Weight Coverage" value={`${fmt(pressure.weight_coverage_pct)}%`} />
                <Metric label="Direction" value={String(pressure.direction ?? "WAITING")} sub={pressure.signals_allowed === true ? "Timeframes agree" : "Directional output suppressed"} />
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-3">
                {["5s","10s","30s"].map((windowKey)=>{const w=(pressure.windows as Row | undefined)?.[windowKey] as Row | undefined;return <div key={windowKey} className="rounded-lg border theme-border p-2"><div className="flex justify-between gap-2 text-xs"><b>{windowKey}</b><b>{String(w?.direction ?? "WAITING")}</b></div><div className="mt-2 text-[11px] theme-muted">Return <b className="theme-text">{delta(w?.weighted_return_pct,"% ",4)}</b></div><div className="mt-1 text-[11px] theme-muted">Coverage {fmt(w?.coverage_pct)}% • Advancing {fmt(w?.advancing_weight_pct)}%</div><div className="mt-2 text-[11px] theme-muted">Top: {Array.isArray(w?.top_contributors)?(w!.top_contributors as Row[]).slice(0,3).map(x=>String(x.symbol??"")).join(", ")||"—":"—"}</div></div>;})}
              </div>
            </div>
          ))}
        </div>
      ) : runtime?.equity_pressure?.enabled !== true ? <p className="mt-3 text-sm theme-muted">{String(runtime?.equity_pressure?.reason ?? "NIFTY aur BANKNIFTY ke complete verified official weight baskets zaroori hain. Har index ke top 10 weights dynamically select honge; missing/stale basket par pressure safely unavailable rahega.")} No orders.</p> : <>
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

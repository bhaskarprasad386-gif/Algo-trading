"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Play, RefreshCw, Square, Wifi, WifiOff } from "lucide-react";
import { appConfig } from "@/lib/config";

type Row = Record<string, unknown>;
type Runtime = {
  status?: string;
  orders_enabled?: boolean;
  refresh_seconds?: number;
  signal_evaluation_seconds?: number;
  spot_feed?: Row;
  indices?: Record<string, { option_feed?: Row; sampler?: Row; signals?: Row; universe_tokens?: number; error?: string | null }>;
  signals?: Record<string, Row>;
  last_refresh_ns?: number | null;
};

const n = (v: unknown) => typeof v === "number" && Number.isFinite(v) ? v : null;
const fmt = (v: unknown, digits = 2) => n(v) === null ? "—" : (v as number).toLocaleString("en-IN", { maximumFractionDigits: digits });
const age = (v: unknown) => n(v) === null ? "—" : n(v)! < 1000 ? `${Math.round(n(v)!)} ms` : `${(n(v)! / 1000).toFixed(1)} s`;
const stamp = (v: unknown) => n(v) === null ? "—" : new Date((v as number) / 1_000_000).toLocaleTimeString("en-IN", { hour12: false });

function Metric({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return <div className="rounded-xl border theme-border theme-surface p-3"><div className="text-[11px] uppercase tracking-wide theme-muted">{label}</div><div className="mt-1 text-lg font-bold theme-text">{value}</div>{sub && <div className="mt-1 text-[11px] theme-muted">{sub}</div>}</div>;
}

function LiveTable({ symbol, feed, sampler }: { symbol: string; feed: Row; sampler: Row }) {
  const quotes = Array.isArray(feed.quotes) ? feed.quotes as Row[] : [];
  const metrics = Array.isArray(sampler.live_metrics) ? sampler.live_metrics as Row[] : [];
  const byToken = new Map(metrics.map(item => [String(item.token ?? ""), item]));
  const rows = quotes.map(q => ({ ...q, ...(byToken.get(String(q.token ?? "")) ?? {}) }));
  return <section className="overflow-hidden rounded-2xl border theme-border theme-surface">
    <div className="flex flex-wrap items-center justify-between gap-2 border-b theme-border p-4">
      <div><h2 className="font-semibold theme-text">{symbol} Live Option Chain</h2><p className="mt-1 text-xs theme-muted">ATM ±3 strikes • {String(feed.expiry_type ?? "—")} • Expiry {String(feed.expiry ?? "—")}</p></div>
      <div className="flex gap-2 text-xs"><span className="rounded-lg border theme-border px-2 py-1 theme-muted">Contracts {fmt(feed.received_contracts, 0)} / {fmt(feed.expected_contracts, 0)}</span><span className="rounded-lg border theme-border px-2 py-1 theme-muted">IV {fmt(sampler.iv_live_count, 0)} • OI {fmt(sampler.oi_live_count, 0)}</span></div>
    </div>
    <div className="overflow-x-auto"><table className="min-w-[1120px] w-full text-left text-xs">
      <thead className="theme-surface-2 theme-muted"><tr>{["Strike","Type","LTP","Bid","Bid Qty","Ask","Ask Qty","IV %","OI","Volume","Tick Age","Freshness","IV Input"].map(x=><th key={x} className="px-3 py-3 font-semibold">{x}</th>)}</tr></thead>
      <tbody>{rows.length === 0 ? <tr><td colSpan={13} className="p-8 text-center theme-muted">Waiting for fresh Angel One option ticks…</td></tr> : rows.map((row,i)=><tr key={String(row.token ?? i)} className={`border-t theme-border ${row.fresh === false ? "opacity-50" : ""}`}>
        <td className="px-3 py-2 font-semibold theme-text">{fmt(row.strike, 2)}</td><td className="px-3 py-2">{String(row.option_type ?? "—")}</td>
        <td className="px-3 py-2">{fmt(row.ltp)}</td><td className="px-3 py-2 text-emerald-500">{fmt(row.bid)}</td><td className="px-3 py-2">{fmt(row.bid_qty,0)}</td><td className="px-3 py-2 text-rose-500">{fmt(row.ask)}</td><td className="px-3 py-2">{fmt(row.ask_qty,0)}</td>
        <td className="px-3 py-2 font-semibold theme-text">{row.iv_live ? fmt(row.iv,4) : "—"}</td><td className="px-3 py-2">{row.oi_live ? fmt(row.oi,0) : "—"}</td><td className="px-3 py-2">{row.fresh ? fmt(row.volume,0) : "—"}</td><td className="px-3 py-2">{age(row.source_age_ms ?? row.age_ms)}</td><td className="px-3 py-2"><span className={row.fresh ? "text-emerald-500" : "text-rose-500"}>{row.fresh ? "LIVE" : "STALE"}</span></td><td className="px-3 py-2">{row.iv_live ? String(row.iv_input ?? "—") : "—"}</td>
      </tr>)}</tbody>
    </table></div>
    <div className="border-t theme-border px-4 py-2 text-[11px] theme-muted">IV is calculated from live bid/ask midpoint (LTP fallback), not exchange-published IV. OI is the broker tick's open interest.</div>
  </section>;
}

export default function IVSkewFlipPage() {
  const [runtime, setRuntime] = useState<Runtime | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const base = appConfig.apiBaseUrl.replace(/\/$/, "");

  const refresh = useCallback(async () => {
    const response = await fetch(`${base}/api/v1/scanner/iv-skew-flip/status`, { cache: "no-store" });
    const body = await response.json();
    if (!response.ok) throw new Error(body?.detail?.message ?? body?.detail ?? `Status HTTP ${response.status}`);
    setRuntime(body.runtime ?? null);
  }, [base]);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const response = await fetch(`${base}/api/v1/scanner/iv-skew-flip/status`, { cache: "no-store" });
        const body = await response.json();
        if (active && response.ok) setRuntime(body.runtime ?? null);
      } catch { /* preserve last known live state */ }
    };
    void poll();
    const timer = window.setInterval(() => { if (active) void poll(); }, 1000);
    return () => { active = false; window.clearInterval(timer); };
  }, [base]);

  async function control(action: "start" | "stop") {
    setBusy(true); setError("");
    try {
      const response = await fetch(`${base}/api/v1/scanner/iv-skew-flip/${action}`, { method: "POST", cache: "no-store" });
      const body = await response.json();
      if (!response.ok) throw new Error(body?.detail?.message ?? body?.detail ?? `Could not ${action} scanner`);
      setRuntime(body);
    } catch (e) { setError(e instanceof Error ? e.message : "Scanner control failed"); }
    finally { setBusy(false); }
  }

  const indices = runtime?.indices ?? {};
  const spot = runtime?.spot_feed && typeof runtime.spot_feed === "object" ? runtime.spot_feed as Row : {};
  const spotRows = spot.spot && typeof spot.spot === "object" ? spot.spot as Row : {};
  const isRunning = runtime?.status === "RUNNING";
  const signalRows = useMemo(() => Object.entries(runtime?.signals ?? {}), [runtime?.signals]);

  return <main className="space-y-5 p-4 theme-text">
    <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div><div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] theme-accent"><Activity size={15}/> Live Options Analytics</div><h1 className="mt-2 text-2xl font-bold sm:text-3xl">IV Skew / IV Flip + Gamma Blast</h1><p className="mt-2 max-w-3xl text-sm theme-muted">NIFTY Weekly and BANKNIFTY Monthly • Live spot, option quotes, modeled IV and open interest from the shared Angel One feed.</p></div>
      <div className="flex gap-2">
        <button onClick={()=>void refresh().catch(e=>setError(String(e)))} disabled={busy} className="inline-flex items-center gap-2 rounded-xl border theme-border theme-surface px-3 py-2 text-sm"><RefreshCw size={15}/> Refresh</button>
        {isRunning ? <button onClick={()=>void control("stop")} disabled={busy} className="inline-flex items-center gap-2 rounded-xl border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-500 disabled:opacity-50"><Square size={15}/> Stop</button> : <button onClick={()=>void control("start")} disabled={busy} className="inline-flex items-center gap-2 rounded-xl border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-500 disabled:opacity-50"><Play size={15}/> Start Live Feed</button>}
      </div>
    </header>
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-2 ${isRunning ? "border-emerald-500/40 text-emerald-500" : "theme-border theme-muted"}`}>{isRunning ? <Wifi size={14}/> : <WifiOff size={14}/>} {runtime?.status ?? "NOT STARTED"}</span>
      <span className="rounded-full border theme-border px-3 py-2 theme-muted">Refresh {fmt(runtime?.refresh_seconds,0)}s</span><span className="rounded-full border theme-border px-3 py-2 theme-muted">Signal evaluation {fmt(runtime?.signal_evaluation_seconds,0)}s</span><span className="rounded-full border theme-border px-3 py-2 theme-muted">Orders OFF</span>
    </div>
    {error && <div role="alert" className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-500">{error}</div>}
    <section className="grid gap-3 sm:grid-cols-2">
      {["NIFTY","BANKNIFTY"].map(symbol=>{const row=spotRows[symbol] as Row | undefined;return <Metric key={symbol} label={`${symbol} Spot`} value={row ? fmt(row.price) : "Waiting for spot tick"} sub={row ? `Tick age ${age(row.age_ms)} • ${String(row.fresh ? "FRESH" : "STALE")}` : "Independent shared-feed spot subscription"}/>;})}
    </section>
    {["NIFTY","BANKNIFTY"].map(symbol=>{const item=indices[symbol] ?? {};const feed=item.option_feed ?? {};const sampler=item.sampler ?? {};return <div key={symbol} className="space-y-3"><div className="grid grid-cols-2 gap-3 sm:grid-cols-4"><Metric label={`${symbol} Feed`} value={String(feed.status ?? "WAITING")} sub={`Fresh ${fmt(feed.fresh_contracts,0)} / ${fmt(feed.expected_contracts,0)}`}/><Metric label="Live IV" value={fmt(sampler.iv_live_count,0)} sub="Contracts with current modeled IV"/><Metric label="Live OI" value={fmt(sampler.oi_live_count,0)} sub="Contracts with current OI tick"/><Metric label="Last tick" value={stamp(feed.last_tick_timestamp_ns)} sub={`Universe ${fmt(item.universe_tokens,0)} tokens`}/></div><LiveTable symbol={symbol} feed={feed} sampler={sampler}/></div>;})}
    <section className="rounded-2xl border theme-border theme-surface p-4"><h2 className="font-semibold theme-text">Live Signal Monitor</h2><p className="mt-1 text-xs theme-muted">IV Skew, ATM IV Flip, Gamma Blast and same-direction confluence. WAITING signals are not actionable.</p><div className="mt-3 grid gap-3 sm:grid-cols-2">{signalRows.length === 0 ? <p className="text-sm theme-muted">Signals will appear after the runtime has fresh spot and option data.</p> : signalRows.map(([symbol,payload])=>{const strategies=Array.isArray(payload.strategies)?payload.strategies as Row[]:[];const conf=payload.confluence as Row|undefined;return <div key={symbol} className="rounded-xl border theme-border p-3"><div className="mb-2 flex justify-between"><b>{symbol}</b><span className="text-xs theme-muted">{stamp(payload.evaluated_at_ns)}</span></div>{[...strategies, ...(conf?[{...conf,name:"CONFLUENCE"}]:[])].map((s,i)=><div key={i} className="flex justify-between gap-3 border-t theme-border py-2 text-xs"><span>{String(s.name ?? s.strategy ?? (i===3?"CONFLUENCE":"SIGNAL"))}</span><b className={String(s.status)==="SIGNAL"?"text-emerald-500": "theme-muted"}>{String(s.direction ?? "—")} • {String(s.status ?? "WAITING")}</b></div>)}</div>;})}</div></section>
    <p className="text-xs theme-muted">The page does not start the runtime automatically. Press Start Live Feed to subscribe. No broker orders are placed. A live connection is confirmed only after fresh broker ticks appear.</p>
  </main>;
}

"use client";

import { useEffect, useState } from "react";
import { appConfig } from "@/lib/config";
import { Activity, AlertCircle, CheckCircle2, LoaderCircle, Search } from "lucide-react";

type Match = {
  symbol: string;
  strength: string;
  indicators_confirmed: string[];
  lows: { week: string; low: number; close: number }[];
  intervening_highs: { week: string; high: number }[];
  third_low_lower_band: number;
  indicator_values_at_third_low: Record<string, number | null>;
  checked_at_week: string;
  price?: number; rsi?: number | null; rsi_rising?: boolean; obv_rising?: boolean;
  price_fall_pct?: number; price_phase?: string; sideways_range_pct?: number;
  rsi_before_sideways?: boolean; sma20_near?: boolean | null; sma20?: number | null; special_timing?: string; timing?: string; timeframe?: string; signal?: string;
};
type Result = { status: string; symbol: string; weekly_bars?: number; count?: number; matches?: Match[]; message?: string; data_source?: string; orders_placed?: boolean };
type UniverseStatus = { status: string; total?: number; scanned?: number; failed?: number; matches?: Match[]; failures?: {symbol:string;reason:string}[]; fatal_error?: string; current_symbol?: string | null; batch_number?: number; batches_total?: number; waiting_for_next_batch_seconds?: number; started_at?: number; finished_at?: number };

const money = (v: number) => v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
export default function WeeklySwingScannerPage() {
  const [symbol, setSymbol] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [universeStatus, setUniverseStatus] = useState<UniverseStatus | null>(null);

  // Restore a scan already running on the backend after a page reload, then
  // keep its status current without requiring the user to press Resume.
  useEffect(() => {
    let active = true;
    let shouldPoll = true;
    const refresh = async () => {
      try {
        const base = appConfig.apiBaseUrl.replace(/\/$/, "");
        const response = await fetch(`${base}/api/v1/scanner/weekly-swing/scan-all/status`, { cache: "no-store" });
        if (!response.ok) return;
        const body: UniverseStatus = await response.json();
        if (!active) return;
        shouldPoll = body.status === "running";
        setUniverseStatus(body);
        if (body.status === "completed") {
          setResult({ status: "success", symbol: "NIFTY 200", count: body.matches?.length ?? 0, matches: body.matches ?? [], data_source: "Official NIFTY 200 constituents + Angel One historical candles", orders_placed: false });
        } else if (body.status === "failed" || body.status === "paused") {
          setError(`${body.status === "paused" ? "Scan paused" : "NIFTY 200 scan failed"}: ${body.fatal_error || "No further scan details provided"}`);
        }
      } catch {
        // The normal scan/status controls surface actionable errors when used.
      }
    };
    void refresh();
    // Poll from server-returned status, not stale React state; never starts a scan.
    const timer = window.setInterval(() => { if (shouldPoll) void refresh(); }, 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  async function scan() {
    setLoading(true); setError(""); setResult(null); setUniverseStatus(null);
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(`${base}/api/v1/scanner/weekly-swing/scan?symbol=${encodeURIComponent(symbol.trim().toUpperCase())}&exchange=NSE`, { cache: "no-store" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || `Scanner request failed (${response.status})`);
      setResult(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Scanner data unavailable");
    } finally { setLoading(false); }
  }

  async function refreshNifty200Status() {
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(`${base}/api/v1/scanner/weekly-swing/scan-all/status`, { cache: "no-store" });
      const body: UniverseStatus = await response.json();
      if (!response.ok) throw new Error("Could not read NIFTY 200 scan status");
      setUniverseStatus(body);
      setError("");
      if (body.status === "completed") {
        setResult({ status: "success", symbol: "NIFTY 200", count: body.matches?.length ?? 0, matches: body.matches ?? [], data_source: "Official NIFTY 200 constituents + Angel One historical candles", orders_placed: false });
      } else if (body.status === "failed" || body.status === "paused") {
        setError(`${body.status === "paused" ? "Scan paused" : "NIFTY 200 scan failed"}: ${body.fatal_error || "No further scan details provided"}`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "NIFTY 200 status unavailable");
    }
  }

  async function resumeNifty200Updates() {
    setLoading(true); setError("");
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      let body: UniverseStatus;
      do {
        const response = await fetch(`${base}/api/v1/scanner/weekly-swing/scan-all/status`, { cache: "no-store" });
        body = await response.json();
        if (!response.ok) throw new Error("Could not read NIFTY 200 scan status");
        setUniverseStatus(body);
        if (body.status === "completed") {
          setResult({ status: "success", symbol: "NIFTY 200", count: body.matches?.length ?? 0, matches: body.matches ?? [], data_source: "Official NIFTY 200 constituents + Angel One historical candles", orders_placed: false });
          return;
        }
        if (body.status === "failed" || body.status === "paused") throw new Error(`${body.status === "paused" ? "Scan paused" : "NIFTY 200 scan failed"}: ${body.fatal_error || "No further scan details provided"}`);
        if (body.status === "idle") throw new Error("No active NIFTY 200 scan found. Start a scan to begin.");
        await new Promise(resolve => window.setTimeout(resolve, 3000));
      } while (body.status === "running");
    } catch (e) {
      setError(e instanceof Error ? e.message : "NIFTY 200 status unavailable");
    } finally {
      setLoading(false);
    }
  }

  async function scanNifty200() {
    setLoading(true); setError(""); setResult(null); setUniverseStatus(null);
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const startResponse = await fetch(`${base}/api/v1/scanner/weekly-swing/scan-all/start`, { method: "POST", cache: "no-store" });
      const started = await startResponse.json();
      if (!startResponse.ok) throw new Error(started.detail || `NIFTY 200 scan failed (${startResponse.status})`);
      const poll = async (): Promise<void> => {
        const response = await fetch(`${base}/api/v1/scanner/weekly-swing/scan-all/status`, { cache: "no-store" });
        const body: UniverseStatus = await response.json();
        if (!response.ok) throw new Error("Could not read NIFTY 200 scan status");
        setUniverseStatus(body);
        if (body.status === "completed") {
          setResult({ status: "success", symbol: "NIFTY 200", count: body.matches?.length ?? 0, matches: body.matches ?? [], data_source: "Official NIFTY 200 constituents + Angel One historical candles", orders_placed: false });
          setLoading(false);
          return;
        }
        if (body.status === "failed" || body.status === "paused") throw new Error(`${body.status === "paused" ? "Scan paused" : "NIFTY 200 scan failed"}: ${body.fatal_error || "No further scan details provided"}`);
        if (body.status === "idle") throw new Error("NIFTY 200 scan is idle; start it again to continue.");
        await new Promise(resolve => window.setTimeout(resolve, 3000));
        return poll();
      };
      await poll();
    } catch (e) {
      setError(e instanceof Error ? e.message : "NIFTY 200 scan unavailable");
      setLoading(false);
    }
  }

  return <div className="space-y-5">
    <header className="space-y-2">
      <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] theme-accent"><Activity size={15}/> Weekly RSI Scanner</div>
      <h1 className="text-2xl font-bold sm:text-3xl">Fall → Fall Stop → Tight Sideways → RSI Rising</h1>
      <p className="max-w-3xl text-sm theme-muted">Weekly candles only. Finds a decline that stops, tight sideways price action, and rising RSI; OBV confirms strength. Price-fall and sideways percentages are informational, not filters. No orders are placed.</p>
    </header>
    <section className="rounded-2xl border theme-border theme-surface p-4 sm:p-5">
      <div className="grid gap-3 sm:grid-cols-[1fr_140px_auto] sm:items-end">
        <label className="space-y-1 text-xs font-semibold theme-muted">Stock symbol
          <input value={symbol} onChange={e=>setSymbol(e.target.value.toUpperCase())} placeholder="Optional single-stock test" className="w-full rounded-xl border theme-border theme-surface-2 px-3 py-3 text-sm theme-text outline-none focus:ring-2 focus:ring-cyan-500"/>
        </label>
        <div className="space-y-1 text-xs font-semibold theme-muted">
          <span>Exchange</span>
          <div className="flex min-h-11 items-center rounded-xl border theme-border theme-surface-2 px-3 text-sm theme-text">NSE only</div>
        </div>
        <div className="flex flex-wrap gap-2"><button onClick={scan} disabled={loading || !symbol.trim()} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-cyan-500 px-5 py-3 text-sm font-bold text-slate-950 disabled:opacity-60">{loading?<LoaderCircle size={16} className="animate-spin"/>:<Search size={16}/>} {loading?"Scanning…":"Scan One Stock"}</button><button onClick={scanNifty200} disabled={loading} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border theme-border px-4 py-3 text-sm font-bold disabled:opacity-60">{loading?<LoaderCircle size={16} className="animate-spin"/>:<Activity size={16}/>} Scan All NIFTY 200</button></div>
      </div>
      <div className="mt-4 grid gap-2 text-xs theme-muted sm:grid-cols-2">
        <p>• 5 years of provider-sourced daily history aggregated into weekly candles.</p>
        <p>• Adaptive swing turns; no fixed gap between the lows.</p>
      </div>
    </section>
    {universeStatus && <section className="rounded-xl border theme-border theme-surface p-4 text-sm"><div className="flex items-center justify-between gap-2"><strong>NIFTY 200 scan progress</strong><div className="flex items-center gap-3"><button type="button" onClick={refreshNifty200Status} className="rounded-lg border theme-border px-3 py-1 text-xs font-semibold">Refresh status</button>{universeStatus.status === "running" && <button type="button" onClick={resumeNifty200Updates} disabled={loading} className="rounded-lg border theme-border px-3 py-1 text-xs font-semibold disabled:opacity-60">{loading ? "Updating…" : "Resume live updates"}</button>}<span className="theme-muted">{universeStatus.scanned ?? 0} / {universeStatus.total ?? 200}</span></div></div><div className="mt-3 h-2 overflow-hidden rounded-full theme-surface-2"><div className="h-full rounded-full bg-cyan-500 transition-all" style={{width: `${Math.min(100, 100*(universeStatus.scanned ?? 0)/Math.max(1, universeStatus.total ?? 200))}%`}} /></div><p className="mt-2 theme-muted">Failed/skipped: {universeStatus.failed ?? 0} · Matches so far: {universeStatus.matches?.length ?? 0} · Status: {universeStatus.status}</p><div className="mt-2 grid gap-1 text-xs theme-muted sm:grid-cols-2"><p>Batch: {universeStatus.batch_number ?? 0} / {universeStatus.batches_total ?? Math.ceil((universeStatus.total ?? 200) / 50)}</p><p>Current stock: <span className="font-semibold theme-text">{universeStatus.current_symbol || "—"}</span></p>{(universeStatus.waiting_for_next_batch_seconds ?? 0) > 0 && <p>Next batch in about {Math.ceil((universeStatus.waiting_for_next_batch_seconds ?? 0) / 60)} min</p>}{universeStatus.started_at && <p>Elapsed: {Math.max(0, Math.floor(Date.now() / 1000 - universeStatus.started_at)) < 3600 ? `${Math.floor(Math.max(0, Date.now() / 1000 - universeStatus.started_at) / 60)} min` : `${Math.floor(Math.max(0, Date.now() / 1000 - universeStatus.started_at) / 3600)} hr ${Math.floor((Math.max(0, Date.now() / 1000 - universeStatus.started_at) % 3600) / 60)} min`}</p>}{universeStatus.status === "running" && (universeStatus.scanned ?? 0) > 0 && (universeStatus.total ?? 0) > (universeStatus.scanned ?? 0) && <p>Estimated remaining: about {Math.max(1, Math.ceil(((Date.now() / 1000 - (universeStatus.started_at ?? Date.now() / 1000)) / Math.max(1, universeStatus.scanned ?? 0)) * ((universeStatus.total ?? 0) - (universeStatus.scanned ?? 0)) / 60))} min</p>}{universeStatus.fatal_error && <p className="text-rose-400 sm:col-span-2">{universeStatus.fatal_error}</p>}</div>{(universeStatus.failures?.length ?? 0) > 0 && <details className="mt-2"><summary className="cursor-pointer">Failed symbols ({universeStatus.failures?.length})</summary><ul className="mt-2 space-y-1">{universeStatus.failures?.slice(0,30).map(f=><li key={f.symbol}>{f.symbol}: {f.reason}</li>)}</ul></details>}</section>}
    {error && <section role="alert" className="flex gap-3 rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-sm"><AlertCircle className="shrink-0 text-rose-400" size={19}/><div><strong>Real data request failed</strong><p className="mt-1 break-words">{error}</p><p className="mt-1 theme-muted">No sample or fabricated opportunities are shown. Check backend availability and the connected historical-data session.</p></div></section>}
    {result && <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border theme-border theme-surface p-4">
        <div><div className="text-xs theme-muted">Scan result</div><div className="mt-1 text-xl font-bold">{result.symbol}</div><div className="mt-1 text-xs theme-muted">{result.weekly_bars ?? 0} weekly bars · {result.data_source ?? "Historical source"}</div></div>
        <div className="text-right"><div className="text-2xl font-bold">{result.count ?? 0}</div><div className="text-xs theme-muted">qualifying setups</div></div>
      </div>
      {result.status === "insufficient_data" && <p className="rounded-xl border theme-border p-4 text-sm theme-muted">{result.message || "Insufficient weekly history."}</p>}
      {(!result.matches || result.matches.length===0) && result.status !== "insufficient_data" && <div className="rounded-xl border theme-border p-5 text-sm"><CheckCircle2 size={18} className="mb-2 text-emerald-400"/><strong>No qualifying setup found in returned history.</strong><p className="mt-1 theme-muted">This is a valid scan result, not a generated opportunity.</p></div>}
      {(result.matches || []).length > 0 && <div className="overflow-x-auto rounded-xl border theme-border"><table className="w-full min-w-[1180px] text-left text-sm"><thead className="theme-surface-2 text-xs theme-muted"><tr>{["Rank","Signal","Stock","Price","RSI","RSI Rising","OBV Rising","Price Fall %","Price Phase","Sideways Range %","RSI Before Sideways","20-SMA Near","Special Timing","Strength","Timeframe"].map(label=><th key={label} className="whitespace-nowrap px-3 py-3">{label}</th>)}</tr></thead><tbody>{(result.matches || []).map((m,idx)=><tr key={m.symbol + m.checked_at_week} className="border-t theme-border"><td className="px-3 py-3 font-bold">{m.timing || (m.rsi_before_sideways ? "A" : "B")}{m.obv_rising ? "🟢" : "🟡"}</td><td className="px-3 py-3 font-semibold">{m.signal || "PASS"}</td><td className="px-3 py-3 font-bold">{m.symbol}</td><td className="px-3 py-3">{m.price == null ? "—" : money(m.price)}</td><td className="px-3 py-3">{m.rsi == null ? "—" : Number(m.rsi).toFixed(2)}</td><td className="px-3 py-3">{m.rsi_rising ? "YES" : "NO"}</td><td className="px-3 py-3">{m.obv_rising ? "YES" : "NO"}</td><td className="px-3 py-3">{m.price_fall_pct == null ? "—" : m.price_fall_pct.toFixed(2) + "%"}</td><td className="px-3 py-3">{m.price_phase || "—"}</td><td className="px-3 py-3">{m.sideways_range_pct == null ? "—" : m.sideways_range_pct.toFixed(2) + "%"}</td><td className="px-3 py-3">{m.rsi_before_sideways ? "YES" : "NO"}</td><td className="px-3 py-3">{m.sma20_near == null ? "—" : m.sma20_near ? "YES" : "NO"}</td><td className="px-3 py-3">{m.special_timing || "—"}</td><td className="px-3 py-3">{m.strength}</td><td className="px-3 py-3">{m.timeframe || "Weekly"}</td></tr>)}</tbody></table></div>}
    </section>}
  </div>;
}

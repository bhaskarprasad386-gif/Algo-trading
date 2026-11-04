"use client";

import { useCallback, useEffect, useState } from "react";
import { Archive, RefreshCw } from "lucide-react";
import { Card } from "@/components/ui";
import { appConfig } from "@/lib/config";

type TraceEntry = {
  id: number;
  created_at: string;
  symbol: string;
  expiry: string;
  direction: string;
  quantity_lots: number;
  lot_size: number;
  quantity: number;
  source_identity: string;
  entry: {
    low_strike: number;
    high_strike: number;
    observed_at: string;
    executable_edge?: number | null;
    expiry_type?: string;
    trace_entry_sides?: Record<string, string>;
    trace_entry_prices?: Record<string, number | null>;
    trace_entry_depth?: Record<string, number | null> | Array<number | null>;
    depth_legs?: Array<{
      role: string; side: string; timestamp_ns?: number | string | null;
      depth_status?: string; vwap?: number | null;
      available_quantity?: number | null; selected_quantity?: number | null;
      bid_depth_levels?: Array<{ price: number; quantity: number }>;
      ask_depth_levels?: Array<{ price: number; quantity: number }>;
    }>;
  };
};
type ExitResult = {
  status: string;
  checked_at: string;
  entry_edge: number | null;
  latest_edge: number | null;
  exit_edge: number | null;
  gross_pnl: number | null;
  gross_pnl_per_lot: number | null;
  max_complete_exit_lots_now: number;
  message: string;
  legs: Array<{
    leg: string; exit_side: string; exit_price: number | null;
    exit_qty: number | null; leg_gross_pnl: number | null;
    quote_age_seconds?: number | null;
    depth_levels?: Array<{ price: number; quantity: number }>;
  }>;
};
const legs = [["Low CE","low_call"],["Low PE","low_put"],["High CE","high_call"],["High PE","high_put"]] as const;
const fmt = (value: unknown) => value == null || !Number.isFinite(Number(value)) ? "—" : Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 });
const time = (value: string) => { const d = new Date(value); return Number.isNaN(d.getTime()) ? value : d.toLocaleString("en-IN", { timeZone:"Asia/Kolkata", dateStyle:"short", timeStyle:"medium" }); };
const expiryDate = (value: string) => {
  const raw = String(value || "").trim().toUpperCase();
  let m = raw.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  m = raw.match(/^(\d{1,2})-([A-Z]{3})-(\d{2,4})$/);
  if (m) { const months = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]; const y = Number(m[3]) < 100 ? 2000 + Number(m[3]) : Number(m[3]); return new Date(y, months.indexOf(m[2]), Number(m[1])); }
  const d = new Date(raw); return Number.isNaN(d.getTime()) ? null : new Date(d.getFullYear(), d.getMonth(), d.getDate());
};
const isExpired = (value: string) => {
  const d = expiryDate(value); if (!d) return false;
  const key = [d.getFullYear(), String(d.getMonth() + 1).padStart(2, "0"), String(d.getDate()).padStart(2, "0")].join("-");
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
  return key < today;
};
const edgeDifference = (entry: number | null | undefined, latest: number | null | undefined) => entry == null || latest == null ? null : latest - entry;

export default function BoxSpreadTraceArea() {
  const [records, setRecords] = useState<TraceEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [results, setResults] = useState<Record<number, ExitResult>>({});
  const [selectedTrace, setSelectedTrace] = useState<TraceEntry | null>(null);
  const api = appConfig.apiBaseUrl.replace(/\/$/, "") + "/api/v1/scanner/box-spread/trace";
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch(api, { cache: "no-store" });
      const body = await response.json();
      if (!response.ok || !Array.isArray(body.data)) throw new Error("Trace list could not be loaded");
      setRecords(body.data);
      setError(null);
    } catch (e) { setError(e instanceof Error ? e.message : "Trace list could not be loaded"); }
    finally { setLoading(false); }
  }, [api]);
  useEffect(() => {
    void load();
    const updated = () => { void load(); };
    window.addEventListener("box-spread-trace-updated", updated);
    return () => window.removeEventListener("box-spread-trace-updated", updated);
  }, [load]);
  const checkExit = async (id: number) => {
    setBusy(id); setError(null);
    try {
      const response = await fetch(api + "/" + id + "/exit-check", { method: "POST", cache: "no-store" });
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : body.detail?.message || "Exit check failed");
      setResults(old => ({ ...old, [id]: body }));
    } catch (e) { setError(e instanceof Error ? e.message : "Exit check failed"); }
    finally { setBusy(null); }
  };
  return <Card id="box-trace" className="box-trace-contrast scroll-mt-24 theme-border theme-surface theme-text p-4 shadow-xl sm:p-5">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="flex items-start gap-3">
        <Archive className="mt-0.5 h-5 w-5 theme-accent" />
        <div><h2 className="text-base font-extrabold theme-text">Box Spread Trace</h2>
          <p className="mt-1 text-xs theme-muted">Alerts manually added from Post-Market History remain here as separate records. Check Status / Exit fetches fresh live quotes and available depth for all four legs; it only calculates exit status and P&L preview — it never places an order.</p>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <span className="rounded-lg border theme-border theme-surface-2 px-3 py-2 text-xs font-bold theme-text">{records.length} traced</span>
        <button type="button" onClick={() => void load()} disabled={loading} className="inline-flex min-h-9 items-center gap-2 rounded-lg border theme-border px-3 text-xs font-semibold theme-text disabled:opacity-50"><RefreshCw className={"h-3.5 w-3.5 " + (loading ? "animate-spin" : "")}/> Refresh</button>
      </div>
    </div>
    {error && <p role="alert" className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs theme-text">{error}</p>}
    {!records.length && loading && <p className="mt-4 text-sm theme-muted">Loading saved Trace alerts…</p>}
    {!records.length && !loading && !error && <p className="mt-4 rounded-lg border theme-border theme-surface-2 p-4 text-sm theme-muted">Abhi koi alert Trace nahi hua. Post-Market History mein alert ke saamne Add to Trace dabayen; woh yahan alag entry ke roop mein dikhega.</p>}
    <div className="mt-4 grid gap-4 xl:grid-cols-2">
      <section className="min-w-0">
        <h3 className="mb-3 text-sm font-extrabold uppercase tracking-wide theme-text">Active Traces</h3>
        <div className="space-y-3">
      {records.filter(trace => !isExpired(trace.expiry)).map(trace => <article key={trace.id} className="rounded-xl border theme-border theme-surface-2 p-3 sm:p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-extrabold theme-text">{trace.symbol}</span><span className="rounded-md bg-cyan-500/10 px-2 py-0.5 text-[10px] font-bold text-cyan-700 dark:text-cyan-300">{trace.direction}</span><span className="text-[10px] font-semibold theme-muted">{trace.entry.expiry_type || "EXPIRY"} · {trace.expiry}</span>{isExpired(trace.expiry) && <span className="rounded-md border border-rose-700 bg-rose-100 px-2 py-1 text-xs font-extrabold text-rose-900">EXPIRED</span>}</div>
            <p className="mt-1 text-xs font-semibold theme-text">Strike {fmt(trace.entry.low_strike)} – {fmt(trace.entry.high_strike)} · {trace.quantity_lots} lots · Qty {fmt(trace.quantity)}</p>
            <p className="mt-1 text-[10px] theme-muted">Trace saved: {time(trace.created_at)} · Original alert: {time(trace.entry.observed_at)}</p>
          </div>
          {!isExpired(trace.expiry) ? <div className="flex flex-wrap gap-2"><button type="button" onClick={() => setSelectedTrace(trace)} className="min-h-10 rounded-lg border-2 border-slate-500 px-3 text-sm font-extrabold theme-text">View Saved Position</button><button type="button" onClick={() => void checkExit(trace.id)} disabled={busy === trace.id} className="min-h-10 rounded-lg border border-slate-600 bg-white px-3 text-sm font-extrabold text-slate-950 disabled:opacity-50">{busy === trace.id ? "Fetching Live Data…" : "Check Status / Exit"}</button></div> : <span className="rounded-lg border border-rose-700 bg-rose-100 px-3 py-2 text-sm font-extrabold text-rose-900">TRACE EXPIRED</span>}
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {legs.map(([label,key],index) => {
            const side = trace.entry.trace_entry_sides?.[key] || "—";
            const price = trace.entry.trace_entry_prices?.[key];
            const depth = Array.isArray(trace.entry.trace_entry_depth) ? trace.entry.trace_entry_depth[index] : trace.entry.trace_entry_depth?.[key];
            return <div key={key} className="rounded-lg border theme-border theme-surface p-2"><p className="text-[10px] font-bold theme-text">{label} · {side}</p><p className="mt-1 text-xs font-semibold theme-text">Entry ₹{fmt(price)}</p><p className="mt-1 text-[10px] theme-muted">Saved depth {fmt(depth)}</p></div>;
          })}
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div className="rounded-md border theme-border theme-surface p-2"><p className="text-[10px] theme-muted">Entry Edge (saved alert)</p><p className="mt-1 text-xs font-extrabold theme-text">₹{fmt(trace.entry.executable_edge)}</p></div>
          <div className="rounded-md border theme-border theme-surface p-2"><p className="text-[10px] theme-muted">Latest Edge (same direction)</p><p className="mt-1 text-xs font-extrabold theme-text">{results[trace.id] ? "₹"+fmt(results[trace.id].latest_edge) : "—"}</p></div>
          <div className="rounded-md border theme-border theme-surface p-2"><p className="text-[10px] theme-muted">Exit Edge (close P&amp;L / unit)</p><p className="mt-1 text-xs font-extrabold theme-text">{results[trace.id] ? "₹"+fmt(results[trace.id].exit_edge) : "—"}</p></div>
          <div className="rounded-md border theme-border theme-surface p-2"><p className="text-sm font-bold theme-muted">Edge Difference (Latest − Entry)</p><p className={"mt-1 text-base font-extrabold " + ((edgeDifference(trace.entry.executable_edge, results[trace.id]?.latest_edge) ?? 0) >= 0 ? "text-emerald-800" : "text-rose-800")}>{edgeDifference(trace.entry.executable_edge, results[trace.id]?.latest_edge) == null ? "—" : (edgeDifference(trace.entry.executable_edge, results[trace.id]?.latest_edge)! > 0 ? "+" : "") + "₹" + fmt(edgeDifference(trace.entry.executable_edge, results[trace.id]?.latest_edge))}</p></div><div className="rounded-md border theme-border theme-surface p-2"><p className="text-sm font-bold theme-muted">Gross Profit (1 lot)</p><p className="mt-1 text-base font-extrabold text-emerald-800">{results[trace.id]?.gross_pnl_per_lot == null ? "—" : "₹"+fmt(results[trace.id].gross_pnl_per_lot)}</p></div>
        </div>
        {results[trace.id] && <div className="mt-3 rounded-lg border theme-border theme-surface p-3 text-xs theme-text">
          <p className="font-bold">{results[trace.id].status} · Gross P&L {results[trace.id].gross_pnl == null ? "—" : "₹"+fmt(results[trace.id].gross_pnl)} · Max exit {results[trace.id].max_complete_exit_lots_now} lots</p>
          <p className="mt-2 theme-muted">Edge Difference = Latest Edge − Entry Edge (e.g. ₹700 − ₹713 = −₹13). Gross Profit / Lot is verified four-leg gross P&L divided by traced lots. Exit Edge is close P&L per unit from fresh four-leg depth VWAP and is shown only when all four legs support the full traced quantity.</p>
          <p className="mt-1 theme-muted">Latest quote check: {time(results[trace.id].checked_at)}</p>
          <p className="mt-1 theme-muted">{results[trace.id].message}</p>
          <div className="mt-2 grid grid-cols-2 gap-2">{results[trace.id].legs.map(leg => <div key={leg.leg} className="rounded-md border theme-border theme-surface-2 p-2"><p className="text-[10px] font-bold theme-text">{leg.leg.replace("_"," ").toUpperCase()} · {leg.exit_side} · {leg.exit_price == null ? "No quote" : "₹"+fmt(leg.exit_price)} · Qty {fmt(leg.exit_qty)}</p><p className="mt-1 text-[10px] theme-muted">Quote age {leg.quote_age_seconds == null ? "—" : fmt(leg.quote_age_seconds)+"s"} · Saved live levels</p>{leg.depth_levels?.map((level,index) => <p key={index} className="flex justify-between border-t theme-border pt-1 text-[10px] theme-muted"><span>₹{fmt(level.price)}</span><span>{fmt(level.quantity)}</span></p>)}</div>)}</div>
        </div>}
      </article>)}
        </div>
      </section>
      <section className="min-w-0">
        <h3 className="mb-3 text-sm font-extrabold uppercase tracking-wide theme-text">Expires</h3>
        <div className="space-y-3">
          {records.filter(trace => isExpired(trace.expiry)).map(trace => <article key={trace.id} className="rounded-xl border-2 border-rose-700/70 bg-rose-950/20 p-3 sm:p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-extrabold theme-text">{trace.symbol}</span><span className="rounded-md border border-rose-700 bg-rose-100 px-2 py-1 text-xs font-extrabold text-rose-900">EXPIRED</span><span className="text-xs font-semibold theme-muted">{trace.expiry}</span></div>
                <p className="mt-1 text-xs font-semibold theme-text">Strike {fmt(trace.entry.low_strike)} – {fmt(trace.entry.high_strike)} · {trace.quantity_lots} lots · Qty {fmt(trace.quantity)}</p>
                <p className="mt-1 text-xs theme-muted">Saved {time(trace.created_at)} · Original alert {time(trace.entry.observed_at)}</p>
                <p className="mt-1 text-sm font-extrabold theme-text">Entry Edge ₹{fmt(trace.entry.executable_edge)}</p>
              </div>
              <button type="button" onClick={() => setSelectedTrace(trace)} className="min-h-9 rounded-lg border-2 border-rose-700 px-3 text-xs font-extrabold theme-text">View Saved Position</button>
            </div>
          </article>)}
          {!records.some(trace => isExpired(trace.expiry)) && <p className="rounded-lg border theme-border theme-surface-2 p-3 text-xs theme-muted">No expired traces.</p>}
        </div>
      </section>
    </div>
    {selectedTrace && <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/75 p-3 sm:p-6" role="dialog" aria-modal="true" aria-label="Saved trace position">
      <div className="max-h-[90vh] w-full max-w-5xl overflow-y-auto rounded-2xl border-2 border-slate-500 bg-slate-950 p-4 text-slate-100 shadow-2xl sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-600 pb-3">
          <div><h3 className="text-lg font-extrabold">Saved Trace Position · {selectedTrace.symbol}</h3><p className="mt-1 text-sm text-slate-300">{selectedTrace.expiry} · {fmt(selectedTrace.entry.low_strike)}–{fmt(selectedTrace.entry.high_strike)} · {selectedTrace.direction} · {selectedTrace.quantity_lots} lots</p><p className="mt-1 text-xs text-slate-400">Original alert: {time(selectedTrace.entry.observed_at)} · Trace saved: {time(selectedTrace.created_at)}</p></div>
          <button type="button" onClick={() => setSelectedTrace(null)} className="min-h-10 rounded-lg border-2 border-slate-400 px-4 text-sm font-bold text-white">Close</button>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div className="rounded-lg border border-slate-600 p-3"><p className="text-xs text-slate-400">Entry Edge</p><p className="mt-1 text-base font-extrabold">₹{fmt(selectedTrace.entry.executable_edge)}</p></div>
          <div className="rounded-lg border border-slate-600 p-3"><p className="text-xs text-slate-400">Lot Size</p><p className="mt-1 text-base font-extrabold">{fmt(selectedTrace.lot_size)}</p></div>
          <div className="rounded-lg border border-slate-600 p-3"><p className="text-xs text-slate-400">Quantity</p><p className="mt-1 text-base font-extrabold">{fmt(selectedTrace.quantity)}</p></div>
          <div className="rounded-lg border border-slate-600 p-3"><p className="text-xs text-slate-400">Saved Position</p><p className="mt-1 text-base font-extrabold">{isExpired(selectedTrace.expiry) ? "EXPIRED" : "ACTIVE"}</p></div>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          {legs.map(([label, key]) => {
            const side = selectedTrace.entry.trace_entry_sides?.[key] || "—";
            const price = selectedTrace.entry.trace_entry_prices?.[key];
            const depth = selectedTrace.entry.depth_legs?.find(item => item.role.toLowerCase() === key.replace("_", "_").replace("low_call", "low_ce").replace("low_put", "low_pe").replace("high_call", "high_ce").replace("high_put", "high_pe"));
            return <section key={key} className="rounded-xl border border-slate-600 bg-slate-900 p-3">
              <h4 className="text-sm font-extrabold">{label} · {side}</h4><p className="mt-1 text-xs text-slate-300">Entry ₹{fmt(price)} · saved L1 qty {fmt(Array.isArray(selectedTrace.entry.trace_entry_depth) ? selectedTrace.entry.trace_entry_depth[legs.findIndex(item => item[1] === key)] : selectedTrace.entry.trace_entry_depth?.[key])}</p>
              {depth ? <><p className="mt-2 text-xs text-slate-400">Depth tick {depth.timestamp_ns ? new Date(Number(depth.timestamp_ns) / 1e6).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" }) + " IST" : "—"} · {depth.depth_status || "DEPTH"} · VWAP ₹{fmt(depth.vwap)}</p>{(depth.bid_depth_levels?.length || 0) + (depth.ask_depth_levels?.length || 0) > 0 ? <div className="mt-2 grid grid-cols-2 gap-2">{([[ "BID", depth.bid_depth_levels || [] ], [ "ASK", depth.ask_depth_levels || [] ]] as const).map(([name, levels]) => <div key={name} className="rounded-lg border border-slate-700 p-2"><p className="mb-1 text-xs font-bold">{name}</p>{levels.map((level, index) => <p key={name + index} className="flex justify-between border-t border-slate-800 py-1 text-xs"><span>₹{fmt(level.price)}</span><span>{fmt(level.quantity)}</span></p>)}</div>)}</div> : <p className="mt-2 text-xs text-amber-200">The trace predates saved depth ladders; only the exact saved entry price and depth summary are available.</p>}</> : <p className="mt-2 text-xs text-amber-200">Full depth was not recorded for this older trace; only the immutable saved entry price and quantity are available.</p>}
            </section>;
          })}
        </div>
        <p className="mt-3 text-xs text-slate-400">This view uses the trace’s immutable entry snapshot. It does not replace saved prices with current quotes and does not place orders.</p>
      </div>
    </div>}
  </Card>;
}

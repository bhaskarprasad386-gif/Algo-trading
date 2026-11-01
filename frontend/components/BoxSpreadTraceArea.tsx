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
    expiry_type?: string;
    trace_entry_sides?: Record<string, string>;
    trace_entry_prices?: Record<string, number | null>;
    trace_entry_depth?: Record<string, number | null> | Array<number | null>;
  };
};
type ExitResult = {
  status: string;
  gross_pnl: number | null;
  max_complete_exit_lots_now: number;
  message: string;
  legs: Array<{ leg: string; exit_side: string; exit_price: number | null; exit_qty: number | null; leg_gross_pnl: number | null }>;
};
const legs = [["Low CE","low_call"],["Low PE","low_put"],["High CE","high_call"],["High PE","high_put"]] as const;
const fmt = (value: unknown) => value == null || !Number.isFinite(Number(value)) ? "—" : Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 });
const time = (value: string) => { const d = new Date(value); return Number.isNaN(d.getTime()) ? value : d.toLocaleString("en-IN", { timeZone:"Asia/Kolkata", dateStyle:"short", timeStyle:"medium" }); };

export default function BoxSpreadTraceArea() {
  const [records, setRecords] = useState<TraceEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [results, setResults] = useState<Record<number, ExitResult>>({});
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
  return <Card id="box-trace" className="scroll-mt-24 theme-border theme-surface theme-text p-4 shadow-xl sm:p-5">
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
    <div className="mt-4 space-y-3">
      {records.map(trace => <article key={trace.id} className="rounded-xl border theme-border theme-surface-2 p-3 sm:p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-extrabold theme-text">{trace.symbol}</span><span className="rounded-md bg-cyan-500/10 px-2 py-0.5 text-[10px] font-bold text-cyan-700 dark:text-cyan-300">{trace.direction}</span><span className="text-[10px] font-semibold theme-muted">{trace.entry.expiry_type || "EXPIRY"} · {trace.expiry}</span></div>
            <p className="mt-1 text-xs font-semibold theme-text">Strike {fmt(trace.entry.low_strike)} – {fmt(trace.entry.high_strike)} · {trace.quantity_lots} lots · Qty {fmt(trace.quantity)}</p>
            <p className="mt-1 text-[10px] theme-muted">Trace saved: {time(trace.created_at)} · Original alert: {time(trace.entry.observed_at)}</p>
          </div>
          <button type="button" onClick={() => void checkExit(trace.id)} disabled={busy === trace.id} className="min-h-9 rounded-lg border border-amber-500/50 px-3 text-xs font-bold theme-text disabled:opacity-50">{busy === trace.id ? "Fetching Live Data…" : "Check Status / Exit"}</button>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {legs.map(([label,key],index) => {
            const side = trace.entry.trace_entry_sides?.[key] || "—";
            const price = trace.entry.trace_entry_prices?.[key];
            const depth = Array.isArray(trace.entry.trace_entry_depth) ? trace.entry.trace_entry_depth[index] : trace.entry.trace_entry_depth?.[key];
            return <div key={key} className="rounded-lg border theme-border theme-surface p-2"><p className="text-[10px] font-bold theme-text">{label} · {side}</p><p className="mt-1 text-xs font-semibold theme-text">Entry ₹{fmt(price)}</p><p className="mt-1 text-[10px] theme-muted">Saved depth {fmt(depth)}</p></div>;
          })}
        </div>
        {results[trace.id] && <div className="mt-3 rounded-lg border theme-border theme-surface p-3 text-xs theme-text">
          <p className="font-bold">{results[trace.id].status} · Gross P&L {results[trace.id].gross_pnl == null ? "—" : "₹"+fmt(results[trace.id].gross_pnl)} · Max exit {results[trace.id].max_complete_exit_lots_now} lots</p>
          <p className="mt-1 theme-muted">{results[trace.id].message}</p>
          <div className="mt-2 grid grid-cols-2 gap-2">{results[trace.id].legs.map(leg => <p key={leg.leg} className="text-[10px] theme-muted">{leg.leg.replace("_"," ").toUpperCase()} · {leg.exit_side} · {leg.exit_price == null ? "No quote" : "₹"+fmt(leg.exit_price)} · Qty {fmt(leg.exit_qty)}</p>)}</div>
        </div>}
      </article>)}
    </div>
  </Card>;
}

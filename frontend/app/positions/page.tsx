"use client";

import { useEffect, useMemo, useState } from "react";
import { BriefcaseBusiness, RefreshCw, ShieldCheck, XCircle } from "lucide-react";
import { Card, PageTitle } from "@/components/ui";
import { appConfig } from "@/lib/config";

type Position = {
  id: number; strategy?: string; symbol?: string; direction?: string | null;
  entry?: number; current?: number; quantity?: number; pnl?: number;
  pnl_pct?: number; capital_allocated?: number; expiry?: string | null;
};

const money = (v: number | null | undefined) =>
  typeof v !== "number" || !Number.isFinite(v) ? "—" : `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
const label = (v?: string | null) => v ? v.replaceAll("-", " ").replace(/\b\w/g, c => c.toUpperCase()) : "—";

export default function PositionsPage() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [closingId, setClosingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const base = appConfig.apiBaseUrl.replace(/\/$/, "");

  const load = async () => {
    setLoading(true);
    try {
      const r = await fetch(`${base}/api/v1/auto/live-paper/ongoing`, { cache: "no-store" });
      if (!r.ok) throw new Error(`Positions HTTP ${r.status}`);
      const data = await r.json();
      const rows = Array.isArray(data) ? data : (data?.data ?? data?.positions ?? []);
      setPositions(Array.isArray(rows) ? rows : []);
      setError(null);
    } catch (e) {
      setPositions([]);
      setError(e instanceof Error ? e.message : "Backend unavailable");
    } finally { setLoading(false); }
  };

  useEffect(() => { void load(); }, []);

  const filtered = useMemo(() => filter === "all" ? positions : positions.filter(p => p.strategy === filter), [filter, positions]);
  const totalPnl = positions.reduce((s, p) => s + (Number(p.pnl) || 0), 0);
  const allocated = positions.reduce((s, p) => s + (Number(p.capital_allocated) || 0), 0);
  const strategies = Array.from(new Set(positions.map(p => p.strategy).filter(Boolean))) as string[];

  const closePosition = async (id: number) => {
    setClosingId(id); setError(null);
    try {
      const r = await fetch(`${base}/api/v1/auto/live-paper/${id}/close`, { method: "POST" });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data?.detail || `Close HTTP ${r.status}`);
      await load();
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to close position"); }
    finally { setClosingId(null); }
  };

  return <div className="space-y-5">
    <PageTitle eyebrow="Phase 7 • Portfolio" title="Positions"
      description="Active paper positions from the backend position engine. Broker orders remain OFF." />

    <div className="grid gap-3 sm:grid-cols-3">
      <Card className="p-4"><p className="text-xs uppercase tracking-wider text-slate-600">Open Positions</p>
        <p className="mt-2 text-2xl font-bold text-slate-900">{loading ? "…" : positions.length}</p></Card>
      <Card className="p-4"><p className="text-xs uppercase tracking-wider text-slate-600">Unrealized P&L</p>
        <p className={`mt-2 text-2xl font-bold ${totalPnl >= 0 ? "text-emerald-700" : "text-red-700"}`}>{money(totalPnl)}</p></Card>
      <Card className="p-4"><p className="text-xs uppercase tracking-wider text-slate-600">Allocated Capital</p>
        <p className="mt-2 text-2xl font-bold text-slate-900">{money(allocated)}</p></Card>
    </div>

    <Card className="p-4">
      <div className="flex flex-wrap items-center gap-2">
        {["all", ...strategies].map(s => <button key={s} type="button" onClick={() => setFilter(s)}
          className={`min-h-10 rounded-lg px-3 text-sm ${filter === s ? "bg-sky-50 text-sky-700" : "bg-slate-50 text-slate-600"}`}>
          {s === "all" ? "All" : label(s)}
        </button>)}
        <button type="button" onClick={() => void load()} disabled={loading}
          className="ml-auto inline-flex min-h-10 items-center gap-2 rounded-lg border border-slate-200 px-3 text-sm text-slate-900 disabled:opacity-50">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
        </button>
      </div>
    </Card>

    {error && <Card className="border-red-300 bg-red-50 p-4 text-sm text-red-700">{error}</Card>}

    <Card className="overflow-hidden">
      <div className="flex items-center gap-2 border-b border-slate-200 p-5">
        <BriefcaseBusiness className="h-5 w-5 text-sky-700" />
        <div><h2 className="text-base font-semibold text-slate-900">Open Paper Positions</h2>
          <p className="text-sm text-slate-600">Backend-supplied values only; no fabricated market data.</p></div>
      </div>
      {loading ? <div className="p-8 text-sm text-slate-600">Loading positions…</div> :
       filtered.length === 0 ? <div className="p-8 text-sm text-slate-600">No active paper positions.</div> :
       <div className="overflow-x-auto"><table className="w-full min-w-[980px] text-left text-sm">
        <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wider text-slate-600">
          <tr>{["Strategy","Symbol","Side","Qty","Entry","Current","P&L","Expiry","Action"].map(h => <th key={h} className="px-5 py-3">{h}</th>)}</tr>
        </thead>
        <tbody>{filtered.map(p => { const pnl = Number(p.pnl) || 0; return <tr key={p.id} className="border-b border-slate-200/70 last:border-0">
          <td className="px-5 py-4 font-medium text-slate-900">{label(p.strategy)}</td>
          <td className="px-5 py-4 text-slate-900">{p.symbol || "—"}</td><td className="px-5 py-4 text-slate-600">{p.direction || "—"}</td>
          <td className="px-5 py-4 text-slate-900">{p.quantity ?? "—"}</td><td className="px-5 py-4 text-slate-900">{money(Number(p.entry))}</td>
          <td className="px-5 py-4 text-slate-900">{money(Number(p.current))}</td>
          <td className={`px-5 py-4 font-semibold ${pnl >= 0 ? "text-emerald-700" : "text-red-700"}`}>{money(pnl)} {typeof p.pnl_pct === "number" ? `(${p.pnl_pct.toFixed(2)}%)` : ""}</td>
          <td className="px-5 py-4 text-slate-600">{p.expiry || "—"}</td>
          <td className="px-5 py-4"><button type="button" onClick={() => void closePosition(p.id)} disabled={closingId !== null}
            className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-red-300 px-3 text-xs font-semibold text-red-700 disabled:opacity-50">
            <XCircle className="h-4 w-4" />{closingId === p.id ? "Closing…" : "Close"}</button></td>
        </tr>; })}</tbody>
       </table></div>}
    </Card>

    <Card className="flex items-start gap-3 p-4"><ShieldCheck className="mt-0.5 h-5 w-5 text-emerald-700" />
      <div><p className="text-sm font-semibold text-slate-900">Paper-only safety</p>
        <p className="mt-1 text-xs leading-5 text-slate-600">Close actions use the paper-trading API. Live broker orders remain disabled.</p></div>
    </Card>
  </div>;
}

"use client";

import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, FileText, RefreshCw, ShieldCheck } from "lucide-react";
import { Card, PageTitle } from "@/components/ui";
import { appConfig } from "@/lib/config";

type Trade = {
  id: number;
  strategy?: string;
  symbol?: string;
  direction?: string;
  expiry?: string | null;
  lot_size?: number;
  lots?: number;
  entry_edge?: number;
  current_edge?: number;
  capital_used?: number;
  realized_pnl?: number;
  pnl_pct?: number;
  exit_reason?: string | null;
  opened_at?: string | null;
  closed_at?: string | null;
};

const money = (v: unknown) => {
  const n = Number(v);
  return Number.isFinite(n) ? `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}` : "—";
};
const label = (v?: string | null) =>
  v ? v.replaceAll("-", " ").replace(/\b\w/g, c => c.toUpperCase()) : "—";
const dateTime = (v?: string | null) =>
  v ? new Date(v).toLocaleString("en-IN", { dateStyle: "short", timeStyle: "short" }) : "—";

export default function CompletedTradesPage() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [strategy, setStrategy] = useState("all");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Trade | null>(null);
  const base = appConfig.apiBaseUrl.replace(/\/$/, "");

  const load = async () => {
    setLoading(true);
    try {
      const r = await fetch(`${base}/api/v1/live-paper/status`, { cache: "no-store" });
      if (!r.ok) throw new Error(`Completed Trades HTTP ${r.status}`);
      const data = await r.json();
      const rows = Array.isArray(data?.completed) ? data.completed : [];
      setTrades(rows);
      setError(null);
    } catch (e) {
      setTrades([]);
      setError(e instanceof Error ? e.message : "Backend unavailable");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const strategies = useMemo(
    () => Array.from(new Set(trades.map(t => t.strategy).filter(Boolean))) as string[],
    [trades],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return trades.filter(t => {
      const matchesStrategy = strategy === "all" || t.strategy === strategy;
      const haystack = [t.symbol, t.strategy, t.direction, t.exit_reason].join(" ").toLowerCase();
      return matchesStrategy && (!q || haystack.includes(q));
    });
  }, [search, strategy, trades]);

  const realized = filtered.reduce((s, t) => s + (Number(t.realized_pnl) || 0), 0);
  const capital = filtered.reduce((s, t) => s + (Number(t.capital_used) || 0), 0);
  const wins = filtered.filter(t => Number(t.realized_pnl) > 0).length;

  return <div className="space-y-5">
    <PageTitle eyebrow="Phase 8 • History" title="Completed Trades"
      description="Closed paper trades with backend-supplied execution and realized P&L details." />

    <div className="grid gap-3 sm:grid-cols-4">
      <Card className="p-4"><p className="text-xs uppercase tracking-wider text-slate-600">Completed</p>
        <p className="mt-2 text-2xl font-bold text-slate-900">{loading ? "…" : filtered.length}</p></Card>
      <Card className="p-4"><p className="text-xs uppercase tracking-wider text-slate-600">Realized P&L</p>
        <p className={`mt-2 text-2xl font-bold ${realized >= 0 ? "text-emerald-700" : "text-red-700"}`}>{money(realized)}</p></Card>
      <Card className="p-4"><p className="text-xs uppercase tracking-wider text-slate-600">Capital Used</p>
        <p className="mt-2 text-2xl font-bold text-slate-900">{money(capital)}</p></Card>
      <Card className="p-4"><p className="text-xs uppercase tracking-wider text-slate-600">Profitable</p>
        <p className="mt-2 text-2xl font-bold text-emerald-700">{loading ? "…" : wins}</p></Card>
    </div>

    <Card className="p-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="flex flex-wrap gap-2">
          {["all", ...strategies].map(s => <button key={s} type="button" onClick={() => setStrategy(s)}
            className={`min-h-10 rounded-lg px-3 text-sm ${strategy === s ? "bg-sky-50 text-sky-700" : "bg-slate-50 text-slate-600"}`}>
            {s === "all" ? "All" : label(s)}
          </button>)}
        </div>
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search symbol, strategy, exit…"
          className="min-h-10 w-full rounded-lg border border-slate-200 bg-slate-50 px-3 text-sm text-slate-900 outline-none lg:ml-auto lg:max-w-xs" />
        <button type="button" onClick={() => void load()} disabled={loading}
          className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 px-3 text-sm text-slate-900 disabled:opacity-50">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
        </button>
      </div>
    </Card>

    {error && <Card className="border-red-300 bg-red-50 p-4 text-sm text-red-700">{error}</Card>}

    <Card className="overflow-hidden">
      <div className="flex items-center gap-2 border-b border-slate-200 p-5">
        <FileText className="h-5 w-5 text-sky-700" />
        <div><h2 className="text-base font-semibold text-slate-900">Closed Paper Trades</h2>
          <p className="text-sm text-slate-600">Realized values come from the backend paper-trade lifecycle.</p></div>
      </div>
      {loading ? <div className="p-8 text-sm text-slate-600">Loading completed trades…</div> :
       filtered.length === 0 ? <div className="p-8 text-sm text-slate-600">No completed paper trades.</div> :
       <div className="overflow-x-auto"><table className="w-full min-w-[1200px] text-left text-sm">
        <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wider text-slate-600">
          <tr>{["Strategy","Symbol","Side","Lots","Entry Edge","Exit/Mark","Realized P&L","Exit","Closed","Detail"].map(h => <th key={h} className="px-5 py-3">{h}</th>)}</tr>
        </thead>
        <tbody>{filtered.map(t => { const pnl = Number(t.realized_pnl) || 0; return <tr key={t.id} className="border-b border-slate-200/70 last:border-0">
          <td className="px-5 py-4 font-medium text-slate-900">{label(t.strategy)}</td>
          <td className="px-5 py-4 text-slate-900">{t.symbol || "—"}</td>
          <td className="px-5 py-4 text-slate-600">{t.direction || "—"}</td>
          <td className="px-5 py-4 text-slate-900">{t.lots ?? "—"}</td>
          <td className="px-5 py-4 text-slate-900">{typeof t.entry_edge === "number" ? t.entry_edge.toFixed(4) : "—"}</td>
          <td className="px-5 py-4 text-slate-900">{typeof t.current_edge === "number" ? t.current_edge.toFixed(4) : "—"}</td>
          <td className={`px-5 py-4 font-semibold ${pnl >= 0 ? "text-emerald-700" : "text-red-700"}`}>{money(pnl)} {typeof t.pnl_pct === "number" ? `(${t.pnl_pct.toFixed(2)}%)` : ""}</td>
          <td className="px-5 py-4 text-slate-600">{label(t.exit_reason)}</td>
          <td className="px-5 py-4 text-slate-600">{dateTime(t.closed_at)}</td>
          <td className="px-5 py-4"><button type="button" onClick={() => setSelected(t)}
            className="min-h-10 rounded-lg border border-slate-200 px-3 text-xs font-semibold text-slate-900">View</button></td>
        </tr>; })}</tbody>
       </table></div>}
    </Card>

    {selected && <Card className="p-5">
      <div className="flex items-center justify-between gap-3">
        <div><h2 className="text-base font-semibold text-slate-900">{selected.symbol || "Trade"} • Detail</h2>
          <p className="text-xs text-slate-600">Trade ID #{selected.id}</p></div>
        <button type="button" onClick={() => setSelected(null)} className="min-h-10 rounded-lg border border-slate-200 px-3 text-sm text-slate-600">Close</button>
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
        <div><span className="text-slate-600">Strategy</span><p className="mt-1 text-slate-900">{label(selected.strategy)}</p></div>
        <div><span className="text-slate-600">Expiry</span><p className="mt-1 text-slate-900">{selected.expiry || "—"}</p></div>
        <div><span className="text-slate-600">Capital</span><p className="mt-1 text-slate-900">{money(selected.capital_used)}</p></div>
        <div><span className="text-slate-600">Opened</span><p className="mt-1 text-slate-900">{dateTime(selected.opened_at)}</p></div>
      </div>
    </Card>}

    <Card className="flex items-start gap-3 p-4">
      <ShieldCheck className="mt-0.5 h-5 w-5 text-emerald-700" />
      <div><p className="text-sm font-semibold text-slate-900">Paper-only safety</p>
        <p className="mt-1 text-xs leading-5 text-slate-600">This page reads completed paper trades only. Broker/live orders remain OFF.</p></div>
    </Card>
  </div>;
}

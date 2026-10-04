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
const dateTime = (v?: string | null) => {
  if (!v) return "—";
  const date = new Date(/(?:Z|[+-]\d{2}:\d{2})$/.test(v) ? v : `${v}Z`);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata",
        dateStyle: "short",
        timeStyle: "short",
      });
};

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
      <Card className="p-4"><p className="text-xs uppercase tracking-wider theme-muted">Completed</p>
        <p className="mt-2 text-2xl font-bold theme-text">{loading ? "…" : filtered.length}</p></Card>
      <Card className="p-4"><p className="text-xs uppercase tracking-wider theme-muted">Realized P&L</p>
        <p className={`mt-2 text-2xl font-bold ${realized >= 0 ? "theme-success" : "theme-danger"}`}>{money(realized)}</p></Card>
      <Card className="p-4"><p className="text-xs uppercase tracking-wider theme-muted">Capital Used</p>
        <p className="mt-2 text-2xl font-bold theme-text">{money(capital)}</p></Card>
      <Card className="p-4"><p className="text-xs uppercase tracking-wider theme-muted">Profitable</p>
        <p className="mt-2 text-2xl font-bold theme-success">{loading ? "…" : wins}</p></Card>
    </div>

    <Card className="p-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="flex flex-wrap gap-2">
          {["all", ...strategies].map(s => <button key={s} type="button" onClick={() => setStrategy(s)}
            className={`min-h-10 rounded-lg px-3 text-sm ${strategy === s ? "theme-accent-bg theme-accent" : "theme-surface-2 theme-muted"}`}>
            {s === "all" ? "All" : label(s)}
          </button>)}
        </div>
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search symbol, strategy, exit…"
          className="min-h-10 w-full rounded-lg border theme-border theme-surface-2 px-3 text-sm theme-text outline-none lg:ml-auto lg:max-w-xs" />
        <button type="button" onClick={() => void load()} disabled={loading}
          className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border theme-border px-3 text-sm theme-text disabled:opacity-50">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
        </button>
      </div>
    </Card>

    {error && <Card className="theme-border theme-danger-bg p-4 text-sm theme-danger">{error}</Card>}

    <Card className="overflow-hidden">
      <div className="flex items-center gap-2 border-b theme-border p-5">
        <FileText className="h-5 w-5 theme-accent" />
        <div><h2 className="text-base font-semibold theme-text">Closed Paper Trades</h2>
          <p className="text-sm theme-muted">Realized values come from the backend paper-trade lifecycle.</p></div>
      </div>
      {loading ? <div className="p-8 text-sm theme-muted">Loading completed trades…</div> :
       filtered.length === 0 ? <div className="p-8 text-sm theme-muted">No completed paper trades.</div> :
       <div className="overflow-x-auto"><table className="w-full min-w-[1200px] text-left text-sm">
        <thead className="border-b theme-border theme-surface-2 text-xs uppercase tracking-wider theme-muted">
          <tr>{["Strategy","Symbol","Side","Lots","Entry Edge","Exit Edge","Realized P&L","Exit","Closed","Detail"].map(h => <th key={h} className="px-5 py-3">{h}</th>)}</tr>
        </thead>
        <tbody>{filtered.map(t => { const pnl = Number(t.realized_pnl) || 0; return <tr key={t.id} className="border-b theme-border/70 last:border-0">
          <td className="px-5 py-4 font-medium theme-text">{label(t.strategy)}</td>
          <td className="px-5 py-4 theme-text">{t.symbol || "—"}</td>
          <td className="px-5 py-4 theme-muted">{t.direction || "—"}</td>
          <td className="px-5 py-4 theme-text">{t.lots ?? "—"}</td>
          <td className="px-5 py-4 theme-text">{typeof t.entry_edge === "number" ? t.entry_edge.toFixed(4) : "—"}</td>
          <td className="px-5 py-4 theme-text">{typeof t.current_edge === "number" ? t.current_edge.toFixed(4) : "—"}</td>
          <td className={`px-5 py-4 font-semibold ${pnl >= 0 ? "theme-success" : "theme-danger"}`}>{money(pnl)} {typeof t.pnl_pct === "number" ? `(${t.pnl_pct.toFixed(2)}%)` : ""}</td>
          <td className="px-5 py-4 theme-muted">{label(t.exit_reason)}</td>
          <td className="px-5 py-4 theme-muted">{dateTime(t.closed_at)}</td>
          <td className="px-5 py-4"><button type="button" onClick={() => setSelected(t)}
            className="min-h-10 rounded-lg border theme-border px-3 text-xs font-semibold theme-text">View</button></td>
        </tr>; })}</tbody>
       </table></div>}
    </Card>

    {selected && <Card className="p-5">
      <div className="flex items-center justify-between gap-3">
        <div><h2 className="text-base font-semibold theme-text">{selected.symbol || "Trade"} • Detail</h2>
          <p className="text-xs theme-muted">Trade ID #{selected.id}</p></div>
        <button type="button" onClick={() => setSelected(null)} className="min-h-10 rounded-lg border theme-border px-3 text-sm theme-muted">Close</button>
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
        <div><span className="theme-muted">Strategy</span><p className="mt-1 theme-text">{label(selected.strategy)}</p></div>
        <div><span className="theme-muted">Expiry</span><p className="mt-1 theme-text">{selected.expiry || "—"}</p></div>
        <div><span className="theme-muted">Capital</span><p className="mt-1 theme-text">{money(selected.capital_used)}</p></div>
        <div><span className="theme-muted">Opened</span><p className="mt-1 theme-text">{dateTime(selected.opened_at)}</p></div>
      </div>
    </Card>}

    <Card className="flex items-start gap-3 rounded-xl theme-surface-2 p-4">
      <ShieldCheck className="mt-0.5 h-5 w-5 theme-success" />
      <div><p className="text-sm font-semibold theme-text">Paper-only safety</p>
        <p className="mt-1 text-xs leading-5 theme-muted">This page reads completed paper trades only. Broker/live orders remain OFF.</p></div>
    </Card>
  </div>;
}

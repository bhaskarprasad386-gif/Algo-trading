"use client";

import { useEffect, useMemo, useState } from "react";
import { History, RefreshCw } from "lucide-react";
import { Card, PageTitle } from "@/components/ui";
import { appConfig } from "@/lib/config";

type Trade = {
  id: number;
  strategy?: string;
  symbol?: string;
  direction?: string;
  entry_edge?: number;
  current_edge?: number;
  realized_pnl?: number;
  capital_used?: number;
  closed_at?: string | null;
  exit_reason?: string | null;
  status?: string;
};

const money = (value: number | undefined) =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(value ?? 0);

const dateOnly = (value: string | null | undefined) => (value ? value.slice(0, 10) : "");

export default function HistoryPage() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [strategy, setStrategy] = useState("ALL");
  const [query, setQuery] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [selected, setSelected] = useState<Trade | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(`${base}/api/v1/live-paper/status`, { cache: "no-store" });
      if (!response.ok) throw new Error("History data unavailable");
      const body = await response.json();
      setTrades(Array.isArray(body.completed) ? body.completed : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load history");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return trades.filter((trade) => {
      const d = dateOnly(trade.closed_at);
      return (
        (strategy === "ALL" || trade.strategy === strategy) &&
        (!q || [trade.symbol, trade.strategy, trade.exit_reason, trade.direction].some((v) => String(v ?? "").toLowerCase().includes(q))) &&
        (!from || !d || d >= from) &&
        (!to || !d || d <= to)
      );
    });
  }, [trades, strategy, query, from, to]);

  const realized = filtered.reduce((sum, trade) => sum + Number(trade.realized_pnl || 0), 0);
  const profitable = filtered.filter((trade) => Number(trade.realized_pnl || 0) > 0).length;

  return (
    <>
      <PageTitle eyebrow="Workspace" title="History" description="Completed paper-trade history with filters and details. Replay is permanently excluded." />

      <div className="grid gap-3 sm:grid-cols-3">
        <Card className="p-4"><p className="flex items-center gap-2 text-xs theme-muted"><History className="h-4 w-4" /> Completed</p><p className="mt-1 text-2xl font-semibold theme-text">{filtered.length}</p></Card>
        <Card className="p-4"><p className="text-xs theme-muted">Realized P&L</p><p className="mt-1 text-2xl font-semibold theme-success">{money(realized)}</p></Card>
        <Card className="p-4"><p className="text-xs theme-muted">Profitable</p><p className="mt-1 text-2xl font-semibold theme-text">{profitable}</p></Card>
      </div>

      <Card className="mt-4 p-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <select value={strategy} onChange={(e) => setStrategy(e.target.value)} className="rounded-xl border theme-border theme-surface px-3 py-2 text-sm theme-text">
            <option value="ALL">All strategies</option>
            <option value="cash-future">Cash-Future</option>
            <option value="calendar-spread">Calendar Spread</option>
            <option value="synthetic-future-cash-carry">Synthetic Arbitrage</option>
            <option value="box-spread">Box Spread</option>
          </select>
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search symbol, strategy, exit..." className="rounded-xl border theme-border theme-surface px-3 py-2 text-sm theme-text placeholder:theme-muted" />
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="rounded-xl border theme-border theme-surface px-3 py-2 text-sm theme-text" />
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="rounded-xl border theme-border theme-surface px-3 py-2 text-sm theme-text" />
          <button onClick={() => void load()} className="rounded-xl border theme-border theme-surface px-3 py-2 text-sm font-medium theme-text">↻ Refresh</button>
        </div>
      </Card>

      {error ? <Card className="mt-4 p-5 text-sm theme-danger">{error}</Card> : null}
      <Card className="mt-4 overflow-hidden">
        {loading ? <div className="p-8 text-sm theme-muted">Loading history…</div> : filtered.length === 0 ? (
          <div className="p-8 text-sm theme-muted">No completed paper trades match the selected filters.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-[900px] w-full text-left text-sm">
              <thead className="border-b theme-border text-xs uppercase tracking-wide theme-muted">
                <tr>{["Strategy","Symbol","Direction","Entry","Exit","Realized P&L","Exit reason","Closed",""].map((h) => <th key={h} className="px-4 py-3 font-medium">{h}</th>)}</tr>
              </thead>
              <tbody>
                {filtered.map((trade) => (
                  <tr key={trade.id} className="border-b theme-border/70 last:border-0">
                    <td className="px-4 py-3 theme-text">{trade.strategy || "—"}</td>
                    <td className="px-4 py-3 font-medium theme-text">{trade.symbol || "—"}</td>
                    <td className="px-4 py-3 theme-muted">{trade.direction || "—"}</td>
                    <td className="px-4 py-3">{trade.entry_edge ?? "—"}</td>
                    <td className="px-4 py-3">{trade.current_edge ?? "—"}</td>
                    <td className={`px-4 py-3 font-semibold ${Number(trade.realized_pnl || 0) >= 0 ? "theme-success" : "theme-danger"}`}>{money(trade.realized_pnl)}</td>
                    <td className="px-4 py-3 theme-muted">{trade.exit_reason || "—"}</td>
                    <td className="px-4 py-3 theme-muted">{trade.closed_at ? new Date(trade.closed_at).toLocaleString("en-IN") : "—"}</td>
                    <td className="px-4 py-3"><button onClick={() => setSelected(trade)} className="rounded-lg border theme-border px-3 py-1.5 text-xs theme-text">View</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {selected ? (
        <Card className="mt-4 p-5">
          <div className="flex items-center justify-between gap-3">
            <div><p className="text-xs uppercase tracking-wide theme-accent">Trade Detail</p><h2 className="mt-1 text-lg font-semibold theme-text">{selected.symbol || "Trade"} · #{selected.id}</h2></div>
            <button onClick={() => setSelected(null)} className="rounded-lg border theme-border px-3 py-1.5 text-xs theme-text">Close</button>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
            <div><span className="theme-muted">Strategy</span><p className="theme-text">{selected.strategy || "—"}</p></div>
            <div><span className="theme-muted">Direction</span><p className="theme-text">{selected.direction || "—"}</p></div>
            <div><span className="theme-muted">Entry Edge</span><p className="theme-text">{selected.entry_edge ?? "—"}</p></div>
            <div><span className="theme-muted">Exit Edge</span><p className="theme-text">{selected.current_edge ?? "—"}</p></div>
            <div><span className="theme-muted">Capital Used</span><p className="theme-text">{money(selected.capital_used)}</p></div>
            <div><span className="theme-muted">Realized P&L</span><p className={Number(selected.realized_pnl || 0) >= 0 ? "theme-success font-semibold" : "theme-danger font-semibold"}>{money(selected.realized_pnl)}</p></div>
            <div><span className="theme-muted">Exit Reason</span><p className="theme-text">{selected.exit_reason || "—"}</p></div>
            <div><span className="theme-muted">Closed</span><p className="theme-text">{selected.closed_at ? new Date(selected.closed_at).toLocaleString("en-IN") : "—"}</p></div>
          </div>
          <p className="mt-5 text-xs theme-muted">Paper-only history. Broker/live orders remain OFF. Replay controls are intentionally not available.</p>
        </Card>
      ) : null}
    </>
  );
}
